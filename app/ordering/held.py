"""Hold / resume: a cart saved on the server, not an order (docs/03 "Hold / resume", docs/14).

No kitchen ticket, number or bill. Prices are never stored: listing re-quotes at today's prices and flags dishes that
are gone or sold out. Placing the order with `held_order_id` consumes the hold in the same transaction.
"""

from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import ApiError, conflict, not_found
from app.models import DiningTable, HeldOrder, User
from app.ordering.router import LineModel, QuoteIn, _lines
from app.ordering.service import quote, totals_json

router = APIRouter(tags=["orders"])


class HoldIn(QuoteIn):
    table_id: UUID | None = None  # None = takeaway / counter
    customer_name: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=300)


async def _json(ctx: Ctx, h: HeldOrder) -> dict:
    lines = [_line_model(x) for x in h.items]
    priced, problem = None, None
    try:
        # Today's prices; serving hours are checked when the order is placed, as for any order.
        priced = totals_json(await quote(ctx.db, _lines(lines), allow_outside_hours=True))
    except ApiError as e:  # a dish removed or sold out since it was held: show it, never send it silently
        problem = {"code": e.code, "message": e.message}
    table = await ctx.db.get(DiningTable, h.table_id) if h.table_id else None
    by = await ctx.db.get(User, h.created_by) if h.created_by else None
    return {
        "id": str(h.id),
        "table": {"id": str(table.id), "label": table.label} if table else None,
        "customer_name": h.customer_name,
        "note": h.note,
        "items": h.items,  # what to put back in the cart (ids and quantities)
        "quote": priced,  # names and today's prices, or None when `problem` is set
        "problem": problem,
        "held_by": by.name if by else None,
        "created_at": h.created_at.isoformat(),
    }


def _line_model(x: dict) -> LineModel:
    return LineModel.model_validate(x)


@router.post("/held-orders", status_code=201)
async def hold(body: HoldIn, ctx: Ctx = HotelCtx("order.create", feature="WAITER_ORDERING")) -> dict:
    if body.table_id and not await ctx.db.get(DiningTable, body.table_id):
        raise not_found("Table")
    await quote(ctx.db, _lines(body.items), allow_outside_hours=True)  # only valid carts are held
    h = HeldOrder(
        hotel_id=ctx.hotel_id,
        table_id=body.table_id,
        customer_name=(body.customer_name or "").strip() or None,
        note=(body.note or "").strip() or None,
        items=[i.model_dump(mode="json", exclude_none=True) for i in body.items],
        created_by=ctx.user_id,
    )
    ctx.db.add(h)
    await ctx.db.flush()
    await ctx.db.refresh(h)
    await ctx.audit("order.hold", "held_order", h.id, after={"lines": len(h.items)})
    return await _json(ctx, h)


@router.get("/held-orders")
async def held(
    table_id: UUID | None = None,
    takeaway: bool = Query(False, description="Only takeaway / counter holds"),
    ctx: Ctx = HotelCtx("order.view"),
) -> list[dict]:
    """Oldest first: the guest who has waited longest is served first."""
    q = select(HeldOrder).order_by(HeldOrder.created_at)
    if table_id:
        q = q.where(HeldOrder.table_id == table_id)
    elif takeaway:
        q = q.where(HeldOrder.table_id.is_(None))
    return [await _json(ctx, h) for h in (await ctx.db.execute(q)).scalars()]


@router.delete("/held-orders/{held_id}", status_code=204)
async def discard(held_id: UUID, ctx: Ctx = HotelCtx("order.create")) -> None:
    h = await ctx.db.get(HeldOrder, held_id, with_for_update=True)
    if not h:
        raise conflict("hold_gone", "This held order was already sent or discarded")
    await ctx.audit("order.hold_discard", "held_order", h.id, before={"lines": len(h.items)})
    await ctx.db.delete(h)


async def consume(ctx: Ctx, held_id: UUID) -> None:
    """Placing the resumed order: the hold goes in the same transaction. Two devices resuming the same hold cannot
    both send it (row lock; the second finds it gone)."""
    h = await ctx.db.get(HeldOrder, held_id, with_for_update=True)
    if not h:
        raise conflict("hold_gone", "This held order was already sent or discarded")
    await ctx.db.delete(h)


async def held_count(ctx: Ctx) -> int:
    return (await ctx.db.execute(select(func.count()).select_from(HeldOrder))).scalar_one()
