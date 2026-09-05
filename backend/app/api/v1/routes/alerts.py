"""Early-warning bulletins, delivery ledger and the response queue."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.alert import Alert, AlertDelivery
from app.models.enums import AlertStatus, Role
from app.models.geo import Zone
from app.models.risk import RiskAssessment
from app.schemas.models import AlertOut, DeliveryOut, ManualAlertIn
from app.services import alerts as alert_service
from app.services import i18n, notifications

router = APIRouter(prefix="/alerts", tags=["alerts"])

DbSession = Annotated[Session, Depends(get_db)]

OFFICIAL = (Role.ADMIN, Role.DM_AUTHORITY, Role.DISTRICT_OFFICER)


@router.get("", response_model=list[AlertOut])
def list_alerts(
    db: DbSession,
    active_only: bool = True,
    state: str | None = None,
    district: str | None = None,
    level: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    query = select(Alert)
    if active_only:
        now = datetime.now(timezone.utc)
        query = query.where(
            Alert.status == AlertStatus.ACTIVE,
            (Alert.expires_at.is_(None)) | (Alert.expires_at > now),
        )
    if state:
        query = query.where(Alert.state == state)
    if district:
        query = query.where(Alert.district == district)
    if level:
        query = query.where(Alert.level == level)
    return db.execute(
        query.order_by(desc(Alert.response_priority), desc(Alert.issued_at)).limit(limit)
    ).scalars().all()


@router.get("/queue")
def response_queue(db: DbSession, limit: int = Query(default=20, ge=1, le=100)):
    """Active alerts ranked for emergency-response sequencing."""
    return alert_service.response_queue(db, limit=limit)


@router.get("/languages")
def languages():
    """Supported alert languages and their translation-review status."""
    return {
        "languages": i18n.supported_languages(),
        "state_defaults": {k: [str(v) for v in vs] for k, vs in i18n.STATE_LANGUAGES.items()},
        "review": i18n.review_status(),
    }


@router.get("/delivery-stats")
def delivery_stats(db: DbSession, hours: int = Query(default=24, ge=1, le=720)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(AlertDelivery).where(AlertDelivery.created_at >= since)
    ).scalars().all()

    by_status: dict[str, int] = {}
    by_language: dict[str, int] = {}
    for row in rows:
        by_status[row.status] = by_status.get(row.status, 0) + 1
        by_language[row.language] = by_language.get(row.language, 0) + 1

    total = len(rows)
    return {
        "window_hours": hours,
        "total": total,
        "by_status": by_status,
        "by_language": by_language,
        "success_rate": round(by_status.get("sent", 0) / total, 3) if total else None,
        "provider": notifications.provider_status(),
    }


@router.get("/{alert_id}", response_model=AlertOut)
def get_alert(db: DbSession, alert_id: int):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return alert


@router.get("/{alert_id}/deliveries", response_model=list[DeliveryOut])
def alert_deliveries(db: DbSession, alert_id: int, _user=Depends(require_roles(*OFFICIAL))):
    if db.get(Alert, alert_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    return db.execute(
        select(AlertDelivery).where(AlertDelivery.alert_id == alert_id)
    ).scalars().all()


@router.post("", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
def issue_manual_alert(
    db: DbSession,
    payload: ManualAlertIn,
    user=Depends(require_roles(*OFFICIAL)),
):
    """Issue a bulletin by hand.

    Officers see things the model cannot - a contractor reporting movement, a
    quarry blast scheduled below a settlement - so the platform must never be
    the only thing able to raise an alarm.
    """
    zone = db.get(Zone, payload.zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Zone not found")
    if user.role == Role.DISTRICT_OFFICER and user.district != zone.district:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only issue alerts within your own district"
        )

    assessment = db.execute(
        select(RiskAssessment)
        .where(RiskAssessment.zone_id == zone.id)
        .order_by(desc(RiskAssessment.assessed_at))
        .limit(1)
    ).scalars().first()

    if assessment is None:
        from app.services import risk_engine

        assessment = risk_engine.assess_zone(db, zone)
        db.commit()
        db.refresh(assessment)

    alert = alert_service.build_alert(db, zone, assessment, issued_by_id=user.id, auto=False)
    alert.level = str(payload.level)
    alert.body = payload.body
    if payload.headline:
        alert.headline = payload.headline
    alert.expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours)

    db.add(alert)
    db.commit()
    db.refresh(alert)

    alert_service.dispatch(db, alert, zone)
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/cancel", response_model=AlertOut)
def cancel_alert(db: DbSession, alert_id: int, _user=Depends(require_roles(*OFFICIAL))):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    alert.status = AlertStatus.CANCELLED
    db.commit()
    db.refresh(alert)
    return alert


@router.post("/{alert_id}/resend")
def resend_alert(db: DbSession, alert_id: int, _user=Depends(require_roles(*OFFICIAL))):
    """Retry every failed delivery for this alert."""
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")

    failed = (
        db.query(AlertDelivery)
        .filter(AlertDelivery.alert_id == alert_id, AlertDelivery.status == "failed")
        .all()
    )
    for delivery in failed:
        delivery.status = "queued"
        delivery.error = None
    db.commit()
    return notifications.flush_queue(db, alert_id=alert_id)
