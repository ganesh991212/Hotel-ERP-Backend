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
    Assignment,
    DiningTable,
    HotelTaxProfile,
    MenuItem,
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
    price_lines = []
    for ln in lines:
        item = items.get(ln.item_id)
        if not item:
            raise bad_request("unknown_item", "An item is no longer on the menu", {"item_id": str(ln.item_id)})
        if not item.is_available:
            raise conflict("item_unavailable", f"{item.name} is sold out", {"item_id": str(item.id)})
        if not 1 <= ln.qty <= s.max_qty_per_line:
            raise bad_request("bad_quantity", "Quantity out of range", {"item_id": str(item.id)})
        price_lines.append(
            pricing.PriceLine(
                str(item.id),
                item.name,
                ln.qty,
                item.price_minor,
                pricing.tax_rate_for(item.tax_code, today, rules, reg),
            )
        )
    return pricing.price(price_lines, profile.service_charge_bp if profile else 0)


def totals_json(t: pricing.Totals) -> dict:
    return {
        "lines": [p.__dict__ for p in t.lines],
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
    notes = {ln.item_id: ln.note for ln in lines}
    for p in totals.lines:
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
            note=notes.get(UUID(p.item_id)),
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
    elif kind == "order.created" and o.status == "CONFIRMED" and outlet.kitchen_mode == "CHEF_ACCEPT":
        targets = await push.targets_for(db, chefs_on_shift=True)
        title = f"New order #{o.number} - Table {table}"
    elif kind == "order.created" and o.status == "CONFIRMED" and outlet.kitchen_mode == "ADMIN_PRINT":
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
