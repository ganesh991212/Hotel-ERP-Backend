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

    # Base of printed QR links. Empty = relative "/#/t/<token>" and the client resolves it against its own
    # origin (dev and preview). Production must set the public https URL so printed codes work anywhere.
    public_diner_url: str = ""
    # Dev convenience: serve the Flutter web build from the API on the same origin.
    web_dir: str = ""
    cors_origins: str = "*"

    trial_days: int = 14
    grace_days: int = 7

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # Firebase Cloud Messaging (HTTP v1). Both empty = push is a silent no-op; realtime + in-app sound still work.
    fcm_project_id: str = ""
    fcm_service_account_json: str = ""  # path to the JSON file, or the JSON itself (from the secret manager)
    # Public Firebase app identifiers, served to the apps at runtime so .env is the only place push is configured.
    # These are not secrets (Firebase ships them inside every client); the service-account JSON above is.
    firebase_api_key: str = ""
    firebase_android_api_key: str = ""  # optional; falls back to firebase_api_key
    firebase_ios_api_key: str = ""  # optional; falls back to firebase_api_key
    firebase_auth_domain: str = ""
    firebase_storage_bucket: str = ""
    firebase_messaging_sender_id: str = ""
    firebase_web_app_id: str = ""
    firebase_android_app_id: str = ""
    firebase_ios_app_id: str = ""
    firebase_ios_bundle_id: str = ""
    firebase_vapid_key: str = ""

    max_items_per_order: int = 50
    max_qty_per_line: int = 20


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if not s.jwt_secret:
        warnings.warn("JWT_SECRET not set; using an ephemeral secret (tokens die on restart)", stacklevel=1)
        s.jwt_secret = secrets.token_urlsafe(48)
    return s
