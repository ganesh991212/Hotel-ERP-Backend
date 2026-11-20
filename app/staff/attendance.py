"""Attendance: clock in / clock out on your own login (docs/03 "Attendance", docs/14).

No biometrics, photos or location. Missed clock-outs are flagged, never closed by the system. A manager's
correction needs a reason and keeps the original times. Chefs go on / off shift with the clock.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, conflict, not_found
from app.core.security import now
from app.kitchen import service as kitchen
from app.models import Attendance, ChefProfile, Hotel, User
from app.ordering.service import Actor

router = APIRouter(tags=["attendance"])

LONG_SHIFT = timedelta(hours=16)  # still open after this: flagged "No clock-out" for a manager
MAX_DAYS = 62


def _minutes(a: Attendance) -> int | None:
    return int((a.clock_out_at - a.clock_in_at).total_seconds() // 60) if a.clock_out_at else None


def _json(a: Attendance, name: str | None, at: datetime) -> dict:
    return {
        "id": str(a.id),
        "user": {"id": str(a.user_id), "name": name},
        "clock_in_at": a.clock_in_at.isoformat(),
        "clock_out_at": a.clock_out_at.isoformat() if a.clock_out_at else None,
        "minutes": _minutes(a),
        "open": a.clock_out_at is None,
        "missing_clock_out": a.clock_out_at is None and at - a.clock_in_at > LONG_SHIFT,
        "corrected": a.corrected_by is not None,
        "correction_reason": a.correction_reason,
    }


async def _open(ctx: Ctx, user_id: UUID) -> Attendance | None:
    return (
        await ctx.db.execute(
            select(Attendance).where(Attendance.user_id == user_id, Attendance.clock_out_at.is_(None)).with_for_update()
        )
    ).scalar_one_or_none()


async def _chef_shift(ctx: Ctx, on: bool) -> None:
    """Chefs: the clock drives the kitchen's on/off shift switch, so routing follows who is actually here."""
    prof = await ctx.db.get(ChefProfile, ctx.user_id)
    if prof is not None and prof.on_shift != on:
        await kitchen.set_shift(ctx.db, ctx.user_id, on, Actor("USER", ctx.user_id), ctx.emit)


@router.get("/attendance/me")
async def me(ctx: Ctx = HotelCtx()) -> dict:
    a = await _open(ctx, ctx.user_id)
    return {"clocked_in": a is not None, "shift": _json(a, None, now()) if a else None}


@router.post("/attendance/clock-in", status_code=201)
async def clock_in(ctx: Ctx = HotelCtx()) -> dict:
    if await _open(ctx, ctx.user_id):
        raise conflict("already_clocked_in", "You are already clocked in")
    at = now()
    a = Attendance(hotel_id=ctx.hotel_id, user_id=ctx.user_id, clock_in_at=at)
    ctx.db.add(a)
    await ctx.db.flush()
    await _chef_shift(ctx, True)
    await ctx.audit("attendance.clock_in", "attendance", a.id, after={"at": at.isoformat()})
    return _json(a, None, at)


@router.post("/attendance/clock-out")
async def clock_out(ctx: Ctx = HotelCtx()) -> dict:
    a = await _open(ctx, ctx.user_id)
    if not a:
        raise conflict("not_clocked_in", "You are not clocked in")
    at = now()
    a.clock_out_at = at
    await _chef_shift(ctx, False)
    await ctx.audit("attendance.clock_out", "attendance", a.id, after={"at": at.isoformat()})
    return _json(a, None, at)


@router.get("/attendance")
async def day_sheet(
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    ctx: Ctx = HotelCtx("attendance.manage"),
) -> dict:
    """Shifts started in the period (hotel-local days, default today), each person's total, and open shifts."""
    tz = (await ctx.db.execute(select(Hotel.timezone))).scalar_one()
    zone = ZoneInfo(tz)
    at = now()
    today = at.astimezone(zone).date()
    start, end = date_from or today, date_to or today  # "from X" alone runs to today
    if end < start:
        raise bad_request("bad_range", "The start is after the end")
    if (end - start).days >= MAX_DAYS:
        raise bad_request("range_too_long", f"Pick at most {MAX_DAYS} days", {"max_days": MAX_DAYS})
    lo = datetime.combine(start, datetime.min.time(), zone)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), zone)
    rows = (
        await ctx.db.execute(
            select(Attendance, User.name)
            .join(User, User.id == Attendance.user_id)
            .where(Attendance.clock_in_at >= lo, Attendance.clock_in_at < hi)
            .order_by(Attendance.clock_in_at)
        )
    ).all()
    people: dict[UUID, dict] = defaultdict(lambda: {"shifts": 0, "minutes": 0, "days": set(), "missing": 0})
    for a, name in rows:
        p = people[a.user_id]
        p["name"] = name
        p["shifts"] += 1
        p["minutes"] += _minutes(a) or 0
        p["days"].add(a.clock_in_at.astimezone(zone).date())
        p["missing"] += a.clock_out_at is None and at - a.clock_in_at > LONG_SHIFT
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "timezone": tz,
        "shifts": [_json(a, name, at) for a, name in rows],
        "people": sorted(
            (
                {
                    "user_id": str(uid),
                    "name": p["name"],
                    "days": len(p["days"]),
                    "shifts": p["shifts"],
                    "minutes": p["minutes"],  # closed shifts only: an open shift has no hours yet
                    "missing_clock_outs": p["missing"],
                }
                for uid, p in people.items()
            ),
            key=lambda x: x["name"],
        ),
    }


class FixIn(BaseModel):
    clock_in_at: datetime | None = None
    clock_out_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _times(self) -> "FixIn":
        for t in (self.clock_in_at, self.clock_out_at):
            if t is not None and t.tzinfo is None:
                raise ValueError("Give times with their time zone")
        if self.clock_in_at is None and self.clock_out_at is None:
            raise ValueError("Give a clock-in or clock-out time")
        return self


@router.post("/attendance/{attendance_id}/fix")
async def fix(attendance_id: UUID, body: FixIn, ctx: Ctx = HotelCtx("attendance.manage")) -> dict:
    """A manager corrects a shift (forgot to clock out, clocked in late on the app). Original times are kept."""
    a = await ctx.db.get(Attendance, attendance_id, with_for_update=True)
    if not a:
        raise not_found("Shift")
    new_in = body.clock_in_at or a.clock_in_at
    new_out = body.clock_out_at or a.clock_out_at
    at = now()
    if new_in > at or (new_out is not None and new_out > at):
        raise bad_request("future_time", "That time has not happened yet")
    if new_out is not None and new_out <= new_in:
        raise bad_request("bad_times", "Clock-out must be after clock-in")
    before = {"in": a.clock_in_at.isoformat(), "out": a.clock_out_at.isoformat() if a.clock_out_at else None}
    if a.corrected_by is None:  # keep the times as punched, once
        a.original_in_at, a.original_out_at = a.clock_in_at, a.clock_out_at
    a.clock_in_at, a.clock_out_at = new_in, new_out
    a.corrected_by, a.correction_reason = ctx.user_id, body.reason.strip()
    await ctx.audit(
        "attendance.fix",
        "attendance",
        a.id,
        before=before,
        after={"in": new_in.isoformat(), "out": new_out.isoformat() if new_out else None, "reason": body.reason},
    )
    name = (await ctx.db.execute(select(User.name).where(User.id == a.user_id))).scalar_one()
    return _json(a, name, at)
