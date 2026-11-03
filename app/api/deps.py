"""Request dependencies: where hotel context is established and permissions checked.

The hotel comes from the token, never from the request. The request path is:

1. Decode the JWT (no I/O).
2. Open a transaction and set ``app.hotel_id`` (one round trip).
3. One indexed query re-validates the session, the user, the membership and the hotel,
   and returns the role's permissions and the entitlements version (one round trip).
4. Handler runs. Entitlements come from the version-keyed cache (no query on a hit).

Re-validating per request is what makes revocation immediate: a disabled waiter loses
access on their next request, not when a 15-minute token expires.

DB dependencies use ``scope="function"`` so the transaction commits **before** the
response is sent. With FastAPI's default scope it commits after, and a client could see
200 for a write that then failed to commit (verified on FastAPI 0.162).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError, forbidden, unauthenticated
from app.core.security import decode_access_token
from app.db.session import hotel_session, system_session
from app.services import entitlements as ent_service
from app.services.audit import client_ip
from app.services.entitlements import Entitlements, feature_denied

_bearer = HTTPBearer(auto_error=False)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    session_id: uuid.UUID
    hotel_id: uuid.UUID | None
    is_platform: bool


async def current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise unauthenticated()
    try:
        claims = decode_access_token(credentials.credentials)
        raw_hid = claims.get("hid")
        return Principal(
            user_id=uuid.UUID(str(claims["sub"])),
            session_id=uuid.UUID(str(claims["sid"])),
            hotel_id=uuid.UUID(str(raw_hid)) if raw_hid else None,
            is_platform=claims.get("plat") is True,
        )
    except (jwt.PyJWTError, KeyError, ValueError, TypeError) as exc:
        # The reason (expired, bad signature) is not echoed back to a prober.
        raise unauthenticated() from exc


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


@dataclass
class HotelContext:
    db: AsyncConnection
    principal: Principal
    hotel_id: uuid.UUID
    role_id: uuid.UUID
    role_code: str
    permissions: frozenset[str]
    outlet_ids: tuple[uuid.UUID, ...] | None
    hotel_status: str
    entitlements_version: int
    ip: str | None
    _entitlements: Entitlements | None = field(default=None, repr=False)

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id

    async def entitlements(self) -> Entitlements:
        if self._entitlements is None:
            self._entitlements = await ent_service.get(
                self.db, self.hotel_id, self.entitlements_version
            )
        return self._entitlements


_ACCESS_SQL = text(
    """
    SELECT m.role_id, r.code AS role_code, r.permissions, m.outlet_ids,
           h.status AS hotel_status, h.entitlements_version
      FROM auth_session s
      JOIN app_user u   ON u.id = s.user_id
      JOIN membership m ON m.hotel_id = s.hotel_id AND m.user_id = s.user_id
      JOIN role r       ON r.id = m.role_id
      JOIN hotel h      ON h.id = s.hotel_id
     WHERE s.id = :sid AND s.user_id = :u AND s.hotel_id = :h
       AND s.kind = 'staff' AND s.revoked_at IS NULL AND s.expires_at > now()
       AND u.status = 'active' AND m.status = 'active' AND h.status <> 'cancelled'
    """
)


async def hotel_context(
    principal: CurrentPrincipal, request: Request
) -> AsyncIterator[HotelContext]:
    async with open_hotel_context(principal, request) as ctx:
        yield ctx


@asynccontextmanager
async def open_hotel_context(principal: Principal, request: Request) -> AsyncIterator[HotelContext]:
    if principal.is_platform or principal.hotel_id is None:
        raise forbidden("HOTEL_CONTEXT_REQUIRED", "This endpoint needs a hotel staff session")
    async with hotel_session(principal.hotel_id) as session:
        row = (
            (
                await session.execute(
                    _ACCESS_SQL,
                    {"sid": principal.session_id, "u": principal.user_id, "h": principal.hotel_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            # Revoked session, disabled member, cancelled hotel: all look the same.
            raise unauthenticated()
        if row["hotel_status"] == "suspended" and request.method not in _SAFE_METHODS:
            raise forbidden(
                "HOTEL_SUSPENDED", "This hotel is suspended; data is read-only until it is resumed"
            )
        outlets = row["outlet_ids"]
        yield HotelContext(
            db=session,
            principal=principal,
            hotel_id=principal.hotel_id,
            role_id=row["role_id"],
            role_code=row["role_code"],
            permissions=frozenset(row["permissions"]),
            outlet_ids=tuple(outlets) if outlets is not None else None,
            hotel_status=row["hotel_status"],
            entitlements_version=int(row["entitlements_version"]),
            ip=client_ip(request),
        )


HotelCtx = Annotated[HotelContext, Depends(hotel_context, scope="function")]


def require_permission(permission: str) -> Callable[[HotelContext], Awaitable[HotelContext]]:
    async def _check(ctx: HotelCtx) -> HotelContext:
        if permission not in ctx.permissions:
            raise AppError(
                403,
                "PERMISSION_DENIED",
                "You do not have permission for this action",
                {"permission": permission},
            )
        return ctx

    return _check


def require_feature(feature: str) -> Callable[[HotelContext], Awaitable[HotelContext]]:
    async def _check(ctx: HotelCtx) -> HotelContext:
        if not (await ctx.entitlements()).allows(feature):
            raise feature_denied(feature)
        return ctx

    return _check


@dataclass
class PlatformContext:
    db: AsyncConnection
    principal: Principal
    ip: str | None

    @property
    def user_id(self) -> uuid.UUID:
        return self.principal.user_id


async def platform_context(
    principal: CurrentPrincipal, request: Request
) -> AsyncIterator[PlatformContext]:
    async with open_platform_context(principal, request) as ctx:
        yield ctx


@asynccontextmanager
async def open_platform_context(
    principal: Principal, request: Request
) -> AsyncIterator[PlatformContext]:
    if not principal.is_platform:
        raise forbidden("PLATFORM_ADMIN_REQUIRED", "Super admin access required")
    async with system_session() as session:
        ok = (
            await session.execute(
                text(
                    "SELECT 1 FROM auth_session s JOIN app_user u ON u.id = s.user_id "
                    "WHERE s.id = :sid AND s.user_id = :u AND s.kind = 'platform' "
                    "AND s.revoked_at IS NULL AND s.expires_at > now() "
                    "AND u.status = 'active' AND u.is_platform_admin"
                ),
                {"sid": principal.session_id, "u": principal.user_id},
            )
        ).scalar_one_or_none()
        if ok is None:
            raise unauthenticated()
        yield PlatformContext(db=session, principal=principal, ip=client_ip(request))


PlatformCtx = Annotated[PlatformContext, Depends(platform_context, scope="function")]
