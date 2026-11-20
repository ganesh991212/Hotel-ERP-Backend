"""Order creation and state machine. Prices are computed here, never accepted from clients (rule 4)."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import bad_request, conflict
from app.core.security import now
from app.models import (
    Addon,
    AddonGroup,
    Assignment,
    DiningTable,
    HotelTaxProfile,
    MenuItem,
    MenuItemAddonGroup,
    MenuItemVariant,
    Order,
    OrderItem,
    OrderItemEvent,
    Outlet,
    TaxRule,
    User,
)
from app.ordering import pricing
from app.realtime.hub import diner_channel, outlet_channel

ORDER_STATES = ("PLACED", "CONFIRMED", "ASSIGNED", "PREPARING", "READY", "SERVED", "CANCELLED")
OPEN_STATES = ("PLACED", "CONFIRMED", "ASSIGNED", "PREPARING", "READY")
TRANSITIONS: dict[str, set[str]] = {
    "PLACED": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"ASSIGNED", "PREPARING", "CANCELLED"},  # PREPARING directly = paper path (KOT printed)
    "ASSIGNED": {"PREPARING", "CONFIRMED", "CANCELLED"},
    "PREPARING": {"READY", "CONFIRMED", "CANCELLED"},
    "READY": {"SERVED"},
    "SERVED": set(),
    "CANCELLED": set(),
}


@dataclass
class LineIn:
    item_id: UUID
    qty: int
    note: str | None = None
    variant_id: UUID | None = None
    addons: tuple[tuple[UUID, int], ...] = ()  # (addon id, picks per unit)


@dataclass
class _Choices:
    """The menu's variants and add-on groups for the items in one order."""

    variants: dict[UUID, list[MenuItemVariant]]
    groups: dict[UUID, list[AddonGroup]]  # item -> its groups
    addons: dict[UUID, Addon]  # addon id -> addon (only groups linked to these items)


async def _choices(db: AsyncSession, item_ids: set[UUID]) -> _Choices:
    variants: dict[UUID, list[MenuItemVariant]] = {}
    for v in (
        await db.execute(
            select(MenuItemVariant).where(MenuItemVariant.item_id.in_(item_ids)).order_by(MenuItemVariant.sort)
        )
    ).scalars():
        variants.setdefault(v.item_id, []).append(v)
    links = (
        await db.execute(
            select(MenuItemAddonGroup.item_id, AddonGroup)
            .join(AddonGroup, AddonGroup.id == MenuItemAddonGroup.group_id)
            .where(MenuItemAddonGroup.item_id.in_(item_ids))
            .order_by(MenuItemAddonGroup.sort)
        )
    ).all()
    groups: dict[UUID, list[AddonGroup]] = {}
    for item_id, g in links:
        groups.setdefault(item_id, []).append(g)
    group_ids = {g.id for _, g in links}
    addons = {}
    if group_ids:
        addons = {a.id: a for a in (await db.execute(select(Addon).where(Addon.group_id.in_(group_ids)))).scalars()}
    return _Choices(variants, groups, addons)


def _choose(item: MenuItem, ln: "LineIn", c: _Choices) -> tuple[int, str, MenuItemVariant | None, tuple]:
    """Validate one line's variant and add-ons against the menu; returns (unit price, name, variant, picks).
    The same rules for every channel: waiter, admin and QR diner (docs/14)."""
    item_ref = {"item_id": str(item.id)}
    variants = c.variants.get(item.id, [])
    variant = None
    if variants:
        variant = next((v for v in variants if v.id == ln.variant_id), None)
        if variant is None:
            raise bad_request("variant_required", f"Choose a size for {item.name}", item_ref)
        if not variant.is_available:
            raise conflict("item_unavailable", f"{item.name} ({variant.name}) is sold out", item_ref)
    elif ln.variant_id is not None:
        raise bad_request("bad_variant", f"{item.name} has no sizes", item_ref)
    groups = c.groups.get(item.id, [])
    allowed = {g.id for g in groups}
    picks: list[pricing.AddonPick] = []
    per_group: dict[UUID, int] = {}
    seen: set[UUID] = set()
    for addon_id, qty in ln.addons:
        a = c.addons.get(addon_id)
        if a is None or a.group_id not in allowed or addon_id in seen or not 1 <= qty <= 20:
            raise bad_request("bad_addon", f"That add-on is not offered with {item.name}", item_ref)
        if not a.is_available:
            raise conflict("item_unavailable", f"{a.name} is sold out", item_ref)
        seen.add(addon_id)
        per_group[a.group_id] = per_group.get(a.group_id, 0) + qty
        picks.append(pricing.AddonPick(str(a.id), a.name, a.price_minor, qty))
    for g in groups:
        n = per_group.get(g.id, 0)
        if not g.min_select <= n <= g.max_select:
            raise bad_request(
                "addon_count",
                f"{item.name}: pick {g.min_select} to {g.max_select} from {g.name}",
                {**item_ref, "group": g.name, "min": g.min_select, "max": g.max_select},
            )
    base = variant.price_minor if variant else item.price_minor
    name = f"{item.name} ({variant.name})" if variant else item.name
    return pricing.unit_price(base, tuple(picks)), name, variant, tuple(picks)


@dataclass
class Actor:
    kind: str  # USER | DINER | SYSTEM
    user_id: UUID | None = None


async def lock_outlet(db: AsyncSession, outlet_id: UUID) -> None:
    """Per-outlet transaction-scoped advisory lock (docs/04). Serialises numbering and routing."""
    await db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": str(outlet_id)})


async def quote(db: AsyncSession, lines: list[LineIn]) -> pricing.Totals:
    s = get_settings()
    if not lines:
        raise bad_request("empty_order", "Add at least one item")
    if len(lines) > s.max_items_per_order:
        raise bad_request("too_many_lines", "Too many items in one order")
    ids = {ln.item_id for ln in lines}
    items = {i.id: i for i in (await db.execute(select(MenuItem).where(MenuItem.id.in_(ids)))).scalars()}
    profile = (await db.execute(select(HotelTaxProfile))).scalar_one_or_none()
    reg = profile.gst_registration if profile else "UNREGISTERED"
    rules = [
        pricing.TaxRuleRow(r.code, r.rate_bp, r.valid_from, r.valid_to)
        for r in (await db.execute(select(TaxRule))).scalars()
    ]
    today = date.today()
    choices = await _choices(db, ids)
    price_lines = []
    for ln in lines:
        item = items.get(ln.item_id)
        if not item:
            raise bad_request("unknown_item", "An item is no longer on the menu", {"item_id": str(ln.item_id)})
        if not item.is_available:
            raise conflict("item_unavailable", f"{item.name} is sold out", {"item_id": str(item.id)})
        if not 1 <= ln.qty <= s.max_qty_per_line:
            raise bad_request("bad_quantity", "Quantity out of range", {"item_id": str(item.id)})
        unit, name, variant, picks = _choose(item, ln, choices)
        price_lines.append(
            pricing.PriceLine(
                str(item.id),
                name,
                ln.qty,
                unit,
                pricing.tax_rate_for(item.tax_code, today, rules, reg),  # add-ons take the dish's rate
                variant_id=str(variant.id) if variant else None,
                variant_name=variant.name if variant else None,
                addons=picks,
            )
        )
    return pricing.price(price_lines, profile.service_charge_bp if profile else 0)


def _line_json(p: pricing.PricedLine) -> dict:
    return {**p.__dict__, "addons": [a.__dict__ for a in p.addons]}


def totals_json(t: pricing.Totals) -> dict:
    return {
        "lines": [_line_json(p) for p in t.lines],
        "subtotal_minor": t.subtotal_minor,
        "tax_minor": t.tax_minor,
        "service_charge_minor": t.service_charge_minor,
        "total_minor": t.total_minor,
    }


async def order_json(db: AsyncSession, o: Order) -> dict:
    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == o.id))).scalars().all()
    table = await db.get(DiningTable, o.table_id) if o.table_id else None
    asg = (
        await db.execute(
            select(Assignment, User.name)
            .join(User, User.id == Assignment.chef_user_id)
            .where(Assignment.order_id == o.id, Assignment.status.in_(("QUEUED", "PREPARING", "HOLD", "READY")))
            .order_by(Assignment.assigned_at.desc())
            .limit(1)
        )
    ).first()
    return {
        "id": str(o.id),
        "number": o.number,
        "status": o.status,
        "channel": o.channel,
        "customer_name": o.customer_name,
        "customer_phone": o.customer_phone,
        "table": {"id": str(table.id), "label": table.label, "area": table.area} if table else None,
        "note": o.note,
        "items": [
            {
                "id": str(i.id),
                "item_id": str(i.item_id),
                "name": i.name,
                "qty": i.qty,
                "note": i.note,
                "unit_price_minor": i.unit_price_minor,
                "line_total_minor": i.line_total_minor,
                "status": i.status,
                "variant_name": i.variant_name,
                "addons": i.addons or [],
            }
            for i in items
        ],
        "subtotal_minor": o.subtotal_minor,
        "tax_minor": o.tax_minor,
        "service_charge_minor": o.service_charge_minor,
        "total_minor": o.total_minor,
        "est_prep_minutes": o.est_prep_minutes,
        "on_paper": o.on_paper,
        "kot_print_count": o.kot_print_count,
        "bill_id": str(o.bill_id) if o.bill_id else None,
        "chef": {
            "id": str(asg[0].chef_user_id),
            "name": asg[1],
            "queue_position": asg[0].queue_position,
            "assignment_status": asg[0].status,
        }
        if asg
        else None,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "started_at": o.started_at.isoformat() if o.started_at else None,
        "ready_at": o.ready_at.isoformat() if o.ready_at else None,
        "served_at": o.served_at.isoformat() if o.served_at else None,
        "pickup_alerted_at": o.pickup_alerted_at.isoformat() if o.pickup_alerted_at else None,
    }


async def transition(db: AsyncSession, o: Order, to: str, actor: Actor, reason: str | None = None) -> None:
    """Move an order and all its live items; every item change is an audited event (rule 6)."""
    if to not in TRANSITIONS[o.status]:
        raise conflict("bad_transition", f"Order is {o.status}; cannot move to {to}", {"from": o.status, "to": to})
    items = (await db.execute(select(OrderItem).where(OrderItem.order_id == o.id))).scalars().all()
    for it in items:
        if it.status == "CANCELLED":
            continue
        db.add(
            OrderItemEvent(
                hotel_id=o.hotel_id,
                order_id=o.id,
                order_item_id=it.id,
                from_status=it.status,
                to_status=to,
                actor_user_id=actor.user_id,
                actor_kind=actor.kind,
                reason=reason,
            )
        )
        it.status = to
    o.status = to
    ts = now()
    if to == "CONFIRMED" and not o.confirmed_at:
        o.confirmed_at = ts
    elif to == "PREPARING":
        o.started_at = ts
    elif to == "READY":
        o.ready_at = ts
    elif to == "SERVED":
        o.served_at = ts


async def mark_ready(db: AsyncSession, o: Order, actor: Actor, reason: str | None = None) -> None:
    """READY; in counter mode also SERVED at once (the diner collects, nobody carries it). served_at equals
    ready_at for counter orders, which the dashboard uses to leave them out of the serving average."""
    await transition(db, o, "READY", actor, reason)
    outlet = await db.get(Outlet, o.outlet_id)
    if outlet is not None and outlet.serve_mode == "COUNTER":
        await transition(db, o, "SERVED", actor, "counter")
        o.served_at = o.ready_at


async def create_order(
    db: AsyncSession,
    *,
    hotel_id: UUID,
    outlet: Outlet,
    table: DiningTable | None,
    session_id: UUID | None,
    channel: str,
    actor: Actor,
    lines: list[LineIn],
    note: str | None,
    client_ref: str | None,
) -> tuple[Order, bool]:
    """Returns (order, created). Idempotent on client_ref (offline waiter queue dedupe, docs/07)."""
    if client_ref:
        existing = (await db.execute(select(Order).where(Order.client_ref == client_ref))).scalar_one_or_none()
        if existing:
            return existing, False
    if table and table.status != "ACTIVE":
        raise conflict("table_inactive", "This table is not taking orders")
    totals = await quote(db, lines)
    await lock_outlet(db, outlet.id)
    number = (
        await db.execute(select(func.coalesce(func.max(Order.number), 0) + 1).where(Order.outlet_id == outlet.id))
    ).scalar_one()
    prep = {
        i.id: i.est_prep_minutes
        for i in (await db.execute(select(MenuItem).where(MenuItem.id.in_({ln.item_id for ln in lines})))).scalars()
    }
    o = Order(
        hotel_id=hotel_id,
        outlet_id=outlet.id,
        table_id=table.id if table else None,
        table_session_id=session_id,
        number=number,
        client_ref=client_ref,
        channel=channel,
        placed_by_user_id=actor.user_id,
        status="PLACED",
        note=note,
        subtotal_minor=totals.subtotal_minor,
        tax_minor=totals.tax_minor,
        service_charge_minor=totals.service_charge_minor,
        total_minor=totals.total_minor,
        est_prep_minutes=max(prep.values()),  # items cook in parallel; the slowest sets the order time
    )
    db.add(o)
    await db.flush()
    # Lines are matched by position: the same dish can be on an order twice (Half and Full).
    for ln, p in zip(lines, totals.lines, strict=True):
        it = OrderItem(
            hotel_id=hotel_id,
            order_id=o.id,
            item_id=UUID(p.item_id),
            name=p.name,
            qty=p.qty,
            unit_price_minor=p.unit_price_minor,
            tax_rate_bp=p.tax_rate_bp,
            line_total_minor=p.line_total_minor,
            est_prep_minutes=prep[UUID(p.item_id)],
            status="PLACED",
            note=ln.note,
            variant_id=UUID(p.variant_id) if p.variant_id else None,
            variant_name=p.variant_name,
            addons=[a.__dict__ for a in p.addons] or None,
        )
        db.add(it)
        await db.flush()
        db.add(
            OrderItemEvent(
                hotel_id=hotel_id,
                order_id=o.id,
                order_item_id=it.id,
                from_status=None,
                to_status="PLACED",
                actor_user_id=actor.user_id,
                actor_kind=actor.kind,
            )
        )
    needs_confirm = channel == "QR" and outlet.qr_confirm_required
    if not needs_confirm:
        await transition(db, o, "CONFIRMED", Actor("SYSTEM"))
    await db.flush()
    return o, True


async def emit_order(db: AsyncSession, emit, o: Order, kind: str) -> dict:
    """Queue realtime events for staff (outlet channel) and the diner who placed it."""
    data = await order_json(db, o)
    emit(outlet_channel(o.outlet_id), {"type": kind, "order": data})
    if o.table_session_id:
        emit(diner_channel(o.table_session_id), {"type": kind, "order": data})
    await _queue_push(db, emit, o, kind, data)
    return data


# Events that put a confirmed order in front of the kitchen: placed already confirmed, or confirmed by staff.
NEW_WORK = ("order.created", "order.confirmed")


async def _queue_push(db: AsyncSession, emit, o: Order, kind: str, data: dict) -> None:
    """Who gets a phone alert for this event (sent after commit; loud sound on mobile)."""
    from app.notify import push

    outlet = await db.get(Outlet, o.outlet_id)
    table = (data.get("table") or {}).get("label") or "-"
    items = ", ".join(f"{i['qty']}x {i['name']}" for i in data["items"] if i.get("status") != "CANCELLED")
    targets: list[tuple[str, str]] = []
    title, sound = "", push.SOUNDS["new_order"]
    if kind == "order.created" and o.status == "PLACED":
        targets = await push.targets_for(db, permission="order.confirm")
        title = f"Confirm order #{o.number} - Table {table}"
    elif kind in NEW_WORK and o.status == "CONFIRMED" and outlet.kitchen_mode == "CHEF_ACCEPT":
        targets = await push.targets_for(db, chefs_on_shift=True)
        title = f"New order #{o.number} - Table {table}"
    elif kind in NEW_WORK and o.status == "CONFIRMED" and outlet.kitchen_mode == "ADMIN_PRINT":
        targets = await push.targets_for(db, permission="kot.print")
        title = f"Print KOT #{o.number} - Table {table}"
    elif kind == "order.assigned" and data.get("chef"):
        targets = await push.targets_for(db, user_ids=[UUID(data["chef"]["id"])])
        title = f"Your next order #{o.number} - Table {table}"
    elif kind == "order.ready":
        targets = await push.targets_for(db, permission="order.serve")
        title, sound = f"Ready to serve #{o.number} - Table {table}", push.SOUNDS["ready"]
    if targets:
        emit(
            "push",
            {
                "targets": targets,
                "title": title,
                "body": items,
                "sound": sound,
                "data": {"type": kind, "order_id": str(o.id), "status": o.status},
            },
        )
