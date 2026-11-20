"""SQLAlchemy models. Every tenant table carries hotel_id and has an RLS policy (see migrations).

Money columns end in _minor (integer paise). Timestamps are UTC.
"""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
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
    # Largest bill discount this role may give, in basis points of the subtotal (0 = none, 10000 = 100%).
    max_discount_bp: Mapped[int] = mapped_column(Integer, server_default="0")


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
    # The current visit (docs/03 "Table visits"): when guests sat down (or first ordered) and how many.
    seated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    guests: Mapped[int | None] = mapped_column(SmallInteger)
    # A manual or automatic hold while nobody is seated: RESERVED (with a note) or CLEANING (after payment).
    hold_state: Mapped[str | None] = mapped_column(String(10))
    hold_note: Mapped[str | None] = mapped_column(String(80))


class Booking(Base):
    """A reservation (BOOKING, with a time) or a walk-in on the waitlist (WAITLIST, with a quoted wait)."""

    __tablename__ = "bookings"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str | None] = mapped_column(String(20))
    guests: Mapped[int] = mapped_column(SmallInteger)
    booked_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quoted_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    table_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dining_tables.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(String(200))
    seated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
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
    # Serving hours (docs/03 "Serving hours"): days bitmask Monday = 1 ... Sunday = 64; no times = all day.
    days: Mapped[int] = mapped_column(SmallInteger, server_default="127")
    available_from: Mapped[time | None] = mapped_column(Time)
    available_to: Mapped[time | None] = mapped_column(Time)


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


class MenuItemVariant(Base):
    """Half / Full, Small / Large: if an item has variants, one is chosen and its price replaces the item's."""

    __tablename__ = "menu_item_variants"
    __table_args__ = (UniqueConstraint("item_id", "name", name="uq_variants_item_name"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))
    price_minor: Mapped[int] = mapped_column(BigInteger)
    is_available: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class MenuItemComponent(Base):
    """A combo is an ordinary item with components (docs/03 "Combos"); no combos inside combos."""

    __tablename__ = "menu_item_components"
    __table_args__ = (UniqueConstraint("combo_item_id", "component_item_id", name="uq_combo_component"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    combo_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"), index=True
    )
    component_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="RESTRICT"), index=True
    )
    qty: Mapped[int] = mapped_column(Integer, server_default="1")
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class AddonGroup(Base):
    """A reusable set of add-ons ("Extras", "Choose your bread") with how many may be picked in total."""

    __tablename__ = "addon_groups"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    name: Mapped[str] = mapped_column(String(60))
    min_select: Mapped[int] = mapped_column(Integer, server_default="0")
    max_select: Mapped[int] = mapped_column(Integer, server_default="1")
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class Addon(Base):
    __tablename__ = "addons"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addon_groups.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    price_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    is_available: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class MenuItemAddonGroup(Base):
    __tablename__ = "menu_item_addon_groups"
    __table_args__ = (UniqueConstraint("item_id", "group_id", name="uq_item_addon_group"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("menu_items.id", ondelete="CASCADE"))
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("addon_groups.id", ondelete="CASCADE"), index=True
    )
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class PriceRule(Base):
    """Happy hour: percent off the dish/size price on some days, in a time window (may cross midnight), for some
    categories (NULL = whole menu). Applied on the server for every channel (docs/03 "Happy hour")."""

    __tablename__ = "price_rules"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    name: Mapped[str] = mapped_column(String(60))
    percent_bp: Mapped[int] = mapped_column(Integer)
    days: Mapped[int] = mapped_column(SmallInteger, server_default="127")  # Monday = 1 ... Sunday = 64
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    category_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created()


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
    # Snapshot of the choice (docs/03 "Variants and add-ons"): `name` already reads "Biryani (Half)";
    # addons = [{"id", "name", "price_minor", "qty"}] at the price of the moment, included in unit_price_minor.
    variant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    variant_name: Mapped[str | None] = mapped_column(String(40))
    addons: Mapped[list | None] = mapped_column(JSONB)
    # Combo contents when ordered: [{"item_id", "name", "qty"}] per unit (docs/03 "Combos").
    components: Mapped[list | None] = mapped_column(JSONB)
    # Position on the order as entered (KOT and order screens list items in this order).
    line_no: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    # Happy hour (docs/03): the dish/size price before the rule, and the rule's name; NULL when no rule applied.
    list_unit_price_minor: Mapped[int | None] = mapped_column(BigInteger)
    price_rule: Mapped[str | None] = mapped_column(String(60))


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


class DayClose(Base):
    """One cash-drawer close per hotel day (docs/03 "Day close"). Final once saved; figures are a snapshot."""

    __tablename__ = "day_closes"
    __table_args__ = (UniqueConstraint("hotel_id", "business_date", name="uq_day_closes_day"),)
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    business_date: Mapped[date] = mapped_column(Date)
    opening_float_minor: Mapped[int] = mapped_column(BigInteger)
    cash_sales_minor: Mapped[int] = mapped_column(BigInteger)
    cash_paid_out_minor: Mapped[int] = mapped_column(BigInteger)
    expected_minor: Mapped[int] = mapped_column(BigInteger)
    counted_minor: Mapped[int] = mapped_column(BigInteger)
    difference_minor: Mapped[int] = mapped_column(BigInteger)  # counted - expected: + over, - short
    float_kept_minor: Mapped[int] = mapped_column(BigInteger)
    deposit_minor: Mapped[int] = mapped_column(BigInteger)
    upi_minor: Mapped[int] = mapped_column(BigInteger)
    card_minor: Mapped[int] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(200))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    closed_at: Mapped[datetime] = created()


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
    # Bill-level discount (before tax: it lowers the taxable value). Who gave it and why are kept for the audit.
    discount_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")
    discount_reason: Mapped[str | None] = mapped_column(String(200))
    discount_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    payment_method: Mapped[str | None] = mapped_column(String(8))  # CASH | UPI | CARD | MIXED (several parts)
    guests: Mapped[int | None] = mapped_column(SmallInteger)  # covers: the visit's guests (first guest bill if split)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    print_count: Mapped[int] = mapped_column(Integer, server_default="0")
    void_reason: Mapped[str | None] = mapped_column(Text)
    # Split by items: guest bills of one visit share split_group_id (= the first guest bill's id, which the visit's
    # orders point to); guest_no of guest_count. All NULL for an ordinary bill.
    split_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("bills.id"), index=True)
    guest_no: Mapped[int | None] = mapped_column(SmallInteger)
    guest_count: Mapped[int | None] = mapped_column(SmallInteger)
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
    discount_minor: Mapped[int] = mapped_column(BigInteger, server_default="0")  # this line's share


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


class BillPayment(Base):
    """One payment towards a bill (a bill can be paid in parts: cash + UPI, or a share per guest)."""

    __tablename__ = "bill_payments"
    id: Mapped[uuid.UUID] = pk()
    hotel_id: Mapped[uuid.UUID] = hotel_fk()
    bill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(8))  # CASH | UPI | CARD
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    reference: Mapped[str | None] = mapped_column(String(60))  # UPI ref / card slip number, optional
    received_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()


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
    "bill_payments",
    "menu_item_variants",
    "addon_groups",
    "addons",
    "menu_item_addon_groups",
    "price_rules",
    "menu_item_components",
    "day_closes",
    "bookings",
]
