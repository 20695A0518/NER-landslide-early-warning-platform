"""The monitoring cycle: score every zone, update roads, issue warnings.

One `run_cycle` pass is the heartbeat of the platform:

    weather -> sensors -> field reports -> hybrid prediction -> persistence
            -> road connectivity -> alert issue/escalate -> SMS dispatch

It is written to be safely re-entrant and idempotent-per-cycle: running it
twice in a minute produces two assessments (which is correct - they are
timestamped observations) but never two alerts for the same zone, because
issuance dedupes on active bulletins.

A failure scoring one zone must never abort the cycle. In a region where a
single malformed sensor payload could otherwise blank out warnings for seven
other states, per-zone isolation is a safety property, not defensive habit.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.ml.features import zone_to_dict
from app.ml.predictor import predict
from app.models.enums import ReportStatus, RiskLevel, RoadStatus
from app.models.geo import RoadSegment, Zone
from app.models.report import FieldReport
from app.models.risk import RiskAssessment
from app.services import alerts as alert_service
from app.services import sensors as sensor_service
from app.services import weather as weather_service

logger = logging.getLogger(__name__)

# How far back a field report still counts as evidence of current instability.
REPORT_EVIDENCE_HOURS = 36


def field_report_score(db: Session, zone_id: int) -> tuple[float, int]:
    """Evidence weight from recent ground observations, and the report count.

    Verified reports count fully; pending ones count at a third. Unverified
    citizen reports are real information - often the only information from a
    remote slope - but they are also how a rumour or a misread photo enters the
    system, so they nudge rather than drive.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=REPORT_EVIDENCE_HOURS)
    reports = db.execute(
        select(FieldReport).where(
            FieldReport.zone_id == zone_id,
            FieldReport.captured_at >= since,
            FieldReport.status != ReportStatus.REJECTED,
        )
    ).scalars().all()

    if not reports:
        return 0.0, 0

    score = 0.0
    for report in reports:
        weight = 1.0 if report.status == ReportStatus.VERIFIED else 0.33
        # Severity 1-5 -> 0.2-1.0
        score += weight * (report.severity / 5.0)

    # Saturating: five reports of a cracking slope is not five times the
    # evidence of one, it is the same slope observed five times.
    return round(min(score / 2.5, 1.0), 3), len(reports)


def assess_zone(db: Session, zone: Zone) -> RiskAssessment:
    """Score one zone and persist the assessment."""
    observation = weather_service.latest_for_zone(db, zone.id)
    sensor_state = sensor_service.zone_sensor_state(db, zone.id)
    anomaly, _reasons = sensor_service.anomaly_score(db, zone.id)
    reports, _count = field_report_score(db, zone.id)

    dynamic = {
        "rainfall_24h_mm": observation.rainfall_24h_mm if observation else 0.0,
        "rainfall_72h_mm": observation.rainfall_72h_mm if observation else 0.0,
        "rainfall_7d_mm": observation.rainfall_7d_mm if observation else 0.0,
        "max_intensity_mm_hr": observation.max_intensity_mm_hr if observation else 0.0,
        "soil_moisture_pct": sensor_state.get("soil_moisture_pct"),
        "pore_pressure_kpa": sensor_state.get("pore_pressure_kpa"),
        "tilt_deg": sensor_state.get("tilt_deg"),
    }

    result = predict(
        zone=zone_to_dict(zone),
        dynamic=dynamic,
        sensor_anomaly=anomaly,
        field_report_score=reports,
    )

    assessment = RiskAssessment(zone_id=zone.id, assessed_at=datetime.now(timezone.utc), **result)
    db.add(assessment)

    # Keep a rolling susceptibility figure on the zone for fast map rendering.
    zone.susceptibility_index = result["probability"]
    return assessment


def update_road_status(db: Session) -> dict:
    """Derive connectivity from the risk of the zones each road passes through.

    Only *reported* blockages close a road outright - a model has no business
    declaring a highway shut. High modelled risk downgrades a road to
    `restricted`, which is an advisory the PWD can act on or override.
    """
    roads = db.execute(select(RoadSegment)).scalars().all()
    zones = {z.code: z for z in db.execute(select(Zone)).scalars().all()}
    now = datetime.now(timezone.utc)

    since = now - timedelta(hours=REPORT_EVIDENCE_HOURS)
    blocking_reports = db.execute(
        select(FieldReport).where(
            FieldReport.category == "road_block",
            FieldReport.status == ReportStatus.VERIFIED,
            FieldReport.captured_at >= since,
        )
    ).scalars().all()
    blocked_zone_ids = {r.zone_id for r in blocking_reports if r.zone_id}

    counts = {"open": 0, "restricted": 0, "blocked": 0}
    for road in roads:
        segment_zones = [zones[c] for c in (road.zone_codes or []) if c in zones]
        if not segment_zones:
            counts[str(road.status)] = counts.get(str(road.status), 0) + 1
            continue

        worst = max((z.susceptibility_index for z in segment_zones), default=0.0)
        has_block_report = any(z.id in blocked_zone_ids for z in segment_zones)

        if has_block_report:
            status, note = RoadStatus.BLOCKED, "Blockage confirmed by verified field report"
        elif worst >= settings.alert_threshold_critical:
            status, note = (
                RoadStatus.RESTRICTED,
                "Critical slope risk on this alignment - advise convoy control or closure",
            )
        elif worst >= settings.alert_threshold_high:
            status, note = (
                RoadStatus.RESTRICTED,
                "High slope risk - single-lane movement and patrolling advised",
            )
        else:
            status, note = RoadStatus.OPEN, None

        if road.status != status:
            road.status_updated_at = now
        road.status = status
        road.status_note = note
        counts[str(status)] += 1

    db.commit()
    return counts


def run_cycle(db: Session, issue_alerts: bool = True) -> dict:
    """Execute one full monitoring pass over every active zone."""
    started = datetime.now(timezone.utc)
    zones = db.execute(select(Zone).where(Zone.is_active.is_(True))).scalars().all()

    level_counts = {"low": 0, "moderate": 0, "high": 0, "critical": 0}
    assessments: list[RiskAssessment] = []
    failures: list[dict] = []

    for zone in zones:
        try:
            assessment = assess_zone(db, zone)
            assessments.append(assessment)
            level_counts[assessment.risk_level] += 1
        except Exception as exc:  # noqa: BLE001 - one bad zone must not blind the region
            logger.exception("Risk assessment failed for zone %s", zone.code)
            failures.append({"zone": zone.code, "error": str(exc)})

    db.commit()

    road_counts = update_road_status(db)
    alert_service.expire_stale(db)

    issued: list[str] = []
    suppressed = 0
    if issue_alerts:
        for assessment in assessments:
            if RiskLevel(assessment.risk_level).rank < RiskLevel.HIGH.rank:
                continue
            zone = db.get(Zone, assessment.zone_id)
            if zone is None:
                continue
            try:
                alert, reason = alert_service.issue_if_needed(db, zone, assessment)
                if alert is not None:
                    issued.append(alert.reference)
                else:
                    suppressed += 1
                    logger.debug("No alert for %s: %s", zone.code, reason)
            except Exception:  # noqa: BLE001
                logger.exception("Alert issuance failed for zone %s", zone.code)

    finished = datetime.now(timezone.utc)
    summary = {
        "started_at": started.isoformat(),
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "zones_assessed": len(assessments),
        "zones_failed": len(failures),
        "failures": failures,
        "risk_distribution": level_counts,
        "road_status": road_counts,
        "alerts_issued": issued,
        "alerts_suppressed": suppressed,
    }
    logger.info(
        "Risk cycle: %d zones in %.2fs | %s | %d alerts issued",
        len(assessments),
        summary["duration_seconds"],
        level_counts,
        len(issued),
    )
    return summary


async def run_full_pipeline(db: Session) -> dict:
    """Weather refresh + sensor round + risk cycle. The scheduled entry point."""
    weather_summary = await weather_service.refresh_all_zones(db)
    if settings.simulate_sensors:
        sensor_summary = sensor_service.simulate_round(db)
    else:
        sensor_summary = {"skipped": "simulate_sensors disabled; awaiting real telemetry"}
    sensor_service.refresh_station_health(db)
    cycle_summary = run_cycle(db)
    return {"weather": weather_summary, "sensors": sensor_summary, "risk": cycle_summary}
