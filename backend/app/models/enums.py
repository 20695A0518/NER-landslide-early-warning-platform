"""Shared vocabulary for roles, risk levels and workflow states."""

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    DM_AUTHORITY = "dm_authority"      # State Disaster Management Authority
    DISTRICT_OFFICER = "district_officer"
    FIELD_OFFICER = "field_officer"
    CITIZEN = "citizen"


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_probability(cls, p: float) -> "RiskLevel":
        if p >= 0.82:
            return cls.CRITICAL
        if p >= 0.65:
            return cls.HIGH
        if p >= 0.40:
            return cls.MODERATE
        return cls.LOW

    @property
    def rank(self) -> int:
        return {"low": 0, "moderate": 1, "high": 2, "critical": 3}[self.value]


class RoadStatus(StrEnum):
    OPEN = "open"
    RESTRICTED = "restricted"   # single lane / slow moving
    BLOCKED = "blocked"


class ReportCategory(StrEnum):
    CRACK = "crack"
    SLOPE_MOVEMENT = "slope_movement"
    ROAD_BLOCK = "road_block"
    DEBRIS_FLOW = "debris_flow"
    WATER_SEEPAGE = "water_seepage"
    SUBSIDENCE = "subsidence"
    OTHER = "other"


class ReportStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    RESOLVED = "resolved"


class AlertStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SensorStatus(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class Language(StrEnum):
    EN = "en"      # English
    HI = "hi"      # Hindi
    AS = "as"      # Assamese
    BN = "bn"      # Bengali (Tripura / Barak valley)
    MNI = "mni"    # Meiteilon (Manipuri)
    KHA = "kha"    # Khasi (Meghalaya)
    LUS = "lus"    # Mizo (Mizoram)
    NE = "ne"      # Nepali (Sikkim)
