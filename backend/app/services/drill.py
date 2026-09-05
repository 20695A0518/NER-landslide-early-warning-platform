"""Scenario injection for drills, training and acceptance testing.

Why this exists
---------------
On a normal day the region is quiet, which is the correct output and a useless
demonstration: the alerting chain, the response queue and the SMS path are all
invisible until something actually happens. Waiting for a real monsoon
depression to test them is not an option.

State Disaster Management Authorities run tabletop exercises for exactly this
reason, so a drill mode is an operational requirement, not a demo shortcut. It
injects a defined rainfall event, re-scores the affected slopes and lets the
whole warning pipeline run end to end.

Safety properties
-----------------
Every injected observation is written with `source="drill"`, never `"imd"` or
`"simulator"`. That makes drill data:

  * distinguishable in the database from real or simulated observations,
  * removable in one statement (`clear_drill`), and
  * visible in the UI, which surfaces a banner whenever any drill observation
    is live.

Alerts generated during a drill are real Alert rows and would really be sent if
a live SMS provider were configured. A drill on a production deployment must
therefore be run with the provider set to `console`, or with a test audience.
The endpoint is restricted to administrators and to state-level authorities for
that reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.geo import Zone
from app.models.weather import WeatherObservation

logger = logging.getLogger(__name__)

DRILL_SOURCE = "drill"

# Rainfall delivered over the drill window, as a multiple of the zone's own
# mean daily rainfall. Normalising to local climatology keeps a single scenario
# meaningful at both Mawsynram and Imphal.
INTENSITY_PROFILES: dict[str, dict] = {
    "moderate": {"multiplier": 3.0, "burst_fraction": 0.14,
                 "label": "Moderate monsoon spell"},
    "heavy": {"multiplier": 7.0, "burst_fraction": 0.18,
              "label": "Heavy rainfall warning"},
    "extreme": {"multiplier": 13.0, "burst_fraction": 0.24,
                "label": "Extreme event / cloudburst"},
}


def active_drill(db: Session) -> dict | None:
    """Report whether any drill observation is still inside a scoring window."""
    latest = db.execute(
        select(WeatherObservation)
        .where(WeatherObservation.source == DRILL_SOURCE)
        .order_by(WeatherObservation.observed_at.desc())
        .limit(1)
    ).scalars().first()
    if latest is None:
        return None

    count = (
        db.query(WeatherObservation)
        .filter(WeatherObservation.source == DRILL_SOURCE)
        .count()
    )
    zones = (
        db.query(WeatherObservation.zone_id)
        .filter(WeatherObservation.source == DRILL_SOURCE)
        .distinct()
        .count()
    )
    return {
        "active": True,
        "observations": count,
        "zones": zones,
        "latest_at": latest.observed_at.isoformat(),
        "warning": (
            "Drill rainfall is present in the database. Risk levels and alerts "
            "on this deployment are exercise data, not real conditions."
        ),
    }


def inject(
    db: Session,
    zone_codes: list[str] | None = None,
    state: str | None = None,
    intensity: str = "heavy",
    duration_hours: int = 24,
) -> dict:
    """Write a rainfall event into the observation history.

    The event is back-dated across `duration_hours` so the antecedent windows
    (24 h / 72 h / 7 d) pick it up the same way they would a real storm - a
    single spike at the current timestamp would raise the 24-hour total but
    leave the slope's saturation history untouched, which is not how a storm
    destabilises a hillside.
    """
    profile = INTENSITY_PROFILES.get(intensity)
    if profile is None:
        raise ValueError(
            f"Unknown intensity '{intensity}'. Choose one of: "
            f"{', '.join(INTENSITY_PROFILES)}"
        )

    query = select(Zone).where(Zone.is_active.is_(True))
    if zone_codes:
        query = query.where(Zone.code.in_(zone_codes))
    elif state:
        query = query.where(Zone.state == state)
    zones = db.execute(query).scalars().all()

    if not zones:
        raise ValueError("No zones matched the drill selection")

    now = datetime.now(timezone.utc)
    written = 0
    affected: list[dict] = []

    for zone in zones:
        daily_normal = max(zone.annual_rainfall_mm / 365.0, 1.0)
        event_total = daily_normal * profile["multiplier"] * (duration_hours / 24.0)
        hourly = event_total / duration_hours

        running: list[float] = []
        for hours_ago in range(duration_hours, 0, -1):
            when = now - timedelta(hours=hours_ago)
            # Front-load slightly so intensity peaks partway through, as a
            # real convective event does.
            phase = 1.0 - abs((duration_hours - hours_ago) / duration_hours - 0.55) * 1.4
            rain = max(0.0, hourly * max(phase, 0.25) * 1.6)
            running.append(rain)

            db.add(
                WeatherObservation(
                    zone_id=zone.id,
                    observed_at=when,
                    rainfall_1h_mm=round(rain, 2),
                    rainfall_24h_mm=round(sum(running[-24:]), 2),
                    rainfall_72h_mm=round(sum(running[-72:]), 2),
                    rainfall_7d_mm=round(sum(running), 2),
                    rainfall_15d_mm=round(sum(running), 2),
                    max_intensity_mm_hr=round(rain * (1.0 + profile["burst_fraction"] * 8), 2),
                    temperature_c=round(21.0 - zone.elevation_m / 200.0, 1),
                    humidity_pct=97.0,
                    wind_speed_kmh=18.0,
                    pressure_hpa=round(996.0 - zone.elevation_m / 90.0, 1),
                    source=DRILL_SOURCE,
                )
            )
            written += 1

        affected.append(
            {
                "code": zone.code,
                "name": zone.name,
                "district": zone.district,
                "state": zone.state,
                "event_total_mm": round(event_total, 1),
                "daily_normal_mm": round(daily_normal, 1),
            }
        )

    db.commit()
    logger.warning(
        "DRILL: injected %s rainfall over %d h into %d zones (%d observations)",
        intensity, duration_hours, len(zones), written,
    )
    return {
        "intensity": intensity,
        "label": profile["label"],
        "duration_hours": duration_hours,
        "zones_affected": len(zones),
        "observations_written": written,
        "zones": affected,
    }


def clear(db: Session) -> int:
    """Remove every drill observation. Real and simulated data are untouched."""
    result = db.execute(
        delete(WeatherObservation).where(WeatherObservation.source == DRILL_SOURCE)
    )
    db.commit()
    removed = result.rowcount or 0
    logger.warning("DRILL: cleared %d drill observations", removed)
    return removed
