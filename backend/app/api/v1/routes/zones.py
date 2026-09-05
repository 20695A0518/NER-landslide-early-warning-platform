"""Monitored zones, their risk history, and the GIS heatmap feed."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import Role
from app.models.geo import HistoricalLandslide, Zone
from app.models.risk import RiskAssessment
from app.schemas.models import (
    AssessmentOut,
    HeatmapPoint,
    HistoricalEventOut,
    ZoneDetail,
    ZoneSummary,
)
from app.services import risk_engine

router = APIRouter(prefix="/zones", tags=["zones"])

DbSession = Annotated[Session, Depends(get_db)]


def latest_assessment(db: Session, zone_id: int) -> RiskAssessment | None:
    return db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.zone_id == zone_id)
        .order_by(desc(RiskAssessment.assessed_at))
        .limit(1)
    ).scalars().first()


def latest_assessments_map(db: Session) -> dict[int, RiskAssessment]:
    """Newest assessment per zone in one pass.

    The obvious implementation runs a subquery per zone; at 37 zones that is 37
    round trips every time the map refreshes. Sorting once and keeping the
    first row per zone is a single query.
    """
    rows = db.execute(
        select(RiskAssessment).order_by(RiskAssessment.zone_id, desc(RiskAssessment.assessed_at))
    ).scalars().all()

    newest: dict[int, RiskAssessment] = {}
    for row in rows:
        if row.zone_id not in newest:
            newest[row.zone_id] = row
    return newest


@router.get("", response_model=list[ZoneSummary])
def list_zones(
    db: DbSession,
    state: str | None = None,
    district: str | None = None,
    min_risk: float = Query(default=0.0, ge=0.0, le=1.0),
):
    query = select(Zone).where(Zone.is_active.is_(True))
    if state:
        query = query.where(Zone.state == state)
    if district:
        query = query.where(Zone.district == district)
    if min_risk > 0:
        query = query.where(Zone.susceptibility_index >= min_risk)
    return db.execute(query.order_by(desc(Zone.susceptibility_index))).scalars().all()


@router.get("/heatmap", response_model=list[HeatmapPoint])
def heatmap(db: DbSession, state: str | None = None):
    """Everything the map layer needs, in one request."""
    query = select(Zone).where(Zone.is_active.is_(True))
    if state:
        query = query.where(Zone.state == state)
    zones = db.execute(query).scalars().all()
    assessments = latest_assessments_map(db)

    points = []
    for zone in zones:
        assessment = assessments.get(zone.id)
        points.append(
            HeatmapPoint(
                zone_id=zone.id,
                code=zone.code,
                name=zone.name,
                district=zone.district,
                state=zone.state,
                latitude=zone.latitude,
                longitude=zone.longitude,
                geometry=zone.geometry,
                probability=assessment.probability if assessment else zone.susceptibility_index,
                risk_level=assessment.risk_level if assessment else "low",
                population=zone.population,
                factor_of_safety=assessment.factor_of_safety if assessment else None,
                lead_time_hours=assessment.lead_time_hours if assessment else None,
            )
        )
    return points


@router.get("/states")
def list_states(db: DbSession):
    """States and districts with live zone and risk counts, for map filters."""
    zones = db.execute(select(Zone).where(Zone.is_active.is_(True))).scalars().all()
    grouped: dict[str, dict] = {}

    for zone in zones:
        entry = grouped.setdefault(
            zone.state,
            {"state": zone.state, "zones": 0, "population": 0, "districts": {}, "max_risk": 0.0},
        )
        entry["zones"] += 1
        entry["population"] += zone.population
        entry["max_risk"] = max(entry["max_risk"], zone.susceptibility_index)
        district = entry["districts"].setdefault(
            zone.district, {"district": zone.district, "zones": 0, "max_risk": 0.0}
        )
        district["zones"] += 1
        district["max_risk"] = max(district["max_risk"], zone.susceptibility_index)

    result = []
    for entry in grouped.values():
        entry["districts"] = sorted(entry["districts"].values(), key=lambda d: d["district"])
        entry["max_risk"] = round(entry["max_risk"], 3)
        result.append(entry)
    return sorted(result, key=lambda e: e["state"])


@router.get("/{zone_id}", response_model=ZoneDetail)
def get_zone(db: DbSession, zone_id: int):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")

    detail = ZoneDetail.model_validate(zone)
    assessment = latest_assessment(db, zone_id)
    if assessment is not None:
        detail.latest_assessment = AssessmentOut.model_validate(assessment)
    return detail


@router.get("/{zone_id}/assessments", response_model=list[AssessmentOut])
def zone_assessments(db: DbSession, zone_id: int, limit: int = Query(default=48, ge=1, le=500)):
    if db.get(Zone, zone_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.zone_id == zone_id)
        .order_by(desc(RiskAssessment.assessed_at))
        .limit(limit)
    ).scalars().all()


@router.get("/{zone_id}/history", response_model=list[HistoricalEventOut])
def zone_history(db: DbSession, zone_id: int):
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    return db.execute(
        select(HistoricalLandslide)
        .where(HistoricalLandslide.zone_code == zone.code)
        .order_by(desc(HistoricalLandslide.event_date))
    ).scalars().all()


@router.post("/{zone_id}/assess", response_model=AssessmentOut)
def assess_now(
    db: DbSession,
    zone_id: int,
    _user=Depends(require_roles(Role.ADMIN, Role.DM_AUTHORITY, Role.DISTRICT_OFFICER)),
):
    """Force an immediate re-score of one zone."""
    zone = db.get(Zone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    assessment = risk_engine.assess_zone(db, zone)
    db.commit()
    db.refresh(assessment)
    return assessment
