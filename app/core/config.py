import secrets
import warnings
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Tenant traffic: a non-superuser role WITHOUT BYPASSRLS. RLS is enforced.
    database_url: str = "postgresql+asyncpg://hotel_app:hotel_app@localhost:5432/hotel_erp"
    # Platform traffic (super admin, pre-auth lookups): role with BYPASSRLS. Used only in platform/ and auth.
    platform_database_url: str = "postgresql+asyncpg://hotel_platform:hotel_platform@localhost:5432/hotel_erp"
    migration_database_url: str = "postgresql+asyncpg://hotel_owner:hotel_owner@localhost:5432/hotel_erp"

    jwt_secret: str = ""
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    diner_session_hours: int = 3

    public_diner_url: str = "http://localhost:8080/#/t"
    cors_origins: str = "*"

    trial_days: int = 14
    grace_days: int = 7

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    max_items_per_order: int = 50
    max_qty_per_line: int = 20


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_secret:
        warnings.warn("JWT_SECRET not set; using an ephemeral secret (tokens die on restart)", stacklevel=1)
        s.jwt_secret = secrets.token_urlsafe(48)
    return s
