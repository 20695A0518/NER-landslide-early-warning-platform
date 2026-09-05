"""Early-warning bulletins and their per-recipient delivery ledger."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import AlertStatus, RiskLevel


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zones.id", ondelete="SET NULL"), index=True
    )
    assessment_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_assessments.id", ondelete="SET NULL")
    )

    level: Mapped[str] = mapped_column(String(16), default=RiskLevel.HIGH, index=True)
    headline: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    # Language code keyed bulletin text, rendered once at issue time.
    translations: Mapped[dict | None] = mapped_column(JSON)
    advisory_actions: Mapped[list | None] = mapped_column(JSON)

    district: Mapped[str | None] = mapped_column(String(64), index=True)
    state: Mapped[str | None] = mapped_column(String(64), index=True)
    affected_roads: Mapped[list | None] = mapped_column(JSON)
    population_at_risk: Mapped[int] = mapped_column(Integer, default=0)

    channels: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=AlertStatus.ACTIVE, index=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    issued_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    # Score used to rank the emergency-response queue.
    response_priority: Mapped[float] = mapped_column(Integer, default=0)

    deliveries = relationship("AlertDelivery", back_populates="alert", cascade="all, delete-orphan")


class AlertDelivery(Base):
    __tablename__ = "alert_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    recipient: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(16), default="sms")
    language: Mapped[str] = mapped_column(String(8), default="en")
    rendered_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    provider_ref: Mapped[str | None] = mapped_column(String(96))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    alert = relationship("Alert", back_populates="deliveries")
