"""Sensor telemetry: ingestion, health tracking and anomaly scoring.

Real deployments will POST to /api/v1/sensors/readings from LoRaWAN or NB-IoT
gateways; `simulate_round` fills the same tables when no hardware is installed
so the rest of the pipeline can be exercised end to end.

The anomaly score is deliberately not a learned model. With a handful of
stations per district there is nowhere near enough labelled data to train an
anomaly detector, and an unsupervised one would spend its first monsoon
learning that monsoons are anomalous. Instead it is an explicit, auditable
combination of the signals that actually precede a shallow failure:
sustained pore-pressure rise, accelerating tilt, absolute tilt magnitude,
and soil moisture at capacity.
A district engineer can check each term by hand, which matters when the output
is used to close a highway.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import SensorStatus
from app.models.geo import Zone
from app.models.sensor import SensorReading, SensorStation
from app.models.weather import WeatherObservation
from app.utils.timeutil import as_utc

logger = logging.getLogger(__name__)

# A station silent for longer than this is treated as offline.
OFFLINE_AFTER_MINUTES = 180
DEGRADED_BATTERY_PCT = 25.0

# Tilt beyond this over 24 h is treated as active movement rather than noise.
TILT_RATE_ALARM_DEG_PER_DAY = 0.35


def record_reading(db: Session, station: SensorStation, payload: dict) -> SensorReading:
    """Persist one reading and update the station's health state."""
    reading = SensorReading(
        station_id=station.id,
        recorded_at=payload.get("recorded_at") or datetime.now(timezone.utc),
        soil_moisture_pct=payload.get("soil_moisture_pct"),
        pore_pressure_kpa=payload.get("pore_pressure_kpa"),
        tilt_deg=payload.get("tilt_deg"),
        displacement_mm=payload.get("displacement_mm"),
        ground_vibration_mm_s=payload.get("ground_vibration_mm_s"),
        rainfall_mm=payload.get("rainfall_mm"),
        temperature_c=payload.get("temperature_c"),
        battery_pct=payload.get("battery_pct"),
    )
    db.add(reading)

    station.last_seen_at = reading.recorded_at
    if reading.battery_pct is not None:
        station.battery_pct = reading.battery_pct
    station.status = (
        SensorStatus.DEGRADED
        if (station.battery_pct or 100) < DEGRADED_BATTERY_PCT
        else SensorStatus.ONLINE
    )
    return reading


def refresh_station_health(db: Session) -> dict:
    """Mark stations offline when they stop reporting."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=OFFLINE_AFTER_MINUTES)
    stations = db.execute(select(SensorStation)).scalars().all()

    counts = {"online": 0, "degraded": 0, "offline": 0}
    for station in stations:
        if station.last_seen_at is None or as_utc(station.last_seen_at) < cutoff:
            station.status = SensorStatus.OFFLINE
        elif (station.battery_pct or 100) < DEGRADED_BATTERY_PCT:
            station.status = SensorStatus.DEGRADED
        else:
            station.status = SensorStatus.ONLINE
        counts[str(station.status)] += 1

    db.commit()
    return counts


def latest_readings_for_zone(db: Session, zone_id: int, hours: int = 6) -> list[SensorReading]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return list(
        db.execute(
            select(SensorReading)
            .join(SensorStation, SensorReading.station_id == SensorStation.id)
            .where(SensorStation.zone_id == zone_id, SensorReading.recorded_at >= since)
            .order_by(SensorReading.recorded_at.desc())
        ).scalars().all()
    )


def zone_sensor_state(db: Session, zone_id: int) -> dict:
    """Current instrument state for a zone, as consumed by the risk engine."""
    recent = latest_readings_for_zone(db, zone_id, hours=6)
    if not recent:
        return {
            "soil_moisture_pct": None,
            "pore_pressure_kpa": None,
            "tilt_deg": None,
            "has_data": False,
            "reading_count": 0,
        }

    def _mean(attr: str) -> float | None:
        values = [getattr(r, attr) for r in recent if getattr(r, attr) is not None]
        return sum(values) / len(values) if values else None

    return {
        "soil_moisture_pct": _mean("soil_moisture_pct"),
        "pore_pressure_kpa": _mean("pore_pressure_kpa"),
        "tilt_deg": _mean("tilt_deg"),
        "displacement_mm": _mean("displacement_mm"),
        "has_data": True,
        "reading_count": len(recent),
        "last_reading_at": recent[0].recorded_at.isoformat(),
    }


def anomaly_score(db: Session, zone_id: int) -> tuple[float, list[str]]:
    """Score 0-1 for how anomalous a zone's instruments look, with reasons.

    Returns (score, human-readable reasons). A zone with no instrumentation
    scores 0.0 - the risk engine treats that as "no evidence", never as
    "no problem".
    """
    recent = latest_readings_for_zone(db, zone_id, hours=24)
    if not recent:
        return 0.0, []

    reasons: list[str] = []
    score = 0.0

    # Trend terms below need at least three samples to estimate a rate; the
    # absolute-threshold term does not. Gating the whole function on a sample
    # count would silently ignore a station reporting 43% soil moisture - a
    # saturated column - purely because it had only reported twice today,
    # which is the normal state of a solar node on a cloudy monsoon week.
    has_trend = len(recent) >= 3

    # --- 1. Tilt acceleration -------------------------------------------
    # An inclinometer trending in one direction is the single most direct
    # precursor available: the slope is already moving.
    tilts = [(r.recorded_at, r.tilt_deg) for r in recent if r.tilt_deg is not None]
    if has_trend and len(tilts) >= 3:
        tilts.sort(key=lambda t: t[0])
        span_hours = max(
            (as_utc(tilts[-1][0]) - as_utc(tilts[0][0])).total_seconds() / 3600.0, 0.5
        )
        tilt_rate = (tilts[-1][1] - tilts[0][1]) / span_hours * 24.0
        if tilt_rate > TILT_RATE_ALARM_DEG_PER_DAY:
            contribution = min(tilt_rate / (TILT_RATE_ALARM_DEG_PER_DAY * 3), 1.0)
            score += 0.45 * contribution
            reasons.append(f"Ground tilt increasing at {tilt_rate:.2f} deg/day")

    # --- 2. Pore pressure ------------------------------------------------
    pressures = [r.pore_pressure_kpa for r in recent if r.pore_pressure_kpa is not None]
    if has_trend and len(pressures) >= 3:
        newest = sum(pressures[: max(len(pressures) // 3, 1)]) / max(len(pressures) // 3, 1)
        oldest = sum(pressures[-max(len(pressures) // 3, 1):]) / max(len(pressures) // 3, 1)
        if oldest > 0.5 and newest > oldest * 1.4:
            contribution = min((newest / oldest - 1.0) / 1.5, 1.0)
            score += 0.35 * contribution
            reasons.append(
                f"Pore pressure risen {((newest / oldest) - 1) * 100:.0f}% in 24 h"
            )

    # --- 3. Soil moisture at capacity ------------------------------------
    moistures = [r.soil_moisture_pct for r in recent if r.soil_moisture_pct is not None]
    if moistures:
        peak = max(moistures)
        if peak > 38.0:
            score += 0.30 * min((peak - 38.0) / 8.0, 1.0)
            reasons.append(f"Soil moisture at {peak:.0f}%, near saturation")

    # --- 4. Absolute tilt -------------------------------------------------
    # A large standing tilt is evidence on its own, with no history required.
    tilt_values = [r.tilt_deg for r in recent if r.tilt_deg is not None]
    if tilt_values:
        peak_tilt = max(tilt_values)
        if peak_tilt > 0.6:
            score += 0.30 * min((peak_tilt - 0.6) / 1.4, 1.0)
            reasons.append(f"Inclinometer reading {peak_tilt:.2f} deg off vertical")

    return round(min(score, 1.0), 3), reasons


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


def simulate_round(db: Session) -> dict:
    """Generate one reading per station, driven by that zone's actual rainfall.

    Coupling the simulated instruments to the persisted weather (rather than to
    independent noise) is what makes the demo coherent: soil moisture rises
    where it is raining, and the anomaly score responds for the right reason.
    """
    stations = db.execute(select(SensorStation)).scalars().all()
    now = datetime.now(timezone.utc)
    written = 0

    for station in stations:
        weather = db.execute(
            select(WeatherObservation)
            .where(WeatherObservation.zone_id == station.zone_id)
            .order_by(WeatherObservation.observed_at.desc())
            .limit(1)
        ).scalars().first()
        zone = db.get(Zone, station.zone_id)
        if zone is None:
            continue

        r72 = weather.rainfall_72h_mm if weather else 0.0
        r24 = weather.rainfall_24h_mm if weather else 0.0
        capacity = max(zone.soil_depth_m, 0.3) * 320.0
        wetness = min(1.0, (r24 + 0.5 * r72) / max(capacity, 1.0))

        caps = station.capabilities or ""
        payload: dict = {"recorded_at": now, "temperature_c": weather.temperature_c if weather else 22.0}

        if "soil_moisture" in caps:
            payload["soil_moisture_pct"] = round(12.0 + wetness * 30.0, 1)
        if "pore_pressure" in caps:
            payload["pore_pressure_kpa"] = round(wetness * 9.81 * zone.soil_depth_m * 0.8, 2)
        if "tilt" in caps:
            # Tilt grows non-linearly once the slope is genuinely wet.
            payload["tilt_deg"] = round(0.02 + 0.9 * max(0.0, wetness - 0.55) ** 1.6, 3)
            payload["displacement_mm"] = round(payload["tilt_deg"] * 34.0, 2)
        if "rain_gauge" in caps:
            payload["rainfall_mm"] = round(weather.rainfall_1h_mm if weather else 0.0, 2)

        # Battery discharges faster in the wet season when the panel sees less sun.
        drain = 0.05 + 0.12 * wetness
        payload["battery_pct"] = round(max(8.0, (station.battery_pct or 100.0) - drain), 1)

        record_reading(db, station, payload)
        written += 1

    db.commit()
    return {"stations": len(stations), "readings_written": written, "at": now.isoformat()}


def seed_battery_variation(db: Session) -> None:
    """Give stations differing battery levels so health monitoring is visible."""
    stations = db.execute(select(SensorStation)).scalars().all()
    for index, station in enumerate(stations):
        station.battery_pct = round(30.0 + 68.0 * abs(math.sin(index * 1.7)), 1)
    db.commit()
