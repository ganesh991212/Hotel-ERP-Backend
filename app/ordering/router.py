from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import forbidden, not_found
from app.core.listing import ListParams
from app.core.security import now
from app.floor.router import current_outlet
from app.kitchen import service as kitchen
from app.models import DiningTable, Hotel, Order
from app.ordering.service import (
    OPEN_STATES,
    Actor,
    LineIn,
    create_order,
    emit_order,
    order_json,
    quote,
    totals_json,
)
from app.ordering.service import transition as do_transition

router = APIRouter(tags=["orders"])


class LineModel(BaseModel):
    item_id: UUID
    qty: int = Field(ge=1, le=99)
    note: str | None = Field(default=None, max_length=200)


class QuoteIn(BaseModel):
    """Item ids and quantities only. Prices are never accepted from the client (rule 4)."""

    items: list[LineModel] = Field(min_length=1, max_length=50)


class OrderIn(QuoteIn):
    table_id: UUID
    note: str | None = Field(default=None, max_length=300)
    client_ref: str | None = Field(default=None, max_length=64)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


def _lines(items: list[LineModel]) -> list[LineIn]:
    return [LineIn(i.item_id, i.qty, i.note) for i in items]


async def ensure_order_quota(ctx_db, entitlements: dict) -> None:
    limit = entitlements["limits"].get("MAX_ORDERS_PER_MONTH")
    if limit is None:
        return
    start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    n = (await ctx_db.execute(select(func.count()).select_from(Order).where(Order.created_at >= start))).scalar()
    if n >= limit:
        raise forbidden("limit_reached", "Monthly order limit reached", {"limit": "MAX_ORDERS_PER_MONTH", "max": limit})


@router.post("/orders/quote")
async def quote_order(body: QuoteIn, ctx: Ctx = HotelCtx("order.create")) -> dict:
    return totals_json(await quote(ctx.db, _lines(body.items)))


@router.post("/orders", status_code=201)
async def place_order(body: OrderIn, ctx: Ctx = HotelCtx("order.create", feature="WAITER_ORDERING")) -> dict:
    table = await ctx.db.get(DiningTable, body.table_id)
    if not table:
        raise not_found("Table")
    await ensure_order_quota(ctx.db, ctx.entitlements)
    outlet = await current_outlet(ctx)
    channel = "ADMIN" if "settings.manage" in ctx.principal.permissions else "WAITER"
    o, created = await create_order(
        ctx.db,
        hotel_id=ctx.hotel_id,
        outlet=outlet,
        table=table,
        session_id=None,
        channel=channel,
        actor=Actor("USER", ctx.user_id),
        lines=_lines(body.items),
        note=body.note,
        client_ref=body.client_ref,
    )
    if created:
        await emit_order(ctx.db, ctx.emit, o, "order.created")
        await kitchen.route_pending(ctx.db, outlet, ctx.emit)
    return await order_json(ctx.db, o)


@router.get("/orders")
async def list_orders(
    scope: str = Query("open", pattern="^(open|attention|today|all)$"),
    table_id: UUID | None = None,
    status: str | None = Query(None, pattern="^(PLACED|CONFIRMED|ASSIGNED|PREPARING|READY|SERVED|CANCELLED)$"),
    page: ListParams = Depends(),
    ctx: Ctx = HotelCtx("order.view"),
) -> list[dict]:
    """Newest first. `from`/`to` (hotel-local days, max 31), `q` (order number or table), `limit`/`offset`."""
    q = select(Order).order_by(Order.created_at.desc(), Order.id).limit(page.limit).offset(page.offset)
    if page.date_from:
        hotel = await ctx.db.get(Hotel, ctx.hotel_id)
        q = q.where(*page.dates(Order.created_at, hotel.timezone))
    if page.q:
        term = page.q.lstrip("#")
        tables = select(DiningTable.id).where(DiningTable.label.ilike(f"%{term}%"))
        q = q.where(or_(Order.table_id.in_(tables), *([Order.number == int(term)] if term.isdigit() else [])))
    if scope == "open":
        q = q.where(Order.status.in_(OPEN_STATES))
    elif scope == "attention":
        # The Floor's "Needs attention": still in progress, or served but not billed yet (next step: bill).
        q = q.where(or_(Order.status.in_(OPEN_STATES), and_(Order.status == "SERVED", Order.bill_id.is_(None))))
    elif scope == "today":
        q = q.where(Order.created_at >= now() - timedelta(hours=24))
    if table_id:
        q = q.where(Order.table_id == table_id)
    if status:
        q = q.where(Order.status == status)
    return [await order_json(ctx.db, o) for o in (await ctx.db.execute(q)).scalars()]


@router.get("/orders/{order_id}")
async def get_order(order_id: UUID, ctx: Ctx = HotelCtx("order.view")) -> dict:
    o = await ctx.db.get(Order, order_id)
    if not o:
        raise not_found("Order")
    return await order_json(ctx.db, o)


@router.post("/orders/{order_id}/confirm")
async def confirm(order_id: UUID, ctx: Ctx = HotelCtx("order.confirm")) -> dict:
    o = await ctx.db.get(Order, order_id, with_for_update=True, populate_existing=True)
    if not o:
        raise not_found("Order")
    await do_transition(ctx.db, o, "CONFIRMED", Actor("USER", ctx.user_id))
    # Its own event: a confirmed QR order is new work for the kitchen and must alert chefs like a new order.
    await emit_order(ctx.db, ctx.emit, o, "order.confirmed")
    outlet = await current_outlet(ctx)
    await kitchen.lock_outlet(ctx.db, outlet.id)
    await kitchen.route_pending(ctx.db, outlet, ctx.emit)
    return await order_json(ctx.db, o)


@router.post("/orders/{order_id}/serve")
async def serve(order_id: UUID, ctx: Ctx = HotelCtx("order.serve")) -> dict:
    o = await ctx.db.get(Order, order_id, with_for_update=True, populate_existing=True)
    if not o:
        raise not_found("Order")
    await do_transition(ctx.db, o, "SERVED", Actor("USER", ctx.user_id))
    o.served_by_user_id = ctx.user_id
    await emit_order(ctx.db, ctx.emit, o, "order.served")
    return await order_json(ctx.db, o)


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: UUID, body: ReasonIn, ctx: Ctx = HotelCtx("order.cancel")) -> dict:
    o = await kitchen.cancel(ctx.db, order_id, Actor("USER", ctx.user_id), body.reason, ctx.emit)
    await ctx.audit("order.cancel", "order", o.id, reason=body.reason)
    return await order_json(ctx.db, o)
