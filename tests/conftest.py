"""Integration tests run against a real Postgres so RLS is actually exercised.

Set TEST_DB_ADMIN_URL (superuser, sync psql-style DSN not needed) or rely on defaults below.
The roles from scripts/db-roles.sql must exist.
"""

import os

DB = os.environ.get("TEST_DB_NAME", "hotel_erp_test")
HOST = os.environ.get("TEST_DB_HOST", "localhost")
os.environ.update(
    DATABASE_URL=f"postgresql+asyncpg://hotel_app:hotel_app@{HOST}:5432/{DB}",
    PLATFORM_DATABASE_URL=f"postgresql+asyncpg://hotel_platform:hotel_platform@{HOST}:5432/{DB}",
    MIGRATION_DATABASE_URL=f"postgresql+asyncpg://hotel_owner:hotel_owner@{HOST}:5432/{DB}",
    JWT_SECRET="test-secret-not-real-0123456789abcdef",
    RAZORPAY_WEBHOOK_SECRET="whsec_test_only",
    TRIAL_DAYS="14",
)

import subprocess  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(__file__))


def _alembic(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, check=True, env=os.environ.copy())


@pytest.fixture(scope="session", autouse=True)
async def database():
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    eng = create_async_engine(os.environ["MIGRATION_DATABASE_URL"])
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.models import User
    from app.platform.provision import seed_plans

    async with async_sessionmaker(eng)() as db, db.begin():
        await seed_plans(db)
        db.add(
            User(
                name="Root",
                email="root@platform.example.com",
                password_hash=hash_password("root-pass-123"),
                is_platform_admin=True,
            )
        )
    await eng.dispose()
    yield


@pytest.fixture(scope="session")
async def client(database):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def login(client, email: str, password: str) -> dict:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    tok = r.json()
    return {"Authorization": f"Bearer {tok['access_token']}", "_refresh": tok["refresh_token"]}


def h(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if not k.startswith("_")}


@pytest.fixture(scope="session")
async def root(client):
    return await login(client, "root@platform.example.com", "root-pass-123")


async def make_hotel(client, root, plan: str = "GROWTH", **extra) -> dict:
    """Provision a hotel with one table, a menu, two chefs and a waiter. Returns handles and headers."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"admin-{tag}@hotel.example.com"
    body = {
        "name": f"Hotel {tag}",
        "plan_code": plan,
        "city": "Hyderabad",
        "admin": {"name": "Admin", "email": admin_email, "password": "admin-pass-123"},
        **extra,
    }
    r = await client.post("/api/v1/platform/hotels", json=body, headers=h(root))
    assert r.status_code == 201, r.text
    hotel = r.json()
    admin = await login(client, admin_email, "admin-pass-123")
    roles = {x["code"]: x["id"] for x in (await client.get("/api/v1/roles", headers=h(admin))).json()}
    staff = {}
    for name, code in [("Ravi", "CHEF"), ("Sita", "CHEF"), ("Kiran", "WAITER")]:
        email = f"{name.lower()}-{tag}@hotel.example.com"
        r = await client.post(
            "/api/v1/users",
            headers=h(admin),
            json={"name": name, "email": email, "password": "staff-pass-123", "role_id": roles[code]},
        )
        assert r.status_code == 201, r.text
        staff[name] = {**await login(client, email, "staff-pass-123"), "_id": r.json()["id"]}
    t = (await client.post("/api/v1/tables", headers=h(admin), json={"label": "T1", "area": "Indoor"})).json()
    cat = (await client.post("/api/v1/categories", headers=h(admin), json={"name": "Mains"})).json()
    items = {}
    for name, price, mins in [("Biryani", 28000, 20), ("Naan", 6000, 6), ("Chai", 4000, 4)]:
        r = await client.post(
            "/api/v1/items",
            headers=h(admin),
            json={"category_id": cat["id"], "name": name, "price_minor": price, "est_prep_minutes": mins},
        )
        assert r.status_code == 201, r.text
        items[name] = r.json()["id"]
    return {"hotel": hotel, "admin": admin, "roles": roles, "table": t, "items": items, **staff}
