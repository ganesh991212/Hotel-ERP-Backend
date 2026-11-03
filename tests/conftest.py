"""Test fixtures. Synthetic data only.

Tests run against a real Postgres as ``hotel_app``, the same non-superuser role production
uses. Against a superuser every isolation test would pass while proving nothing.

Provision the database first:  ``scripts/dev_db.sh hotel_erp_test --reset``

Each test creates its own hotels and people with unique slugs and phone numbers, so tests
never depend on each other's data and the suite can be re-run without cleanup.
"""

from __future__ import annotations

import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://hotel_app:app-dev@127.0.0.1:5432/hotel_erp_test"
)
os.environ.setdefault(
    "OWNER_DATABASE_URL",
    "postgresql+asyncpg://hotel_owner:owner-dev@127.0.0.1:5432/hotel_erp_test",
)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-only-signing-key-0123456789abcdef0123456789")

import itertools
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from sqlalchemy import text

from app.core.security import hash_password
from app.db.session import dispose_engine, system_session
from app.main import app

PASSWORD = "correct-horse-battery"
_seq = itertools.count()


def unique_phone() -> str:
    # 9 + 9 digits: a valid-looking Indian mobile number that is unique per run.
    return f"9{secrets.randbelow(10**9):09d}"


def unique_slug(prefix: str = "h") -> str:
    return f"{prefix}-{next(_seq)}-{secrets.token_hex(3)}"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session", autouse=True)
async def _engine() -> AsyncIterator[None]:
    yield
    await dispose_engine()


@pytest.fixture(scope="session")
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@dataclass
class Session:
    access: str
    refresh: str
    user_id: uuid.UUID
    login: str

    @property
    def headers(self) -> dict[str, str]:
        return auth(self.access)


async def login(
    client: httpx.AsyncClient,
    login_id: str,
    password: str = PASSWORD,
    hotel_id: uuid.UUID | None = None,
) -> Session:
    body: dict[str, object] = {"login": login_id, "password": password}
    if hotel_id:
        body["hotel_id"] = str(hotel_id)
    r = await client.post("/v1/auth/login", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    return Session(
        data["tokens"]["access_token"],
        data["tokens"]["refresh_token"],
        uuid.UUID(data["user"]["id"]),
        login_id,
    )


@pytest.fixture(scope="session")
async def platform_admin(client: httpx.AsyncClient) -> Session:
    phone = unique_phone()
    async with system_session() as db:
        await db.execute(
            text(
                "INSERT INTO app_user (full_name, phone, password_hash, is_platform_admin) "
                "VALUES ('Test Super Admin', :p, :h, true)"
            ),
            {"p": f"+91{phone}", "h": hash_password(PASSWORD)},
        )
    return await login(client, phone)


def tax_profile(registration: str = "regular") -> dict[str, object]:
    if registration == "unregistered":
        return {"gst_registration": "unregistered", "legal_name": "Test Foods", "state_code": "36"}
    return {
        "gst_registration": registration,
        "gstin": "27AAPFU0939F1ZV",
        "legal_name": "Test Foods Pvt Ltd",
        "state_code": "27",
    }


@dataclass
class Hotel:
    id: uuid.UUID
    admin: Session
    outlet_id: uuid.UUID | None


async def accept(client: httpx.AsyncClient, token: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/v1/auth/invites/accept", json={"token": token, "password": password})


async def onboard(
    client: httpx.AsyncClient, platform: Session, plan: str = "growth", trial_days: int = 14
) -> Hotel:
    admin_phone = unique_phone()
    r = await client.post(
        "/v1/platform/hotels",
        headers=platform.headers,
        json={
            "name": "Test Hotel",
            "slug": unique_slug(),
            "plan_code": plan,
            "trial_days": trial_days,
            "tax_profile": tax_profile(),
            "admin": {"full_name": "Hotel Admin", "login": admin_phone},
        },
    )
    assert r.status_code == 201, r.text
    data = r.json()
    accepted = await accept(client, data["admin_invite"]["token"])
    assert accepted.status_code == 200, accepted.text
    tokens = accepted.json()["tokens"]
    session = Session(
        tokens["access_token"],
        tokens["refresh_token"],
        uuid.UUID(accepted.json()["user"]["id"]),
        admin_phone,
    )
    outlets = (await client.get("/v1/outlets", headers=session.headers)).json()
    return Hotel(
        uuid.UUID(data["hotel"]["id"]), session, uuid.UUID(outlets[0]["id"]) if outlets else None
    )


async def system_role_id(client: httpx.AsyncClient, admin: Session, code: str) -> str:
    roles = (await client.get("/v1/roles", headers=admin.headers)).json()
    return next(r["id"] for r in roles if r["code"] == code)


async def add_staff(
    client: httpx.AsyncClient,
    admin: Session,
    role_id: str,
    phone: str | None = None,
    outlet_ids: list[str] | None = None,
) -> tuple[str, Session]:
    """Invite and accept. Returns (membership_id, session)."""
    phone = phone or unique_phone()
    body: dict[str, object] = {"full_name": "Staff Member", "login": phone, "role_id": role_id}
    if outlet_ids is not None:
        body["outlet_ids"] = outlet_ids
    r = await client.post("/v1/staff", headers=admin.headers, json=body)
    assert r.status_code == 201, r.text
    accepted = await accept(client, r.json()["invite"]["token"])
    assert accepted.status_code == 200, accepted.text
    tokens = accepted.json()["tokens"]
    return r.json()["member"]["id"], Session(
        tokens["access_token"],
        tokens["refresh_token"],
        uuid.UUID(accepted.json()["user"]["id"]),
        phone,
    )
