"""Permission strings (docs/08) and the default permission sets of system roles.

Hotel admins can edit any role's permissions except HOTEL_ADMIN (which always has all),
so the hotel can never lock itself out.
"""

PERMISSIONS: dict[str, str] = {
    "dashboard.view": "See the hotel dashboard",
    "order.view": "See orders",
    "order.create": "Take orders for a table",
    "order.confirm": "Confirm QR orders before the kitchen sees them",
    "order.serve": "Mark ready orders as served",
    "order.cancel": "Cancel orders (reason required)",
    "kitchen.cook": "Pick up and cook orders",
    "kitchen.manage": "Reassign or reorder chef queues",
    "table.manage": "Add tables and print QR codes",
    "menu.manage": "Edit the menu and prices",
    "menu.availability": "Mark items sold out or available",
    "staff.manage": "Add and disable staff",
    "role.manage": "Configure what each role can do",
    "expense.view": "See expenses",
    "expense.manage": "Record and edit expenses",
    "report.view": "See sales reports",
    "settings.manage": "Change outlet settings and subscription",
    "kot.print": "Print kitchen tickets (and hand orders to the kitchen on paper)",
    "bill.manage": "Create, print and settle table bills",
    "bill.void": "Void an issued bill (reason required)",
}

ALL = set(PERMISSIONS)

SYSTEM_ROLES: dict[str, tuple[str, set[str]]] = {
    "HOTEL_ADMIN": ("Hotel admin", ALL),
    "MANAGER": (
        "Manager",
        ALL - {"role.manage", "settings.manage"},
    ),
    "CASHIER": (
        "Cashier",
        {"dashboard.view", "order.view", "order.create", "order.serve", "expense.view", "kot.print", "bill.manage"},
    ),
    "WAITER": (
        "Server",
        {"order.view", "order.create", "order.confirm", "order.serve", "menu.availability", "kot.print", "bill.manage"},
    ),
    "CHEF": ("Chef", {"order.view", "kitchen.cook", "menu.availability", "kot.print"}),
}

LOCKED_ROLE = "HOTEL_ADMIN"
