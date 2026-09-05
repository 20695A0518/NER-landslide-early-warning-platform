"""Weather ingestion: IMD, OpenWeather, or a deterministic monsoon simulator.

Provider selection is automatic and ordered by trust:

    1. IMD (Mausam)      authoritative for India; used when IMD_API_KEY is set
    2. OpenWeather       global fallback; used when OPENWEATHER_API_KEY is set
    3. Simulator         always available, never fails

The simulator is not decoration. A landslide early-warning system whose
dashboard goes blank when an upstream API rate-limits is useless precisely when
it matters, so a degraded-but-labelled source beats an empty screen. Every
observation records which provider produced it, and the dashboard renders
simulated data with a visible badge.

Antecedent accumulations (24 h / 72 h / 7 d) are what actually drive slope
failure, and no free endpoint returns them directly. They are therefore
recomputed from this platform's own observation history, which also means the
numbers stay consistent when the upstream provider changes.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.geo import Zone
from app.models.weather import WeatherObservation

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12.0

# Monsoon climatology for the NER: onset late May, peak June-July, withdrawal
# by mid-October. Values are the fraction of annual rainfall falling in each
# month, indexed 1-12 and summing to 1.0.
MONTHLY_RAIN_SHARE = [
    0.008, 0.018, 0.045, 0.095, 0.140, 0.185,
    0.190, 0.150, 0.095, 0.045, 0.019, 0.010,
]


class WeatherSnapshot(dict):
    """A single zone-hour of weather. Plain dict for easy persistence."""


def _active_provider() -> str:
    if settings.imd_api_key:
        return "imd"
    if settings.openweather_api_key:
        return "openweather"
    return "simulator"


# --------------------------------------------------------------------------
# Simulator
# --------------------------------------------------------------------------


def _seasonal_daily_normal(zone: Zone, when: datetime) -> float:
    """Expected daily rainfall for this zone on this date, in mm."""
    share = MONTHLY_RAIN_SHARE[when.month - 1]
    days_in_month = 30.4
    return (zone.annual_rainfall_mm * share) / days_in_month


def _deterministic_noise(zone_id: int, when: datetime, salt: int = 0) -> float:
    """Reproducible pseudo-random value in [0, 1).

    Deterministic on (zone, hour) so repeated calls in one cycle agree, a
    restarted worker produces a continuous series rather than a discontinuity,
    and a demo can be replayed exactly.
    """
    seed = (zone_id * 7919 + int(when.timestamp()) // 3600 * 104729 + salt * 15485863) % 2147483647
    x = math.sin(seed) * 43758.5453
    return x - math.floor(x)


def simulate_observation(zone: Zone, when: datetime | None = None) -> WeatherSnapshot:
    """Generate a physically plausible observation for one zone."""
    when = when or datetime.now(timezone.utc)
    normal = _seasonal_daily_normal(zone, when)

    # --- Daily total -----------------------------------------------------
    # The whole day's rainfall is drawn once, per zone, from a heavy-tailed
    # distribution, and then distributed across the hours.
    #
    # Sampling each hour independently instead would be a modelling error, not
    # just a stylistic one: 24 independent draws average out, every zone lands
    # near the same daily total, and the region shows no spatial contrast at
    # all. Real monsoon rainfall is dominated by synoptic systems that park
    # over one district and miss the next - which is precisely the signal a
    # landslide warning system needs to resolve.
    day_key = when.replace(hour=0, minute=0, second=0, microsecond=0)
    day_var = _deterministic_noise(zone.id, day_key, salt=3)
    # 0.06x normal on a dry day, ~0.7x typical, out to ~7x in a depression.
    day_multiplier = 0.06 + 7.0 * day_var**3.5

    # Multi-day spells: a system sitting over a district for three days is what
    # saturates a slope, so neighbouring days are correlated rather than
    # independent.
    spell_key = day_key - timedelta(days=when.toordinal() % 3)
    spell_var = _deterministic_noise(zone.id, spell_key, salt=4)
    day_multiplier *= 0.55 + 1.5 * spell_var

    # --- Hourly distribution ---------------------------------------------
    hourly_var = _deterministic_noise(zone.id, when, salt=1)
    burst_var = _deterministic_noise(zone.id, when, salt=2)
    # Shape averages ~1.0 across the day, so the hours sum back to the total.
    hour_shape = 0.35 + 1.95 * hourly_var**2

    rain_1h = max(0.0, (normal / 24.0) * day_multiplier * hour_shape)
    peak_intensity = rain_1h * (1.0 + 2.4 * burst_var)

    wet = day_multiplier > 1.0
    temp_seasonal = 26.0 - 8.0 * math.cos((when.month - 1) / 12.0 * 2 * math.pi)
    temperature = temp_seasonal - zone.elevation_m / 165.0 + 3.0 * (hourly_var - 0.5)

    return WeatherSnapshot(
        zone_id=zone.id,
        observed_at=when,
        rainfall_1h_mm=round(rain_1h, 2),
        max_intensity_mm_hr=round(peak_intensity, 2),
        temperature_c=round(temperature, 1),
        humidity_pct=round(min(99.0, 62.0 + 30.0 * min(day_multiplier, 1.2) * hourly_var), 1),
        wind_speed_kmh=round(3.0 + 14.0 * burst_var, 1),
        pressure_hpa=round(
            1008.0 - 6.0 * min(day_multiplier, 1.5) - zone.elevation_m / 90.0, 1
        ),
        source="simulator",
    )


# --------------------------------------------------------------------------
# Live providers
# --------------------------------------------------------------------------


async def _fetch_openweather(client: httpx.AsyncClient, zone: Zone) -> WeatherSnapshot | None:
    try:
        response = await client.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": zone.latitude,
                "lon": zone.longitude,
                "appid": settings.openweather_api_key,
                "units": "metric",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("OpenWeather fetch failed for %s: %s", zone.code, exc)
        return None

    rain_1h = float((payload.get("rain") or {}).get("1h", 0.0))
    main = payload.get("main") or {}
    wind = payload.get("wind") or {}

    return WeatherSnapshot(
        zone_id=zone.id,
        observed_at=datetime.now(timezone.utc),
        rainfall_1h_mm=round(rain_1h, 2),
        max_intensity_mm_hr=round(rain_1h, 2),
        temperature_c=round(float(main.get("temp", 22.0)), 1),
        humidity_pct=round(float(main.get("humidity", 80.0)), 1),
        wind_speed_kmh=round(float(wind.get("speed", 2.0)) * 3.6, 1),
        pressure_hpa=round(float(main.get("pressure", 1006.0)), 1),
        source="openweather",
    )


async def _fetch_imd(client: httpx.AsyncClient, zone: Zone) -> WeatherSnapshot | None:
    """Fetch from the IMD API.

    IMD does not publish a single stable public schema across its endpoints, so
    this reads defensively and returns None on any shape it does not recognise,
    which drops the caller through to the next provider. Adjust the parsing to
    match whichever IMD product the deployment is licensed for.
    """
    try:
        response = await client.get(
            f"{settings.imd_api_base.rstrip('/')}/current",
            params={"lat": zone.latitude, "lon": zone.longitude},
            headers={"Authorization": f"Bearer {settings.imd_api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("IMD fetch failed for %s: %s", zone.code, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("IMD returned an unexpected payload shape for %s", zone.code)
        return None

    try:
        rain_1h = float(payload.get("rainfall_mm") or payload.get("rain_1h") or 0.0)
        temperature = float(payload.get("temperature") or payload.get("temp") or 22.0)
        humidity = float(payload.get("humidity") or 80.0)
    except (TypeError, ValueError):
        logger.warning("IMD payload for %s had non-numeric fields", zone.code)
        return None

    return WeatherSnapshot(
        zone_id=zone.id,
        observed_at=datetime.now(timezone.utc),
        rainfall_1h_mm=round(rain_1h, 2),
        max_intensity_mm_hr=round(rain_1h, 2),
        temperature_c=round(temperature, 1),
        humidity_pct=round(humidity, 1),
        wind_speed_kmh=round(float(payload.get("wind_speed") or 5.0), 1),
        pressure_hpa=round(float(payload.get("pressure") or 1006.0), 1),
        source="imd",
    )


# --------------------------------------------------------------------------
# Accumulation + persistence
# --------------------------------------------------------------------------


def compute_accumulations(db: Session, zone_id: int, now: datetime, rain_1h: float) -> dict:
    """Roll this platform's own observation history into antecedent totals."""
    windows = {"rainfall_24h_mm": 24, "rainfall_72h_mm": 72, "rainfall_7d_mm": 168,
               "rainfall_15d_mm": 360}
    result: dict[str, float] = {}

    for field, hours in windows.items():
        since = now - timedelta(hours=hours)
        rows = db.execute(
            select(WeatherObservation.rainfall_1h_mm).where(
                WeatherObservation.zone_id == zone_id,
                WeatherObservation.observed_at >= since,
                WeatherObservation.is_forecast.is_(False),
            )
        ).scalars().all()
        result[field] = round(sum(rows) + rain_1h, 2)

    # Observations are hourly at best and often sparser. Scale the shortest
    # window up to a true 24-hour equivalent so a zone polled four times today
    # is not reported as four times drier than one polled hourly.
    observed_hours = db.execute(
        select(WeatherObservation.id).where(
            WeatherObservation.zone_id == zone_id,
            WeatherObservation.observed_at >= now - timedelta(hours=24),
        )
    ).scalars().all()
    sample_count = len(observed_hours) + 1
    if sample_count < 24:
        scale = min(24.0 / sample_count, 6.0)
        result["rainfall_24h_mm"] = round(result["rainfall_24h_mm"] * scale, 2)
        result["rainfall_72h_mm"] = round(
            max(result["rainfall_72h_mm"] * scale, result["rainfall_24h_mm"]), 2
        )
        result["rainfall_7d_mm"] = round(
            max(result["rainfall_7d_mm"] * scale, result["rainfall_72h_mm"]), 2
        )
        result["rainfall_15d_mm"] = round(
            max(result["rainfall_15d_mm"] * scale, result["rainfall_7d_mm"]), 2
        )

    return result


async def refresh_all_zones(db: Session) -> dict:
    """Poll every active zone and persist one observation each."""
    zones = db.execute(select(Zone).where(Zone.is_active.is_(True))).scalars().all()
    provider = _active_provider()
    now = datetime.now(timezone.utc)

    snapshots: list[WeatherSnapshot] = []
    if provider == "simulator":
        snapshots = [simulate_observation(z, now) for z in zones]
    else:
        fetch = _fetch_imd if provider == "imd" else _fetch_openweather
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for zone in zones:
                snap = await fetch(client, zone)
                # Per-zone fallback, not all-or-nothing: one bad response must
                # not blank the whole region.
                snapshots.append(snap or simulate_observation(zone, now))

    written = 0
    for snap in snapshots:
        accumulations = compute_accumulations(db, snap["zone_id"], now, snap["rainfall_1h_mm"])
        db.add(WeatherObservation(**snap, **accumulations))
        written += 1

    db.commit()
    degraded = sum(1 for s in snapshots if s["source"] == "simulator")
    return {
        "provider": provider,
        "zones_updated": written,
        "simulated": degraded,
        "live": written - degraded,
        "observed_at": now.isoformat(),
    }


def latest_for_zone(db: Session, zone_id: int) -> WeatherObservation | None:
    return db.execute(
        select(WeatherObservation)
        .where(WeatherObservation.zone_id == zone_id, WeatherObservation.is_forecast.is_(False))
        .order_by(WeatherObservation.observed_at.desc())
        .limit(1)
    ).scalars().first()


def build_forecast(db: Session, zone: Zone, horizons: tuple[int, ...] = (6, 12, 24, 48)) -> list[dict]:
    """Project rainfall forward for the weather-linked risk outlook.

    Persistence-plus-climatology: near horizons stay close to what is falling
    now, far horizons relax toward the seasonal normal. Crude, but honest about
    its own skill, and replaceable by an IMD gridded forecast product without
    touching callers.
    """
    latest = latest_for_zone(db, zone.id)
    current_rate = latest.rainfall_1h_mm if latest else 0.0
    now = datetime.now(timezone.utc)
    normal_hourly = _seasonal_daily_normal(zone, now) / 24.0

    forecasts = []
    for horizon in horizons:
        decay = math.exp(-horizon / 30.0)
        projected_rate = current_rate * decay + normal_hourly * (1 - decay)
        expected_total = projected_rate * horizon
        forecasts.append(
            {
                "horizon_hours": horizon,
                "expected_rainfall_mm": round(expected_total, 1),
                "expected_intensity_mm_hr": round(projected_rate, 2),
                "confidence": round(max(0.25, 0.9 - horizon / 90.0), 2),
                "source": f"{_active_provider()}+persistence",
            }
        )
    return forecasts


def provider_status() -> dict:
    provider = _active_provider()
    return {
        "active_provider": provider,
        "is_live": provider != "simulator",
        "imd_configured": bool(settings.imd_api_key),
        "openweather_configured": bool(settings.openweather_api_key),
        "poll_interval_minutes": settings.weather_poll_minutes,
        "note": (
            "No weather API key configured - rainfall is generated by the "
            "built-in monsoon simulator and is NOT real observed data."
        )
        if provider == "simulator"
        else None,
    }
