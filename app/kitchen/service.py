"""Kitchen queue operations. All mutations run under the per-outlet lock (rule 5).

Assignment *decisions* come from the pure routing module; this file only loads snapshots and applies them.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import conflict, forbidden, not_found
from app.core.security import now
from app.kitchen import routing
from app.models import ACTIVE_ASSIGNMENT, Assignment, ChefProfile, Order, Outlet, User
from app.ordering.service import Actor, emit_order, lock_outlet, mark_ready, transition


async def active_queue(db: AsyncSession, chef_id: UUID) -> list[Assignment]:
    return list(
        (
            await db.execute(
                select(Assignment)
                .where(Assignment.chef_user_id == chef_id, Assignment.status.in_(ACTIVE_ASSIGNMENT))
                .order_by(Assignment.queue_position)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def _close_up(db: AsyncSession, chef_id: UUID) -> None:
    """Renumber a chef's active queue 1..n after a removal (example 12)."""
    for pos, a in enumerate(await active_queue(db, chef_id), start=1):
        a.queue_position = pos


def _remaining(o: Order, a: Assignment, at: datetime) -> int:
    if a.status == "PREPARING" and a.started_at:
        elapsed = int((at - a.started_at).total_seconds() // 60)
        return max(0, o.est_prep_minutes - elapsed)
    return o.est_prep_minutes


async def snapshot(db: AsyncSession, outlet: Outlet) -> routing.Snapshot:
    at = now()
    chefs = (
        await db.execute(
            select(ChefProfile, User)
            .join(User, User.id == ChefProfile.user_id)
            .where(ChefProfile.outlet_id == outlet.id)
        )
    ).all()
    out = []
    for prof, user in chefs:
        rows = (
            await db.execute(
                select(Assignment, Order)
                .join(Order, Order.id == Assignment.order_id)
                .where(Assignment.chef_user_id == user.id, Assignment.status.in_(ACTIVE_ASSIGNMENT))
                .order_by(Assignment.queue_position)
            )
        ).all()
        out.append(
            routing.ChefState(
                chef_id=str(user.id),
                on_shift=prof.on_shift,
                active=user.status == "ACTIVE",
                queue_minutes=tuple(_remaining(o, a, at) for a, o in rows),
                last_completed_at=prof.last_completed_at,
            )
        )
    return routing.Snapshot(out)


async def _append(db: AsyncSession, o: Order, chef_id: UUID, actor: Actor, reason: str | None = None) -> Assignment:
    queue = await active_queue(db, chef_id)
    a = Assignment(
        hotel_id=o.hotel_id,
        order_id=o.id,
        chef_user_id=chef_id,
        queue_position=len(queue) + 1,
        status="QUEUED",
        reason=reason,
    )
    db.add(a)
    await transition(db, o, "ASSIGNED", actor, reason)
    await db.flush()
    return a


async def route_pending(db: AsyncSession, outlet: Outlet, emit) -> None:
    """AUTO_ASSIGN: place every unassigned CONFIRMED order, oldest first. Caller holds the lock."""
    if outlet.kitchen_mode != "AUTO_ASSIGN":
        return
    pending = (
        (
            await db.execute(
                select(Order)
                .where(Order.outlet_id == outlet.id, Order.status == "CONFIRMED")
                .order_by(Order.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        return
    snap = await snapshot(db, outlet)
    cfg = routing.RoutingConfig(outlet.max_queue_orders, outlet.rating_band_minutes)
    for o in pending:
        chef = routing.assign(snap, cfg)
        if chef is None:
            break  # waits unassigned; re-run on READY / shift / cancel (docs/04 step 6)
        await _append(db, o, UUID(chef), Actor("SYSTEM"), "auto")
        snap = routing.with_order(snap, chef, o.est_prep_minutes)
        await emit_order(db, emit, o, "order.assigned")


async def _get_order(db: AsyncSession, order_id: UUID, lock: bool = True) -> Order:
    q = select(Order).where(Order.id == order_id)
    if lock:
        # populate_existing: after taking the lock, overwrite any stale copy already in the session,
        # otherwise a concurrent claim committed meanwhile would be invisible (example 8).
        q = q.with_for_update().execution_options(populate_existing=True)
    o = (await db.execute(q)).scalar_one_or_none()
    if not o:
        raise not_found("Order")
    return o


async def _outlet(db: AsyncSession, o: Order) -> Outlet:
    return await db.get(Outlet, o.outlet_id)


async def accept(db: AsyncSession, order_id: UUID, chef_id: UUID, emit) -> Order:
    """CHEF_ACCEPT atomic claim (example 8). If it lands at the head, cooking starts immediately."""
    o0 = await _get_order(db, order_id, lock=False)
    outlet = await _outlet(db, o0)
    if outlet.kitchen_mode != "CHEF_ACCEPT":
        raise conflict("wrong_mode", "This kitchen assigns orders automatically")
    prof = await db.get(ChefProfile, chef_id)
    if not prof:
        raise forbidden("not_a_chef", "Only chefs can pick up orders")
    if not prof.on_shift:
        raise conflict("off_shift", "Go on shift to pick up orders")
    await lock_outlet(db, outlet.id)
    o = await _get_order(db, order_id)
    if o.status != "CONFIRMED":
        raise conflict("taken", "Another chef already picked this order", {"status": o.status})
    if len(await active_queue(db, chef_id)) >= outlet.max_queue_orders:
        raise conflict("queue_full", "Finish an order before picking another")
    a = await _append(db, o, chef_id, Actor("USER", chef_id), "accepted")
    if a.queue_position == 1:
        a.status, a.started_at = "PREPARING", now()
        await transition(db, o, "PREPARING", Actor("USER", chef_id))
    await db.flush()
    await emit_order(db, emit, o, "order.updated")
    return o


async def start(db: AsyncSession, order_id: UUID, chef_id: UUID, emit) -> Order:
    """Strict queue: only the head can start (example 1)."""
    o0 = await _get_order(db, order_id, lock=False)
    await lock_outlet(db, o0.outlet_id)
    o = await _get_order(db, order_id)
    queue = await active_queue(db, chef_id)
    mine = next((a for a in queue if a.order_id == o.id), None)
    if not mine:
        raise forbidden("not_your_order", "This order is not in your queue")
    if queue[0].order_id != o.id:
        raise conflict(
            "not_head", "Finish the order ahead of this one first", {"head_order_id": str(queue[0].order_id)}
        )
    if any(a.status == "PREPARING" for a in queue):
        raise conflict("already_cooking", "You are already preparing an order")
    mine.status, mine.started_at = "PREPARING", now()
    await transition(db, o, "PREPARING", Actor("USER", chef_id))
    await db.flush()
    await emit_order(db, emit, o, "order.updated")
    return o


async def ready(db: AsyncSession, order_id: UUID, chef_id: UUID, emit) -> Order:
    o0 = await _get_order(db, order_id, lock=False)
    outlet = await _outlet(db, o0)
    await lock_outlet(db, outlet.id)
    o = await _get_order(db, order_id)
    a = (
        await db.execute(
            select(Assignment).where(
                Assignment.order_id == o.id, Assignment.chef_user_id == chef_id, Assignment.status == "PREPARING"
            )
        )
    ).scalar_one_or_none()
    if not a:
        raise conflict("not_preparing", "Start this order before marking it ready")
    a.status, a.completed_at = "READY", now()
    prof = await db.get(ChefProfile, chef_id)
    if prof:
        prof.last_completed_at = a.completed_at
    await mark_ready(db, o, Actor("USER", chef_id))
    await db.flush()
    await _close_up(db, chef_id)
    await emit_order(db, emit, o, "order.ready")
    await route_pending(db, outlet, emit)  # freed capacity (example 7)
    return o


async def remove_from_queue(db: AsyncSession, o: Order, status: str, reason: str | None) -> UUID | None:
    a = (
        await db.execute(
            select(Assignment).where(Assignment.order_id == o.id, Assignment.status.in_(ACTIVE_ASSIGNMENT))
        )
    ).scalar_one_or_none()
    if not a:
        return None
    a.status, a.reason, a.completed_at = status, reason, now()
    await db.flush()
    await _close_up(db, a.chef_user_id)
    return a.chef_user_id


async def cancel(db: AsyncSession, order_id: UUID, actor: Actor, reason: str, emit) -> Order:
    o0 = await _get_order(db, order_id, lock=False)
    outlet = await _outlet(db, o0)
    await lock_outlet(db, outlet.id)
    o = await _get_order(db, order_id)
    await transition(db, o, "CANCELLED", actor, reason)
    await remove_from_queue(db, o, "CANCELLED", reason)
    await emit_order(db, emit, o, "order.cancelled")
    await route_pending(db, outlet, emit)
    return o


async def release(
    db: AsyncSession, order_id: UUID, actor: Actor, reason: str, emit, to_chef: UUID | None = None
) -> Order:
    """Admin reassign (kitchen.manage, audited) or return to the shared board."""
    o0 = await _get_order(db, order_id, lock=False)
    outlet = await _outlet(db, o0)
    await lock_outlet(db, outlet.id)
    o = await _get_order(db, order_id)
    if o.status not in ("ASSIGNED", "PREPARING"):
        raise conflict("bad_transition", "Only queued or cooking orders can be reassigned")
    await remove_from_queue(db, o, "REASSIGNED", reason)
    await transition(db, o, "CONFIRMED", actor, reason)
    if to_chef:
        if not await db.get(ChefProfile, to_chef):
            raise not_found("Chef")
        await _append(db, o, to_chef, actor, reason)
    await emit_order(db, emit, o, "order.updated")
    await route_pending(db, outlet, emit)
    return o


async def set_shift(db: AsyncSession, chef_id: UUID, on_shift: bool, actor: Actor, emit) -> ChefProfile:
    """Going off shift returns queued (not yet cooking) orders, in order (example 11)."""
    prof = await db.get(ChefProfile, chef_id)
    if not prof:
        raise not_found("Chef")
    outlet = await db.get(Outlet, prof.outlet_id)
    await lock_outlet(db, outlet.id)
    prof.on_shift = on_shift
    if not on_shift:
        for a in await active_queue(db, chef_id):
            if a.status == "QUEUED":
                o = await _get_order(db, a.order_id)
                a.status, a.reason, a.completed_at = "REASSIGNED", "off_shift", now()
                await transition(db, o, "CONFIRMED", actor, "chef off shift")
                await emit_order(db, emit, o, "order.updated")
        await db.flush()
        await _close_up(db, chef_id)
    await route_pending(db, outlet, emit)
    emit(f"outlet:{outlet.id}", {"type": "chef.shift", "chef_id": str(chef_id), "on_shift": on_shift})
    return prof
