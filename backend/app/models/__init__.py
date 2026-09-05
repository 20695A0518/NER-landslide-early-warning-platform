"""Importing this package registers every table on the shared Base metadata."""

from app.models.alert import Alert, AlertDelivery
from app.models.enums import (
    AlertStatus,
    Language,
    ReportCategory,
    ReportStatus,
    RiskLevel,
    RoadStatus,
    Role,
    SensorStatus,
)
from app.models.geo import HistoricalLandslide, RoadSegment, Zone
from app.models.report import FieldReport
from app.models.risk import RiskAssessment
from app.models.sensor import SensorReading, SensorStation
from app.models.user import User
from app.models.weather import WeatherObservation

__all__ = [
    "Alert",
    "AlertDelivery",
    "AlertStatus",
    "FieldReport",
    "HistoricalLandslide",
    "Language",
    "ReportCategory",
    "ReportStatus",
    "RiskAssessment",
    "RiskLevel",
    "RoadSegment",
    "RoadStatus",
    "Role",
    "SensorReading",
    "SensorStation",
    "SensorStatus",
    "User",
    "WeatherObservation",
    "Zone",
]
