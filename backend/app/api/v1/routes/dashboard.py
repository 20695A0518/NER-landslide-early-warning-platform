"""Aggregated dashboard feeds for district and state control rooms."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import require_roles
from app.models.alert import Alert
from app.models.enums import AlertStatus, ReportStatus, RoadStatus, Role, SensorStatus
from app.models.geo import HistoricalLandslide, RoadSegment, Zone
from app.models.report import FieldReport
from app.models.risk import RiskAssessment
# METRICS_PATH comes from the training module rather than being rebuilt by
# counting `..` segments, so moving this router cannot silently break the
# model-provenance panel.
from app.ml.train import METRICS_PATH
from app.models.sensor import SensorStation
from app.schemas.models import DashboardSummary, DrillRequest, ModelInfo, RiskDistribution
from app.utils.timeutil import as_utc
from app.services import alerts as alert_service
from app.services import drill as drill_service
from app.services import notifications, risk_engine
from app.services import weather as weather_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

DbSession = Annotated[Session, Depends(get_db)]


def _latest_assessments(db: Session) -> dict[int, RiskAssessment]:
    rows = db.execute(
        select(RiskAssessment).order_by(RiskAssessment.zone_id, desc(RiskAssessment.assessed_at))
    ).scalars().all()
    newest: dict[int, RiskAssessment] = {}
    for row in rows:
        if row.zone_id not in newest:
            newest[row.zone_id] = row
    return newest


def _recent_trend(db: Session, limit_per_zone: int = 12) -> dict[int, list[float]]:
    """Recent probability history per zone, oldest first, for sparklines.

    One ordered scan rather than a subquery per zone: at 37 zones the obvious
    implementation costs 37 round trips every time the dashboard refreshes,
    which is a minute-by-minute cost for a decorative column.
    """
    rows = db.execute(
        select(RiskAssessment.zone_id, RiskAssessment.probability)
        .order_by(RiskAssessment.zone_id, desc(RiskAssessment.assessed_at))
    ).all()

    trend: dict[int, list[float]] = {}
    for zone_id, probability in rows:
        series = trend.setdefault(zone_id, [])
        if len(series) < limit_per_zone:
            series.append(round(probability, 4))
    return {zone_id: list(reversed(series)) for zone_id, series in trend.items()}


@router.get("/summary", response_model=DashboardSummary)
def summary(db: DbSession, state: str | None = None):
    """The single call that paints the control-room overview."""
    now = datetime.now(timezone.utc)
    zone_query = select(Zone).where(Zone.is_active.is_(True))
    if state:
        zone_query = zone_query.where(Zone.state == state)
    zones = db.execute(zone_query).scalars().all()
    zone_ids = {z.id for z in zones}

    assessments = _latest_assessments(db)
    trends = _recent_trend(db)
    distribution = RiskDistribution()
    population_at_risk = 0
    ranked: list[dict] = []

    for zone in zones:
        assessment = assessments.get(zone.id)
        level = assessment.risk_level if assessment else "low"
        setattr(distribution, level, getattr(distribution, level) + 1)
        if level in ("high", "critical"):
            population_at_risk += zone.population
        ranked.append(
            {
                "zone_id": zone.id,
                "code": zone.code,
                "name": zone.name,
                "district": zone.district,
                "state": zone.state,
                "latitude": zone.latitude,
                "longitude": zone.longitude,
                "probability": assessment.probability if assessment else 0.0,
                "risk_level": level,
                "population": zone.population,
                "lead_time_hours": assessment.lead_time_hours if assessment else None,
                "factor_of_safety": assessment.factor_of_safety if assessment else None,
                "narrative": assessment.narrative if assessment else None,
                "trend": trends.get(zone.id, []),
            }
        )

    ranked.sort(key=lambda z: z["probability"], reverse=True)

    alert_query = select(Alert).where(
        Alert.status == AlertStatus.ACTIVE,
        (Alert.expires_at.is_(None)) | (Alert.expires_at > now),
    )
    if state:
        alert_query = alert_query.where(Alert.state == state)
    active_alerts = db.execute(alert_query).scalars().all()

    road_query = select(RoadSegment)
    if state:
        road_query = road_query.where(RoadSegment.state == state)
    roads = db.execute(road_query).scalars().all()

    station_query = select(SensorStation)
    if state:
        station_query = station_query.where(SensorStation.zone_id.in_(zone_ids or {-1}))
    stations = db.execute(station_query).scalars().all()

    report_query = select(FieldReport)
    if state:
        report_query = report_query.where(FieldReport.zone_id.in_(zone_ids or {-1}))
    reports = db.execute(report_query).scalars().all()
    day_ago = now - timedelta(hours=24)

    return DashboardSummary(
        generated_at=now,
        zones_monitored=len(zones),
        population_monitored=sum(z.population for z in zones),
        risk_distribution=distribution,
        population_at_risk=population_at_risk,
        active_alerts=len(active_alerts),
        critical_alerts=sum(1 for a in active_alerts if a.level == "critical"),
        roads_total=len(roads),
        roads_blocked=sum(1 for r in roads if r.status == RoadStatus.BLOCKED),
        roads_restricted=sum(1 for r in roads if r.status == RoadStatus.RESTRICTED),
        lifeline_roads_affected=sum(
            1
            for r in roads
            if r.is_lifeline and r.status in (RoadStatus.BLOCKED, RoadStatus.RESTRICTED)
        ),
        sensors_online=sum(1 for s in stations if s.status == SensorStatus.ONLINE),
        sensors_total=len(stations),
        pending_reports=sum(1 for r in reports if r.status == ReportStatus.PENDING),
        reports_last_24h=sum(1 for r in reports if as_utc(r.captured_at) >= day_ago),
        data_sources={
            "weather": weather_service.provider_status(),
            "sms": notifications.provider_status(),
            "sensors": {
                "simulated": settings.simulate_sensors,
                "note": "Sensor telemetry is generated, not measured."
                if settings.simulate_sensors
                else None,
            },
            "drill": drill_service.active_drill(db) or {"active": False},
        },
        top_risk_zones=ranked[:10],
        response_queue=alert_service.response_queue(db, limit=8),
    )


@router.get("/trends")
def trends(db: DbSession, hours: int = Query(default=72, ge=6, le=720)):
    """Region-wide risk over time, bucketed hourly, for the trend chart."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.assessed_at >= since)
        .order_by(RiskAssessment.assessed_at)
    ).scalars().all()

    buckets: dict[str, dict] = {}
    for row in rows:
        key = row.assessed_at.replace(minute=0, second=0, microsecond=0).isoformat()
        bucket = buckets.setdefault(
            key,
            {"timestamp": key, "low": 0, "moderate": 0, "high": 0, "critical": 0,
             "mean_probability": 0.0, "_n": 0},
        )
        bucket[row.risk_level] += 1
        bucket["mean_probability"] += row.probability
        bucket["_n"] += 1

    series = []
    for bucket in buckets.values():
        n = bucket.pop("_n") or 1
        bucket["mean_probability"] = round(bucket["mean_probability"] / n, 4)
        series.append(bucket)
    return sorted(series, key=lambda b: b["timestamp"])


@router.get("/statistics")
def statistics(db: DbSession):
    """Regional roll-up by state - the view a central ministry asks for."""
    zones = db.execute(select(Zone)).scalars().all()
    assessments = _latest_assessments(db)
    events = db.execute(select(HistoricalLandslide)).scalars().all()

    by_state: dict[str, dict] = {}
    for zone in zones:
        entry = by_state.setdefault(
            zone.state,
            {
                "state": zone.state,
                "zones": 0,
                "population": 0,
                "high_risk_zones": 0,
                "mean_probability": 0.0,
                "historical_events": 0,
                "historical_fatalities": 0,
            },
        )
        assessment = assessments.get(zone.id)
        entry["zones"] += 1
        entry["population"] += zone.population
        entry["mean_probability"] += assessment.probability if assessment else 0.0
        if assessment and assessment.risk_level in ("high", "critical"):
            entry["high_risk_zones"] += 1

    for event in events:
        entry = by_state.get(event.state)
        if entry:
            entry["historical_events"] += 1
            entry["historical_fatalities"] += event.fatalities

    for entry in by_state.values():
        entry["mean_probability"] = round(entry["mean_probability"] / max(entry["zones"], 1), 4)

    return {
        "by_state": sorted(by_state.values(), key=lambda e: e["mean_probability"], reverse=True),
        "inventory_note": (
            "Historical events shown here are synthetic seed data unless imported "
            "from a real inventory. Check the `source` field on each record."
        ),
    }


@router.get("/model", response_model=ModelInfo)
def model_info():
    """Model provenance and held-out metrics, caveats included."""
    from app.ml.predictor import load_model

    bundle = load_model()
    if not METRICS_PATH.exists():
        return ModelInfo(
            available=bundle is not None,
            model_version=(bundle or {}).get("model_version", "physics-only"),
            caveat="No metrics file found. Train with: python -m app.ml.train",
        )

    metrics = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    return ModelInfo(
        available=bundle is not None,
        model_version=metrics.get("model_version", "unknown"),
        algorithm=metrics.get("algorithm"),
        trained_at=metrics.get("trained_at"),
        data_source=metrics.get("data_source"),
        is_synthetic=metrics.get("is_synthetic"),
        n_samples=metrics.get("n_samples"),
        n_features=metrics.get("n_features"),
        roc_auc=metrics.get("roc_auc"),
        pr_auc=metrics.get("pr_auc"),
        brier_score=metrics.get("brier_score"),
        precision=metrics.get("precision"),
        recall=metrics.get("recall"),
        operating_threshold=metrics.get("operating_threshold"),
        feature_importances=metrics.get("feature_importances", [])[:12],
        caveat=metrics.get("caveat"),
    )


@router.post("/run-cycle", status_code=status.HTTP_202_ACCEPTED)
async def run_cycle(
    db: DbSession,
    full: bool = Query(default=True, description="Also refresh weather and sensors"),
    _user=Depends(require_roles(Role.ADMIN, Role.DM_AUTHORITY)),
):
    """Trigger a monitoring cycle immediately instead of waiting for the schedule."""
    if full:
        return await risk_engine.run_full_pipeline(db)
    return risk_engine.run_cycle(db)


@router.get("/drill")
def drill_status(db: DbSession):
    """Whether exercise rainfall is currently present in the database."""
    state = drill_service.active_drill(db)
    return state or {"active": False}


@router.post("/drill", status_code=status.HTTP_202_ACCEPTED)
async def run_drill(
    db: DbSession,
    payload: DrillRequest,
    _user=Depends(require_roles(Role.ADMIN, Role.DM_AUTHORITY)),
):
    """Inject a rainfall scenario, re-score the region and let alerting run.

    Restricted to administrators and state authorities: a drill issues real
    Alert rows, and on a deployment with a live SMS gateway those would reach
    real recipients.
    """
    try:
        injection = drill_service.inject(
            db,
            zone_codes=payload.zone_codes,
            state=payload.state,
            intensity=payload.intensity,
            duration_hours=payload.duration_hours,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    cycle = risk_engine.run_cycle(db, issue_alerts=payload.issue_alerts)
    return {"drill": injection, "cycle": cycle}


@router.delete("/drill")
def clear_drill(db: DbSession, _user=Depends(require_roles(Role.ADMIN, Role.DM_AUTHORITY))):
    """Remove all exercise data and re-score against real observations."""
    removed = drill_service.clear(db)
    cycle = risk_engine.run_cycle(db, issue_alerts=False)
    return {"observations_removed": removed, "cycle": cycle}


@router.get("/history/summary")
def history_summary(db: DbSession):
    """Past-event statistics for the analytics panel."""
    rows = db.execute(
        select(
            HistoricalLandslide.state,
            func.count(HistoricalLandslide.id),
            func.sum(HistoricalLandslide.fatalities),
            func.sum(HistoricalLandslide.road_blocked_hours),
        ).group_by(HistoricalLandslide.state)
    ).all()

    by_month = dict(
        db.execute(
            select(
                func.strftime("%m", HistoricalLandslide.event_date),
                func.count(HistoricalLandslide.id),
            ).group_by(func.strftime("%m", HistoricalLandslide.event_date))
        ).all()
    )

    return {
        "by_state": [
            {
                "state": state,
                "events": count,
                "fatalities": int(fatalities or 0),
                "road_blocked_hours": round(float(blocked or 0), 1),
            }
            for state, count, fatalities, blocked in rows
        ],
        "by_month": [
            {"month": int(m), "events": c} for m, c in sorted(by_month.items()) if m
        ],
    }
