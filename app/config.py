"""Application settings, all from the environment. Nothing sensitive is committed."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-change-me"  # noqa: S105 - the placeholder we refuse in production


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://hotel_app:change-me@localhost:5432/hotel_erp"),
        description=(
            "Runtime DSN. The role MUST NOT be a superuser, the table owner, or have "
            "BYPASSRLS: any of those silently disables tenant isolation."
        ),
    )
    owner_database_url: PostgresDsn | None = Field(
        default=None,
        description="Owner DSN for migrations and scripts only. The app never opens it.",
    )

    # Sized per API process. asyncpg connections are cheap to hold, expensive to open.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 5.0
    # Pre-ping costs one extra round trip on EVERY checkout (measured: ~10% of request CPU).
    # Off by default: after a database restart SQLAlchemy invalidates the whole pool on the
    # first disconnect error, so each worker fails about one request instead of every
    # request paying for a SELECT 1. Recycling retires connections before server or
    # load-balancer idle timeouts cut them.
    db_pool_pre_ping: bool = False
    db_pool_recycle_seconds: int = 1800
    # Behind PgBouncer in transaction mode this MUST be 0 (prepared statements do not
    # survive a server switch). Direct connections keep the cache: it saves a parse
    # and plan on every repeated query.
    db_statement_cache_size: int = 512

    jwt_secret: str = _DEV_SECRET
    jwt_audience: str = "hotel-erp"
    access_token_ttl_seconds: int = 15 * 60
    # Sliding session lifetime: each refresh extends it, inactivity ends it.
    session_ttl_seconds: int = 30 * 24 * 3600
    # A rotated refresh token presented again within this window is treated as a client
    # race (two tabs refreshing at once): rejected, but the session is NOT revoked.
    # Outside it, reuse means a copied token and the whole session is revoked.
    refresh_reuse_grace_seconds: int = 30
    invite_ttl_seconds: int = 7 * 24 * 3600

    max_failed_logins: int = 5
    lockout_seconds: int = 15 * 60

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8080", "http://127.0.0.1:8080"]
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def require_production_secrets(self) -> None:
        """Refuse to boot in production with the development signing key."""
        if self.is_production and self.jwt_secret == _DEV_SECRET:
            raise RuntimeError("JWT_SECRET is the development default; refusing to start.")
        if self.is_production and len(self.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET must be at least 32 characters in production.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
