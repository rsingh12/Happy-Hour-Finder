"""
Centralized settings loaded from environment variables (or .env file).
Uses pydantic-settings for type validation.
"""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = (
        "postgresql+psycopg://happyhour:happyhour_dev@localhost:5432/happyhour"
    )

    # JWT
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 30

    # Email
    sendgrid_api_key: str = ""
    sendgrid_from_email: str = "noreply@happyhour.app"

    # Anthropic
    anthropic_api_key: str = ""

    # Yelp Fusion API (free tier, used for venue discovery)
    yelp_api_key: str = ""

    # Google Places API (optional, Phase 2 enhancement)
    google_places_api_key: str = ""

    # APNs (filled later)
    apns_key_path: str = ""
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = "app.happyhour.ios"
    apns_use_sandbox: bool = True

    # CORS
    cors_origins: str = "*"

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()


# Promote .env values into os.environ for libraries that auto-read env vars
# (e.g., the Anthropic SDK reads ANTHROPIC_API_KEY directly from os.environ).
# We don't override anything already set at the system level.
def _promote_to_env(key: str, value: str) -> None:
    if value and key not in os.environ:
        os.environ[key] = value


_promote_to_env("ANTHROPIC_API_KEY", settings.anthropic_api_key)
_promote_to_env("YELP_API_KEY", settings.yelp_api_key)
_promote_to_env("GOOGLE_PLACES_API_KEY", settings.google_places_api_key)
