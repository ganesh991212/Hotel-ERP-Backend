"""Permission registry and the system roles every hotel starts with.

Permissions are strings checked on the server. Roles hold a set of them. A hotel can add
custom roles but never grant a permission that is not in this registry, and a member can
never hand out a permission they do not hold themselves (no privilege escalation).

Later phases add permissions here; the registry is the single place they are defined.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

PERMISSIONS: dict[str, str] = {
    "hotel.manage": "Edit hotel profile and tax details",
    "outlet.manage": "Create, edit and archive outlets",
    "staff.manage": "Invite staff, change their role and outlets, disable them",
    "role.manage": "Create and edit custom roles",
    "audit.read": "Read the audit log",
    "menu.manage": "Edit menus, items and prices",
    "table.manage": "Edit areas, tables and QR codes",
    "order.create": "Take orders",
    "order.view": "See orders",
    "order.cancel": "Cancel orders or items",
    "kitchen.work": "Accept and prepare orders in the kitchen",
    "kitchen.manage": "Reassign, reorder or hold kitchen orders",
    "bill.create": "Create and print bills",
    "bill.discount": "Apply discounts",
    "payment.collect": "Record payments",
    "report.view": "See reports",
}


@dataclass(frozen=True)
class SystemRole:
    id: uuid.UUID
    code: str
    name: str
    permissions: frozenset[str]


def _rid(code: str) -> uuid.UUID:
    # Deterministic ids so code, migrations and tests agree without a lookup.
    return uuid.uuid5(uuid.NAMESPACE_URL, f"hotel-erp/system-role/{code}")


SYSTEM_ROLES: tuple[SystemRole, ...] = (
    # Everything except kitchen.work: that permission means "receives kitchen orders", and
    # an admin must never be put in the chef queue (Phase 4 routing).
    SystemRole(
        _rid("hotel_admin"),
        "hotel_admin",
        "Hotel admin",
        frozenset(PERMISSIONS) - {"kitchen.work"},
    ),
    SystemRole(
        _rid("manager"),
        "manager",
        "Manager",
        frozenset(
            {
                "menu.manage",
                "table.manage",
                "order.create",
                "order.view",
                "order.cancel",
                "kitchen.manage",
                "bill.create",
                "bill.discount",
                "payment.collect",
                "report.view",
                "audit.read",
            }
        ),
    ),
    SystemRole(
        _rid("cashier"),
        "cashier",
        "Cashier",
        frozenset({"order.view", "bill.create", "payment.collect"}),
    ),
    SystemRole(_rid("waiter"), "waiter", "Waiter", frozenset({"order.create", "order.view"})),
    SystemRole(_rid("chef"), "chef", "Chef", frozenset({"order.view", "kitchen.work"})),
)

SYSTEM_ROLE_BY_CODE = {r.code: r for r in SYSTEM_ROLES}
HOTEL_ADMIN_ROLE_ID = SYSTEM_ROLE_BY_CODE["hotel_admin"].id


def unknown_permissions(perms: set[str] | frozenset[str]) -> set[str]:
    return set(perms) - set(PERMISSIONS)
