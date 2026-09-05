"""Populate a fresh database with the NER reference data and demo accounts.

Idempotent: re-running updates existing rows by natural key rather than
duplicating them, so `seed()` is safe to call on every startup in development.

The historical landslide inventory produced here is SYNTHETIC. It is generated
from each zone's susceptibility so the map and the "past events" panel are
coherent, and every row is stamped `source="synthetic seed"` so it can be told
apart from imported records and deleted wholesale before real data lands. It
must not be cited as a record of actual events.
"""

from __future__ import annotations

import logging
import math
import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.data.ner_roads import NER_ROADS
from app.data.ner_zones import NER_ZONES
from app.ml.features import ROOT_COHESION_KPA
from app.ml.physics import calibrate_suction_cohesion, seismic_amplification
from app.models.enums import Language, Role, SensorStatus
from app.models.geo import HistoricalLandslide, RoadSegment, Zone
from app.models.sensor import SensorStation
from app.models.user import User
from app.models.weather import WeatherObservation
from app.services import weather as weather_service

logger = logging.getLogger(__name__)

SYNTHETIC_SOURCE = "synthetic seed"


def _polygon_for(lat: float, lon: float, area_sq_km: float) -> dict:
    """Approximate the zone footprint as a hexagon around its centroid.

    Real slope-unit polygons come from watershed segmentation of a DEM. Until
    those are loaded, a correctly-sized hexagon renders honestly on the map and
    keeps the GeoJSON contract stable.
    """
    radius_km = math.sqrt(area_sq_km / (1.5 * math.sqrt(3))) or 0.5
    dlat = radius_km / 110.574
    dlon = radius_km / (111.320 * math.cos(math.radians(lat)))

    ring = []
    for i in range(6):
        angle = math.radians(60 * i)
        ring.append([round(lon + dlon * math.cos(angle), 6), round(lat + dlat * math.sin(angle), 6)])
    ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def seed_zones(db: Session) -> int:
    """Insert or refresh every monitored zone, applying strength back-analysis."""
    existing = {z.code: z for z in db.execute(select(Zone)).scalars().all()}
    written = 0

    for record in NER_ZONES:
        data = dict(record)
        amplification = seismic_amplification(data["seismic_zone"], data["slope_deg"])
        data["suction_cohesion_kpa"] = calibrate_suction_cohesion(
            slope_deg=data["slope_deg"],
            soil_depth_m=data["soil_depth_m"],
            friction_angle_deg=data["friction_angle_deg"],
            cohesion_kpa=data["cohesion_kpa"],
            root_cohesion_kpa=ROOT_COHESION_KPA.get(data["land_cover"], 1.0),
            amplification=amplification,
        )
        data["geometry"] = _polygon_for(data["latitude"], data["longitude"], data["area_sq_km"])

        zone = existing.get(data["code"])
        if zone is None:
            db.add(Zone(**data))
        else:
            for key, value in data.items():
                setattr(zone, key, value)
        written += 1

    db.commit()
    return written


def seed_roads(db: Session) -> int:
    existing = {r.code: r for r in db.execute(select(RoadSegment)).scalars().all()}
    for record in NER_ROADS:
        road = existing.get(record["code"])
        if road is None:
            db.add(RoadSegment(**record))
        else:
            for key, value in record.items():
                setattr(road, key, value)
    db.commit()
    return len(NER_ROADS)


def seed_sensors(db: Session) -> int:
    """Install a plausible instrument network, weighted toward risky zones.

    Real networks are not uniform - money goes where the last disaster was and
    where a highway needs protecting. Zones with steep slopes and heavy hill
    cutting get a full station; gentler zones get a rain gauge or nothing.
    """
    zones = db.execute(select(Zone)).scalars().all()
    existing = {s.code: s for s in db.execute(select(SensorStation)).scalars().all()}
    rng = random.Random(4711)
    created = 0

    for zone in zones:
        exposure = zone.slope_deg / 50.0 + zone.hill_cutting_index
        if exposure > 1.35:
            station_count, capabilities = 2, "soil_moisture,pore_pressure,tilt,rain_gauge"
        elif exposure > 0.95:
            station_count, capabilities = 1, "soil_moisture,tilt,rain_gauge"
        elif exposure > 0.6:
            station_count, capabilities = 1, "soil_moisture,rain_gauge"
        else:
            continue

        for index in range(station_count):
            code = f"{zone.code}-S{index + 1}"
            if code in existing:
                continue
            # Offset each station a few hundred metres from the centroid.
            db.add(
                SensorStation(
                    code=code,
                    name=f"{zone.name} station {index + 1}",
                    zone_id=zone.id,
                    latitude=round(zone.latitude + rng.uniform(-0.008, 0.008), 6),
                    longitude=round(zone.longitude + rng.uniform(-0.008, 0.008), 6),
                    installed_depth_m=round(min(zone.soil_depth_m * 0.7, 2.5), 2),
                    capabilities=capabilities,
                    status=SensorStatus.ONLINE,
                    battery_pct=round(rng.uniform(42, 100), 1),
                    signal_strength=rng.randint(2, 5),
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            created += 1

    db.commit()
    return created


def seed_users(db: Session) -> list[dict]:
    """Create one demo account per role. Passwords are for development only."""
    demo = [
        dict(username="admin", full_name="Platform Administrator", role=Role.ADMIN,
             designation="NER Disaster Data Cell", phone="+919000000001",
             state=None, district=None, language=Language.EN, password="admin123"),
        dict(username="sdma.mizoram", full_name="R. Lalthanmawia", role=Role.DM_AUTHORITY,
             designation="State Disaster Management Authority", phone="+919000000002",
             state="Mizoram", district="Aizawl", language=Language.LUS, password="prahari123"),
        dict(username="dc.aizawl", full_name="Lalrinpuii Sailo", role=Role.DISTRICT_OFFICER,
             designation="Deputy Commissioner, Aizawl", phone="+919000000003",
             state="Mizoram", district="Aizawl", language=Language.LUS, password="prahari123"),
        dict(username="dc.dimahasao", full_name="Nabanita Bora", role=Role.DISTRICT_OFFICER,
             designation="Deputy Commissioner, Dima Hasao", phone="+919000000004",
             state="Assam", district="Dima Hasao", language=Language.AS, password="prahari123"),
        dict(username="dc.eastkhasi", full_name="Banteilang Syiem", role=Role.DISTRICT_OFFICER,
             designation="Deputy Commissioner, East Khasi Hills", phone="+919000000005",
             state="Meghalaya", district="East Khasi Hills", language=Language.KHA,
             password="prahari123"),
        dict(username="field.noney", full_name="Kaisii Gonmei", role=Role.FIELD_OFFICER,
             designation="Circle Officer, Noney", phone="+919000000006",
             state="Manipur", district="Noney", language=Language.MNI, password="prahari123"),
        dict(username="field.gangtok", full_name="Pema Bhutia", role=Role.FIELD_OFFICER,
             designation="Highway Patrol, NH-10", phone="+919000000007",
             state="Sikkim", district="Gangtok", language=Language.NE, password="prahari123"),
        dict(username="citizen.aizawl", full_name="Zothanpuia", role=Role.CITIZEN,
             designation=None, phone="+919000000008",
             state="Mizoram", district="Aizawl", language=Language.LUS, password="prahari123"),
    ]

    existing = {u.username for u in db.execute(select(User)).scalars().all()}
    created = []
    for record in demo:
        if record["username"] in existing:
            continue
        password = record.pop("password")
        db.add(User(**record, hashed_password=hash_password(password)))
        created.append({"username": record["username"], "role": str(record["role"]),
                        "password": password})
    db.commit()
    return created


def seed_history(db: Session, years: int = 8) -> int:
    """Generate a synthetic landslide inventory consistent with zone terrain."""
    if db.query(HistoricalLandslide).filter(
        HistoricalLandslide.source == SYNTHETIC_SOURCE
    ).count():
        return 0

    zones = db.execute(select(Zone)).scalars().all()
    rng = random.Random(90210)
    today = date.today()
    created = 0

    for zone in zones:
        # Expected events scale with steepness, weak cover and hill cutting.
        rate = (
            (zone.slope_deg / 45.0) ** 2
            * (1.0 + zone.hill_cutting_index)
            * (zone.annual_rainfall_mm / 2500.0) ** 0.5
        )
        count = min(int(rng.gauss(rate * years * 0.55, 1.2)), 14)

        for _ in range(max(count, 0)):
            # Monsoon-weighted: June-September carries most of the inventory.
            year = today.year - rng.randint(0, years - 1)
            month = rng.choices(
                [4, 5, 6, 7, 8, 9, 10],
                weights=[3, 9, 22, 26, 20, 12, 5],
            )[0]
            day = rng.randint(1, 28)
            event_date = date(year, month, day)
            if event_date > today:
                continue

            severity = rng.choices(
                ["minor", "moderate", "major"], weights=[55, 33, 12]
            )[0]
            fatalities = {"minor": 0, "moderate": rng.randint(0, 2),
                          "major": rng.randint(1, 18)}[severity]

            db.add(
                HistoricalLandslide(
                    event_date=event_date,
                    latitude=round(zone.latitude + rng.uniform(-0.02, 0.02), 6),
                    longitude=round(zone.longitude + rng.uniform(-0.02, 0.02), 6),
                    district=zone.district,
                    state=zone.state,
                    zone_code=zone.code,
                    trigger=rng.choices(
                        ["rainfall", "rainfall_and_cutting", "seismic", "toe_erosion"],
                        weights=[62, 25, 6, 7],
                    )[0],
                    magnitude=severity,
                    fatalities=fatalities,
                    injured=fatalities * rng.randint(1, 3),
                    houses_damaged={"minor": rng.randint(0, 3), "moderate": rng.randint(2, 12),
                                    "major": rng.randint(8, 60)}[severity],
                    road_blocked_hours=round(
                        {"minor": rng.uniform(1, 8), "moderate": rng.uniform(6, 48),
                         "major": rng.uniform(24, 240)}[severity], 1
                    ),
                    rainfall_72h_mm=round(
                        zone.annual_rainfall_mm / 365 * rng.uniform(8, 26), 1
                    ),
                    description=f"{severity.title()} slope failure near {zone.name}.",
                    source=SYNTHETIC_SOURCE,
                )
            )
            created += 1

    db.commit()

    # Feed the counts back onto zones - the model uses them as a prior.
    for zone in zones:
        zone.historical_event_count = (
            db.query(HistoricalLandslide)
            .filter(HistoricalLandslide.zone_code == zone.code)
            .count()
        )
    db.commit()
    return created


def seed_weather_history(db: Session, hours: int = 96) -> int:
    """Backfill enough observations for antecedent windows to be meaningful.

    Without this the first risk cycle sees a 72-hour rainfall total of nearly
    zero for every zone and reports the entire region as safe - which is both
    wrong and an unconvincing first impression.
    """
    if db.query(WeatherObservation).count():
        return 0

    zones = db.execute(select(Zone)).scalars().all()
    now = datetime.now(timezone.utc)
    written = 0

    for zone in zones:
        running: list[float] = []
        for hours_ago in range(hours, 0, -1):
            when = now - timedelta(hours=hours_ago)
            snapshot = weather_service.simulate_observation(zone, when)
            running.append(snapshot["rainfall_1h_mm"])

            db.add(
                WeatherObservation(
                    **snapshot,
                    rainfall_24h_mm=round(sum(running[-24:]), 2),
                    rainfall_72h_mm=round(sum(running[-72:]), 2),
                    rainfall_7d_mm=round(sum(running), 2),
                    rainfall_15d_mm=round(sum(running), 2),
                )
            )
            written += 1
        db.commit()

    return written


def seed(db: Session, include_history: bool = True) -> dict:
    """Full seed. Safe to re-run."""
    summary = {
        "zones": seed_zones(db),
        "roads": seed_roads(db),
        "sensors": seed_sensors(db),
        "users": seed_users(db),
    }
    if include_history:
        summary["historical_events"] = seed_history(db)
        summary["weather_observations"] = seed_weather_history(db)
    logger.info("Seed complete: %s", summary)
    return summary
