"""Geo-tagged field reports from citizens and officials, with offline sync.

Two properties matter more here than anywhere else in the API:

Idempotency
    The PWA queues reports while offline and replays them on reconnect. Mobile
    networks in the hills drop connections mid-request constantly, so the same
    report will be submitted more than once. Every submission carries a
    client-generated UUID, and a repeat returns the original row with 200
    instead of creating a duplicate. Without this, one crack reported from a
    valley with intermittent signal becomes five reports and inflates the
    evidence score fivefold.

Accepting reports from anyone
    Report submission does not require authentication. A villager watching a
    slope move should not be blocked by a login screen, and an unverified
    report only nudges the risk score (see `risk_engine.field_report_score`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_roles
from app.models.enums import ReportCategory, ReportStatus, Role
from app.models.geo import Zone
from app.models.report import FieldReport
from app.schemas.models import FieldReportIn, FieldReportOut, ReportVerification

router = APIRouter(prefix="/reports", tags=["field reports"])

DbSession = Annotated[Session, Depends(get_db)]

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
ALLOWED_MEDIA = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
}


def nearest_zone(db: Session, latitude: float, longitude: float) -> Zone | None:
    """Attach a report to the closest monitored zone.

    Equirectangular distance on the raw coordinates: at NER latitudes the
    longitude scaling matters (a degree of longitude is ~0.9 of a degree of
    latitude here), and ignoring it biases matches east-west. Exact geodesics
    are unnecessary to pick a nearest neighbour from 37 candidates.
    """
    import math

    zones = db.execute(select(Zone).where(Zone.is_active.is_(True))).scalars().all()
    if not zones:
        return None

    lat_scale = math.cos(math.radians(latitude))
    best, best_distance = None, float("inf")
    for zone in zones:
        dy = zone.latitude - latitude
        dx = (zone.longitude - longitude) * lat_scale
        distance = dy * dy + dx * dx
        if distance < best_distance:
            best, best_distance = zone, distance

    # Beyond roughly 40 km the nearest zone is not meaningfully "the" zone.
    return best if math.sqrt(best_distance) * 111.0 <= 40.0 else None


def _persist_media(upload: UploadFile) -> str:
    if upload.content_type not in ALLOWED_MEDIA:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported media type {upload.content_type}. "
            f"Allowed: {', '.join(sorted(ALLOWED_MEDIA))}",
        )

    payload = upload.file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
        )

    # Never trust the client filename - it reaches the filesystem.
    name = f"{uuid.uuid4().hex}{ALLOWED_MEDIA[upload.content_type]}"
    destination = Path(settings.media_root) / "reports" / name
    destination.write_bytes(payload)
    return f"reports/{name}"


def _create(
    db: Session,
    payload: FieldReportIn,
    media_path: str | None,
    media_type: str | None,
    reporter_id: int | None,
) -> tuple[FieldReport, bool]:
    """Create a report, or return the existing one for a replayed UUID."""
    if payload.client_uuid:
        existing = db.execute(
            select(FieldReport).where(FieldReport.client_uuid == payload.client_uuid)
        ).scalars().first()
        if existing is not None:
            return existing, True

    zone = nearest_zone(db, payload.latitude, payload.longitude)
    report = FieldReport(
        **payload.model_dump(exclude={"captured_at"}),
        captured_at=payload.captured_at or datetime.now(timezone.utc),
        synced_at=datetime.now(timezone.utc),
        zone_id=zone.id if zone else None,
        media_path=media_path,
        media_type=media_type,
        reporter_id=reporter_id,
        status=ReportStatus.PENDING,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report, False


@router.post("", response_model=FieldReportOut, status_code=status.HTTP_201_CREATED)
async def submit_report(
    request: Request,
    db: DbSession,
    latitude: Annotated[float, Form()],
    longitude: Annotated[float, Form()],
    category: Annotated[ReportCategory, Form()] = ReportCategory.OTHER,
    severity: Annotated[int, Form()] = 2,
    description: Annotated[str | None, Form()] = None,
    location_name: Annotated[str | None, Form()] = None,
    road_affected: Annotated[str | None, Form()] = None,
    reporter_name: Annotated[str | None, Form()] = None,
    reporter_phone: Annotated[str | None, Form()] = None,
    client_uuid: Annotated[str | None, Form()] = None,
    accuracy_m: Annotated[float | None, Form()] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    was_offline: Annotated[bool, Form()] = False,
    media: Annotated[UploadFile | None, File()] = None,
):
    """Submit a geo-tagged observation, optionally with a photo or video."""
    # The form fields are assembled into the model by hand, so a validation
    # failure here is raised outside FastAPI's request-validation layer and
    # would otherwise surface as a 500. Translate it into the 422 the client
    # expects, with the same error shape as any other validation failure.
    try:
        payload = FieldReportIn(
            client_uuid=client_uuid,
            latitude=latitude,
            longitude=longitude,
            accuracy_m=accuracy_m,
            location_name=location_name,
            category=category,
            severity=severity,
            description=description,
            road_affected=road_affected,
            reporter_name=reporter_name,
            reporter_phone=reporter_phone,
            captured_at=captured_at,
            was_offline=was_offline,
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]}
                for e in exc.errors()
            ],
        ) from exc

    media_path = media_type = None
    if media is not None and media.filename:
        media_path = _persist_media(media)
        media_type = "video" if (media.content_type or "").startswith("video") else "image"

    # Authentication is optional here; attribute the report when a valid token
    # happens to be present.
    reporter_id = None
    if request.headers.get("authorization"):
        try:
            reporter_id = get_current_user(
                request.headers["authorization"].removeprefix("Bearer ").strip(), db
            ).id
        except HTTPException:
            reporter_id = None

    report, was_duplicate = _create(db, payload, media_path, media_type, reporter_id)
    if was_duplicate:
        # Replay of an already-accepted report: return it, do not create another.
        return report
    return report


@router.get("", response_model=list[FieldReportOut])
def list_reports(
    db: DbSession,
    status_filter: ReportStatus | None = Query(default=None, alias="status"),
    district: str | None = None,
    zone_id: int | None = None,
    hours: int = Query(default=168, ge=1, le=8760),
    limit: int = Query(default=100, ge=1, le=500),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(FieldReport).where(FieldReport.captured_at >= since)
    if status_filter:
        query = query.where(FieldReport.status == status_filter)
    if zone_id:
        query = query.where(FieldReport.zone_id == zone_id)
    if district:
        query = query.join(Zone, FieldReport.zone_id == Zone.id).where(Zone.district == district)
    return db.execute(
        query.order_by(desc(FieldReport.captured_at)).limit(limit)
    ).scalars().all()


@router.get("/stats")
def report_stats(db: DbSession):
    day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    by_status = dict(
        db.execute(
            select(FieldReport.status, func.count(FieldReport.id)).group_by(FieldReport.status)
        ).all()
    )
    by_category = dict(
        db.execute(
            select(FieldReport.category, func.count(FieldReport.id)).group_by(
                FieldReport.category
            )
        ).all()
    )
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_category": by_category,
        "last_24h": db.query(FieldReport).filter(FieldReport.captured_at >= day_ago).count(),
        "submitted_offline": db.query(FieldReport)
        .filter(FieldReport.was_offline.is_(True))
        .count(),
    }


@router.get("/{report_id}", response_model=FieldReportOut)
def get_report(db: DbSession, report_id: int):
    report = db.get(FieldReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return report


@router.patch("/{report_id}/verify", response_model=FieldReportOut)
def verify_report(
    db: DbSession,
    report_id: int,
    payload: ReportVerification,
    user=Depends(
        require_roles(Role.ADMIN, Role.DM_AUTHORITY, Role.DISTRICT_OFFICER, Role.FIELD_OFFICER)
    ),
):
    """Verify, reject or resolve a report.

    Verification carries real weight - a verified `road_block` closes a
    highway on the dashboard - so it is restricted to officials and records who
    made the call.
    """
    report = db.get(FieldReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")

    report.status = payload.status
    report.verification_note = payload.note
    report.verified_by_id = user.id
    db.commit()
    db.refresh(report)
    return report
