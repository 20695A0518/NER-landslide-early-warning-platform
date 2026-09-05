"""Weather observations, forecasts and the weather-linked risk outlook."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import RiskLevel, Role
from app.models.geo import Zone
from app.models.weather import WeatherObservation
from app.schemas.models import WeatherOut, ZoneForecast
from app.services import weather as weather_service

router = APIRouter(prefix="/weather", tags=["weather"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("/status")
def provider_status():
    """Which upstream provider is live, and whether data is simulated."""
    return weather_service.provider_status()


@router.get("/observations", response_model=list[WeatherOut])
def observations(
    db: DbSession,
    zone_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
):
    query = select(WeatherObservation).where(WeatherObservation.is_forecast.is_(False))
    if zone_id:
        query = query.where(WeatherObservation.zone_id == zone_id)
    return db.execute(
        query.order_by(desc(WeatherObservation.observed_at)).limit(limit)
    ).scalars().all()


@router.get("/rainfall-leaders")
def rainfall_leaders(db: DbSession, limit: int = Query(default=10, ge=1, le=50)):
    """Zones with the most unusual recent rainfall, against their own normals.

    Ranking by absolute rainfall would return the Khasi hills every single day.
    Ranking by departure from the local normal surfaces the places where the
    rain is actually anomalous - which is where slopes fail.
    """
    zones = {z.id: z for z in db.execute(select(Zone)).scalars().all()}
    rows: dict[int, WeatherObservation] = {}
    for observation in db.execute(
        select(WeatherObservation)
        .where(WeatherObservation.is_forecast.is_(False))
        .order_by(WeatherObservation.zone_id, desc(WeatherObservation.observed_at))
    ).scalars().all():
        rows.setdefault(observation.zone_id, observation)

    leaders = []
    for zone_id, observation in rows.items():
        zone = zones.get(zone_id)
        if zone is None:
            continue
        daily_normal = max(zone.annual_rainfall_mm / 365.0, 0.5)
        leaders.append(
            {
                "zone_id": zone.id,
                "code": zone.code,
                "name": zone.name,
                "district": zone.district,
                "state": zone.state,
                "rainfall_24h_mm": observation.rainfall_24h_mm,
                "rainfall_72h_mm": observation.rainfall_72h_mm,
                "daily_normal_mm": round(daily_normal, 1),
                "anomaly_ratio": round(observation.rainfall_24h_mm / daily_normal, 2),
                "source": observation.source,
            }
        )

    leaders.sort(key=lambda r: r["anomaly_ratio"], reverse=True)
    return leaders[:limit]


@router.get("/forecast/{zone_id}", response_model=ZoneForecast)
def forecast(db: DbSession, zone_id: int):
    """Rainfall outlook, and the risk level that outlook would imply."""
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")

    points = weather_service.build_forecast(db, zone)

    # Re-score the zone under the 24-hour projection, so the dashboard can say
    # what the rain is expected to do - not only what it has already done.
    from app.ml.features import zone_to_dict
    from app.ml.predictor import predict
    from app.services import risk_engine
    from app.services import sensors as sensor_service

    horizon = next((p for p in points if p["horizon_hours"] == 24), points[-1])
    latest = weather_service.latest_for_zone(db, zone.id)
    r24 = latest.rainfall_24h_mm if latest else 0.0
    r72 = latest.rainfall_72h_mm if latest else 0.0
    r7d = latest.rainfall_7d_mm if latest else 0.0
    expected = horizon["expected_rainfall_mm"]

    # Advance the accumulation windows by 24 hours rather than simply adding
    # the forecast to today's totals. At t+24 the 72-hour window is
    # [t-48, t+24], so the oldest day drops out of it - distributing the older
    # 48 h band evenly is a crude but unbiased way to drop it. Adding without
    # subtracting would inflate antecedent rainfall a little more with every
    # horizon and make every forecast look worse than the present.
    older_band = max(r72 - r24, 0.0)
    r48_now = r24 + older_band * 0.5

    # Carry the current instrument and field-report evidence into the
    # projection unchanged. Only rainfall is being varied, so dropping the
    # other terms would compare "wet slope with a tilting inclinometer" against
    # "drier slope with no instruments at all" and make the outlook look better
    # than it is - the one direction an early-warning system must not err in.
    anomaly, _ = sensor_service.anomaly_score(db, zone.id)
    reports, _ = risk_engine.field_report_score(db, zone.id)

    projected = predict(
        zone=zone_to_dict(zone),
        dynamic={
            "rainfall_24h_mm": expected,
            "rainfall_72h_mm": r48_now + expected,
            "rainfall_7d_mm": max(r7d - (r7d - r72) / 4.0, r48_now) + expected,
            "max_intensity_mm_hr": horizon["expected_intensity_mm_hr"],
        },
        sensor_anomaly=anomaly,
        field_report_score=reports,
    )

    current = RiskLevel.from_probability(zone.susceptibility_index).value
    return ZoneForecast(
        zone_id=zone.id,
        zone_code=zone.code,
        zone_name=zone.name,
        current_risk_level=current,
        forecast=points,
        projected_risk_level=projected["risk_level"],
        note=(
            "Projection uses persistence blended toward seasonal climatology. "
            "Skill degrades sharply beyond 24 hours."
        ),
    )


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh(db: DbSession, _user=Depends(require_roles(Role.ADMIN))):
    """Force an immediate poll of every zone."""
    return await weather_service.refresh_all_zones(db)
