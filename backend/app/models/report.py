"""Citizen and field-officer observations, including offline-queued submissions."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import ReportCategory, ReportStatus


class FieldReport(Base):
    __tablename__ = "field_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Client-generated UUID makes offline replay idempotent on reconnect.
    client_uuid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)

    reporter_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reporter_name: Mapped[str | None] = mapped_column(String(128))
    reporter_phone: Mapped[str | None] = mapped_column(String(20))

    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), index=True
    )
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    location_name: Mapped[str | None] = mapped_column(String(160))

    category: Mapped[str] = mapped_column(String(32), default=ReportCategory.OTHER, index=True)
    severity: Mapped[int] = mapped_column(Integer, default=2)
    description: Mapped[str | None] = mapped_column(Text)
    road_affected: Mapped[str | None] = mapped_column(String(96))

    media_path: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[str | None] = mapped_column(String(16))

    status: Mapped[str] = mapped_column(String(16), default=ReportStatus.PENDING, index=True)
    verified_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    verification_note: Mapped[str | None] = mapped_column(Text)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    synced_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    was_offline: Mapped[bool] = mapped_column(Boolean, default=False)
