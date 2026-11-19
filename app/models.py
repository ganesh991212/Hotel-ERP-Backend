"""SQLAlchemy models. Every tenant table carries hotel_id and has an RLS policy (see migrations).

Money columns end in _minor (integer paise). Timestamps are UTC.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def hotel_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), index=True)


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- platform (no RLS) --


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[uuid.UUID] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    interval: Mapped[str] = mapped_column(String(10), server_default="MONTHLY")  # MONTHLY | ANNUAL
    is_placeholder: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort: Mapped[int] = mapped_column(Integer, server_default="0")
    razorpay_plan_id: Mapped[str | None] = mapped_column(String(64))


class PlanFeature(Base):
    __tablename__ = "plan_features"
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(String(48), primary_key=True)
    bool_value: Mapped[bool | None] = mapped_column(Boolean)
    int_limit: Mapped[int | None] = mapped_column(Integer)


class Hotel(Base):
    __tablename__ = "hotels"
    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(120))
    legal_name: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str | None] = mapped_column(String(80))
    contact_email: Mapped[str | None] = mapped_column(String(160))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), server_default="TRIAL")  # TRIAL|ACTIVE|SUSPENDED
    timezone: Mapped[str] = mapped_column(String(40), server_default="Asia/Kolkata")
    created_at: Mapped[datetime] = created()


class WebhookEvent(Base):
    """Idempotency ledger for provider webhooks (Razorpay)."""

    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20))
    event: Mapped[str] = mapped_column(String(60))
    received_at: Mapped[datetime] = created()


class RefreshToken(Base):
    """Auth store; accessed only through the platform session during login/refresh/logout."""

    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


# ------------------------------------------------------------------- tenant (RLS) --


class HotelTaxProfile(Base):
    __tablename__ = "hotel_tax_profiles"
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    gst_registration: Mapped[str] = mapped_column(String(14), server_default="UNREGISTERED")
    gstin: Mapped[str | None] = mapped_column(String(15))
    state_code: Mapped[str | None] = mapped_column(String(2))
    service_charge_bp: Mapped[int] = mapped_column(Integer, server_default="0")


class Subscription(Base):
    __tablename__ = "subscriptions"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), unique=True
    )
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("plans.id"))
    status: Mapped[str] = mapped_column(String(12))  # TRIAL|ACTIVE|PAST_DUE|SUSPENDED|CANCELLED
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    razorpay_sub_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    checkout_url: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = created()


class SubscriptionInvoice(Base):
    __tablename__ = "subscription_invoices"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    subscription_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subscriptions.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12))  # PAID|FAILED
    provider_ref: Mapped[str | None] = mapped_column(String(64), unique=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created()


class EntitlementOverride(Base):
    __tablename__ = "hotel_entitlement_overrides"
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(String(48), primary_key=True)
    bool_value: Mapped[bool | None] = mapped_column(Boolean)
    int_limit: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    set_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("hotel_id", "code"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(80))
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(48), primary_key=True)
    hotel_id: Mapped[uuid.UUID] = hotel_fk()


class User(Base):
    """hotel_id is NULL only for platform (super) admins."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), index=True, nullable=True
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"))
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    password_hash: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), server_default="ACTIVE")  # ACTIVE|DISABLED
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = created()


class Outlet(Base):
    __tablename__ = "outlets"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    name: Mapped[str] = mapped_column(String(120))
    kitchen_mode: Mapped[str] = mapped_column(String(14), server_default="CHEF_ACCEPT")
    require_accept: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    max_queue_orders: Mapped[int] = mapped_column(Integer, server_default="3")
    rating_band_minutes: Mapped[int] = mapped_column(Integer, server_default="5")
    qr_confirm_required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Order workflow (docs/04): who brings the food, and when an unclaimed order raises an alert (null = off).
    serve_mode: Mapped[str] = mapped_column(String(8), server_default="SERVER")  # SERVER|COUNTER
    pickup_alert_minutes: Mapped[int | None] = mapped_column(Integer, server_default="5")
    created_at: Mapped[datetime] = created()


class DiningTable(Base):
    __tablename__ = "dining_tables"
    __table_args__ = (UniqueConstraint("outlet_id", "label"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(20))
    area: Mapped[str | None] = mapped_column(String(40))
    seats: Mapped[int] = mapped_column(Integer, server_default="4")
    qr_token: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(10), server_default="ACTIVE")
    created_at: Mapped[datetime] = created()


class TableSession(Base):
    __tablename__ = "table_sessions"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    table_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dining_tables.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    opened_at: Mapped[datetime] = created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MenuCategory(Base):
    __tablename__ = "menu_categories"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(80))
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class MenuItem(Base):
    __tablename__ = "menu_items"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_categories.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_veg: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    tax_code: Mapped[str] = mapped_column(String(20), server_default="FOOD")
    est_prep_minutes: Mapped[int] = mapped_column(Integer, server_default="10")
    is_available: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class TaxRule(Base):
    """Effective-dated tax rates in basis points. Never constants in code (rule 7)."""

    __tablename__ = "tax_rules"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    code: Mapped[str] = mapped_column(String(20))
    rate_bp: Mapped[int] = mapped_column(Integer)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("outlet_id", "number"),
        UniqueConstraint("hotel_id", "client_ref"),
        Index("ix_orders_outlet_status", "outlet_id", "status"),
        Index("ix_orders_hotel_created", "hotel_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    table_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dining_tables.id"))
    table_session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("table_sessions.id"))
    number: Mapped[int] = mapped_column(Integer)
    client_ref: Mapped[str | None] = mapped_column(String(64))  # idempotency / offline dedupe
    channel: Mapped[str] = mapped_column(String(8))  # QR|WAITER|ADMIN|TAKEAWAY
    # Takeaway / counter orders have no table; the name (and phone) identify the guest at pickup and on the bill.
    customer_name: Mapped[str | None] = mapped_column(String(80))
    customer_phone: Mapped[str | None] = mapped_column(String(20))
    placed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(Text)
    subtotal_minor: Mapped[int] = mapped_column(BigInteger)
    tax_minor: Mapped[int] = mapped_column(BigInteger)
    service_charge_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    total_minor: Mapped[int] = mapped_column(BigInteger)
    est_prep_minutes: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = created()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pickup_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # no-pickup alert sent
    served_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    served_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    kot_print_count: Mapped[int] = mapped_column(Integer, server_default="0")
    on_paper: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))  # handed to kitchen as a printed KOT
    bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", use_alter=True, name="fk_orders_bill_id"), index=True
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id"))
    name: Mapped[str] = mapped_column(String(120))  # snapshot at order time
    qty: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    tax_rate_bp: Mapped[int] = mapped_column(Integer)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)
    est_prep_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    note: Mapped[str | None] = mapped_column(Text)


class OrderItemEvent(Base):
    """Audit of every order-item state change (rule 6)."""

    __tablename__ = "order_item_events"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"))
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_items.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_kind: Mapped[str] = mapped_column(String(10))  # USER|DINER|SYSTEM
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = created()


class ChefProfile(Base):
    __tablename__ = "chef_profiles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    on_shift: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


ACTIVE_ASSIGNMENT = ("QUEUED", "PREPARING", "HOLD")


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        # One active assignment per order; backstop for the atomic claim.
        Index(
            "uq_assignments_active_order",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('QUEUED','PREPARING','HOLD')"),
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"))
    chef_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    queue_position: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))  # QUEUED|PREPARING|HOLD|READY|REASSIGNED|CANCELLED
    assigned_at: Mapped[datetime] = created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)


class ExpenseCategory(Base):
    """hotel_id NULL = global category created by the super admin, visible to every hotel (read-only to them).
    hotel_id set = that hotel's own category. RLS: read own + global, write own only."""

    __tablename__ = "expense_categories"
    __table_args__ = (
        Index(
            "uq_expense_categories_global_name",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("hotel_id IS NULL"),
        ),
        Index(
            "uq_expense_categories_hotel_name",
            "hotel_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("hotel_id IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (Index("ix_expenses_hotel_date", "hotel_id", "spent_on"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("expense_categories.id"))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    spent_on: Mapped[date] = mapped_column(Date)
    vendor: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(Text)
    payment_method: Mapped[str] = mapped_column(String(10), server_default="CASH")  # CASH|UPI|CARD|BANK
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()


class Bill(Base):
    """Customer bill for a table. Numbered sequentially per hotel per financial year; immutable once issued.
    Correction = VOID (audited, reason required) and re-issue; credit notes come with Phase 5."""

    __tablename__ = "bills"
    __table_args__ = (
        UniqueConstraint("hotel_id", "fy", "number"),
        Index("ix_bills_hotel_created", "hotel_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    outlet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("outlets.id"))
    table_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("dining_tables.id"))
    fy: Mapped[str] = mapped_column(String(7))  # "2026-27"
    number: Mapped[int] = mapped_column(Integer)
    invoice_no: Mapped[str] = mapped_column(String(16))
    doc_type: Mapped[str] = mapped_column(String(16))  # TAX_INVOICE | BILL_OF_SUPPLY | BILL
    status: Mapped[str] = mapped_column(String(8), server_default="ISSUED")  # ISSUED | PAID | VOID
    subtotal_minor: Mapped[int] = mapped_column(BigInteger)
    tax_minor: Mapped[int] = mapped_column(BigInteger)
    cgst_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    sgst_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    service_charge_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    total_minor: Mapped[int] = mapped_column(BigInteger)
    payment_method: Mapped[str | None] = mapped_column(String(8))  # CASH | UPI | CARD
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    print_count: Mapped[int] = mapped_column(Integer, server_default="0")
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()


class BillLine(Base):
    """Snapshot of what was billed; later menu price changes never alter an issued bill."""

    __tablename__ = "bill_lines"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    qty: Mapped[int] = mapped_column(Integer)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger)
    tax_rate_bp: Mapped[int] = mapped_column(Integer)
    line_total_minor: Mapped[int] = mapped_column(BigInteger)


class PrintLog(Base):
    """Every KOT and bill print, including reprints (a common leakage point worth auditing)."""

    __tablename__ = "print_log"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    kind: Mapped[str] = mapped_column(String(4))  # KOT | BILL
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"))
    bill_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"))
    copy_no: Mapped[int] = mapped_column(Integer)
    printed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    at: Mapped[datetime] = created()


class DeviceToken(Base):
    """FCM registration tokens for push (ported from GigBridge's device_tokens)."""

    __tablename__ = "device_tokens"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(400), unique=True)
    platform: Mapped[str] = mapped_column(String(10))  # android | ios | web
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HotelBranding(Base):
    """What a hotel prints and shows: logo (sanitised PNG, re-encoded on upload) and a one-line address."""

    __tablename__ = "hotel_branding"
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("hotels.id", ondelete="CASCADE"), primary_key=True
    )
    address_line: Mapped[str | None] = mapped_column(String(200))
    logo_png: Mapped[bytes | None] = mapped_column(LargeBinary)
    logo_version: Mapped[int] = mapped_column(Integer, server_default="0")  # cache-busting for clients
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = created()


# Tables with tenant RLS. The migration enables RLS on each and the RLS test iterates this list.
TENANT_TABLES = [
    "hotel_tax_profiles",
    "subscriptions",
    "subscription_invoices",
    "hotel_entitlement_overrides",
    "roles",
    "role_permissions",
    "users",
    "outlets",
    "dining_tables",
    "table_sessions",
    "menu_categories",
    "menu_items",
    "tax_rules",
    "orders",
    "order_items",
    "order_item_events",
    "chef_profiles",
    "assignments",
    "expense_categories",  # own policy: also reads global (hotel_id NULL) rows, see migration 0002
    "expenses",
    "audit_log",
    "bills",
    "bill_lines",
    "print_log",
    "device_tokens",
    "hotel_branding",
]
