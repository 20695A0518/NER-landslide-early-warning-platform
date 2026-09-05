"""Output of the hybrid risk engine: one scored row per zone per cycle."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import RiskLevel


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (Index("ix_risk_zone_time", "zone_id", "assessed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    # --- Composite output ---
    probability: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[str] = mapped_column(String(16), default=RiskLevel.LOW, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    lead_time_hours: Mapped[int] = mapped_column(Integer, default=24)

    # --- Component scores, kept separately so the dashboard can explain itself ---
    ml_probability: Mapped[float] = mapped_column(Float, default=0.0)
    factor_of_safety: Mapped[float] = mapped_column(Float, default=1.5)
    rainfall_threshold_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sensor_anomaly_score: Mapped[float] = mapped_column(Float, default=0.0)
    field_report_score: Mapped[float] = mapped_column(Float, default=0.0)

    # Ranked list of factor/contribution/value/direction entries for explainability.
    contributing_factors: Mapped[list | None] = mapped_column(JSON)
    narrative: Mapped[str | None] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(32), default="v1")
    trigger_forecast_horizon_h: Mapped[int | None] = mapped_column(Integer)

    zone = relationship("Zone", back_populates="assessments")
