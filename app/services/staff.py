"""Adding people to a hotel. Used by staff management and by hotel onboarding.

A person is a global ``app_user`` (one login across hotels); their link to a hotel is a
``membership``. Every new membership starts ``invited`` and becomes active only when the
person accepts, so nobody is added to a hotel without consenting.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.core.errors import conflict, unprocessable
from app.core.identifiers import parse_login
from app.core.security import generate_token, hash_token


async def issue_invite(
    db: AsyncConnection, *, hotel_id: uuid.UUID, user_id: uuid.UUID, created_by: uuid.UUID | None
) -> tuple[str, datetime]:
    """Revoke any open invite for this member and issue a fresh one."""
    await db.execute(
        text(
            "UPDATE invite_token SET revoked_at = now() WHERE hotel_id = :h AND user_id = :u "
            "AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {"h": hotel_id, "u": user_id},
    )
    token = generate_token()
    expires_at: datetime = (
        await db.execute(
            text(
                "INSERT INTO invite_token (token_hash, hotel_id, user_id, created_by_user_id, "
                "expires_at) VALUES (:th, :h, :u, :c, now() + make_interval(secs => :ttl)) "
                "RETURNING expires_at"
            ),
            {
                "th": hash_token(token),
                "h": hotel_id,
                "u": user_id,
                "c": created_by,
                "ttl": get_settings().invite_ttl_seconds,
            },
        )
    ).scalar_one()
    return token, expires_at


async def find_or_create_user(db: AsyncConnection, *, full_name: str, login: str) -> uuid.UUID:
    login_id = parse_login(login)
    if login_id is None:
        raise unprocessable("INVALID_LOGIN", "Enter a valid email or Indian mobile number")
    column = "email" if login_id.kind == "email" else "phone"
    # INSERT ... ON CONFLICT DO NOTHING then SELECT: race-free when two admins add the
    # same phone number at once. An existing user's name is never changed by a hotel.
    await db.execute(
        text(
            f"INSERT INTO app_user (full_name, {column}) VALUES (:n, :v) "  # noqa: S608
            f"ON CONFLICT ({column}) DO NOTHING"
        ),
        {"n": full_name, "v": login_id.value},
    )
    user_id: uuid.UUID = (
        await db.execute(
            text(f"SELECT id FROM app_user WHERE {column} = :v"),  # noqa: S608
            {"v": login_id.value},
        )
    ).scalar_one()
    return user_id


async def add_member(
    db: AsyncConnection,
    *,
    hotel_id: uuid.UUID,
    user_id: uuid.UUID,
    role_id: uuid.UUID,
    outlet_ids: list[uuid.UUID] | None,
    invited_by: uuid.UUID | None,
) -> uuid.UUID:
    membership_id: uuid.UUID | None = (
        await db.execute(
            text(
                "INSERT INTO membership (hotel_id, user_id, role_id, outlet_ids, status, "
                "invited_by_user_id) VALUES (:h, :u, :r, :o, 'invited', :by) "
                "ON CONFLICT (hotel_id, user_id) DO NOTHING RETURNING id"
            ),
            {"h": hotel_id, "u": user_id, "r": role_id, "o": outlet_ids, "by": invited_by},
        )
    ).scalar_one_or_none()
    if membership_id is None:
        raise conflict("ALREADY_MEMBER", "This person is already on your staff list")
    return membership_id
