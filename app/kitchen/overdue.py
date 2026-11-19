"""No-pickup alert (docs/04 "Order workflow").

A confirmed order still waiting on the chefs' board after the outlet's `pickup_alert_minutes` alerts admins
(`kitchen.manage`) and chefs on shift: a realtime event for open screens and a loud push for phones. Once per
order (`orders.pickup_alerted_at`).

Runs in the API process every POLL_SECONDS. That is safe only because the API runs as one instance (docs/10,
"Scaling limit"); the claim below is also atomic, so a second instance would not double-alert.
"""

import asyncio
import logging
from uuid import UUID

from sqlalchemy import literal_column, select, update

from app.core.db import platform_session, tenant_session
from app.core.security import now
from app.models import Order, Outlet
from app.notify import push
from app.ordering.service import order_json
from app.realtime.hub import outlet_channel
from app.realtime.publish import PUSH, publish_all

POLL_SECONDS = 30
log = logging.getLogger("app.overdue")

# Modes where an order waits for a chef to take it. ADMIN_PRINT orders go to paper, not a chef.
WAITING_MODES = ("CHEF_ACCEPT", "AUTO_ASSIGN")


async def _claim() -> list[tuple[UUID, UUID, int]]:
    """Marks overdue orders as alerted and returns (order_id, hotel_id, minutes). Atomic: an order is claimed by
    exactly one sweep. Cross-hotel scan, so it runs as the platform role; nothing tenant-specific is read here."""
    minute = literal_column("interval '1 minute'")
    async with platform_session() as db:
        due = (
            select(Order.id)
            .join(Outlet, Outlet.id == Order.outlet_id)
            .where(
                Order.status == "CONFIRMED",
                Order.on_paper.is_(False),
                Order.pickup_alerted_at.is_(None),
                Order.confirmed_at.is_not(None),
                Outlet.pickup_alert_minutes.is_not(None),
                Outlet.kitchen_mode.in_(WAITING_MODES),
                Order.confirmed_at <= now() - Outlet.pickup_alert_minutes * minute,
            )
            .with_for_update(of=Order, skip_locked=True)
        )
        ids = [r for (r,) in (await db.execute(due)).all()]
        if not ids:
            return []
        rows = (
            await db.execute(
                update(Order)
                .where(Order.id.in_(ids))
                .values(pickup_alerted_at=now())
                .returning(Order.id, Order.hotel_id, Order.outlet_id)
            )
        ).all()
        outlets = select(Outlet.id, Outlet.pickup_alert_minutes).where(Outlet.id.in_({r[2] for r in rows}))
        mins = dict((await db.execute(outlets)).all())
        return [(oid, hid, mins[out]) for oid, hid, out in rows]


async def _announce(order_id: UUID, hotel_id: UUID, minutes: int) -> list[tuple[str, dict]]:
    """Builds the events inside the hotel's own RLS transaction, so recipients can only be that hotel's staff."""
    events: list[tuple[str, dict]] = []
    async with tenant_session(hotel_id) as db:
        o = await db.get(Order, order_id)
        if o is None or o.status != "CONFIRMED":  # picked up in the meantime
            return []
        data = await order_json(db, o)
        event = {"type": "order.pickup_overdue", "order": data, "minutes": minutes}
        events.append((outlet_channel(o.outlet_id), event))
        admins = await push.targets_for(db, permission="kitchen.manage")
        chefs = await push.targets_for(db, chefs_on_shift=True)
        targets = list(dict.fromkeys(admins + chefs))
        if targets:
            table = (data.get("table") or {}).get("label") or "-"
            events.append(
                (
                    PUSH,
                    {
                        "targets": targets,
                        "title": f"Not picked up: order #{o.number} - Table {table}",
                        "body": f"Waiting more than {minutes} min. Please pick it up or assign a chef.",
                        "sound": push.SOUNDS["new_order"],
                        "data": {"type": "order.pickup_overdue", "order_id": str(o.id), "status": o.status},
                    },
                )
            )
    return events


async def sweep() -> list[tuple[str, dict]]:
    """One pass. Returns the events it published (for tests)."""
    published: list[tuple[str, dict]] = []
    for order_id, hotel_id, minutes in await _claim():
        try:
            events = await _announce(order_id, hotel_id, minutes)
        except Exception:
            log.exception("pickup alert failed for order %s", order_id)
            continue
        publish_all(events)
        published += events
    return published


async def run_forever() -> None:
    while True:
        try:
            await sweep()
        except Exception:
            log.exception("pickup alert sweep failed")
        await asyncio.sleep(POLL_SECONDS)
