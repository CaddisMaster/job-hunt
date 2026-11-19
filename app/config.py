"""Typed settings, read once from the environment (and .env via compose).

Every setting is declared here with a type, so a typo'd or missing variable
fails at startup instead of at the first request that needs it. Add a new one
here AND in .env.example.
"""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    # Environment only: compose's `env_file:` injects .env.
    model_config = SettingsConfigDict(extra="ignore")

    app_version: str = "dev"
    app_commit: str = "dev"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "apply"
    db_user: str = "apply"
    db_password: str = ""
    # Least-privilege runtime role (migration 0001). Blank = connect as the
    # owner (local dev and tests).
    db_app_user: str = ""
    db_app_password: str = ""

    secret_key: str = Field(min_length=16)
    cookie_secure: bool = False
    templates_auto_reload: bool = False
    # "Today" for the morning queue and the nightly run (0.4).
    app_timezone: str = "America/New_York"

    # Login attempts allowed per client address per minute.
    login_rate_limit: int = Field(default=10, ge=1)

    # Claude (#22). Every call runs on this one key (plan §10, decision 2). Blank =
    # AI features are off and say so; nothing else changes.
    anthropic_api_key: str = ""
    # Monthly caps, in cents, over calendar months in APP_TIMEZONE (app/spend.py):
    # one per user, and one for everyone together, so the key itself is bounded.
    ai_monthly_budget_cents_per_user: int = Field(default=200, ge=0)
    ai_monthly_budget_cents: int = Field(default=1000, ge=0)

    @field_validator("app_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)  # raises on an unknown zone, at startup
        return value

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    def database_url(self, *, owner: bool = False) -> URL:
        """The runtime URL uses the app role when one is configured; migrations
        pass owner=True because the app role cannot run DDL."""
        use_owner = owner or not self.db_app_user
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user if use_owner else self.db_app_user,
            password=self.db_password if use_owner else self.db_app_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
