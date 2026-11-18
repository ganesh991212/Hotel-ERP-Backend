"""Roles: five system roles shared by every hotel, plus a hotel's own custom roles.

Role permissions are read on every request, so editing a role changes what its members can
do on their very next request.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import HotelContext, HotelCtx, require_permission
from app.api.schemas import Strict
from app.core.errors import AppError, conflict, not_found, unprocessable
from app.core.permissions import PERMISSIONS, SYSTEM_ROLE_BY_CODE, unknown_permissions
from app.services.audit import write_hotel_audit
from app.services.rbac import ADMIN_PERMISSION, assert_within, count_admins, last_admin, lock_admins

router = APIRouter(tags=["roles"])
ManageRoles = Annotated[HotelContext, Depends(require_permission("role.manage"))]


class PermissionOut(BaseModel):
    key: str
    description: str


class RoleOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    permissions: list[str]
    is_system: bool
    member_count: int


def _valid_permissions(value: list[str]) -> list[str]:
    unknown = unknown_permissions(set(value))
    if unknown:
        raise ValueError(f"unknown permissions: {sorted(unknown)}")
    return sorted(set(value))


class RoleCreate(Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(min_length=1, max_length=len(PERMISSIONS))

    _perms = field_validator("permissions")(_valid_permissions)


class RolePatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = Field(default=None, min_length=1, max_length=len(PERMISSIONS))

    @field_validator("permissions")
    @classmethod
    def _perms(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _valid_permissions(value)


_SELECT = (
    "SELECT r.id, r.code, r.name, r.description, r.permissions, r.is_system, "
    "  (SELECT count(*) FROM membership m WHERE m.hotel_id = :h AND m.role_id = r.id "
    "    AND m.status <> 'disabled') AS member_count "
    "FROM role r WHERE (r.hotel_id IS NULL OR r.hotel_id = :h)"
)


async def _get(ctx: HotelContext, role_id: uuid.UUID) -> RoleOut:
    row = (
        (
            await ctx.db.execute(
                text(_SELECT + " AND r.id = :id"), {"h": ctx.hotel_id, "id": role_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise not_found("Role not found")
    return RoleOut(**{**row, "permissions": sorted(row["permissions"])})


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(_: HotelCtx) -> list[PermissionOut]:
    return [PermissionOut(key=k, description=v) for k, v in PERMISSIONS.items()]


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(ctx: HotelCtx) -> list[RoleOut]:
    rows = (
        (
            await ctx.db.execute(
                text(_SELECT + " ORDER BY r.is_system DESC, r.name"), {"h": ctx.hotel_id}
            )
        )
        .mappings()
        .all()
    )
    return [RoleOut(**{**r, "permissions": sorted(r["permissions"])}) for r in rows]


@router.post("/roles", response_model=RoleOut, status_code=status.HTTP_201_CREATED)
async def create_role(body: RoleCreate, ctx: ManageRoles) -> RoleOut:
    assert_within(ctx.permissions, frozenset(body.permissions))
    if body.code in SYSTEM_ROLE_BY_CODE:
        raise conflict("ROLE_CODE_TAKEN", "This code is reserved for a system role")
    try:
        async with ctx.db.begin_nested():
            role_id = (
                await ctx.db.execute(
                    text(
                        "INSERT INTO role (hotel_id, code, name, description, permissions) "
                        "VALUES (:h, :c, :n, :d, :p) RETURNING id"
                    ),
                    {
                        "h": ctx.hotel_id,
                        "c": body.code,
                        "n": body.name,
                        "d": body.description,
                        "p": body.permissions,
                    },
                )
            ).scalar_one()
    except IntegrityError as exc:
        raise conflict("ROLE_CODE_TAKEN", "Another role already uses this code") from exc
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="role.created",
        entity_type="role",
        entity_id=role_id,
        actor_user_id=ctx.user_id,
        after=body.model_dump(),
        ip=ctx.ip,
    )
    return await _get(ctx, role_id)


async def _editable(ctx: HotelContext, role_id: uuid.UUID) -> RoleOut:
    role = await _get(ctx, role_id)
    if role.is_system:
        raise AppError(403, "SYSTEM_ROLE_READ_ONLY", "System roles cannot be changed")
    assert_within(ctx.permissions, frozenset(role.permissions))
    return role


@router.patch("/roles/{role_id}", response_model=RoleOut)
async def update_role(role_id: uuid.UUID, body: RolePatch, ctx: ManageRoles) -> RoleOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes or any(v is None for k, v in changes.items() if k != "description"):
        raise unprocessable("NOTHING_TO_UPDATE", "No valid fields given")
    role = await _editable(ctx, role_id)
    if "permissions" in changes:
        new_perms = frozenset(changes["permissions"])
        assert_within(ctx.permissions, new_perms)
        if ADMIN_PERMISSION in role.permissions and ADMIN_PERMISSION not in new_perms:
            await lock_admins(ctx.db, ctx.hotel_id)
            if await count_admins(ctx.db, ctx.hotel_id, excluding_role=role_id) == 0:
                raise last_admin()
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    await ctx.db.execute(
        text(f"UPDATE role SET {sets} WHERE hotel_id = :h AND id = :id"),  # noqa: S608
        {**changes, "h": ctx.hotel_id, "id": role_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="role.updated",
        entity_type="role",
        entity_id=role_id,
        actor_user_id=ctx.user_id,
        before={"permissions": role.permissions},
        after=changes,
        ip=ctx.ip,
    )
    return await _get(ctx, role_id)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(role_id: uuid.UUID, ctx: ManageRoles) -> None:
    role = await _editable(ctx, role_id)
    in_use = (
        await ctx.db.execute(
            text("SELECT count(*) FROM membership WHERE hotel_id = :h AND role_id = :r"),
            {"h": ctx.hotel_id, "r": role_id},
        )
    ).scalar_one()
    if in_use:
        raise conflict(
            "ROLE_IN_USE", "Move its members to another role first", {"member_count": in_use}
        )
    await ctx.db.execute(
        text("DELETE FROM role WHERE hotel_id = :h AND id = :r"), {"h": ctx.hotel_id, "r": role_id}
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="role.deleted",
        entity_type="role",
        entity_id=role_id,
        actor_user_id=ctx.user_id,
        before={"code": role.code},
        ip=ctx.ip,
    )
