"""Feature registry (docs/05). Booleans gate capabilities; limits cap counts (None = unlimited).

Kept in code for v1 with descriptions; per-hotel enablement is via overrides.
"""

BOOL_FEATURES: dict[str, str] = {
    "QR_ORDERING": "Diners order by scanning the table QR",
    "WAITER_ORDERING": "Servers and admins take orders",
    "CHEF_ACCEPT_MODE": "Chefs pick up orders from a shared board",
    "AUTO_ROUTING": "System assigns orders to the least busy chef",
    "ADMIN_PRINT_MODE": "Kitchen cooks from printed tickets",
    "EXPENSES": "Record hotel expenses",
    "CUSTOM_ROLES": "Configure permissions per role",
    "REPORTS_ADVANCED": "Advanced reports",
    "RATINGS": "Diner ratings",
    "API_WEBHOOKS": "API keys and webhooks",
    "ROOMS": "Rooms module",
}

LIMITS: dict[str, str] = {
    "MAX_OUTLETS": "Outlets",
    "MAX_TABLES": "Tables",
    "MAX_STAFF": "Staff accounts",
    "MAX_CHEFS": "Chefs",
    "MAX_MENU_ITEMS": "Menu items",
    "MAX_ORDERS_PER_MONTH": "Orders per month",
}

# Seed plans. Prices are PLACEHOLDERS (is_placeholder=true), not market-validated (docs/05).
# Decision 2026-11-18: CHEF_ACCEPT_MODE is in every plan because "chef picks up the order"
# is the core flow the owner described; recorded in docs/09.
SEED_PLANS = [
    {
        "code": "STARTER",
        "name": "Starter",
        "price_minor": 99_900,
        "sort": 1,
        "features": {
            "WAITER_ORDERING": True,
            "CHEF_ACCEPT_MODE": True,
            "ADMIN_PRINT_MODE": True,
            "QR_ORDERING": True,
            "EXPENSES": True,
            "MAX_OUTLETS": 1,
            "MAX_TABLES": 15,
            "MAX_STAFF": 8,
            "MAX_CHEFS": 3,
            "MAX_MENU_ITEMS": 150,
            "MAX_ORDERS_PER_MONTH": 3000,
        },
    },
    {
        "code": "GROWTH",
        "name": "Growth",
        "price_minor": 249_900,
        "sort": 2,
        "features": {
            "WAITER_ORDERING": True,
            "CHEF_ACCEPT_MODE": True,
            "ADMIN_PRINT_MODE": True,
            "QR_ORDERING": True,
            "AUTO_ROUTING": True,
            "EXPENSES": True,
            "CUSTOM_ROLES": True,
            "RATINGS": True,
            "MAX_OUTLETS": 3,
            "MAX_TABLES": 60,
            "MAX_STAFF": 30,
            "MAX_CHEFS": 10,
            "MAX_MENU_ITEMS": 500,
            "MAX_ORDERS_PER_MONTH": None,
        },
    },
    {
        "code": "PRO",
        "name": "Pro",
        "price_minor": 499_900,
        "sort": 3,
        "features": {
            **{k: True for k in BOOL_FEATURES},
            "MAX_OUTLETS": None,
            "MAX_TABLES": None,
            "MAX_STAFF": None,
            "MAX_CHEFS": None,
            "MAX_MENU_ITEMS": None,
            "MAX_ORDERS_PER_MONTH": None,
        },
    },
]
