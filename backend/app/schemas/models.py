"""Pydantic request/response models for the v1 API.

Kept in one module because the schemas are heavily cross-referential (a zone
detail embeds its latest assessment, its sensors and its roads) and splitting
them across files buys nothing but import cycles at this size.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Language, ReportCategory, ReportStatus, RiskLevel, Role, RoadStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    user: "UserOut"


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    role: str
    designation: str | None = None
    phone: str | None = None
    state: str | None = None
    district: str | None = None
    language: str
    subscribe_sms: bool


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    full_name: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    role: Role = Role.CITIZEN
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = None
    designation: str | None = None
    state: str | None = None
    district: str | None = None
    language: Language = Language.EN
    subscribe_sms: bool = True


class UserPreferences(BaseModel):
    language: Language | None = None
    subscribe_sms: bool | None = None
    phone: str | None = Field(default=None, max_length=20)


# --------------------------------------------------------------------------
# Zones and risk
# --------------------------------------------------------------------------


class ContributingFactor(BaseModel):
    factor: str
    label: str
    contribution: float
    value: float
    unit: str
    note: str


class AssessmentOut(ORMModel):
    id: int
    zone_id: int
    assessed_at: datetime
    probability: float
    risk_level: str
    confidence: float
    lead_time_hours: int
    ml_probability: float
    factor_of_safety: float
    rainfall_threshold_ratio: float
    sensor_anomaly_score: float
    field_report_score: float
    contributing_factors: list[ContributingFactor] | None = None
    narrative: str | None = None
    model_version: str


class ZoneSummary(ORMModel):
    id: int
    code: str
    name: str
    district: str
    state: str
    latitude: float
    longitude: float
    slope_deg: float
    elevation_m: float
    population: int
    susceptibility_index: float
    historical_event_count: int


class ZoneDetail(ZoneSummary):
    geometry: dict | None = None
    area_sq_km: float
    aspect_deg: float
    lithology: str
    soil_type: str
    soil_depth_m: float
    friction_angle_deg: float
    cohesion_kpa: float
    suction_cohesion_kpa: float
    land_cover: str
    ndvi: float
    annual_rainfall_mm: float
    distance_to_road_m: float
    distance_to_fault_m: float
    distance_to_stream_m: float
    hill_cutting_index: float
    seismic_zone: int
    villages: list[str] | None = None
    critical_infrastructure: list[str] | None = None
    latest_assessment: AssessmentOut | None = None


# --------------------------------------------------------------------------
# Weather
# --------------------------------------------------------------------------


class WeatherOut(ORMModel):
    id: int
    zone_id: int
    observed_at: datetime
    rainfall_1h_mm: float
    rainfall_24h_mm: float
    rainfall_72h_mm: float
    rainfall_7d_mm: float
    max_intensity_mm_hr: float
    temperature_c: float
    humidity_pct: float
    source: str


class ForecastPoint(BaseModel):
    horizon_hours: int
    expected_rainfall_mm: float
    expected_intensity_mm_hr: float
    confidence: float
    source: str


class ZoneForecast(BaseModel):
    zone_id: int
    zone_code: str
    zone_name: str
    current_risk_level: str
    forecast: list[ForecastPoint]
    projected_risk_level: str
    note: str


# --------------------------------------------------------------------------
# Sensors
# --------------------------------------------------------------------------


class SensorStationOut(ORMModel):
    id: int
    code: str
    name: str
    zone_id: int
    latitude: float
    longitude: float
    capabilities: str
    status: str
    battery_pct: float
    signal_strength: int
    last_seen_at: datetime | None = None


class SensorReadingIn(BaseModel):
    """Telemetry payload from a gateway. All measurements optional."""

    station_code: str
    recorded_at: datetime | None = None
    soil_moisture_pct: float | None = Field(default=None, ge=0, le=100)
    pore_pressure_kpa: float | None = Field(default=None, ge=0, le=500)
    tilt_deg: float | None = Field(default=None, ge=-90, le=90)
    displacement_mm: float | None = None
    ground_vibration_mm_s: float | None = Field(default=None, ge=0)
    rainfall_mm: float | None = Field(default=None, ge=0, le=500)
    temperature_c: float | None = Field(default=None, ge=-40, le=60)
    battery_pct: float | None = Field(default=None, ge=0, le=100)


class SensorReadingBatch(BaseModel):
    readings: list[SensorReadingIn] = Field(min_length=1, max_length=500)


class SensorReadingOut(ORMModel):
    id: int
    station_id: int
    recorded_at: datetime
    soil_moisture_pct: float | None = None
    pore_pressure_kpa: float | None = None
    tilt_deg: float | None = None
    displacement_mm: float | None = None
    rainfall_mm: float | None = None
    battery_pct: float | None = None


# --------------------------------------------------------------------------
# Field reports
# --------------------------------------------------------------------------


class FieldReportIn(BaseModel):
    client_uuid: str | None = Field(default=None, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = None
    location_name: str | None = Field(default=None, max_length=160)
    category: ReportCategory = ReportCategory.OTHER
    severity: int = Field(default=2, ge=1, le=5)
    description: str | None = Field(default=None, max_length=2000)
    road_affected: str | None = Field(default=None, max_length=96)
    reporter_name: str | None = Field(default=None, max_length=128)
    reporter_phone: str | None = Field(default=None, max_length=20)
    captured_at: datetime | None = None
    was_offline: bool = False

    @field_validator("latitude")
    @classmethod
    def _within_ner(cls, v: float) -> float:
        # Generous bounding box around the eight states. Catches transposed
        # lat/lon and a GPS fix that never acquired, both of which otherwise
        # plant a report in the Gulf of Guinea.
        if not 20.0 <= v <= 30.5:
            raise ValueError("latitude is outside the North Eastern Region")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon_within_ner(cls, v: float) -> float:
        if not 87.0 <= v <= 98.0:
            raise ValueError("longitude is outside the North Eastern Region")
        return v


class FieldReportOut(ORMModel):
    id: int
    client_uuid: str | None = None
    reporter_name: str | None = None
    zone_id: int | None = None
    latitude: float
    longitude: float
    location_name: str | None = None
    category: str
    severity: int
    description: str | None = None
    road_affected: str | None = None
    media_path: str | None = None
    media_type: str | None = None
    status: str
    verification_note: str | None = None
    captured_at: datetime
    synced_at: datetime
    was_offline: bool


class ReportVerification(BaseModel):
    status: ReportStatus
    note: str | None = Field(default=None, max_length=1000)


class SyncRequest(BaseModel):
    """Offline replay envelope from the PWA.

    `reports` is deliberately untyped at this level. Declaring it as
    `list[FieldReportIn]` would make FastAPI validate the whole batch up front
    and return 422 for *everything* when a single queued row is malformed - so
    one bad GPS fix would permanently block a field officer's entire queue,
    which is the exact failure this endpoint exists to avoid. Each item is
    validated individually inside the handler instead, and the bad ones are
    named in the response so the client can drop just those.
    """

    reports: list[dict] = Field(default_factory=list, max_length=200)
    last_sync_at: datetime | None = None


class SyncResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: list[dict]
    server_time: datetime
    zones: list[ZoneSummary]
    active_alerts: list["AlertOut"]
    roads: list["RoadOut"]


# --------------------------------------------------------------------------
# Roads
# --------------------------------------------------------------------------


class RoadOut(ORMModel):
    id: int
    code: str
    name: str
    highway_no: str | None = None
    category: str
    start_point: str
    end_point: str
    district: str
    state: str
    length_km: float
    path: list | None = None
    zone_codes: list | None = None
    status: str
    status_note: str | None = None
    status_updated_at: datetime
    criticality: int
    is_lifeline: bool
    detour_km: float | None = None
    population_served: int


class RoadStatusUpdate(BaseModel):
    status: RoadStatus
    note: str | None = Field(default=None, max_length=500)


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------


class AlertOut(ORMModel):
    id: int
    reference: str
    zone_id: int | None = None
    level: str
    headline: str
    body: str
    translations: dict | None = None
    advisory_actions: list | None = None
    district: str | None = None
    state: str | None = None
    affected_roads: list | None = None
    population_at_risk: int
    channels: list | None = None
    status: str
    issued_at: datetime
    expires_at: datetime | None = None
    auto_generated: bool
    response_priority: float


class ManualAlertIn(BaseModel):
    zone_id: int
    level: RiskLevel = RiskLevel.HIGH
    headline: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=10, max_length=2000)
    expires_in_hours: int = Field(default=12, ge=1, le=168)


class DeliveryOut(ORMModel):
    id: int
    alert_id: int
    recipient: str
    channel: str
    language: str
    rendered_text: str | None = None
    status: str
    error: str | None = None
    sent_at: datetime | None = None


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


class HistoricalEventOut(ORMModel):
    id: int
    event_date: date
    latitude: float
    longitude: float
    district: str
    state: str
    zone_code: str | None = None
    trigger: str
    magnitude: str
    fatalities: int
    houses_damaged: int
    road_blocked_hours: float
    description: str | None = None
    source: str


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------


class RiskDistribution(BaseModel):
    low: int = 0
    moderate: int = 0
    high: int = 0
    critical: int = 0


class DashboardSummary(BaseModel):
    generated_at: datetime
    zones_monitored: int
    population_monitored: int
    risk_distribution: RiskDistribution
    population_at_risk: int
    active_alerts: int
    critical_alerts: int
    roads_total: int
    roads_blocked: int
    roads_restricted: int
    lifeline_roads_affected: int
    sensors_online: int
    sensors_total: int
    pending_reports: int
    reports_last_24h: int
    data_sources: dict
    top_risk_zones: list[dict]
    response_queue: list[dict]


class HeatmapPoint(BaseModel):
    zone_id: int
    code: str
    name: str
    district: str
    state: str
    latitude: float
    longitude: float
    geometry: dict | None = None
    probability: float
    risk_level: str
    population: int
    factor_of_safety: float | None = None
    lead_time_hours: int | None = None


class DrillRequest(BaseModel):
    """Scenario definition for a tabletop exercise."""

    zone_codes: list[str] | None = Field(default=None, max_length=60)
    state: str | None = None
    intensity: str = Field(default="heavy", pattern="^(moderate|heavy|extreme)$")
    duration_hours: int = Field(default=24, ge=6, le=168)
    issue_alerts: bool = True


class ModelInfo(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    available: bool
    model_version: str
    algorithm: str | None = None
    trained_at: str | None = None
    data_source: str | None = None
    is_synthetic: bool | None = None
    n_samples: int | None = None
    n_features: int | None = None
    roc_auc: float | None = None
    pr_auc: float | None = None
    brier_score: float | None = None
    precision: float | None = None
    recall: float | None = None
    operating_threshold: float | None = None
    feature_importances: list[dict] | None = None
    caveat: str | None = None


TokenResponse.model_rebuild()
SyncResponse.model_rebuild()
