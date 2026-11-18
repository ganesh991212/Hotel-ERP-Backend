"""Device sessions and refresh-token rotation with real reuse detection.

Each signed-in device is one ``auth_session``. Its refresh tokens form a chain in
``refresh_token``: every refresh marks the presented token used and issues the next one.
Keeping the used hashes is what makes reuse detectable. vethra_api overwrote the hash on
rotation, so a replayed old token was simply "not found" and never treated as theft.

Rotation is a single atomic ``UPDATE ... WHERE used_at IS NULL RETURNING``. Of two
concurrent refreshes with the same token, exactly one wins. The loser sees a token used
moments ago, which is a client race (two tabs), not theft: it gets
``REFRESH_TOKEN_SUPERSEDED`` and the session survives. The same token presented after the
grace window means a copy exists somewhere: the whole session is revoked.

Writes that must persist (a revocation) are committed *before* the error is raised, since
raising inside the transaction would roll the revocation back.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import get_settings
from app.core.errors import AppError, unauthenticated
from app.core.security import generate_token, hash_token, issue_access_token
from app.db.session import set_context, system_session
from app.services.audit import write_hotel_audit, write_platform_audit


@dataclass(frozen=True)
class DeviceInfo:
    platform: str = "web"
    device_label: str | None = None
    app_version: str | None = None
    ip: str | None = None


@dataclass(frozen=True)
class IssuedTokens:
    session_id: uuid.UUID
    access_token: str
    refresh_token: str
    expires_in: int


async def open_session(
    db: AsyncConnection,
    *,
    user_id: uuid.UUID,
    hotel_id: uuid.UUID | None,
    kind: Literal["staff", "platform"],
    device: DeviceInfo,
) -> IssuedTokens:
    settings = get_settings()
    session_id = uuid.uuid4()
    refresh = generate_token()
    ttl = settings.session_ttl_seconds
    await db.execute(
        text(
            "INSERT INTO auth_session (id, user_id, hotel_id, kind, device_label, platform, "
            "app_version, ip, expires_at) VALUES (:id, :u, :h, :k, :dl, :p, :av, :ip, "
            "now() + make_interval(secs => :ttl))"
        ),
        {
            "id": session_id,
            "u": user_id,
            "h": hotel_id,
            "k": kind,
            "dl": device.device_label,
            "p": device.platform[:16],
            "av": device.app_version,
            "ip": device.ip,
            "ttl": ttl,
        },
    )
    await _store_refresh(db, session_id, refresh, ttl)
    access, expires_in = issue_access_token(
        user_id=user_id, session_id=session_id, hotel_id=hotel_id, is_platform=kind == "platform"
    )
    return IssuedTokens(session_id, access, refresh, expires_in)


async def _store_refresh(db: AsyncConnection, session_id: uuid.UUID, token: str, ttl: int) -> None:
    await db.execute(
        text(
            "INSERT INTO refresh_token (token_hash, session_id, expires_at) "
            "VALUES (:h, :s, now() + make_interval(secs => :ttl))"
        ),
        {"h": hash_token(token), "s": session_id, "ttl": ttl},
    )


async def rotate(raw_refresh_token: str) -> IssuedTokens:
    async with system_session() as db:
        outcome = await _rotate(db, hash_token(raw_refresh_token))
    # The transaction has committed (including any revocation) before this raises.
    if isinstance(outcome, AppError):
        raise outcome
    return outcome


_INVALID = "REFRESH_TOKEN_INVALID"


async def _rotate(db: AsyncConnection, token_hash: str) -> IssuedTokens | AppError:
    settings = get_settings()
    won = (
        await db.execute(
            text(
                "UPDATE refresh_token SET used_at = now() "
                "WHERE token_hash = :h AND used_at IS NULL AND expires_at > now() "
                "RETURNING session_id"
            ),
            {"h": token_hash},
        )
    ).scalar_one_or_none()

    if won is None:
        return await _classify_failed_refresh(db, token_hash, settings.refresh_reuse_grace_seconds)

    sess = (
        (
            await db.execute(
                text(
                    "SELECT s.user_id, s.hotel_id, s.kind, "
                    "       (s.revoked_at IS NULL AND s.expires_at > now()) AS live, "
                    "       u.status, u.is_platform_admin "
                    "FROM auth_session s JOIN app_user u ON u.id = s.user_id "
                    "WHERE s.id = :sid FOR UPDATE OF s"
                ),
                {"sid": won},
            )
        )
        .mappings()
        .one()
    )
    if not sess["live"] or sess["status"] != "active":
        return unauthenticated(_INVALID, "Session is no longer valid")

    hotel_id: uuid.UUID | None = sess["hotel_id"]
    if sess["kind"] == "platform":
        if not sess["is_platform_admin"]:
            return unauthenticated(_INVALID, "Session is no longer valid")
    else:
        assert hotel_id is not None
        await set_context(db, hotel_id=hotel_id)
        # Re-checked on every refresh: a removed member or a cancelled hotel ends here.
        still_member = (
            await db.execute(
                text(
                    "SELECT 1 FROM membership m JOIN hotel h ON h.id = m.hotel_id "
                    "WHERE m.hotel_id = :h AND m.user_id = :u AND m.status = 'active' "
                    "AND h.status <> 'cancelled'"
                ),
                {"h": hotel_id, "u": sess["user_id"]},
            )
        ).scalar_one_or_none()
        if still_member is None:
            return unauthenticated(_INVALID, "Session is no longer valid")

    new_refresh = generate_token()
    await _store_refresh(db, won, new_refresh, settings.session_ttl_seconds)
    await db.execute(
        text(
            "UPDATE auth_session SET last_seen_at = now(), "
            "expires_at = now() + make_interval(secs => :ttl) WHERE id = :sid"
        ),
        {"sid": won, "ttl": settings.session_ttl_seconds},
    )
    access, expires_in = issue_access_token(
        user_id=sess["user_id"],
        session_id=won,
        hotel_id=hotel_id,
        is_platform=sess["kind"] == "platform",
    )
    return IssuedTokens(won, access, new_refresh, expires_in)


async def _classify_failed_refresh(
    db: AsyncConnection, token_hash: str, grace_seconds: int
) -> AppError:
    prior = (
        (
            await db.execute(
                text(
                    "SELECT r.session_id, s.user_id, s.hotel_id, s.kind, s.revoked_at, "
                    "       r.used_at IS NOT NULL AS was_used, "
                    "       r.used_at > now() - make_interval(secs => :g) AS within_grace "
                    "FROM refresh_token r JOIN auth_session s ON s.id = r.session_id "
                    "WHERE r.token_hash = :h"
                ),
                {"h": token_hash, "g": grace_seconds},
            )
        )
        .mappings()
        .one_or_none()
    )

    if prior is None or not prior["was_used"] or prior["revoked_at"] is not None:
        return unauthenticated(_INVALID, "Refresh token is invalid or expired")
    if prior["within_grace"]:
        return unauthenticated(
            "REFRESH_TOKEN_SUPERSEDED", "A newer token was already issued; use it instead"
        )

    await revoke_session(db, prior["session_id"], "refresh_reuse")
    if prior["kind"] == "staff":
        await set_context(db, hotel_id=prior["hotel_id"])
        await write_hotel_audit(
            db,
            hotel_id=prior["hotel_id"],
            action="auth.refresh_reuse_detected",
            entity_type="auth_session",
            entity_id=prior["session_id"],
            actor_user_id=prior["user_id"],
            actor_kind="system",
        )
    else:
        await write_platform_audit(
            db,
            action="auth.refresh_reuse_detected",
            entity_type="auth_session",
            entity_id=prior["session_id"],
            actor_user_id=prior["user_id"],
        )
    return unauthenticated(
        "REFRESH_TOKEN_REUSED", "This session was ended for security. Please sign in again."
    )


async def revoke_session(db: AsyncConnection, session_id: uuid.UUID, reason: str) -> None:
    await db.execute(
        text(
            "UPDATE auth_session SET revoked_at = now(), revoke_reason = :r "
            "WHERE id = :sid AND revoked_at IS NULL"
        ),
        {"sid": session_id, "r": reason},
    )


async def revoke_member_sessions(
    db: AsyncConnection, user_id: uuid.UUID, hotel_id: uuid.UUID, reason: str
) -> None:
    await db.execute(
        text(
            "UPDATE auth_session SET revoked_at = now(), revoke_reason = :r "
            "WHERE user_id = :u AND hotel_id = :h AND revoked_at IS NULL"
        ),
        {"u": user_id, "h": hotel_id, "r": reason},
    )
