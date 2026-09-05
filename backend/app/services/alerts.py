"""Alert issuance: who gets warned, in which language, and in what order.

Three behaviours here are what separate a usable warning system from a noisy one:

Deduplication
    A zone that stays critical for six hours must not generate 24 alerts. An
    active alert at the same or higher level suppresses re-issue; an escalation
    supersedes the standing alert instead of stacking on it.

Escalation-only updates
    Risk falling from critical to high does not cancel the bulletin - people
    have already acted on it. It expires on its own schedule, so nobody is
    told "all clear" by a model that is merely oscillating.

Response prioritisation
    When several zones alert at once, the district has to sequence its teams.
    Priority combines severity, exposed population, lifeline-road impact and
    remoteness, so an isolated village on the only road in outranks a larger
    settlement with three alternative approaches.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.alert import Alert, AlertDelivery
from app.models.enums import AlertStatus, Role, RiskLevel
from app.models.geo import RoadSegment, Zone
from app.models.risk import RiskAssessment
from app.models.user import User
from app.services import notifications
from app.services.i18n import languages_for_state, render_actions, render_sms

logger = logging.getLogger(__name__)

# How long a bulletin stays active, by level.
ALERT_TTL_HOURS = {"critical": 12, "high": 18, "moderate": 24, "low": 24}


def _reference(zone_code: str, when: datetime) -> str:
    return f"PRH-{zone_code}-{when.strftime('%y%m%d%H%M')}"


def active_alert_for_zone(db: Session, zone_id: int) -> Alert | None:
    now = datetime.now(timezone.utc)
    return db.execute(
        select(Alert)
        .where(
            Alert.zone_id == zone_id,
            Alert.status == AlertStatus.ACTIVE,
            (Alert.expires_at.is_(None)) | (Alert.expires_at > now),
        )
        .order_by(Alert.issued_at.desc())
        .limit(1)
    ).scalars().first()


def compute_response_priority(
    zone: Zone, level: str, affected_roads: list[RoadSegment]
) -> float:
    """Rank this alert against others competing for the same response teams.

    Scored 0-100. The weighting is a policy choice, not a physical one, and is
    kept in one readable place so a district authority can argue with it.
    """
    severity = {"critical": 40.0, "high": 28.0, "moderate": 14.0, "low": 4.0}.get(level, 4.0)

    # Population, compressed: 100k people is not ten times more urgent than
    # 10k when both are in the path of a moving slope.
    import math

    exposure = min(math.log1p(zone.population) / math.log1p(150000) * 22.0, 22.0)

    lifeline = 0.0
    for road in affected_roads:
        lifeline = max(lifeline, road.criticality * 3.0 + (6.0 if road.is_lifeline else 0.0))
    lifeline = min(lifeline, 21.0)

    # Remoteness: a zone whose road has no detour is one landslide from being
    # cut off entirely, and reaching it later costs far more.
    remoteness = 0.0
    for road in affected_roads:
        if road.is_lifeline and not road.detour_km:
            remoteness = 10.0
            break
        if road.detour_km and road.detour_km > 60:
            remoteness = max(remoteness, 6.0)

    infrastructure = min(len(zone.critical_infrastructure or []) * 2.0, 7.0)

    return round(min(severity + exposure + lifeline + remoteness + infrastructure, 100.0), 1)


def _affected_roads(db: Session, zone: Zone) -> list[RoadSegment]:
    roads = db.execute(select(RoadSegment)).scalars().all()
    return [r for r in roads if zone.code in (r.zone_codes or [])]


def build_alert(
    db: Session,
    zone: Zone,
    assessment: RiskAssessment,
    issued_by_id: int | None = None,
    auto: bool = True,
) -> Alert:
    """Construct (but do not commit) an Alert for a zone at risk."""
    now = datetime.now(timezone.utc)
    level = assessment.risk_level
    roads = _affected_roads(db, zone)

    headline = f"{level.upper()} landslide risk - {zone.name}, {zone.district}"
    body = (
        f"{assessment.narrative} "
        f"Estimated lead time {assessment.lead_time_hours} hours. "
        f"Model confidence {assessment.confidence:.0%}."
    )

    translations: dict[str, str] = {}
    for language in languages_for_state(zone.state):
        text, _ = render_sms(
            language=language,
            level=level,
            location=zone.name,
            district=zone.district,
            window_hours=assessment.lead_time_hours,
        )
        translations[str(language)] = text

    alert = Alert(
        reference=_reference(zone.code, now),
        zone_id=zone.id,
        assessment_id=assessment.id,
        level=level,
        headline=headline,
        body=body,
        translations=translations,
        advisory_actions=render_actions("en", level),
        district=zone.district,
        state=zone.state,
        affected_roads=[{"code": r.code, "name": r.name, "status": r.status} for r in roads],
        population_at_risk=zone.population,
        channels=["sms", "push", "dashboard"],
        status=AlertStatus.ACTIVE,
        issued_at=now,
        expires_at=now + timedelta(hours=ALERT_TTL_HOURS.get(level, 18)),
        auto_generated=auto,
        issued_by_id=issued_by_id,
        response_priority=compute_response_priority(zone, level, roads),
    )
    return alert


def resolve_audience(db: Session, zone: Zone) -> list[User]:
    """Everyone who should receive this bulletin.

    Officials are scoped by jurisdiction: state authorities see their whole
    state, district officers only their district. Citizens are included when
    they have opted in and sit in the affected district.
    """
    official_roles = (Role.ADMIN, Role.DM_AUTHORITY, Role.DISTRICT_OFFICER, Role.FIELD_OFFICER)
    users = db.execute(select(User).where(User.is_active.is_(True))).scalars().all()

    audience: list[User] = []
    for user in users:
        if not user.phone:
            continue
        if user.role == Role.ADMIN:
            audience.append(user)
        elif user.role == Role.DM_AUTHORITY and user.state == zone.state:
            audience.append(user)
        elif user.role in official_roles and user.district == zone.district:
            audience.append(user)
        elif (
            user.role == Role.CITIZEN
            and user.subscribe_sms
            and user.district == zone.district
        ):
            audience.append(user)
    return audience


def dispatch(db: Session, alert: Alert, zone: Zone) -> dict:
    """Queue one delivery per recipient in that recipient's own language."""
    audience = resolve_audience(db, zone)
    queued = 0

    for user in audience:
        text, used_fallback = render_sms(
            language=user.language,
            level=alert.level,
            location=zone.name,
            district=zone.district,
            window_hours=(
                db.get(RiskAssessment, alert.assessment_id).lead_time_hours
                if alert.assessment_id
                else 12
            ),
        )
        if used_fallback:
            logger.warning(
                "No template for language %s; alert %s fell back to English for %s",
                user.language,
                alert.reference,
                user.username,
            )
        notifications.queue_delivery(
            db,
            alert=alert,
            recipient=user.phone,
            text=text,
            language=str(user.language),
            channel="sms",
            user_id=user.id,
        )
        queued += 1

    db.commit()
    result = notifications.flush_queue(db, alert_id=alert.id)
    result["queued"] = queued
    result["audience_size"] = len(audience)
    return result


def issue_if_needed(
    db: Session, zone: Zone, assessment: RiskAssessment
) -> tuple[Alert | None, str]:
    """Issue, escalate or suppress. Returns (alert, reason)."""
    level = RiskLevel(assessment.risk_level)
    if assessment.probability < settings.alert_threshold_high:
        return None, "below alert threshold"

    existing = active_alert_for_zone(db, zone.id)
    if existing is not None:
        if RiskLevel(existing.level).rank >= level.rank:
            return None, f"suppressed - active {existing.level} alert {existing.reference}"
        # Escalation: supersede rather than run two bulletins for one slope.
        existing.status = AlertStatus.CANCELLED
        db.flush()

    alert = build_alert(db, zone, assessment)
    db.add(alert)
    db.commit()
    db.refresh(alert)

    dispatch(db, alert, zone)
    return alert, "issued"


def expire_stale(db: Session) -> int:
    """Retire alerts past their TTL."""
    now = datetime.now(timezone.utc)
    stale = db.execute(
        select(Alert).where(
            Alert.status == AlertStatus.ACTIVE,
            Alert.expires_at.is_not(None),
            Alert.expires_at <= now,
        )
    ).scalars().all()
    for alert in stale:
        alert.status = AlertStatus.EXPIRED
    db.commit()
    return len(stale)


def response_queue(db: Session, limit: int = 20) -> list[dict]:
    """Active alerts ordered by response priority - the dispatcher's worklist."""
    now = datetime.now(timezone.utc)
    alerts = db.execute(
        select(Alert)
        .where(
            Alert.status == AlertStatus.ACTIVE,
            (Alert.expires_at.is_(None)) | (Alert.expires_at > now),
        )
        .order_by(Alert.response_priority.desc(), Alert.issued_at.desc())
        .limit(limit)
    ).scalars().all()

    queue = []
    for rank, alert in enumerate(alerts, start=1):
        delivered = (
            db.query(AlertDelivery)
            .filter(AlertDelivery.alert_id == alert.id, AlertDelivery.status == "sent")
            .count()
        )
        queue.append(
            {
                "rank": rank,
                "reference": alert.reference,
                "zone_id": alert.zone_id,
                "level": alert.level,
                "headline": alert.headline,
                "district": alert.district,
                "state": alert.state,
                "population_at_risk": alert.population_at_risk,
                "affected_roads": alert.affected_roads or [],
                "response_priority": alert.response_priority,
                "issued_at": alert.issued_at.isoformat(),
                "expires_at": alert.expires_at.isoformat() if alert.expires_at else None,
                "notifications_delivered": delivered,
            }
        )
    return queue
