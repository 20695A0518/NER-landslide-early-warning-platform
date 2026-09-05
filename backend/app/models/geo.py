"""Geospatial entities: monitored zones, road segments and the historical inventory.

Geometry is stored as GeoJSON in a JSON column so the platform runs on plain
SQLite for a field/offline deployment. On PostGIS the same columns can be
migrated to `geometry(...)` without touching the API contract.
"""

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import RoadStatus


class Zone(Base):
    """A monitored slope unit - the atomic object the risk engine scores."""

    __tablename__ = "zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(24), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    district: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(64), index=True)

    # Centroid + footprint
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    geometry: Mapped[dict | None] = mapped_column(JSON)  # GeoJSON Polygon
    area_sq_km: Mapped[float] = mapped_column(Float, default=1.0)

    # --- Static terrain conditioning factors (from DEM / geological survey) ---
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0)
    slope_deg: Mapped[float] = mapped_column(Float, default=0.0)
    aspect_deg: Mapped[float] = mapped_column(Float, default=0.0)
    curvature: Mapped[float] = mapped_column(Float, default=0.0)
    lithology: Mapped[str] = mapped_column(String(48), default="sandstone_shale")
    soil_type: Mapped[str] = mapped_column(String(48), default="sandy_loam")
    soil_depth_m: Mapped[float] = mapped_column(Float, default=1.5)
    friction_angle_deg: Mapped[float] = mapped_column(Float, default=30.0)
    cohesion_kpa: Mapped[float] = mapped_column(Float, default=8.0)
    # Apparent cohesion from matric suction, recovered by back-analysis at
    # seed time (see app.ml.physics.calibrate_suction_cohesion). Decays to
    # zero as the slope saturates.
    suction_cohesion_kpa: Mapped[float] = mapped_column(Float, default=0.0)
    land_cover: Mapped[str] = mapped_column(String(48), default="forest")
    ndvi: Mapped[float] = mapped_column(Float, default=0.6)

    # Long-period climatology, used by the weather simulator and to normalise
    # rainfall-threshold ratios against what the slope is locally adapted to.
    annual_rainfall_mm: Mapped[float] = mapped_column(Float, default=2500.0)

    # --- Anthropogenic / exposure factors ---
    distance_to_road_m: Mapped[float] = mapped_column(Float, default=500.0)
    distance_to_fault_m: Mapped[float] = mapped_column(Float, default=5000.0)
    distance_to_stream_m: Mapped[float] = mapped_column(Float, default=800.0)
    hill_cutting_index: Mapped[float] = mapped_column(Float, default=0.2)  # 0-1, unplanned cutting
    seismic_zone: Mapped[int] = mapped_column(Integer, default=5)          # IS-1893 zone

    # --- Exposure ---
    population: Mapped[int] = mapped_column(Integer, default=0)
    villages: Mapped[list | None] = mapped_column(JSON)
    critical_infrastructure: Mapped[list | None] = mapped_column(JSON)

    # --- Derived ---
    historical_event_count: Mapped[int] = mapped_column(Integer, default=0)
    susceptibility_index: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    sensors = relationship("SensorStation", back_populates="zone", cascade="all, delete-orphan")
    assessments = relationship(
        "RiskAssessment", back_populates="zone", cascade="all, delete-orphan"
    )


class RoadSegment(Base):
    """Highway / rural road stretch whose connectivity is tracked."""

    __tablename__ = "road_segments"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    highway_no: Mapped[str | None] = mapped_column(String(24))
    category: Mapped[str] = mapped_column(String(24), default="NH")  # NH | SH | MDR | rural
    start_point: Mapped[str] = mapped_column(String(96))
    end_point: Mapped[str] = mapped_column(String(96))
    district: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(64), index=True)
    length_km: Mapped[float] = mapped_column(Float, default=0.0)
    path: Mapped[list | None] = mapped_column(JSON)  # [[lat, lon], ...]

    zone_codes: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=RoadStatus.OPEN, index=True)
    status_note: Mapped[str | None] = mapped_column(Text)
    status_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # 1-5; how badly a closure isolates population downstream.
    criticality: Mapped[int] = mapped_column(Integer, default=3)
    is_lifeline: Mapped[bool] = mapped_column(Boolean, default=False)
    detour_km: Mapped[float | None] = mapped_column(Float)
    population_served: Mapped[int] = mapped_column(Integer, default=0)


class HistoricalLandslide(Base):
    """Inventory of recorded events - training labels and susceptibility priors."""

    __tablename__ = "historical_landslides"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    district: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(64), index=True)
    zone_code: Mapped[str | None] = mapped_column(
        String(24), ForeignKey("zones.code", ondelete="SET NULL"), index=True
    )

    trigger: Mapped[str] = mapped_column(String(48), default="rainfall")
    magnitude: Mapped[str] = mapped_column(String(24), default="moderate")
    fatalities: Mapped[int] = mapped_column(Integer, default=0)
    injured: Mapped[int] = mapped_column(Integer, default=0)
    houses_damaged: Mapped[int] = mapped_column(Integer, default=0)
    road_blocked_hours: Mapped[float] = mapped_column(Float, default=0.0)
    rainfall_72h_mm: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(96), default="GSI inventory")
