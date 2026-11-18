import secrets
import warnings
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # local | test | staging | production. Deployed environments refuse to start when misconfigured (see validate()).
    app_env: str = "local"

    # Tenant traffic: a non-superuser role WITHOUT BYPASSRLS. RLS is enforced.
    database_url: str = "postgresql+asyncpg://hotel_app:hotel_app@localhost:5432/hotel_erp"
    # Platform traffic (super admin, pre-auth lookups): role with BYPASSRLS. Used only in platform/ and auth.
    platform_database_url: str = "postgresql+asyncpg://hotel_platform:hotel_platform@localhost:5432/hotel_erp"
    migration_database_url: str = "postgresql+asyncpg://hotel_owner:hotel_owner@localhost:5432/hotel_erp"
    # Per engine (there are two). Keep pool_size + max_overflow, times 2, under the pooler's per-user limit.
    db_pool_size: int = 5
    db_max_overflow: int = 5

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

    # "Powered by" line on printed QR cards (your company). Public text, not a secret.
    powered_by_name: str = "Vethra"
    powered_by_contact: str = ""

    max_items_per_order: int = 50
    max_qty_per_line: int = 20


DEPLOYED = ("staging", "production")


def validate(s: Settings) -> list[str]:
    """Problems that must stop a deployed environment from starting. Local and test runs are never blocked."""
    if s.app_env not in DEPLOYED:
        return []
    problems = []
    if len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be set to at least 32 random characters")
    if not s.public_diner_url.startswith("https://"):
        problems.append("PUBLIC_DINER_URL must be the https web URL ending in /#/t (it is printed on table QR codes)")
    origins = [o.strip() for o in s.cors_origins.split(",") if o.strip()]
    if not origins or "*" in origins:
        problems.append("CORS_ORIGINS must list the web app origin(s), not *")
    for name in ("database_url", "platform_database_url", "migration_database_url"):
        if "localhost" in getattr(s, name):
            problems.append(f"{name.upper()} still points at localhost")
    if s.app_env == "production" and s.razorpay_key_id.startswith("rzp_test_"):
        problems.append("production is using Razorpay test keys")
    return problems


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    problems = validate(s)
    if problems:
        raise RuntimeError(f"{s.app_env} configuration is invalid: " + "; ".join(problems))
    if not s.jwt_secret:
        warnings.warn("JWT_SECRET not set; using an ephemeral secret (tokens die on restart)", stacklevel=1)
        s.jwt_secret = secrets.token_urlsafe(48)
    return s
