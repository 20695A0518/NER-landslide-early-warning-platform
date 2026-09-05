"""Observed and forecast meteorology, per zone, from IMD/OpenWeather or the simulator."""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WeatherObservation(Base):
    __tablename__ = "weather_observations"
    __table_args__ = (Index("ix_weather_zone_time", "zone_id", "observed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    # Antecedent rainfall accumulations - the dominant landslide trigger in the NER.
    rainfall_1h_mm: Mapped[float] = mapped_column(Float, default=0.0)
    rainfall_24h_mm: Mapped[float] = mapped_column(Float, default=0.0)
    rainfall_72h_mm: Mapped[float] = mapped_column(Float, default=0.0)
    rainfall_7d_mm: Mapped[float] = mapped_column(Float, default=0.0)
    rainfall_15d_mm: Mapped[float] = mapped_column(Float, default=0.0)
    max_intensity_mm_hr: Mapped[float] = mapped_column(Float, default=0.0)

    temperature_c: Mapped[float] = mapped_column(Float, default=22.0)
    humidity_pct: Mapped[float] = mapped_column(Float, default=80.0)
    wind_speed_kmh: Mapped[float] = mapped_column(Float, default=6.0)
    pressure_hpa: Mapped[float] = mapped_column(Float, default=1006.0)

    # Forecast rows carry is_forecast=True and a horizon in hours.
    is_forecast: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    forecast_horizon_h: Mapped[int | None] = mapped_column()
    source: Mapped[str] = mapped_column(String(32), default="simulator")
