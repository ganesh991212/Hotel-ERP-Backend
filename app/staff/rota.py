"""Shift rota (docs/03 "Shift rota", docs/14): planned shifts, matched with the person's own clock-ins.

The server decides each shift's state; the app only shows it. An end time before the start runs past midnight.
"""

from datetime import date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, conflict, not_found
from app.core.security import now
from app.models import Attendance, Hotel, ShiftPlan, User

router = APIRouter(tags=["attendance"])

GRACE = timedelta(minutes=10)  # later than this after the start is "Late"
EARLY = timedelta(hours=2)  # a clock-in this long before the start still counts for the shift
MAX_DAYS = 31


def shift_state(starts_at: datetime, ends_at: datetime, clock_ins: list[tuple[datetime, bool]], at: datetime) -> dict:
    """Pure. `clock_ins` = (clock-in time, still open) for this person, oldest first.
    UPCOMING | WORKING | ON_TIME | LATE | NOT_IN (late and not here yet) | NO_SHOW."""
    match = next(((t, open_) for t, open_ in clock_ins if starts_at - EARLY <= t <= ends_at), None)
    if match:
        late = int((match[0] - starts_at).total_seconds() // 60)
        on_time = match[0] <= starts_at + GRACE
        return {
            "state": "WORKING" if match[1] and on_time else ("ON_TIME" if on_time else "LATE"),
            "late_minutes": 0 if on_time else late,
            "clocked_in_at": match[0].isoformat(),
            "working": match[1],
        }
    if at <= starts_at + GRACE:
        return {"state": "UPCOMING", "late_minutes": 0, "clocked_in_at": None, "working": False}
    if at < ends_at:
        late = int((at - starts_at).total_seconds() // 60)
        return {"state": "NOT_IN", "late_minutes": late, "clocked_in_at": None, "working": False}
    return {"state": "NO_SHOW", "late_minutes": 0, "clocked_in_at": None, "working": False}


async def _tz(ctx: Ctx) -> ZoneInfo:
    return ZoneInfo((await ctx.db.execute(select(Hotel.timezone))).scalar_one())


def _span(day: date, start: time, end: time, zone: ZoneInfo) -> tuple[datetime, datetime]:
    s = datetime.combine(day, start, zone)
    e = datetime.combine(day, end, zone)
    if e <= s:  # 18:00 - 02:00 runs past midnight
        e = datetime.combine(day + timedelta(days=1), end, zone)
    return s, e


async def _overlap(ctx: Ctx, user_id: UUID, s: datetime, e: datetime) -> ShiftPlan | None:
    q = select(ShiftPlan).where(ShiftPlan.user_id == user_id, ShiftPlan.starts_at < e, ShiftPlan.ends_at > s)
    return (await ctx.db.execute(q.limit(1))).scalar_one_or_none()


async def _rows(ctx: Ctx, start: date, end: date, user_id: UUID | None = None) -> list[dict]:
    zone = await _tz(ctx)
    q = select(ShiftPlan, User.name).join(User, User.id == ShiftPlan.user_id)
    q = q.where(ShiftPlan.day >= start, ShiftPlan.day <= end)
    if user_id:
        q = q.where(ShiftPlan.user_id == user_id)
    plans = (await ctx.db.execute(q.order_by(ShiftPlan.starts_at))).all()
    if not plans:
        return []
    lo = min(p.starts_at for p, _ in plans) - EARLY
    hi = max(p.ends_at for p, _ in plans)
    clocks: dict[UUID, list[tuple[datetime, bool]]] = {}
    for a in (
        await ctx.db.execute(
            select(Attendance)
            .where(and_(Attendance.clock_in_at >= lo, Attendance.clock_in_at <= hi))
            .order_by(Attendance.clock_in_at)
        )
    ).scalars():
        clocks.setdefault(a.user_id, []).append((a.clock_in_at, a.clock_out_at is None))
    at = now()
    return [
        {
            "id": str(p.id),
            "user": {"id": str(p.user_id), "name": name},
            "day": p.day.isoformat(),
            "starts_at": p.starts_at.isoformat(),
            "ends_at": p.ends_at.isoformat(),
            "start": p.starts_at.astimezone(zone).strftime("%H:%M"),
            "end": p.ends_at.astimezone(zone).strftime("%H:%M"),
            "overnight": p.ends_at.astimezone(zone).date() != p.day,
            "note": p.note,
            **shift_state(p.starts_at, p.ends_at, clocks.get(p.user_id, []), at),
        }
        for p, name in plans
    ]


async def _today(ctx: Ctx) -> date:
    return now().astimezone(await _tz(ctx)).date()


@router.get("/rota")
async def rota(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    ctx: Ctx = HotelCtx("attendance.manage"),
) -> dict:
    """Planned shifts by day (default this week from today), each with its state against the clock-ins."""
    today = await _today(ctx)
    start = date_from or today
    end = date_to or start + timedelta(days=6)
    if end < start:
        raise bad_request("bad_range", "The start is after the end")
    if (end - start).days >= MAX_DAYS:
        raise bad_request("range_too_long", f"Pick at most {MAX_DAYS} days", {"max_days": MAX_DAYS})
    return {"from": start.isoformat(), "to": end.isoformat(), "shifts": await _rows(ctx, start, end)}


@router.get("/rota/me")
async def my_rota(ctx: Ctx = HotelCtx()) -> dict:
    """My shifts from today for a week (shown on My work)."""
    today = await _today(ctx)
    return {"shifts": await _rows(ctx, today, today + timedelta(days=6), ctx.user_id)}


class PlanIn(BaseModel):
    user_id: UUID
    day: date
    start: time
    end: time
    note: str | None = Field(default=None, max_length=80)


@router.post("/rota", status_code=201)
async def plan(body: PlanIn, ctx: Ctx = HotelCtx("attendance.manage")) -> dict:
    if not await ctx.db.get(User, body.user_id):
        raise not_found("Person")
    if body.start == body.end:
        raise bad_request("bad_times", "The shift needs a length")
    s, e = _span(body.day, body.start, body.end, await _tz(ctx))
    if e - s > timedelta(hours=16):
        raise bad_request("bad_times", "A shift can be at most 16 hours")
    other = await _overlap(ctx, body.user_id, s, e)
    if other:
        raise conflict(
            "shift_overlap",
            "This person already has a shift at that time",
            {"starts_at": other.starts_at.isoformat(), "ends_at": other.ends_at.isoformat()},
        )
    p = ShiftPlan(
        hotel_id=ctx.hotel_id,
        user_id=body.user_id,
        day=body.day,
        starts_at=s,
        ends_at=e,
        note=(body.note or "").strip() or None,
        created_by=ctx.user_id,
    )
    ctx.db.add(p)
    await ctx.db.flush()
    await ctx.audit("rota.plan", "shift_plan", p.id, after={"user": str(body.user_id), "starts_at": s.isoformat()})
    return next(r for r in await _rows(ctx, body.day, body.day, body.user_id) if r["id"] == str(p.id))


@router.delete("/rota/{plan_id}", status_code=204)
async def remove(plan_id: UUID, ctx: Ctx = HotelCtx("attendance.manage")) -> None:
    p = await ctx.db.get(ShiftPlan, plan_id)
    if not p:
        raise not_found("Shift")
    await ctx.audit(
        "rota.remove", "shift_plan", p.id, before={"user": str(p.user_id), "starts_at": p.starts_at.isoformat()}
    )
    await ctx.db.delete(p)


class CopyIn(BaseModel):
    week_start: date  # first day of the week to copy FROM; it goes to the 7 days after


@router.post("/rota/copy-week")
async def copy_week(body: CopyIn, ctx: Ctx = HotelCtx("attendance.manage")) -> dict:
    """Copy a week's shifts one week later, same times; anything that would overlap is skipped and counted."""
    zone = await _tz(ctx)
    src = (
        (
            await ctx.db.execute(
                select(ShiftPlan).where(
                    ShiftPlan.day >= body.week_start, ShiftPlan.day < body.week_start + timedelta(days=7)
                )
            )
        )
        .scalars()
        .all()
    )
    copied = skipped = 0
    for p in src:
        day = p.day + timedelta(days=7)
        s, e = _span(day, p.starts_at.astimezone(zone).time(), p.ends_at.astimezone(zone).time(), zone)
        if await _overlap(ctx, p.user_id, s, e):
            skipped += 1
            continue
        ctx.db.add(
            ShiftPlan(
                hotel_id=ctx.hotel_id,
                user_id=p.user_id,
                day=day,
                starts_at=s,
                ends_at=e,
                note=p.note,
                created_by=ctx.user_id,
            )
        )
        await ctx.db.flush()
        copied += 1
    await ctx.audit("rota.copy_week", "shift_plan", None, after={"from": body.week_start.isoformat(), "copied": copied})
    return {"copied": copied, "skipped": skipped}
