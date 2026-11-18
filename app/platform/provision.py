"""Hotel provisioning: everything a new tenant needs, in one transaction (platform role)."""

from datetime import date, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password, now
from app.iam.permissions import SYSTEM_ROLES
from app.models import (
    ExpenseCategory,
    Hotel,
    HotelTaxProfile,
    Outlet,
    Plan,
    PlanFeature,
    Role,
    RolePermission,
    Subscription,
    TaxRule,
    User,
)
from app.platform.features import SEED_PLANS

DEFAULT_EXPENSE_CATEGORIES = ["Groceries", "Vegetables", "Gas", "Salaries", "Rent", "Utilities", "Maintenance", "Other"]
# Default restaurant GST rate as effective-dated config (rule 7). CA review pending (BUILD-PLAN Phase 5).
DEFAULT_TAX_RULES = [("FOOD", 500, date(2017, 7, 1))]


async def seed_plans(db: AsyncSession) -> None:
    for p in SEED_PLANS:
        existing = (await db.execute(select(Plan).where(Plan.code == p["code"]))).scalar_one_or_none()
        if existing:
            continue
        plan = Plan(code=p["code"], name=p["name"], price_minor=p["price_minor"], sort=p["sort"], is_placeholder=True)
        db.add(plan)
        await db.flush()
        for key, val in p["features"].items():
            if isinstance(val, bool):
                db.add(PlanFeature(plan_id=plan.id, feature_key=key, bool_value=val))
            else:
                db.add(PlanFeature(plan_id=plan.id, feature_key=key, int_limit=val))


async def provision_hotel(
    db: AsyncSession,
    *,
    name: str,
    plan: Plan,
    admin_name: str,
    admin_email: str,
    admin_password: str,
    legal_name: str | None = None,
    city: str | None = None,
    contact_phone: str | None = None,
    gst_registration: str = "UNREGISTERED",
    gstin: str | None = None,
    state_code: str | None = None,
    service_charge_bp: int = 0,
) -> tuple[Hotel, User]:
    hotel = Hotel(
        id=uuid4(),
        name=name,
        legal_name=legal_name,
        city=city,
        contact_email=admin_email.lower(),
        contact_phone=contact_phone,
        status="TRIAL",
    )
    db.add(hotel)
    await db.flush()
    db.add(
        HotelTaxProfile(
            hotel_id=hotel.id,
            gst_registration=gst_registration,
            gstin=gstin,
            state_code=state_code,
            service_charge_bp=service_charge_bp,
        )
    )
    db.add(Outlet(hotel_id=hotel.id, name=name))
    admin_role_id = None
    for code, (role_name, perms) in SYSTEM_ROLES.items():
        role = Role(hotel_id=hotel.id, code=code, name=role_name, is_system=True)
        db.add(role)
        await db.flush()
        for perm in perms:
            db.add(RolePermission(role_id=role.id, permission=perm, hotel_id=hotel.id))
        if code == "HOTEL_ADMIN":
            admin_role_id = role.id
    admin = User(
        hotel_id=hotel.id,
        role_id=admin_role_id,
        name=admin_name,
        email=admin_email.lower(),
        password_hash=hash_password(admin_password),
    )
    db.add(admin)
    s = get_settings()
    db.add(
        Subscription(
            hotel_id=hotel.id, plan_id=plan.id, status="TRIAL", trial_ends_at=now() + timedelta(days=s.trial_days)
        )
    )
    for cat in DEFAULT_EXPENSE_CATEGORIES:
        db.add(ExpenseCategory(hotel_id=hotel.id, name=cat))
    for code, bp, start in DEFAULT_TAX_RULES:
        db.add(TaxRule(hotel_id=hotel.id, code=code, rate_bp=bp, valid_from=start))
    await db.flush()
    return hotel, admin
