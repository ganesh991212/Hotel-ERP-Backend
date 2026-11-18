from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import jwt
from fastapi import Depends, Header
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import platform_session, tenant_session
from app.core.errors import ApiError, forbidden
from app.core.security import decode_access_token, now
from app.models import (
    AuditLog,
    EntitlementOverride,
    Plan,
    PlanFeature,
    Role,
    RolePermission,
    Subscription,
    User,
)
from app.platform.entitlements import FeatureValue, resolve, within_limit
from app.platform.lifecycle import effective_status
from app.realtime.hub import hub


@dataclass
class Principal:
    user_id: UUID
    hotel_id: UUID | None
    platform: bool
    name: str = ""
    role_code: str | None = None
    permissions: set[str] = field(default_factory=set)


@dataclass
class Ctx:
    """Request context: verified principal, RLS-scoped session, entitlements, post-commit events."""

    principal: Principal
    db: AsyncSession
    entitlements: dict
    events: list[tuple[str, dict]] = field(default_factory=list)

    @property
    def hotel_id(self) -> UUID:
        assert self.principal.hotel_id
        return self.principal.hotel_id

    @property
    def user_id(self) -> UUID:
        return self.principal.user_id

    def emit(self, channel: str, event: dict) -> None:
        self.events.append((channel, event))

    def require_feature(self, key: str) -> None:
        if not self.entitlements["features"].get(key):
            raise forbidden("feature_locked", f"Your plan does not include {key}", {"feature": key})

    def require_permission(self, perm: str) -> None:
        if perm not in self.principal.permissions:
            raise forbidden("permission_denied", f"Missing permission {perm}", {"permission": perm})

    async def require_limit(self, key: str, count_stmt) -> None:
        current = (await self.db.execute(count_stmt)).scalar_one()
        limit = self.entitlements["limits"].get(key)
        if not within_limit(limit, current):
            raise forbidden("limit_reached", f"Plan limit reached for {key}", {"limit": key, "max": limit})

    async def audit(
        self, action: str, entity: str, entity_id: Any = None, before=None, after=None, reason: str | None = None
    ) -> None:
        self.db.add(
            AuditLog(
                hotel_id=self.principal.hotel_id,
                actor_user_id=self.user_id,
                action=action,
                entity=entity,
                entity_id=str(entity_id) if entity_id else None,
                before=before,
                after=after,
                reason=reason,
            )
        )


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError(401, "unauthenticated", "Missing bearer token")
    return authorization.split(" ", 1)[1]


def claims_from_header(authorization: str | None = Header(default=None)) -> dict:
    try:
        return decode_access_token(_bearer(authorization))
    except jwt.ExpiredSignatureError as e:
        raise ApiError(401, "token_expired", "Access token expired") from e
    except jwt.InvalidTokenError as e:
        raise ApiError(401, "unauthenticated", "Invalid token") from e


async def load_entitlements(db: AsyncSession, hotel_id: UUID) -> dict:
    sub = (await db.execute(select(Subscription).where(Subscription.hotel_id == hotel_id))).scalar_one_or_none()
    if not sub:
        return resolve({}, {}, "SUSPENDED") | {"plan": None}
    plan = await db.get(Plan, sub.plan_id)
    rows = (await db.execute(select(PlanFeature).where(PlanFeature.plan_id == sub.plan_id))).scalars()
    plan_map = {r.feature_key: FeatureValue(r.bool_value, r.int_limit) for r in rows}
    ovr = (await db.execute(select(EntitlementOverride).where(EntitlementOverride.hotel_id == hotel_id))).scalars()
    ovr_map = {r.feature_key: FeatureValue(r.bool_value, r.int_limit) for r in ovr}
    status = effective_status(sub.status, now(), sub.trial_ends_at, sub.current_period_end, get_settings().grace_days)
    out = resolve(plan_map, ovr_map, status)
    out["plan"] = {"code": plan.code, "name": plan.name} if plan else None
    out["trial_ends_at"] = sub.trial_ends_at.isoformat() if sub.trial_ends_at else None
    out["current_period_end"] = sub.current_period_end.isoformat() if sub.current_period_end else None
    return out


async def load_principal(db: AsyncSession, claims: dict) -> Principal:
    uid = UUID(claims["sub"])
    user = await db.get(User, uid)
    if not user or user.status != "ACTIVE":
        raise ApiError(401, "unauthenticated", "User is disabled")
    perms: set[str] = set()
    code = None
    if user.role_id:
        role = await db.get(Role, user.role_id)
        code = role.code if role else None
        perms = set(
            (
                await db.execute(select(RolePermission.permission).where(RolePermission.role_id == user.role_id))
            ).scalars()
        )
    return Principal(uid, user.hotel_id, False, user.name, code, perms)


def hotel_ctx(permission: str | None = None, feature: str | None = None) -> Callable:
    """Dependency factory: tenant transaction + permission + plan feature checks (rules 2, 3)."""

    async def dep(claims: dict = Depends(claims_from_header)) -> AsyncIterator[Ctx]:
        if claims.get("plt") or not claims.get("hid"):
            raise forbidden("wrong_audience", "Hotel endpoint")
        hotel_id = UUID(claims["hid"])
        ctx: Ctx | None = None
        async with tenant_session(hotel_id) as db:
            principal = await load_principal(db, claims)
            ctx = Ctx(principal, db, await load_entitlements(db, hotel_id))
            if permission:
                ctx.require_permission(permission)
            if feature:
                ctx.require_feature(feature)
            yield ctx
        # Committed: now it is safe to tell clients.
        for channel, event in ctx.events:
            hub.publish(channel, event)

    return dep


async def platform_ctx(claims: dict = Depends(claims_from_header)) -> AsyncIterator[Ctx]:
    if not claims.get("plt"):
        raise forbidden("wrong_audience", "Platform admin only")
    async with platform_session() as db:
        user = await db.get(User, UUID(claims["sub"]))
        if not user or not user.is_platform_admin or user.status != "ACTIVE":
            raise ApiError(401, "unauthenticated", "Not a platform admin")
        yield Ctx(Principal(user.id, None, True, user.name, "PLATFORM_ADMIN"), db, {})


def count(model, *where):
    return select(func.count()).select_from(model).where(*where)


def HotelCtx(permission: str | None = None, feature: str | None = None):  # noqa: N802
    """Use as `ctx: Ctx = HotelCtx("order.create")`. scope=function: commit happens before the response."""
    return Depends(hotel_ctx(permission, feature), scope="function")


PlatformCtx = Depends(platform_ctx, scope="function")
