"""Offline synchronisation for the field PWA.

Remote NER valleys lose signal for hours at a time, so the mobile client is
built offline-first: it keeps a local copy of the zones, roads and active
alerts, queues outbound reports in IndexedDB, and reconciles here on reconnect.

Two endpoints, deliberately shaped for bad networks:

  GET  /sync/bundle    everything the client needs to work offline, one request
  POST /sync/push      replay the outbound queue, idempotently

`/sync/bundle` is one call rather than five because each extra round trip on a
2G link is another chance to fail halfway; the client either gets a coherent
snapshot or it keeps the one it has. `/sync/push` accepts a partial success -
if three of ten queued reports are malformed, the other seven are still
accepted and the three are named, so the client can drop them from its queue
instead of retrying forever.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.alert import Alert
from app.models.enums import AlertStatus
from app.models.geo import RoadSegment, Zone
from app.models.risk import RiskAssessment
from app.schemas.models import (
    AlertOut,
    FieldReportIn,
    RoadOut,
    SyncRequest,
    SyncResponse,
    ZoneSummary,
)
from app.services import i18n

router = APIRouter(prefix="/sync", tags=["offline sync"])

DbSession = Annotated[Session, Depends(get_db)]


def _active_alerts(db: Session, state: str | None = None) -> list[Alert]:
    now = datetime.now(timezone.utc)
    query = select(Alert).where(
        Alert.status == AlertStatus.ACTIVE,
        (Alert.expires_at.is_(None)) | (Alert.expires_at > now),
    )
    if state:
        query = query.where(Alert.state == state)
    return list(db.execute(query.order_by(desc(Alert.response_priority))).scalars().all())


@router.get("/bundle")
def sync_bundle(
    db: DbSession,
    state: str | None = None,
    district: str | None = None,
    since: datetime | None = Query(
        default=None, description="Only send zones whose risk changed after this time"
    ),
):
    """One-shot snapshot for offline operation.

    `since` enables a delta sync: a client that already holds a bundle asks
    only for what moved. On a 2G link that is the difference between a 300 KB
    refresh and a 12 KB one.
    """
    zone_query = select(Zone).where(Zone.is_active.is_(True))
    if state:
        zone_query = zone_query.where(Zone.state == state)
    if district:
        zone_query = zone_query.where(Zone.district == district)
    zones = db.execute(zone_query).scalars().all()

    changed_zone_ids: set[int] | None = None
    if since is not None:
        changed_zone_ids = {
            row.zone_id
            for row in db.execute(
                select(RiskAssessment).where(RiskAssessment.assessed_at > since)
            ).scalars().all()
        }
        zones = [z for z in zones if z.id in changed_zone_ids]

    road_query = select(RoadSegment)
    if state:
        road_query = road_query.where(RoadSegment.state == state)
    roads = db.execute(road_query).scalars().all()

    alerts = _active_alerts(db, state)

    # Latest assessment per zone, so the client can render risk while offline.
    newest: dict[int, RiskAssessment] = {}
    for row in db.execute(
        select(RiskAssessment).order_by(RiskAssessment.zone_id, desc(RiskAssessment.assessed_at))
    ).scalars().all():
        newest.setdefault(row.zone_id, row)

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "is_delta": since is not None,
        "zones": [
            {
                **ZoneSummary.model_validate(z).model_dump(mode="json"),
                "geometry": z.geometry,
                "villages": z.villages,
                "risk_level": newest[z.id].risk_level if z.id in newest else "low",
                "probability": newest[z.id].probability if z.id in newest else 0.0,
                "narrative": newest[z.id].narrative if z.id in newest else None,
                "lead_time_hours": newest[z.id].lead_time_hours if z.id in newest else None,
            }
            for z in zones
        ],
        "roads": [RoadOut.model_validate(r).model_dump(mode="json") for r in roads],
        "alerts": [AlertOut.model_validate(a).model_dump(mode="json") for a in alerts],
        "languages": i18n.supported_languages(),
        "cache_ttl_minutes": 180,
        "advice": (
            "Cache this bundle and keep working offline. Queue reports locally "
            "and POST them to /sync/push when a connection returns."
        ),
    }


@router.post("/push", response_model=SyncResponse)
def sync_push(db: DbSession, payload: SyncRequest, state: str | None = None):
    """Replay a queued batch of offline reports.

    Idempotent on `client_uuid`: a batch delivered twice (because the response
    to the first attempt was lost) counts the repeats as duplicates rather than
    creating a second set of reports.
    """
    from app.api.v1.routes.reports import _create

    accepted = duplicates = 0
    rejected: list[dict] = []

    for index, item in enumerate(payload.reports):
        try:
            # Validated per item, not per batch: the queue may hold rows
            # written by an older client build, or one bad GPS fix, and neither
            # may cost the officer the rest of the queue.
            report_in = FieldReportIn.model_validate(item)
        except ValidationError as exc:
            rejected.append(
                {
                    "index": index,
                    "client_uuid": item.get("client_uuid") if isinstance(item, dict) else None,
                    "errors": [e["msg"] for e in exc.errors()],
                }
            )
            continue

        try:
            _report, was_duplicate = _create(db, report_in, None, None, None)
        except Exception as exc:  # noqa: BLE001 - never lose the rest of the batch
            rejected.append(
                {"index": index, "client_uuid": report_in.client_uuid, "errors": [str(exc)]}
            )
            continue

        if was_duplicate:
            duplicates += 1
        else:
            accepted += 1

    zone_query = select(Zone).where(Zone.is_active.is_(True))
    if state:
        zone_query = zone_query.where(Zone.state == state)
    zones = db.execute(zone_query.order_by(desc(Zone.susceptibility_index))).scalars().all()

    road_query = select(RoadSegment)
    if state:
        road_query = road_query.where(RoadSegment.state == state)

    return SyncResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        server_time=datetime.now(timezone.utc),
        zones=[ZoneSummary.model_validate(z) for z in zones],
        active_alerts=[AlertOut.model_validate(a) for a in _active_alerts(db, state)],
        roads=[RoadOut.model_validate(r) for r in db.execute(road_query).scalars().all()],
    )


@router.get("/status")
def sync_status(db: DbSession):
    """What a client should know before deciding to refresh."""
    from app.models.report import FieldReport

    now = datetime.now(timezone.utc)
    return {
        "server_time": now.isoformat(),
        "zones": db.query(Zone).count(),
        "active_alerts": len(_active_alerts(db)),
        "reports_last_24h": db.query(FieldReport)
        .filter(FieldReport.captured_at >= now - timedelta(hours=24))
        .count(),
        "offline_submitted": db.query(FieldReport)
        .filter(FieldReport.was_offline.is_(True))
        .count(),
    }
