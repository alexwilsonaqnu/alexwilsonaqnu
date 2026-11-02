"""Runtime configuration. Every secret comes from the environment (Fly secrets in prod)."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "staging", "production"] = "dev"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/solbrief"

    # Staging is ALWAYS a dry run, regardless of what a caller asks for.
    dry_run: bool = True

    admin_token: str | None = None
    hmac_secret: str | None = None

    # Scheduling (America/Chicago per brief)
    schedule_timezone: str = "America/Chicago"
    daily_run_time: time = time(4, 30)
    run_deadline: time = time(6, 30)
    alert_email: str | None = None

    # Providers
    anthropic_api_key: str | None = None
    voyage_api_key: str | None = None
    postmark_server_token: str | None = None
    postmark_inbound_webhook_secret: str | None = None
    postmark_from: str = "briefing@example.org"
    postmark_broadcast_stream: str = "broadcast"
    postmark_transactional_stream: str = "outbound"
    logfire_token: str | None = None

    # Source API keys (registered by Knight Lab staff, never by the agent)
    openalex_api_key: str | None = None
    bls_api_key: str | None = None
    census_api_key: str | None = None
    eia_api_key: str | None = None
    nws_user_agent: str = "solbrief (Knight Lab, Northwestern University)"

    card_reverify_days: int = Field(180, ge=1)

    # Model per stage (plan §4.1: Haiku-class for classification; strongest model for draft/verify/evaluate)
    classifier_model: str = "claude-haiku-5-5"

    @property
    def effective_dry_run(self) -> bool:
        """Staging can never send; other envs honour DRY_RUN."""
        return self.app_env == "staging" or self.dry_run

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Fly/MPG hand out postgres:// URLs; SQLAlchemy wants an explicit driver.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @property
    def libpq_url(self) -> str:
        """Plain libpq URL for psycopg/procrastinate."""
        return self.sqlalchemy_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
