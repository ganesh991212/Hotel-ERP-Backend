from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import forbidden
from app.floor.router import current_outlet
from app.kitchen import service
from app.models import ACTIVE_ASSIGNMENT, Assignment, ChefProfile, Order, User
from app.ordering.service import Actor, order_json

router = APIRouter(tags=["kitchen"])


@router.get("/kitchen/board")
async def board(ctx: Ctx = HotelCtx("order.view")) -> dict:
    """Everything a chef screen needs in one call; WebSocket events keep it fresh (5 s poll fallback)."""
    outlet = await current_outlet(ctx)
    db = ctx.db
    waiting = (
        (await db.execute(select(Order).where(Order.status.in_(("PLACED", "CONFIRMED"))).order_by(Order.created_at)))
        .scalars()
        .all()
    )
    ready = (await db.execute(select(Order).where(Order.status == "READY").order_by(Order.ready_at))).scalars().all()
    chefs = (
        await db.execute(
            select(ChefProfile, User)
            .join(User, User.id == ChefProfile.user_id)
            .where(User.status == "ACTIVE")
            .order_by(User.name)
        )
    ).all()
    chef_rows = []
    for prof, user in chefs:
        queue = []
        for a in await service.active_queue(db, user.id):
            o = await db.get(Order, a.order_id)
            queue.append(await order_json(db, o))
        chef_rows.append(
            {
                "id": str(user.id),
                "name": user.name,
                "on_shift": prof.on_shift,
                "is_me": user.id == ctx.user_id,
                "queue": queue,
            }
        )
    return {
        "kitchen_mode": outlet.kitchen_mode,
        "max_queue_orders": outlet.max_queue_orders,
        "awaiting_confirmation": [await order_json(db, o) for o in waiting if o.status == "PLACED"],
        "up_for_grabs": [await order_json(db, o) for o in waiting if o.status == "CONFIRMED"],
        # Handed to the kitchen as printed KOTs; staff mark these ready (paper path).
        "on_paper": [
            await order_json(db, o)
            for o in (
                await db.execute(
                    select(Order).where(Order.on_paper, Order.status == "PREPARING").order_by(Order.started_at)
                )
            ).scalars()
        ],
        "chefs": chef_rows,
        "ready": [await order_json(db, o) for o in ready],
    }


@router.post("/orders/{order_id}/accept")
async def accept(order_id: UUID, ctx: Ctx = HotelCtx("kitchen.cook", feature="CHEF_ACCEPT_MODE")) -> dict:
    o = await service.accept(ctx.db, order_id, ctx.user_id, ctx.emit)
    return await order_json(ctx.db, o)


@router.post("/orders/{order_id}/start")
async def start(order_id: UUID, ctx: Ctx = HotelCtx("kitchen.cook")) -> dict:
    o = await service.start(ctx.db, order_id, ctx.user_id, ctx.emit)
    return await order_json(ctx.db, o)


@router.post("/orders/{order_id}/ready")
async def ready(order_id: UUID, ctx: Ctx = HotelCtx("kitchen.cook")) -> dict:
    o = await service.ready(ctx.db, order_id, ctx.user_id, ctx.emit)
    return await order_json(ctx.db, o)


class ReassignIn(BaseModel):
    chef_id: UUID | None = None  # None = back to the shared board
    reason: str = Field(min_length=3, max_length=300)


@router.post("/orders/{order_id}/reassign")
async def reassign(order_id: UUID, body: ReassignIn, ctx: Ctx = HotelCtx("kitchen.manage")) -> dict:
    o = await service.release(ctx.db, order_id, Actor("USER", ctx.user_id), body.reason, ctx.emit, body.chef_id)
    await ctx.audit("kitchen.reassign", "order", o.id, after={"chef_id": str(body.chef_id)}, reason=body.reason)
    return await order_json(ctx.db, o)


class ShiftIn(BaseModel):
    on_shift: bool


@router.put("/kitchen/chefs/{chef_id}/shift")
async def set_shift(chef_id: UUID, body: ShiftIn, ctx: Ctx = HotelCtx("order.view")) -> dict:
    if chef_id != ctx.user_id and "kitchen.manage" not in ctx.principal.permissions:
        raise forbidden("permission_denied", "Only managers can change another chef's shift")
    prof = await service.set_shift(ctx.db, chef_id, body.on_shift, Actor("USER", ctx.user_id), ctx.emit)
    await ctx.audit("kitchen.shift", "chef", chef_id, after={"on_shift": prof.on_shift})
    return {"chef_id": str(chef_id), "on_shift": prof.on_shift}


@router.get("/kitchen/history")
async def my_history(ctx: Ctx = HotelCtx("kitchen.cook")) -> list[dict]:
    rows = (
        await ctx.db.execute(
            select(Assignment)
            .where(Assignment.chef_user_id == ctx.user_id, Assignment.status.not_in(ACTIVE_ASSIGNMENT))
            .order_by(Assignment.completed_at.desc().nulls_last())
            .limit(30)
        )
    ).scalars()
    return [await order_json(ctx.db, await ctx.db.get(Order, a.order_id)) for a in rows]
