"""Application settings, loaded from environment with safe development defaults."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core ---
    app_name: str = "PRAHARI"
    app_env: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"
    secret_key: str = "dev-only-insecure-key-change-in-production"
    access_token_expire_minutes: int = 720

    # --- Storage ---
    database_url: str = f"sqlite:///{BASE_DIR / 'prahari.db'}"
    media_root: Path = BASE_DIR / "media"

    # --- CORS ---
    # NoDecode: pydantic-settings would otherwise JSON-parse this before the
    # validator runs, and a comma-separated .env value is not valid JSON.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # --- Weather providers ---
    imd_api_base: str = "https://mausam.imd.gov.in/api"
    imd_api_key: str = ""
    openweather_api_key: str = ""
    weather_poll_minutes: int = 30

    # --- Satellite ---
    bhuvan_api_key: str = ""
    sentinelhub_client_id: str = ""
    sentinelhub_client_secret: str = ""

    # --- SMS ---
    sms_provider: str = "console"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    msg91_auth_key: str = ""
    msg91_sender_id: str = "PRAHRI"

    # Generate sensor telemetry when no physical network reports in.
    # Set false once real gateways POST to /sensors/readings.
    simulate_sensors: bool = True

    # --- Risk engine ---
    risk_cycle_minutes: int = 15
    alert_threshold_high: float = 0.65
    alert_threshold_critical: float = 0.82

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def live_weather_enabled(self) -> bool:
        """True when a real upstream weather provider is configured."""
        return bool(self.imd_api_key or self.openweather_api_key)

    @property
    def live_satellite_enabled(self) -> bool:
        return bool(self.bhuvan_api_key or self.sentinelhub_client_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
settings.media_root.mkdir(parents=True, exist_ok=True)
(settings.media_root / "reports").mkdir(parents=True, exist_ok=True)
