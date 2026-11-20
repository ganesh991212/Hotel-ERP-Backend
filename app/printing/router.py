"""Kitchen order tickets (KOT).

Use case: the chef cannot check a phone. A server or manager prints the KOT and walks it to the kitchen.
Printing with send_to_kitchen=true moves a waiting order onto the *paper path*: it leaves the chefs' pick-up
board (no chef claims it twice), is marked PREPARING, and staff mark it READY when it comes out. Works in
every kitchen mode; in ADMIN_PRINT mode it is the normal flow.
"""

import base64
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import conflict, forbidden, not_found
from app.kitchen.service import lock_outlet
from app.models import ACTIVE_ASSIGNMENT, Assignment, Hotel, Order, PrintLog, User
from app.ordering.service import Actor, emit_order, order_json, transition
from app.ordering.service import mark_ready as finish_ready
from app.printing import escpos
from app.printing.documents import kot_blocks, kot_stations

router = APIRouter(tags=["printing"])


class KotIn(BaseModel):
    send_to_kitchen: bool = True
    paper_mm: int | None = Field(default=None, description="58 or 80: also return ESC/POS bytes for a thermal printer")


async def _kot(ctx: Ctx, o: Order, copy_no: int) -> dict:
    hotel = await ctx.db.get(Hotel, ctx.hotel_id)
    me = await ctx.db.get(User, ctx.user_id)
    data = await order_json(ctx.db, o)
    common = {
        "hotel_name": hotel.name,
        "order": data,
        "tz": hotel.timezone,
        "copy_no": copy_no,
        "printed_by": me.name if me else None,
        "created_at": o.created_at,
    }
    # One ticket per station when the order spans several (Bar, Tandoor, Kitchen...); the full ticket stays too.
    stations = kot_stations(data)
    tickets = (
        [{"station": st, "blocks": kot_blocks(**common, station=st)} for st in stations] if len(stations) > 1 else []
    )
    return {"order": data, "copy_no": copy_no, "blocks": kot_blocks(**common), "tickets": tickets}


@router.get("/orders/{order_id}/kot")
async def preview_kot(order_id: UUID, ctx: Ctx = HotelCtx("kot.print")) -> dict:
    o = await ctx.db.get(Order, order_id)
    if not o:
        raise not_found("Order")
    return await _kot(ctx, o, o.kot_print_count + 1)


@router.post("/orders/{order_id}/kot")
async def print_kot(order_id: UUID, body: KotIn, ctx: Ctx = HotelCtx("kot.print")) -> dict:
    o0 = await ctx.db.get(Order, order_id)
    if not o0:
        raise not_found("Order")
    await lock_outlet(ctx.db, o0.outlet_id)
    o = await ctx.db.get(Order, order_id, with_for_update=True, populate_existing=True)
    if o.status in ("CANCELLED", "SERVED"):
        raise conflict("bad_transition", f"Order is {o.status}")
    if body.send_to_kitchen and o.status == "PLACED":
        raise conflict("needs_confirmation", "Confirm this QR order before sending it to the kitchen")
    if body.send_to_kitchen and o.status == "CONFIRMED":
        o.on_paper = True
        await transition(ctx.db, o, "PREPARING", Actor("USER", ctx.user_id), "kot printed")
    o.kot_print_count += 1
    ctx.db.add(
        PrintLog(hotel_id=ctx.hotel_id, kind="KOT", order_id=o.id, copy_no=o.kot_print_count, printed_by=ctx.user_id)
    )
    await ctx.db.flush()
    await emit_order(ctx.db, ctx.emit, o, "order.updated")
    out = await _kot(ctx, o, o.kot_print_count)
    if body.paper_mm:
        out["escpos_b64"] = base64.b64encode(escpos.render(out["blocks"], body.paper_mm)).decode()
        for t in out["tickets"]:  # network printers get each station's slip as raw bytes too
            t["escpos_b64"] = base64.b64encode(escpos.render(t["blocks"], body.paper_mm)).decode()
    return out


@router.post("/orders/{order_id}/mark-ready")
async def mark_ready(order_id: UUID, ctx: Ctx = HotelCtx("order.view")) -> dict:
    """Paper path: staff mark a printed order ready when it comes out of the kitchen. Chef-assigned orders are
    marked ready by their chef (strict queue), not here."""
    if not ({"order.serve", "kitchen.manage"} & ctx.principal.permissions):
        raise forbidden("permission_denied", "Missing permission order.serve")
    o = await ctx.db.get(Order, order_id, with_for_update=True)
    if not o:
        raise not_found("Order")
    active = (
        await ctx.db.execute(
            select(Assignment.id).where(Assignment.order_id == o.id, Assignment.status.in_(ACTIVE_ASSIGNMENT))
        )
    ).first()
    if active:
        raise conflict("chef_assigned", "A chef is cooking this order; they mark it ready")
    if o.status != "PREPARING":
        raise conflict("bad_transition", f"Order is {o.status}")
    await finish_ready(ctx.db, o, Actor("USER", ctx.user_id), "paper")
    await emit_order(ctx.db, ctx.emit, o, "order.ready")
    return await order_json(ctx.db, o)
