"""Reservations and the walk-in waitlist (docs/03 "Bookings", docs/14). Staff-entered, one book: nothing to sync."""

from datetime import date, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Date, cast, func, select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, conflict, not_found
from app.core.security import now
from app.floor.router import _check_unbilled, _visit_orders
from app.models import Booking, DiningTable, Hotel
from app.realtime.hub import outlet_channel

router = APIRouter(tags=["bookings"])

SLOT = timedelta(minutes=90)  # how long a booked table is taken, for the overlap warning
SOON = timedelta(hours=2)  # the floor shows bookings starting within this


async def _tz(ctx: Ctx) -> str:
    return (await ctx.db.execute(select(Hotel.timezone))).scalar_one()


async def _json(ctx: Ctx, b: Booking, at: datetime) -> dict:
    no_shows = 0
    if b.phone:
        no_shows = (
            await ctx.db.execute(
                select(func.count()).where(Booking.phone == b.phone, Booking.status == "NO_SHOW", Booking.id != b.id)
            )
        ).scalar_one()
    table = await ctx.db.get(DiningTable, b.table_id) if b.table_id else None
    waited = None
    if b.kind == "WAITLIST":
        waited = int(((b.seated_at or at) - b.created_at).total_seconds() // 60)
    return {
        "id": str(b.id),
        "kind": b.kind,
        "status": b.status,
        "name": b.name,
        "phone": b.phone,
        "guests": b.guests,
        "booked_for": b.booked_for.isoformat() if b.booked_for else None,
        "quoted_minutes": b.quoted_minutes,
        "waited_minutes": waited,
        "table": {"id": str(table.id), "label": table.label} if table else None,
        "note": b.note,
        "no_shows_before": no_shows,  # earlier no-shows with this phone number (docs/14)
        "created_at": b.created_at.isoformat(),
    }


async def _overlap(ctx: Ctx, table_id: UUID, at: datetime, exclude: UUID | None = None) -> Booking | None:
    q = select(Booking).where(
        Booking.table_id == table_id,
        Booking.status == "BOOKED",
        Booking.booked_for > at - SLOT,
        Booking.booked_for < at + SLOT,
    )
    if exclude:
        q = q.where(Booking.id != exclude)
    return (await ctx.db.execute(q.limit(1))).scalar_one_or_none()


class BookingIn(BaseModel):
    kind: str = Field(pattern="^(BOOKING|WAITLIST)$")
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, max_length=20, pattern=r"^[0-9+ -]*$")
    guests: int = Field(ge=1, le=99)
    booked_for: datetime | None = None  # BOOKING: when (with time zone)
    quoted_minutes: int | None = Field(default=None, ge=0, le=600)  # WAITLIST: the wait told to the guest
    table_id: UUID | None = None
    note: str | None = Field(default=None, max_length=200)
    allow_overlap: bool = False  # after the app warned about another booking on that table

    @model_validator(mode="after")
    def _shape(self) -> "BookingIn":
        if (self.kind == "BOOKING") != (self.booked_for is not None):
            raise ValueError("A booking needs a time; a waitlist entry has none")
        if self.booked_for is not None and self.booked_for.tzinfo is None:
            raise ValueError("Give the time with its time zone")
        return self


@router.post("/bookings", status_code=201)
async def create(body: BookingIn, ctx: Ctx = HotelCtx("order.create")) -> dict:
    at = now()
    if body.booked_for is not None and body.booked_for < at - timedelta(hours=1):
        raise bad_request("past_time", "That time has passed")
    if body.table_id:
        if not await ctx.db.get(DiningTable, body.table_id):
            raise not_found("Table")
        if body.booked_for and not body.allow_overlap:
            other = await _overlap(ctx, body.table_id, body.booked_for)
            if other:
                raise conflict(
                    "table_booked",
                    "That table has another booking around this time",
                    {"name": other.name, "booked_for": other.booked_for.isoformat()},
                )
    b = Booking(
        hotel_id=ctx.hotel_id,
        kind=body.kind,
        status="BOOKED" if body.kind == "BOOKING" else "WAITING",
        name=body.name.strip(),
        phone=(body.phone or "").strip() or None,
        guests=body.guests,
        booked_for=body.booked_for,
        quoted_minutes=body.quoted_minutes,
        table_id=body.table_id,
        note=(body.note or "").strip() or None,
        created_by=ctx.user_id,
    )
    ctx.db.add(b)
    await ctx.db.flush()
    await ctx.db.refresh(b)
    # Audit without the guest's phone (personal data stays in the booking only).
    await ctx.audit("booking.create", "booking", b.id, after={"kind": b.kind, "guests": b.guests})
    return await _json(ctx, b, at)


@router.get("/bookings")
async def day(d: date | None = Query(None, alias="date"), ctx: Ctx = HotelCtx("order.view")) -> dict:
    """The day's book (hotel-local, default today), the live waitlist, and today's actual average wait so quotes
    follow reality."""
    tz = await _tz(ctx)
    today = (await ctx.db.execute(select(cast(func.timezone(tz, func.now()), Date)))).scalar_one()
    d = d or today
    at = now()
    local = cast(func.timezone(tz, Booking.booked_for), Date)
    books = (
        (
            await ctx.db.execute(
                select(Booking).where(Booking.kind == "BOOKING", local == d).order_by(Booking.booked_for)
            )
        )
        .scalars()
        .all()
    )
    waiting = (
        (
            await ctx.db.execute(
                select(Booking)
                .where(Booking.kind == "WAITLIST", Booking.status == "WAITING")
                .order_by(Booking.created_at)
            )
        )
        .scalars()
        .all()
    )
    created_day = cast(func.timezone(tz, Booking.created_at), Date)
    avg = (
        await ctx.db.execute(
            select(func.avg(func.extract("epoch", Booking.seated_at - Booking.created_at) / 60)).where(
                Booking.kind == "WAITLIST", Booking.status == "SEATED", created_day == today
            )
        )
    ).scalar()
    return {
        "date": d.isoformat(),
        "timezone": tz,
        "bookings": [await _json(ctx, b, at) for b in books],
        "waitlist": [await _json(ctx, b, at) for b in waiting],
        "avg_wait_minutes_today": round(float(avg)) if avg is not None else None,
    }


async def _get(ctx: Ctx, booking_id: UUID) -> Booking:
    b = await ctx.db.get(Booking, booking_id, with_for_update=True)
    if not b:
        raise not_found("Booking")
    return b


class SeatIn(BaseModel):
    table_id: UUID | None = None  # default: the booked table


@router.post("/bookings/{booking_id}/seat")
async def seat(booking_id: UUID, body: SeatIn, ctx: Ctx = HotelCtx("order.create")) -> dict:
    """The party arrived (or their table is ready): start the table visit with the booking's guest count."""
    b = await _get(ctx, booking_id)
    if b.status not in ("BOOKED", "WAITING"):
        raise conflict("bad_booking_state", f"This booking is {b.status.lower()}")
    table_id = body.table_id or b.table_id
    if not table_id:
        raise bad_request("table_required", "Pick a table")
    t = await ctx.db.get(DiningTable, table_id, with_for_update=True)
    if not t:
        raise not_found("Table")
    if t.seated_at is not None or await _visit_orders(ctx, t):
        raise conflict("table_in_use", f"Table {t.label} has guests", {"table": t.label})
    await _check_unbilled(ctx, t)
    at = now()
    t.seated_at, t.guests, t.hold_state, t.hold_note = at, b.guests, None, None
    b.status, b.seated_at, b.table_id = "SEATED", at, t.id
    await ctx.audit("booking.seat", "booking", b.id, after={"table": t.label, "guests": b.guests})
    ctx.emit(outlet_channel(t.outlet_id), {"type": "table.updated", "table_id": str(t.id)})
    return await _json(ctx, b, at)


class StatusIn(BaseModel):
    status: str = Field(pattern="^(CANCELLED|NO_SHOW)$")


@router.post("/bookings/{booking_id}/status")
async def set_status(booking_id: UUID, body: StatusIn, ctx: Ctx = HotelCtx("order.create")) -> dict:
    b = await _get(ctx, booking_id)
    if b.status not in ("BOOKED", "WAITING"):
        raise conflict("bad_booking_state", f"This booking is {b.status.lower()}")
    if body.status == "NO_SHOW" and b.kind != "BOOKING":
        raise bad_request("bad_status", "Walk-ins who leave are cancelled, not no-shows")
    b.status = body.status
    await ctx.audit("booking.status", "booking", b.id, after={"status": b.status})
    return await _json(ctx, b, now())


async def next_bookings(ctx: Ctx) -> dict[UUID, dict]:
    """Per table, the next booking starting within two hours (the floor shows "Booked 8:00 pm")."""
    at = now()
    rows = (
        await ctx.db.execute(
            select(Booking)
            .where(
                Booking.status == "BOOKED",
                Booking.table_id.is_not(None),
                Booking.booked_for >= at - timedelta(minutes=30),
                Booking.booked_for <= at + SOON,
            )
            .order_by(Booking.booked_for)
        )
    ).scalars()
    out: dict[UUID, dict] = {}
    for b in rows:
        out.setdefault(b.table_id, {"at": b.booked_for.isoformat(), "name": b.name, "guests": b.guests})
    return out
