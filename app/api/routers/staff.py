"""Staff: invite people, set their role and outlets, disable and re-enable them.

Disabling a member revokes their sessions in this hotel at once (their next request is a
401), and only their membership here: the same person may still work at another hotel.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, text

from app.api.deps import HotelContext, require_permission
from app.api.schemas import InviteOut, Page, Strict
from app.core.errors import AppError, conflict, not_found, unprocessable
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.services.audit import write_hotel_audit
from app.services.auth_sessions import revoke_member_sessions
from app.services.entitlements import enforce_limit
from app.services.rbac import (
    ADMIN_PERMISSION,
    RoleRow,
    assert_within,
    count_admins,
    get_role,
    last_admin,
    lock_admins,
)
from app.services.staff import add_member, find_or_create_user, issue_invite

router = APIRouter(prefix="/staff", tags=["staff"])
ManageStaff = Annotated[HotelContext, Depends(require_permission("staff.manage"))]

MemberStatus = Literal["invited", "active", "disabled"]
KITCHEN_PERMISSION = "kitchen.work"


class StaffOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    full_name: str
    email: str | None
    phone: str | None
    role_id: uuid.UUID
    role_code: str
    role_name: str
    outlet_ids: list[uuid.UUID] | None
    status: MemberStatus
    joined_at: datetime | None
    created_at: datetime


class StaffCreatedOut(BaseModel):
    member: StaffOut
    invite: InviteOut


class StaffCreate(Strict):
    full_name: str = Field(min_length=2, max_length=200)
    login: str = Field(min_length=3, max_length=320, description="Email or mobile number")
    role_id: uuid.UUID
    outlet_ids: list[uuid.UUID] | None = Field(
        default=None, min_length=1, description="null = all outlets"
    )


class StaffPatch(Strict):
    role_id: uuid.UUID | None = None
    outlet_ids: list[uuid.UUID] | None = Field(default=None, min_length=1)


_SELECT = (
    "SELECT m.id, m.user_id, u.full_name, u.email, u.phone, m.role_id, r.code AS role_code, "
    "       r.name AS role_name, m.outlet_ids, m.status, m.joined_at, m.created_at "
    "FROM membership m JOIN app_user u ON u.id = m.user_id JOIN role r ON r.id = m.role_id "
    "WHERE m.hotel_id = :h"
)
_COUNT_STAFF = (
    "SELECT count(*) FROM membership WHERE hotel_id = :h AND status IN ('invited', 'active')"
)
_COUNT_CHEFS = (
    "SELECT count(*) FROM membership m JOIN role r ON r.id = m.role_id WHERE m.hotel_id = :h "
    "AND m.status IN ('invited', 'active') AND 'kitchen.work' = ANY(r.permissions)"
)


def _out(r: RowMapping) -> StaffOut:
    return StaffOut(**{**r, "outlet_ids": list(r["outlet_ids"]) if r["outlet_ids"] else None})


async def _get(ctx: HotelContext, member_id: uuid.UUID, *, for_update: bool = False) -> StaffOut:
    lock = " FOR UPDATE OF m" if for_update else ""
    row = (
        (
            await ctx.db.execute(
                text(_SELECT + " AND m.id = :id" + lock), {"h": ctx.hotel_id, "id": member_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise not_found("Staff member not found")
    return _out(row)


async def _check_outlets(ctx: HotelContext, outlet_ids: list[uuid.UUID] | None) -> None:
    if outlet_ids is None:
        if ctx.outlet_ids is not None:
            # An outlet-restricted manager cannot grant access to every outlet.
            raise AppError(403, "PRIVILEGE_ESCALATION", "You can only assign your own outlets")
        return
    wanted = set(outlet_ids)
    if ctx.outlet_ids is not None and not wanted <= set(ctx.outlet_ids):
        raise AppError(403, "PRIVILEGE_ESCALATION", "You can only assign your own outlets")
    found = (
        await ctx.db.execute(
            text(
                "SELECT count(*) FROM outlet WHERE hotel_id = :h AND status = 'active' "
                "AND id = ANY(:ids)"
            ),
            {"h": ctx.hotel_id, "ids": list(wanted)},
        )
    ).scalar_one()
    if found != len(wanted):
        raise unprocessable("UNKNOWN_OUTLET", "One or more outlets do not exist or are archived")


async def _check_role_limits(ctx: HotelContext, role: RoleRow) -> None:
    if KITCHEN_PERMISSION in role.permissions:
        await enforce_limit(
            ctx.db, await ctx.entitlements(), "MAX_CHEFS", _COUNT_CHEFS, {"h": ctx.hotel_id}
        )


async def _manageable(ctx: HotelContext, member: StaffOut) -> RoleRow:
    if member.user_id == ctx.user_id:
        raise AppError(403, "CANNOT_MODIFY_SELF", "Ask another admin to change your own access")
    current = await get_role(ctx.db, ctx.hotel_id, member.role_id)
    assert_within(ctx.permissions, current.permissions)
    return current


@router.get("", response_model=Page[StaffOut])
async def list_staff(
    ctx: ManageStaff,
    status_filter: Annotated[MemberStatus | None, Query(alias="status")] = None,
    role_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[StaffOut]:
    sql, params = _SELECT, {"h": ctx.hotel_id, "lim": limit + 1}
    if status_filter:
        sql += " AND m.status = :st"
        params["st"] = status_filter
    if role_id:
        sql += " AND m.role_id = :r"
        params["r"] = role_id
    if q:
        sql += " AND (u.full_name ILIKE :q OR u.phone LIKE :q OR u.email ILIKE :q)"
        params["q"] = f"%{q}%"
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        sql += " AND (m.created_at, m.id) < (:cs, :ci)"
        params.update(cs=stamp, ci=row_id)
    rows = (
        (
            await ctx.db.execute(
                text(sql + " ORDER BY m.created_at DESC, m.id DESC LIMIT :lim"), params
            )
        )
        .mappings()
        .all()
    )
    items = [_out(r) for r in rows[:limit]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return Page[StaffOut](items=items, next_cursor=nxt)


@router.post("", response_model=StaffCreatedOut, status_code=status.HTTP_201_CREATED)
async def invite_staff(body: StaffCreate, ctx: ManageStaff) -> StaffCreatedOut:
    """Add a person. They join when they accept the invite (shown once, to share with them).

    If the phone or email already has an account (they work at another hotel), the same
    account is linked; its name and password are never changed by this hotel.
    """
    role = await get_role(ctx.db, ctx.hotel_id, body.role_id)
    assert_within(ctx.permissions, role.permissions)
    await _check_outlets(ctx, body.outlet_ids)
    await enforce_limit(
        ctx.db, await ctx.entitlements(), "MAX_STAFF", _COUNT_STAFF, {"h": ctx.hotel_id}
    )
    await _check_role_limits(ctx, role)
    user_id = await find_or_create_user(ctx.db, full_name=body.full_name, login=body.login)
    member_id = await add_member(
        ctx.db,
        hotel_id=ctx.hotel_id,
        user_id=user_id,
        role_id=role.id,
        outlet_ids=body.outlet_ids,
        invited_by=ctx.user_id,
    )
    token, expires_at = await issue_invite(
        ctx.db, hotel_id=ctx.hotel_id, user_id=user_id, created_by=ctx.user_id
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="staff.invited",
        entity_type="membership",
        entity_id=member_id,
        actor_user_id=ctx.user_id,
        after={"role": role.code, "outlet_ids": body.outlet_ids},
        ip=ctx.ip,
    )
    return StaffCreatedOut(
        member=await _get(ctx, member_id), invite=InviteOut(token=token, expires_at=expires_at)
    )


@router.get("/{member_id}", response_model=StaffOut)
async def get_staff(member_id: uuid.UUID, ctx: ManageStaff) -> StaffOut:
    return await _get(ctx, member_id)


@router.patch("/{member_id}", response_model=StaffOut)
async def update_staff(member_id: uuid.UUID, body: StaffPatch, ctx: ManageStaff) -> StaffOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes or ("role_id" in changes and changes["role_id"] is None):
        raise unprocessable("NOTHING_TO_UPDATE", "No valid fields given")
    member = await _get(ctx, member_id, for_update=True)
    current = await _manageable(ctx, member)
    if "outlet_ids" in changes:
        await _check_outlets(ctx, body.outlet_ids)
    if "role_id" in changes and body.role_id != member.role_id:
        assert body.role_id is not None
        new_role = await get_role(ctx.db, ctx.hotel_id, body.role_id)
        assert_within(ctx.permissions, new_role.permissions)
        if KITCHEN_PERMISSION not in current.permissions and member.status != "disabled":
            await _check_role_limits(ctx, new_role)
        if (
            ADMIN_PERMISSION in current.permissions
            and ADMIN_PERMISSION not in new_role.permissions
            and member.status == "active"
        ):
            await lock_admins(ctx.db, ctx.hotel_id)
            if await count_admins(ctx.db, ctx.hotel_id, excluding_membership=member_id) == 0:
                raise last_admin()
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    await ctx.db.execute(
        text(f"UPDATE membership SET {sets} WHERE hotel_id = :h AND id = :id"),  # noqa: S608
        {**changes, "h": ctx.hotel_id, "id": member_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="staff.updated",
        entity_type="membership",
        entity_id=member_id,
        actor_user_id=ctx.user_id,
        before={"role_id": member.role_id, "outlet_ids": member.outlet_ids},
        after=changes,
        ip=ctx.ip,
    )
    return await _get(ctx, member_id)


@router.post("/{member_id}/disable", response_model=StaffOut)
async def disable_staff(member_id: uuid.UUID, ctx: ManageStaff) -> StaffOut:
    member = await _get(ctx, member_id, for_update=True)
    current = await _manageable(ctx, member)
    if member.status == "disabled":
        return member
    if ADMIN_PERMISSION in current.permissions and member.status == "active":
        await lock_admins(ctx.db, ctx.hotel_id)
        if await count_admins(ctx.db, ctx.hotel_id, excluding_membership=member_id) == 0:
            raise last_admin()
    await ctx.db.execute(
        text("UPDATE membership SET status = 'disabled' WHERE hotel_id = :h AND id = :id"),
        {"h": ctx.hotel_id, "id": member_id},
    )
    await ctx.db.execute(
        text(
            "UPDATE invite_token SET revoked_at = now() WHERE hotel_id = :h AND user_id = :u "
            "AND used_at IS NULL AND revoked_at IS NULL"
        ),
        {"h": ctx.hotel_id, "u": member.user_id},
    )
    await revoke_member_sessions(ctx.db, member.user_id, ctx.hotel_id, "member_disabled")
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="staff.disabled",
        entity_type="membership",
        entity_id=member_id,
        actor_user_id=ctx.user_id,
        ip=ctx.ip,
    )
    return await _get(ctx, member_id)


@router.post("/{member_id}/enable", response_model=StaffOut)
async def enable_staff(member_id: uuid.UUID, ctx: ManageStaff) -> StaffOut:
    member = await _get(ctx, member_id, for_update=True)
    current = await _manageable(ctx, member)
    if member.status != "disabled":
        return member
    await enforce_limit(
        ctx.db, await ctx.entitlements(), "MAX_STAFF", _COUNT_STAFF, {"h": ctx.hotel_id}
    )
    await _check_role_limits(ctx, current)
    new_status = "active" if member.joined_at is not None else "invited"
    await ctx.db.execute(
        text("UPDATE membership SET status = :s WHERE hotel_id = :h AND id = :id"),
        {"s": new_status, "h": ctx.hotel_id, "id": member_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="staff.enabled",
        entity_type="membership",
        entity_id=member_id,
        actor_user_id=ctx.user_id,
        after={"status": new_status},
        ip=ctx.ip,
    )
    return await _get(ctx, member_id)


@router.post("/{member_id}/invite", response_model=InviteOut)
async def reissue_invite(member_id: uuid.UUID, ctx: ManageStaff) -> InviteOut:
    """Issue a fresh invite (the previous one stops working)."""
    member = await _get(ctx, member_id, for_update=True)
    await _manageable(ctx, member)
    if member.status != "invited":
        raise conflict(
            "NOT_INVITED", "Only invited members need an invite", {"status": member.status}
        )
    token, expires_at = await issue_invite(
        ctx.db, hotel_id=ctx.hotel_id, user_id=member.user_id, created_by=ctx.user_id
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="staff.invite_reissued",
        entity_type="membership",
        entity_id=member_id,
        actor_user_id=ctx.user_id,
        ip=ctx.ip,
    )
    return InviteOut(token=token, expires_at=expires_at)
