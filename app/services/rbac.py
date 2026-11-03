"""Guards that keep role management safe.

- **No privilege escalation**: you can only grant permissions you hold yourself, and you
  can only manage people and roles whose permissions are within yours. Without this a
  manager with ``staff.manage`` could make themselves (or a friend) hotel admin.
- **Last admin**: a hotel must always keep at least one active member who can manage it.
  Changes that could remove the last one take a per-hotel advisory lock, so two admins
  disabling each other at the same moment cannot both succeed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError, conflict, unprocessable

ADMIN_PERMISSION = "hotel.manage"


@dataclass(frozen=True)
class RoleRow:
    id: uuid.UUID
    code: str
    name: str
    permissions: frozenset[str]
    is_system: bool


async def get_role(db: AsyncConnection, hotel_id: uuid.UUID, role_id: uuid.UUID) -> RoleRow:
    """A system role or one of this hotel's roles. RLS hides every other hotel's roles."""
    row = (
        (
            await db.execute(
                text(
                    "SELECT id, code, name, permissions, is_system FROM role "
                    "WHERE id = :r AND (hotel_id IS NULL OR hotel_id = :h)"
                ),
                {"r": role_id, "h": hotel_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise unprocessable("UNKNOWN_ROLE", "Role not found")
    return RoleRow(
        row["id"], row["code"], row["name"], frozenset(row["permissions"]), row["is_system"]
    )


def assert_within(actor_permissions: frozenset[str], permissions: frozenset[str]) -> None:
    """``hotel.manage`` is full authority over the hotel, so its holders may grant any
    permission (including duties they do not perform themselves, like kitchen.work).
    Everyone else may grant only what they hold."""
    if ADMIN_PERMISSION in actor_permissions:
        return
    missing = permissions - actor_permissions
    if missing:
        raise AppError(
            403,
            "PRIVILEGE_ESCALATION",
            "You cannot grant or manage permissions you do not have yourself",
            {"missing": sorted(missing)},
        )


async def lock_admins(db: AsyncConnection, hotel_id: uuid.UUID) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"admins:{hotel_id}"}
    )


async def count_admins(
    db: AsyncConnection,
    hotel_id: uuid.UUID,
    *,
    excluding_membership: uuid.UUID | None = None,
    excluding_role: uuid.UUID | None = None,
) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM membership m JOIN role r ON r.id = m.role_id "
                    "WHERE m.hotel_id = :h AND m.status = 'active' AND :perm = ANY(r.permissions) "
                    "AND (CAST(:xm AS uuid) IS NULL OR m.id <> :xm) "
                    "AND (CAST(:xr AS uuid) IS NULL OR m.role_id <> :xr)"
                ),
                {
                    "h": hotel_id,
                    "perm": ADMIN_PERMISSION,
                    "xm": excluding_membership,
                    "xr": excluding_role,
                },
            )
        ).scalar_one()
    )


def last_admin() -> AppError:
    return conflict("LAST_ADMIN", "The hotel must keep at least one active admin")
