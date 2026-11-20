"""Hotel dashboard aggregates. All money is summed on the server; the client only formats."""

from datetime import timedelta

from fastapi import APIRouter, Query
from sqlalchemy import Date, cast, func, select

from app.core.deps import Ctx, HotelCtx
from app.core.security import now
from app.models import (
    ACTIVE_ASSIGNMENT,
    Assignment,
    Bill,
    BillPayment,
    ChefProfile,
    DiningTable,
    Expense,
    Hotel,
    Order,
    OrderItem,
    User,
)
from app.ordering.service import OPEN_STATES, order_json

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary")
async def summary(ctx: Ctx = HotelCtx("dashboard.view")) -> dict:
    db = ctx.db
    hotel = await db.get(Hotel, ctx.hotel_id)
    tz = hotel.timezone
    local = func.timezone(tz, Order.created_at)
    local_now = (await db.execute(select(func.timezone(tz, func.now())))).scalar_one()
    today = local_now.date()
    is_today = cast(local, Date) == today
    billable = Order.status != "CANCELLED"

    count_today = (await db.execute(select(func.count()).where(is_today, billable))).scalar_one()
    # Revenue = issued, not-cancelled bills on the hotel's day: the same definition as Reports (docs/14), so the
    # Overview and the reports never disagree. Food on no bill yet is shown separately, never as revenue.
    bill_day = cast(func.timezone(tz, Bill.created_at), Date)
    live_bill = Bill.status.in_(("ISSUED", "PAID"))
    bills_today, revenue_today = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Bill.total_minor), 0)).where(bill_day == today, live_bill)
        )
    ).one()
    unbilled_today = (
        await db.execute(
            select(func.coalesce(func.sum(Order.total_minor), 0)).where(is_today, billable, Order.bill_id.is_(None))
        )
    ).scalar_one()
    by_status = dict(
        (await db.execute(select(Order.status, func.count()).where(is_today).group_by(Order.status))).all()
    )
    by_channel = dict(
        (await db.execute(select(Order.channel, func.count()).where(is_today, billable).group_by(Order.channel))).all()
    )
    hourly = dict(
        (
            await db.execute(
                select(func.extract("hour", local), func.count())
                .where(is_today, billable)
                .group_by(func.extract("hour", local))
            )
        ).all()
    )
    avg_prep = (
        await db.execute(
            select(func.avg(func.extract("epoch", Order.ready_at - Order.started_at) / 60)).where(
                is_today, Order.ready_at.is_not(None), Order.started_at.is_not(None)
            )
        )
    ).scalar()

    week_start = today - timedelta(days=6)
    week = dict(
        (
            await db.execute(
                select(bill_day, func.coalesce(func.sum(Bill.total_minor), 0))
                .where(bill_day >= week_start, live_bill)
                .group_by(bill_day)
            )
        ).all()
    )

    top_items = (
        await db.execute(
            select(OrderItem.name, func.sum(OrderItem.qty), func.sum(OrderItem.line_total_minor))
            .join(Order, Order.id == OrderItem.order_id)
            .where(cast(local, Date) >= week_start, billable, OrderItem.status != "CANCELLED")
            .group_by(OrderItem.name)
            .order_by(func.sum(OrderItem.qty).desc())
            .limit(5)
        )
    ).all()

    month_start = today.replace(day=1)
    revenue_month = (
        await db.execute(select(func.coalesce(func.sum(Bill.total_minor), 0)).where(bill_day >= month_start, live_bill))
    ).scalar_one()
    expenses_month = (
        await db.execute(
            select(func.coalesce(func.sum(Expense.amount_minor), 0)).where(Expense.spent_on >= month_start)
        )
    ).scalar_one()

    tables_total = (
        await db.execute(select(func.count()).select_from(DiningTable).where(DiningTable.status == "ACTIVE"))
    ).scalar_one()
    tables_busy = (
        await db.execute(select(func.count(func.distinct(Order.table_id))).where(Order.status.in_(OPEN_STATES)))
    ).scalar_one()

    # Stage timings for orders created today (hotel-local). Average plus p90: the slow tail is what guests remember.
    def minutes(start, end):
        return func.extract("epoch", end - start) / 60

    stages = {
        "wait_for_chef": (Order.confirmed_at, Order.started_at),  # placed/confirmed -> chef starts (or KOT printed)
        "preparation": (Order.started_at, Order.ready_at),  # cooking
        "serving": (Order.ready_at, Order.served_at),  # ready at the pass -> on the table
        "total": (Order.created_at, Order.served_at),  # guest's view: order -> food on table
    }
    # Counter orders (diner collects) have served_at == ready_at: no serving step to measure.
    only = {"serving": Order.served_at > Order.ready_at}
    timings = {}
    for key, (start, end) in stages.items():
        m = minutes(start, end)
        n, avg, p90 = (
            await db.execute(
                select(func.count(), func.avg(m), func.percentile_cont(0.9).within_group(m)).where(
                    is_today, start.is_not(None), end.is_not(None), *([only[key]] if key in only else [])
                )
            )
        ).one()
        timings[key] = {
            "count": int(n),
            "avg_minutes": round(float(avg), 1) if avg is not None else None,
            "p90_minutes": round(float(p90), 1) if p90 is not None else None,
        }

    servers = []
    serve_m = minutes(Order.ready_at, Order.served_at)
    for name, n, avg in (
        await db.execute(
            select(User.name, func.count(), func.avg(serve_m))
            .join(User, User.id == Order.served_by_user_id)
            .where(is_today, Order.ready_at.is_not(None), Order.served_at.is_not(None))
            .group_by(User.name)
            .order_by(func.avg(serve_m))
        )
    ).all():
        servers.append({"name": name, "served": int(n), "avg_serve_minutes": round(float(avg), 1)})

    chefs = []
    for prof, user in (
        await db.execute(
            select(ChefProfile, User)
            .join(User, User.id == ChefProfile.user_id)
            .where(User.status == "ACTIVE")
            .order_by(User.name)
        )
    ).all():
        done = (
            await db.execute(
                select(
                    func.count(), func.avg(func.extract("epoch", Assignment.completed_at - Assignment.started_at) / 60)
                ).where(
                    Assignment.chef_user_id == user.id,
                    Assignment.status == "READY",
                    cast(func.timezone(tz, Assignment.completed_at), Date) == today,
                )
            )
        ).one()
        qlen = (
            await db.execute(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.chef_user_id == user.id, Assignment.status.in_(ACTIVE_ASSIGNMENT))
            )
        ).scalar_one()
        chefs.append(
            {
                "id": str(user.id),
                "name": user.name,
                "on_shift": prof.on_shift,
                "completed_today": done[0],
                "avg_minutes": round(float(done[1]), 1) if done[1] is not None else None,
                "queue": qlen,
            }
        )

    live = (
        (await db.execute(select(Order).where(Order.status.in_(OPEN_STATES)).order_by(Order.created_at).limit(8)))
        .scalars()
        .all()
    )
    return {
        "date": today.isoformat(),
        "now_hour": local_now.hour,  # hotel-local hour; clients must not use the device clock
        "hotel": {"name": hotel.name},
        "orders_today": int(count_today),
        "revenue_today_minor": int(revenue_today),
        "bills_today": int(bills_today),
        "unbilled_today_minor": int(unbilled_today),
        "avg_ticket_minor": int(revenue_today) // bills_today if bills_today else 0,  # per bill, like Reports
        "avg_prep_minutes": round(float(avg_prep), 1) if avg_prep is not None else None,
        "by_status": {k: int(v) for k, v in by_status.items()},
        "by_channel": by_channel,
        "hourly": [{"hour": h, "orders": int(hourly.get(h, 0))} for h in range(24)],
        "week": [
            {
                "date": (week_start + timedelta(days=i)).isoformat(),
                "revenue_minor": int(week.get(week_start + timedelta(days=i), 0)),
            }
            for i in range(7)
        ],
        "top_items": [{"name": n, "qty": int(q), "revenue_minor": int(r)} for n, q, r in top_items],
        "revenue_month_minor": int(revenue_month),
        "expenses_month_minor": int(expenses_month),
        "net_month_minor": int(revenue_month) - int(expenses_month),
        "tables_total": tables_total,
        "tables_busy": tables_busy,
        "chefs": chefs,
        "timings": timings,
        "servers": servers,
        "live_orders": [await order_json(db, o) for o in live],
        "generated_at": now().isoformat(),
    }


# ------------------------------------------------------------------- my work --


def _avg(v) -> float | None:
    return round(float(v), 1) if v is not None else None


@router.get("/dashboard/me")
async def my_work(period: str = Query("today", pattern="^(today|week)$"), ctx: Ctx = HotelCtx()) -> dict:
    """The signed-in person's own work, by what their role does: cooking, serving, billing. Each figure comes
    with the team's figure for context. No other person's name or numbers (staff see themselves; admins see
    everyone on the main dashboard)."""
    db, me = ctx.db, ctx.user_id
    hotel = await db.get(Hotel, ctx.hotel_id)
    tz = hotel.timezone
    today = (await db.execute(select(cast(func.timezone(tz, func.now()), Date)))).scalar_one()
    first = today - timedelta(days=6 if period == "week" else 0)

    def within(ts):  # hotel-local business date of a timestamp falls in the period
        d = cast(func.timezone(tz, ts), Date)
        return d.between(first, today)

    def minutes(start, end):
        return func.extract("epoch", end - start) / 60

    out: dict = {"period": period, "from": first.isoformat(), "to": today.isoformat(), "sections": []}

    if ctx.principal.permissions & {"kitchen.cook"}:
        cooked = (Assignment.status == "READY", within(Assignment.completed_at))
        prep = minutes(Assignment.started_at, Assignment.completed_at)
        n, mine = (
            await db.execute(select(func.count(), func.avg(prep)).where(Assignment.chef_user_id == me, *cooked))
        ).one()
        team = (await db.execute(select(func.avg(prep)).where(*cooked))).scalar_one()
        # How long guests waited before this chef started cooking their order.
        wait = minutes(Order.confirmed_at, Order.started_at)
        my_wait, team_wait = (
            await db.execute(
                select(
                    func.avg(wait).filter(Assignment.chef_user_id == me),
                    func.avg(wait),
                )
                .select_from(Assignment)
                .join(Order, Order.id == Assignment.order_id)
                .where(*cooked, Order.confirmed_at.is_not(None), Order.started_at.is_not(None))
            )
        ).one()
        items = (
            await db.execute(
                select(func.coalesce(func.sum(OrderItem.qty), 0))
                .join(Assignment, Assignment.order_id == OrderItem.order_id)
                .where(Assignment.chef_user_id == me, *cooked, OrderItem.status != "CANCELLED")
            )
        ).scalar_one()
        queue = (
            await db.execute(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.chef_user_id == me, Assignment.status.in_(ACTIVE_ASSIGNMENT))
            )
        ).scalar_one()
        out["sections"].append(
            {
                "kind": "cooking",
                "orders_cooked": int(n),
                "items_cooked": int(items),
                "avg_prep_minutes": _avg(mine),
                "team_avg_prep_minutes": _avg(team),
                "avg_wait_before_start_minutes": _avg(my_wait),
                "team_avg_wait_before_start_minutes": _avg(team_wait),
                "in_queue_now": int(queue),
            }
        )

    if ctx.principal.permissions & {"order.serve", "order.create"}:
        # Counter orders (served_at == ready_at) have no serving step to time.
        served = (Order.status == "SERVED", within(Order.served_at))
        serve = minutes(Order.ready_at, Order.served_at)
        timed = (Order.ready_at.is_not(None), Order.served_at > Order.ready_at)
        n, tables, mine = (
            await db.execute(
                select(
                    func.count(),
                    func.count(func.distinct(Order.table_id)),
                    func.avg(serve).filter(*timed),
                ).where(Order.served_by_user_id == me, *served)
            )
        ).one()
        team = (await db.execute(select(func.avg(serve)).where(*served, *timed))).scalar_one()
        taken, taken_minor = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(Order.total_minor), 0)).where(
                    Order.placed_by_user_id == me, Order.status != "CANCELLED", within(Order.created_at)
                )
            )
        ).one()
        ready_now = (await db.execute(select(func.count()).where(Order.status == "READY"))).scalar_one()
        out["sections"].append(
            {
                "kind": "serving",
                "orders_served": int(n),
                "tables_served": int(tables),
                "avg_serve_minutes": _avg(mine),
                "team_avg_serve_minutes": _avg(team),
                "orders_taken": int(taken),
                "orders_taken_minor": int(taken_minor),
                "ready_to_serve_now": int(ready_now),
            }
        )

    if ctx.principal.permissions & {"bill.manage"}:
        mine_q = (Bill.created_by == me, within(Bill.created_at), Bill.status != "VOID")
        n, total = (
            await db.execute(select(func.count(), func.coalesce(func.sum(Bill.total_minor), 0)).where(*mine_q))
        ).one()
        # Money this person actually took (each part payment counts for whoever received it).
        by_method = {
            m: int(v)
            for m, v in (
                await db.execute(
                    select(BillPayment.method, func.coalesce(func.sum(BillPayment.amount_minor), 0))
                    .join(Bill, Bill.id == BillPayment.bill_id)
                    .where(BillPayment.received_by == me, within(BillPayment.created_at), Bill.status != "VOID")
                    .group_by(BillPayment.method)
                )
            ).all()
        }
        unpaid_n, unpaid = (
            await db.execute(
                select(func.count(), func.coalesce(func.sum(Bill.total_minor), 0)).where(Bill.status == "ISSUED")
            )
        ).one()
        out["sections"].append(
            {
                "kind": "billing",
                "bills_raised": int(n),
                "bills_raised_minor": int(total),
                "collected_minor": sum(by_method.values()),
                "collected_by_method_minor": by_method,
                "unpaid_bills_now": int(unpaid_n),
                "unpaid_now_minor": int(unpaid),
            }
        )
    return out
