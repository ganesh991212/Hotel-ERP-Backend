"""Day close / cash drawer (docs/03 "Day close", docs/14). Every figure is computed here from what was recorded on
every device (payments and cash expenses); the counter only types what is in the drawer."""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import Date, cast, func, select, text

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, conflict, not_found
from app.core.security import now
from app.models import Bill, BillPayment, CashHandover, DayClose, Expense, Hotel, User
from app.ordering.held import held_count

router = APIRouter(tags=["day close"])


async def _tz_today(ctx: Ctx) -> tuple[str, date]:
    tz = (await ctx.db.execute(select(Hotel.timezone))).scalar_one()
    today = (await ctx.db.execute(select(cast(func.timezone(tz, func.now()), Date)))).scalar_one()
    return tz, today


async def _figures(ctx: Ctx, tz: str, day: date) -> dict:
    """The drawer as the server sees it for one hotel day."""
    pay_day = cast(func.timezone(tz, BillPayment.created_at), Date)
    by_method = dict(
        (
            await ctx.db.execute(
                select(BillPayment.method, func.coalesce(func.sum(BillPayment.amount_minor), 0))
                .where(pay_day == day)
                .group_by(BillPayment.method)
            )
        ).all()
    )
    paid_out = (
        await ctx.db.execute(
            select(func.coalesce(func.sum(Expense.amount_minor), 0)).where(
                Expense.spent_on == day, Expense.payment_method == "CASH"
            )
        )
    ).scalar_one()
    previous = (
        await ctx.db.execute(
            select(DayClose).where(DayClose.business_date < day).order_by(DayClose.business_date.desc()).limit(1)
        )
    ).scalar_one_or_none()
    bill_day = cast(func.timezone(tz, Bill.created_at), Date)
    unpaid_n, unpaid_v = (
        await ctx.db.execute(
            select(func.count(), func.coalesce(func.sum(Bill.total_minor), 0)).where(
                bill_day == day, Bill.status == "ISSUED"
            )
        )
    ).one()
    return {
        "cash_sales_minor": int(by_method.get("CASH", 0)),
        "upi_minor": int(by_method.get("UPI", 0)),
        "card_minor": int(by_method.get("CARD", 0)),
        "cash_paid_out_minor": int(paid_out),
        # Carried from the previous close's cash kept in the drawer; None on the very first close.
        "carried_float_minor": previous.float_kept_minor if previous else None,
        "unpaid_bills": {"count": int(unpaid_n), "total_minor": int(unpaid_v)},
        # Carts put on hold and never sent: a guest may still be waiting (docs/14 "Hold / resume").
        "held_orders": await held_count(ctx),
    }


async def _close_json(ctx: Ctx, tz: str, c: DayClose) -> dict:
    """A saved close, plus cash taken that day after it was closed (shown, never folded in)."""
    pay_day = cast(func.timezone(tz, BillPayment.created_at), Date)
    after = (
        await ctx.db.execute(
            select(func.coalesce(func.sum(BillPayment.amount_minor), 0)).where(
                pay_day == c.business_date, BillPayment.method == "CASH", BillPayment.created_at > c.closed_at
            )
        )
    ).scalar_one()
    who = (await ctx.db.get(User, c.closed_by)) if c.closed_by else None
    return {
        "date": c.business_date.isoformat(),
        "opening_float_minor": c.opening_float_minor,
        "cash_sales_minor": c.cash_sales_minor,
        "cash_paid_out_minor": c.cash_paid_out_minor,
        "expected_minor": c.expected_minor,
        "counted_minor": c.counted_minor,
        "difference_minor": c.difference_minor,
        "result": "OVER" if c.difference_minor > 0 else "SHORT" if c.difference_minor < 0 else "EXACT",
        "float_kept_minor": c.float_kept_minor,
        "deposit_minor": c.deposit_minor,
        "upi_minor": c.upi_minor,
        "card_minor": c.card_minor,
        "note": c.note,
        "closed_by": who.name if who else None,
        "closed_at": c.closed_at.isoformat(),
        "cash_after_close_minor": int(after),
    }


async def _day(ctx: Ctx, day: date | None) -> tuple[str, date]:
    tz, today = await _tz_today(ctx)
    day = day or today
    if day > today:
        raise bad_request("future_day", "That day hasn't happened yet")
    return tz, day


@router.get("/day-close")
async def preview(
    day: date | None = Query(None, alias="date"),
    counted_minor: int | None = Query(None, ge=0, le=10**12),
    float_kept_minor: int = Query(0, ge=0, le=10**12),
    opening_float_minor: int | None = Query(None, ge=0, le=10**12),
    ctx: Ctx = HotelCtx("dayclose.manage"),
) -> dict:
    """What the drawer should hold now for that hotel day (default today), or the saved close if already done.
    With `counted_minor` (as the cashier types), also the would-be difference and deposit, so the app shows
    Over / Short without doing money arithmetic itself (rule 1)."""
    tz, day = await _day(ctx, day)
    saved = (await ctx.db.execute(select(DayClose).where(DayClose.business_date == day))).scalar_one_or_none()
    f = await _figures(ctx, tz, day)
    if f["carried_float_minor"] is not None:
        opening = f["carried_float_minor"]
    else:
        opening = opening_float_minor or 0
    expected = opening + f["cash_sales_minor"] - f["cash_paid_out_minor"]
    check = None
    if counted_minor is not None:
        diff = counted_minor - expected
        check = {
            "difference_minor": diff,
            "result": "OVER" if diff > 0 else "SHORT" if diff < 0 else "EXACT",
            "deposit_minor": counted_minor - float_kept_minor,
        }
    return {
        "date": day.isoformat(),
        "timezone": tz,
        **f,
        "opening_float_minor": opening,
        "expected_minor": expected,
        "check": check,
        "closed": await _close_json(ctx, tz, saved) if saved else None,
        # The day's shift changes, each with its own over / short (docs/14 "Cash handover").
        "handovers": await handovers_on(ctx, day),
    }


class CloseIn(BaseModel):
    day: date | None = Field(default=None, alias="date")  # the hotel day; default today
    counted_minor: int = Field(ge=0, le=10**12)
    float_kept_minor: int = Field(default=0, ge=0, le=10**12)
    # Only for the very first close; afterwards the float is what the previous close kept in the drawer.
    opening_float_minor: int | None = Field(default=None, ge=0, le=10**12)
    note: str | None = Field(default=None, max_length=200)


@router.post("/day-close", status_code=201)
async def close(body: CloseIn, ctx: Ctx = HotelCtx("dayclose.manage")) -> dict:
    tz, day = await _day(ctx, body.day)
    if (await ctx.db.execute(select(DayClose.id).where(DayClose.business_date == day))).first():
        raise conflict("already_closed", "This day is already closed")
    if body.float_kept_minor > body.counted_minor:
        raise bad_request("bad_float", "You can't keep more than you counted")
    f = await _figures(ctx, tz, day)
    if f["carried_float_minor"] is not None:
        opening = f["carried_float_minor"]  # can't be edited to hide a gap
    else:
        opening = body.opening_float_minor or 0
    expected = opening + f["cash_sales_minor"] - f["cash_paid_out_minor"]
    difference = body.counted_minor - expected
    note = (body.note or "").strip() or None
    if difference and (note is None or len(note) < 3):
        raise bad_request("note_required", "Say why the cash is over or short", {"difference_minor": difference})
    c = DayClose(
        hotel_id=ctx.hotel_id,
        business_date=day,
        opening_float_minor=opening,
        cash_sales_minor=f["cash_sales_minor"],
        cash_paid_out_minor=f["cash_paid_out_minor"],
        expected_minor=expected,
        counted_minor=body.counted_minor,
        difference_minor=difference,
        float_kept_minor=body.float_kept_minor,
        deposit_minor=body.counted_minor - body.float_kept_minor,
        upi_minor=f["upi_minor"],
        card_minor=f["card_minor"],
        note=note,
        closed_by=ctx.user_id,
    )
    ctx.db.add(c)
    await ctx.db.flush()
    await ctx.db.refresh(c)
    out = await _close_json(ctx, tz, c)
    await ctx.audit(
        "day.close",
        "day_close",
        c.id,
        after={k: out[k] for k in ("date", "expected_minor", "counted_minor", "difference_minor", "note")},
    )
    return out


@router.get("/day-closes")
async def history(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    ctx: Ctx = HotelCtx("dayclose.manage"),
) -> dict:
    """Closes in a period (default the last 30 days), newest first, with the running over/short total and how many
    days were short: small nightly shortfalls are a pattern worth seeing (docs/14)."""
    tz, today = await _tz_today(ctx)
    end = date_to or today
    start = date_from or end - timedelta(days=29)
    if end < start or (end - start).days > 366:
        raise bad_request("bad_range", "Pick up to a year")
    rows = (
        (
            await ctx.db.execute(
                select(DayClose)
                .where(DayClose.business_date >= start, DayClose.business_date <= end)
                .order_by(DayClose.business_date.desc())
            )
        )
        .scalars()
        .all()
    )
    closes = [await _close_json(ctx, tz, c) for c in rows]
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "closes": closes,
        "net_difference_minor": sum(c["difference_minor"] for c in closes),
        "short_days": sum(1 for c in closes if c["result"] == "SHORT"),
        "over_days": sum(1 for c in closes if c["result"] == "OVER"),
    }


# ---------- Cash handover between shifts (docs/03 "Cash handover", docs/14) ----------


async def _segment(ctx: Ctx, tz: str, day: date) -> dict:
    """The current shift's window: since the last handover today, else the start of the hotel day."""
    last = (
        await ctx.db.execute(
            select(CashHandover).where(CashHandover.business_date == day).order_by(CashHandover.at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    previous_close = (
        await ctx.db.execute(
            select(DayClose).where(DayClose.business_date < day).order_by(DayClose.business_date.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if last is not None:
        opening, since = last.counted_minor, last.at  # the next shift starts from what was counted
    else:
        opening = previous_close.float_kept_minor if previous_close else None
        since = None
    return {"opening": opening, "since": since}


async def _segment_money(ctx: Ctx, tz: str, day: date, since: datetime | None, until: datetime) -> dict:
    pay_day = cast(func.timezone(tz, BillPayment.created_at), Date)
    q = select(BillPayment.method, func.coalesce(func.sum(BillPayment.amount_minor), 0)).where(
        pay_day == day, BillPayment.created_at <= until
    )
    if since is not None:
        q = q.where(BillPayment.created_at > since)
    by_method = dict((await ctx.db.execute(q.group_by(BillPayment.method))).all())
    e = select(func.coalesce(func.sum(Expense.amount_minor), 0)).where(
        Expense.spent_on == day, Expense.payment_method == "CASH", Expense.created_at <= until
    )
    if since is not None:
        e = e.where(Expense.created_at > since)
    return {
        "cash_in": int(by_method.get("CASH", 0)),
        "upi": int(by_method.get("UPI", 0)),
        "card": int(by_method.get("CARD", 0)),
        "paid_out": int((await ctx.db.execute(e)).scalar_one()),
    }


async def _handover_json(ctx: Ctx, h: CashHandover) -> dict:
    names = dict((await ctx.db.execute(select(User.id, User.name).where(User.id.in_([h.handed_by, h.taken_by])))).all())
    return {
        "id": str(h.id),
        "date": h.business_date.isoformat(),
        "since": h.since_at.isoformat() if h.since_at else None,
        "at": h.at.isoformat(),
        "handed_by": names.get(h.handed_by),
        "taken_by": names.get(h.taken_by),
        "opening_minor": h.opening_minor,
        "cash_in_minor": h.cash_in_minor,
        "paid_out_minor": h.paid_out_minor,
        "expected_minor": h.expected_minor,
        "counted_minor": h.counted_minor,
        "difference_minor": h.difference_minor,
        "result": "OVER" if h.difference_minor > 0 else "SHORT" if h.difference_minor < 0 else "EXACT",
        "upi_minor": h.upi_minor,
        "card_minor": h.card_minor,
        "note": h.note,
    }


async def handovers_on(ctx: Ctx, day: date) -> list[dict]:
    rows = (
        await ctx.db.execute(select(CashHandover).where(CashHandover.business_date == day).order_by(CashHandover.at))
    ).scalars()
    return [await _handover_json(ctx, h) for h in rows]


@router.get("/handover")
async def handover_start(ctx: Ctx = HotelCtx("dayclose.manage")) -> dict:
    """What the handover screen needs, **without** the expected cash (a blind count: it is shown after counting)."""
    tz, day = await _tz_today(ctx)
    seg = await _segment(ctx, tz, day)
    closed = (await ctx.db.execute(select(DayClose.id).where(DayClose.business_date == day))).first() is not None
    return {
        "date": day.isoformat(),
        "since": seg["since"].isoformat() if seg["since"] else None,
        "needs_opening_float": seg["opening"] is None,  # the very first count ever: type the starting float
        "day_closed": closed,
        "today": await handovers_on(ctx, day),
    }


class HandoverIn(BaseModel):
    counted_minor: int = Field(ge=0, le=10**12)
    taken_by: UUID  # the cashier taking over (named on the record)
    opening_float_minor: int | None = Field(default=None, ge=0, le=10**12)


@router.post("/handover", status_code=201)
async def handover(body: HandoverIn, ctx: Ctx = HotelCtx("dayclose.manage")) -> dict:
    """Save the outgoing cashier's blind count, then return expected and over / short. The count is final."""
    tz, day = await _tz_today(ctx)
    if (await ctx.db.execute(select(DayClose.id).where(DayClose.business_date == day))).first():
        raise conflict("already_closed", "This day is already closed")
    if body.taken_by == ctx.user_id:
        raise bad_request("same_person", "Hand over to someone else")
    if not await ctx.db.get(User, body.taken_by):
        raise not_found("Person")
    # One handover at a time per hotel, so two windows cannot overlap.
    await ctx.db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"drawer:{ctx.hotel_id}"})
    seg = await _segment(ctx, tz, day)
    opening = seg["opening"] if seg["opening"] is not None else (body.opening_float_minor or 0)
    at = now()
    m = await _segment_money(ctx, tz, day, seg["since"], at)
    expected = opening + m["cash_in"] - m["paid_out"]
    h = CashHandover(
        hotel_id=ctx.hotel_id,
        business_date=day,
        since_at=seg["since"],
        at=at,
        opening_minor=opening,
        cash_in_minor=m["cash_in"],
        paid_out_minor=m["paid_out"],
        expected_minor=expected,
        counted_minor=body.counted_minor,
        difference_minor=body.counted_minor - expected,
        upi_minor=m["upi"],
        card_minor=m["card"],
        handed_by=ctx.user_id,
        taken_by=body.taken_by,
    )
    ctx.db.add(h)
    await ctx.db.flush()
    out = await _handover_json(ctx, h)
    await ctx.audit(
        "cash.handover",
        "cash_handover",
        h.id,
        after={k: out[k] for k in ("expected_minor", "counted_minor", "difference_minor", "taken_by")},
    )
    return out


class NoteIn(BaseModel):
    note: str = Field(min_length=3, max_length=200)


@router.post("/handover/{handover_id}/note")
async def handover_note(handover_id: UUID, body: NoteIn, ctx: Ctx = HotelCtx("dayclose.manage")) -> dict:
    """Why the cash was over or short: added once, after the count (the count itself never changes)."""
    h = await ctx.db.get(CashHandover, handover_id, with_for_update=True)
    if not h:
        raise not_found("Handover")
    if h.note:
        raise conflict("note_exists", "This handover already has a note")
    h.note = body.note.strip()
    await ctx.audit("cash.handover_note", "cash_handover", h.id, after={"note": h.note})
    return await _handover_json(ctx, h)
