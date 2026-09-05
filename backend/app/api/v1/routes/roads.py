"""Road connectivity: status, lifeline impact and isolation risk."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.enums import Role, RoadStatus
from app.models.geo import RoadSegment, Zone
from app.schemas.models import RoadOut, RoadStatusUpdate

router = APIRouter(prefix="/roads", tags=["road connectivity"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[RoadOut])
def list_roads(
    db: DbSession,
    state: str | None = None,
    district: str | None = None,
    status_filter: RoadStatus | None = None,
    lifeline_only: bool = False,
):
    query = select(RoadSegment)
    if state:
        query = query.where(RoadSegment.state == state)
    if district:
        query = query.where(RoadSegment.district == district)
    if status_filter:
        query = query.where(RoadSegment.status == status_filter)
    if lifeline_only:
        query = query.where(RoadSegment.is_lifeline.is_(True))
    return db.execute(
        query.order_by(RoadSegment.criticality.desc(), RoadSegment.name)
    ).scalars().all()


@router.get("/connectivity")
def connectivity_summary(db: DbSession):
    """Network-level view: what is cut, who is affected, what has no detour."""
    roads = db.execute(select(RoadSegment)).scalars().all()
    zones = {z.code: z for z in db.execute(select(Zone)).scalars().all()}

    by_status = {"open": 0, "restricted": 0, "blocked": 0}
    impacted_population = 0
    isolated: list[dict] = []
    at_risk: list[dict] = []

    for road in roads:
        by_status[str(road.status)] = by_status.get(str(road.status), 0) + 1

        if road.status in (RoadStatus.BLOCKED, RoadStatus.RESTRICTED):
            impacted_population += road.population_served
            worst = max(
                (zones[c].susceptibility_index for c in (road.zone_codes or []) if c in zones),
                default=0.0,
            )
            entry = {
                "code": road.code,
                "name": road.name,
                "highway_no": road.highway_no,
                "state": road.state,
                "district": road.district,
                "status": road.status,
                "status_note": road.status_note,
                "criticality": road.criticality,
                "is_lifeline": road.is_lifeline,
                "detour_km": road.detour_km,
                "population_served": road.population_served,
                "max_zone_risk": round(worst, 3),
                "updated_at": road.status_updated_at.isoformat(),
            }
            at_risk.append(entry)
            # No alternative alignment: a closure here isolates outright.
            if road.is_lifeline and not road.detour_km:
                isolated.append(entry)

    at_risk.sort(key=lambda r: (r["criticality"], r["population_served"]), reverse=True)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_segments": len(roads),
        "total_km": round(sum(r.length_km for r in roads), 1),
        "by_status": by_status,
        "population_affected": impacted_population,
        "segments_at_risk": at_risk,
        "isolation_risk": isolated,
        "note": (
            "Roads are downgraded to `restricted` by modelled slope risk; only a "
            "verified field report marks a segment `blocked`."
        ),
    }


@router.get("/{road_id}", response_model=RoadOut)
def get_road(db: DbSession, road_id: int):
    road = db.get(RoadSegment, road_id)
    if road is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Road segment not found")
    return road


@router.patch("/{road_id}/status", response_model=RoadOut)
def update_status(
    db: DbSession,
    road_id: int,
    payload: RoadStatusUpdate,
    user=Depends(require_roles(Role.ADMIN, Role.DM_AUTHORITY, Role.DISTRICT_OFFICER,
                               Role.FIELD_OFFICER)),
):
    """Manually set a segment status.

    The next risk cycle may recompute this from modelled slope risk. Ground
    truth from a patrol should win, so the note records who set it and when.
    """
    road = db.get(RoadSegment, road_id)
    if road is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Road segment not found")

    road.status = payload.status
    road.status_note = (
        f"{payload.note} (set by {user.full_name})" if payload.note
        else f"Set manually by {user.full_name}"
    )
    road.status_updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(road)
    return road
