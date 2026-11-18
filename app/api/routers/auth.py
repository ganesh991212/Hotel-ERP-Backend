"""Sign-in, refresh, sign-out, invite acceptance and ``/me``.

Login never holds a database transaction open across the Argon2 check (about 50 ms of
CPU in a worker thread): the user is read in one short transaction, the password is
verified with no connection held, and the outcome is written in a second transaction.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request, status
from pydantic import Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import CurrentPrincipal, open_hotel_context, open_platform_context
from app.api.schemas import (
    DeviceIn,
    HotelBrief,
    LoginOut,
    MeOut,
    RoleBrief,
    Strict,
    TokenPair,
    UserOut,
)
from app.config import get_settings
from app.core.errors import AppError, conflict, unauthenticated
from app.core.identifiers import parse_login
from app.core.security import hash_password_async, hash_token, verify_password_async
from app.db.session import set_context, system_session
from app.services import auth_sessions
from app.services.audit import client_ip, write_hotel_audit
from app.services.auth_sessions import DeviceInfo, IssuedTokens

router = APIRouter(prefix="/auth", tags=["auth"])
me_router = APIRouter(tags=["auth"])

_BAD_CREDENTIALS_MSG = "Incorrect login or password"


def _bad_credentials() -> AppError:
    return unauthenticated("INVALID_CREDENTIALS", _BAD_CREDENTIALS_MSG)


class LoginIn(DeviceIn):
    login: str = Field(min_length=3, max_length=320, description="Email or phone number")
    password: str = Field(min_length=1, max_length=256, repr=False)
    hotel_id: uuid.UUID | None = Field(
        default=None, description="Required only when the user works at more than one hotel"
    )


class RefreshIn(Strict):
    refresh_token: str = Field(min_length=20, max_length=200, repr=False)


class InviteInfoOut(Strict):
    hotel_name: str
    full_name: str
    needs_new_password: bool


class InviteAcceptIn(DeviceIn):
    token: str = Field(min_length=20, max_length=200, repr=False)
    password: str = Field(min_length=10, max_length=128, repr=False)


def _tokens(issued: IssuedTokens) -> TokenPair:
    return TokenPair(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=issued.expires_in,
    )


def _device(body: DeviceIn, request: Request) -> DeviceInfo:
    return DeviceInfo(body.platform, body.device_label, body.app_version, client_ip(request))


async def _staff_view(db: AsyncConnection, hotel_id: uuid.UUID, user_id: uuid.UUID) -> MeOut:
    row = (
        (
            await db.execute(
                text(
                    "SELECT u.id AS user_id, u.full_name, u.email, u.phone, h.id AS hotel_id, "
                    "       h.name AS hotel_name, h.status AS hotel_status, r.id AS role_id, "
                    "       r.code AS role_code, r.name AS role_name, r.permissions, m.outlet_ids "
                    "FROM membership m "
                    "JOIN app_user u ON u.id = m.user_id "
                    "JOIN hotel h ON h.id = m.hotel_id "
                    "JOIN role r ON r.id = m.role_id "
                    "WHERE m.hotel_id = :h AND m.user_id = :u"
                ),
                {"h": hotel_id, "u": user_id},
            )
        )
        .mappings()
        .one()
    )
    return MeOut(
        user=UserOut(
            id=row["user_id"], full_name=row["full_name"], email=row["email"], phone=row["phone"]
        ),
        is_platform_admin=False,
        hotel=HotelBrief(id=row["hotel_id"], name=row["hotel_name"], status=row["hotel_status"]),
        role=RoleBrief(
            id=row["role_id"],
            code=row["role_code"],
            name=row["role_name"],
            permissions=sorted(row["permissions"]),
        ),
        outlet_ids=list(row["outlet_ids"]) if row["outlet_ids"] is not None else None,
    )


async def _platform_view(db: AsyncConnection, user_id: uuid.UUID) -> MeOut:
    row = (
        (
            await db.execute(
                text("SELECT id, full_name, email, phone FROM app_user WHERE id = :u"),
                {"u": user_id},
            )
        )
        .mappings()
        .one()
    )
    return MeOut(
        user=UserOut(
            id=row["id"], full_name=row["full_name"], email=row["email"], phone=row["phone"]
        ),
        is_platform_admin=True,
        hotel=None,
        role=None,
        outlet_ids=None,
    )


@router.post("/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request) -> LoginOut:
    settings = get_settings()
    login_id = parse_login(body.login)

    user = None
    if login_id is not None:
        column = "email" if login_id.kind == "email" else "phone"
        async with system_session() as db:
            user = (
                (
                    await db.execute(
                        text(
                            "SELECT id, password_hash, status, is_platform_admin, "  # noqa: S608
                            "       locked_until > now() AS locked "
                            f"FROM app_user WHERE {column} = :v"
                        ),
                        {"v": login_id.value},
                    )
                )
                .mappings()
                .one_or_none()
            )

    usable = user is not None and user["status"] == "active"
    # Always pay for one Argon2 verification, so unknown and known logins take equal time.
    password_ok = await verify_password_async(
        body.password, user["password_hash"] if usable and user is not None else None
    )

    if user is None or not usable:
        raise _bad_credentials()
    if user["locked"]:
        if password_ok:
            raise AppError(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "ACCOUNT_LOCKED",
                "Too many failed attempts. Try again later.",
            )
        raise _bad_credentials()

    user_id: uuid.UUID = user["id"]
    outcome: LoginOut | AppError
    async with system_session() as db:
        if not password_ok:
            await db.execute(
                text(
                    "UPDATE app_user SET "
                    "  locked_until = CASE WHEN failed_login_count + 1 >= :max "
                    "                      THEN now() + make_interval(secs => :lock) "
                    "                      ELSE locked_until END, "
                    "  failed_login_count = CASE WHEN failed_login_count + 1 >= :max "
                    "                            THEN 0 ELSE failed_login_count + 1 END "
                    "WHERE id = :u"
                ),
                {"u": user_id, "max": settings.max_failed_logins, "lock": settings.lockout_seconds},
            )
            outcome = _bad_credentials()
        else:
            await db.execute(
                text(
                    "UPDATE app_user SET failed_login_count = 0, locked_until = NULL, "
                    "last_login_at = now() WHERE id = :u"
                ),
                {"u": user_id},
            )
            outcome = await _complete_login(
                db, user_id, bool(user["is_platform_admin"]), body, request
            )
    # Committed (the failure counter included) before any error is raised.
    if isinstance(outcome, AppError):
        raise outcome
    return outcome


async def _complete_login(
    db: AsyncConnection,
    user_id: uuid.UUID,
    is_platform_admin: bool,
    body: LoginIn,
    request: Request,
) -> LoginOut | AppError:
    device = _device(body, request)
    if is_platform_admin:
        issued = await auth_sessions.open_session(
            db, user_id=user_id, hotel_id=None, kind="platform", device=device
        )
        view = await _platform_view(db, user_id)
        return LoginOut(**view.model_dump(), tokens=_tokens(issued))

    # Safe only after the password has been verified: exposes this user's own memberships.
    await set_context(db, user_id=user_id)
    hotels = (
        await db.execute(
            text(
                "SELECT m.hotel_id, h.name FROM membership m JOIN hotel h ON h.id = m.hotel_id "
                "WHERE m.user_id = :u AND m.status = 'active' AND h.status <> 'cancelled' "
                "ORDER BY h.name"
            ),
            {"u": user_id},
        )
    ).all()
    if not hotels:
        return _bad_credentials()

    chosen: uuid.UUID | None = None
    if body.hotel_id is not None:
        chosen = next((h for h, _ in hotels if h == body.hotel_id), None)
    elif len(hotels) == 1:
        chosen = hotels[0][0]
    if chosen is None:
        return conflict(
            "HOTEL_SELECTION_REQUIRED",
            "Choose which hotel to sign in to",
            [{"hotel_id": str(h), "name": n} for h, n in hotels],
        )

    await set_context(db, hotel_id=chosen)
    issued = await auth_sessions.open_session(
        db, user_id=user_id, hotel_id=chosen, kind="staff", device=device
    )
    view = await _staff_view(db, chosen, user_id)
    return LoginOut(**view.model_dump(), tokens=_tokens(issued))


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn) -> TokenPair:
    """Rotate. Clients must serialise refresh calls: see docs/08 and the error codes:

    - ``REFRESH_TOKEN_SUPERSEDED``: another refresh already won; use the newer token.
    - ``REFRESH_TOKEN_REUSED``: an old token was replayed; the session is revoked.
    """
    return _tokens(await auth_sessions.rotate(body.refresh_token))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: CurrentPrincipal) -> None:
    async with system_session() as db:
        await db.execute(
            text(
                "UPDATE auth_session SET revoked_at = now(), revoke_reason = 'logout' "
                "WHERE id = :sid AND user_id = :u AND revoked_at IS NULL"
            ),
            {"sid": principal.session_id, "u": principal.user_id},
        )


def _invite_gone() -> AppError:
    return AppError(status.HTTP_410_GONE, "INVITE_INVALID", "Invite is invalid or expired")


@router.get("/invites/{token}", response_model=InviteInfoOut)
async def invite_info(token: Annotated[str, Path(min_length=20, max_length=200)]) -> InviteInfoOut:
    async with system_session() as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT h.name AS hotel_name, u.full_name, u.password_hash IS NULL AS needs "
                        "FROM invite_token i JOIN hotel h ON h.id = i.hotel_id "
                        "JOIN app_user u ON u.id = i.user_id "
                        "WHERE i.token_hash = :h AND i.used_at IS NULL AND i.revoked_at IS NULL "
                        "AND i.expires_at > now() AND h.status <> 'cancelled'"
                    ),
                    {"h": hash_token(token)},
                )
            )
            .mappings()
            .one_or_none()
        )
    if row is None:
        raise _invite_gone()
    return InviteInfoOut(
        hotel_name=row["hotel_name"],
        full_name=row["full_name"],
        needs_new_password=bool(row["needs"]),
    )


@router.post("/invites/accept", response_model=LoginOut)
async def accept_invite(body: InviteAcceptIn, request: Request) -> LoginOut:
    """Join a hotel.

    A person with no password yet chooses one here. A person who **already** has a
    password (they work at another hotel) must enter that existing password: otherwise
    any hotel admin holding an invite could overwrite it and take over the account,
    including its access to other hotels.
    """
    token_hash = hash_token(body.token)
    async with system_session() as db:
        found = (
            (
                await db.execute(
                    text(
                        "SELECT i.user_id, u.password_hash FROM invite_token i "
                        "JOIN app_user u ON u.id = i.user_id WHERE i.token_hash = :h "
                        "AND i.used_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now() "
                        "AND u.status = 'active'"
                    ),
                    {"h": token_hash},
                )
            )
            .mappings()
            .one_or_none()
        )
    if found is None:
        raise _invite_gone()

    existing_hash: str | None = found["password_hash"]
    new_hash: str | None = None
    if existing_hash is None:
        new_hash = await hash_password_async(body.password)
    elif not await verify_password_async(body.password, existing_hash):
        raise unauthenticated(
            "INVALID_CREDENTIALS", "Enter the password you already use to sign in"
        )

    # Raising inside the transaction rolls it back, so a failed accept leaves the invite
    # unused and the password untouched.
    async with system_session() as db:
        claimed = (
            (
                await db.execute(
                    text(
                        "UPDATE invite_token SET used_at = now() WHERE token_hash = :h "
                        "AND used_at IS NULL AND revoked_at IS NULL AND expires_at > now() "
                        "RETURNING hotel_id, user_id"
                    ),
                    {"h": token_hash},
                )
            )
            .mappings()
            .one_or_none()
        )
        if claimed is None:
            raise _invite_gone()
        outcome = await _activate_membership(
            db, claimed["hotel_id"], claimed["user_id"], new_hash, body, request
        )
        if isinstance(outcome, AppError):
            raise outcome
        return outcome


async def _activate_membership(
    db: AsyncConnection,
    hotel_id: uuid.UUID,
    user_id: uuid.UUID,
    new_hash: str | None,
    body: InviteAcceptIn,
    request: Request,
) -> LoginOut | AppError:
    await set_context(db, hotel_id=hotel_id)
    if new_hash is not None:
        # "AND password_hash IS NULL": if a password was set meanwhile, do not overwrite it.
        updated = (
            await db.execute(
                text(
                    "UPDATE app_user SET password_hash = :p, password_updated_at = now() "
                    "WHERE id = :u AND password_hash IS NULL RETURNING id"
                ),
                {"p": new_hash, "u": user_id},
            )
        ).scalar_one_or_none()
        if updated is None:
            return conflict("PASSWORD_ALREADY_SET", "Sign in with your existing password")
    activated = (
        await db.execute(
            text(
                "UPDATE membership SET status = 'active', joined_at = COALESCE(joined_at, now()) "
                "WHERE hotel_id = :h AND user_id = :u AND status = 'invited' "
                "AND EXISTS (SELECT 1 FROM hotel WHERE id = :h AND status <> 'cancelled') "
                "RETURNING id"
            ),
            {"h": hotel_id, "u": user_id},
        )
    ).scalar_one_or_none()
    if activated is None:
        return _invite_gone()
    await write_hotel_audit(
        db,
        hotel_id=hotel_id,
        action="staff.invite_accepted",
        entity_type="membership",
        entity_id=activated,
        actor_user_id=user_id,
        ip=client_ip(request),
    )
    issued = await auth_sessions.open_session(
        db, user_id=user_id, hotel_id=hotel_id, kind="staff", device=_device(body, request)
    )
    view = await _staff_view(db, hotel_id, user_id)
    return LoginOut(**view.model_dump(), tokens=_tokens(issued))


@me_router.get("/me", response_model=MeOut)
async def me(principal: CurrentPrincipal, request: Request) -> MeOut:
    if principal.is_platform:
        async with open_platform_context(principal, request) as pctx:
            return await _platform_view(pctx.db, principal.user_id)
    async with open_hotel_context(principal, request) as hctx:
        return await _staff_view(hctx.db, hctx.hotel_id, principal.user_id)
