"""In-situ instrumentation: telemetry stations and their time-series readings."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import SensorStatus


class SensorStation(Base):
    __tablename__ = "sensor_stations"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.id", ondelete="CASCADE"), index=True)

    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    installed_depth_m: Mapped[float] = mapped_column(Float, default=1.0)

    # Comma-separated capability list: soil_moisture,pore_pressure,tilt,rain_gauge
    capabilities: Mapped[str] = mapped_column(String(160), default="soil_moisture,rain_gauge")
    status: Mapped[str] = mapped_column(String(16), default=SensorStatus.ONLINE, index=True)
    battery_pct: Mapped[float] = mapped_column(Float, default=100.0)
    signal_strength: Mapped[int] = mapped_column(Integer, default=4)  # 0-5 bars
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    zone = relationship("Zone", back_populates="sensors")
    readings = relationship(
        "SensorReading", back_populates="station", cascade="all, delete-orphan"
    )


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (Index("ix_reading_station_time", "station_id", "recorded_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("sensor_stations.id", ondelete="CASCADE"), index=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), index=True
    )

    soil_moisture_pct: Mapped[float | None] = mapped_column(Float)
    pore_pressure_kpa: Mapped[float | None] = mapped_column(Float)
    tilt_deg: Mapped[float | None] = mapped_column(Float)
    displacement_mm: Mapped[float | None] = mapped_column(Float)
    ground_vibration_mm_s: Mapped[float | None] = mapped_column(Float)
    rainfall_mm: Mapped[float | None] = mapped_column(Float)
    temperature_c: Mapped[float | None] = mapped_column(Float)
    battery_pct: Mapped[float | None] = mapped_column(Float)

    station = relationship("SensorStation", back_populates="readings")
