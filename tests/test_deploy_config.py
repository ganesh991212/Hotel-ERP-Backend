"""Deployed environments must refuse to start with settings that would leak data or print broken QR codes."""

from app.core.config import Settings, validate

GOOD = {
    "app_env": "staging",
    "jwt_secret": "x" * 48,
    "public_diner_url": "https://web.example.com/#/t",
    "cors_origins": "https://web.example.com",
    "database_url": "postgresql+asyncpg://hotel_app.ref:pw@pooler.example.com:5432/postgres?ssl=require",
    "platform_database_url": "postgresql+asyncpg://hotel_platform.ref:pw@pooler.example.com:5432/postgres",
    "migration_database_url": "postgresql+asyncpg://hotel_owner.ref:pw@pooler.example.com:5432/postgres",
}


def _settings(**over) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **over})


def test_complete_staging_config_passes():
    assert validate(_settings()) == []


def test_local_is_never_blocked():
    assert validate(Settings(_env_file=None, app_env="local")) == []


def test_each_misconfiguration_is_reported():
    cases = {
        "JWT_SECRET": {"jwt_secret": ""},
        "PUBLIC_DINER_URL": {"public_diner_url": "http://web.example.com/#/t"},
        "CORS_ORIGINS": {"cors_origins": "*"},
        "DATABASE_URL": {"database_url": "postgresql+asyncpg://hotel_app:x@localhost:5432/hotel_erp"},
    }
    for needle, over in cases.items():
        problems = validate(_settings(**over))
        assert any(needle in p for p in problems), (needle, problems)


def test_production_rejects_razorpay_test_keys():
    assert validate(_settings(app_env="staging", razorpay_key_id="rzp_test_abc")) == []
    assert validate(_settings(app_env="production", razorpay_key_id="rzp_test_abc"))
