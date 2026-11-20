from datetime import datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.deps import Ctx, HotelCtx, count
from app.core.errors import bad_request, conflict, not_found
from app.core.security import new_opaque_token, now
from app.models import Bill, DiningTable, Order, Outlet, TableSession
from app.realtime.hub import outlet_channel  # noqa: F401  (re-exported)

router = APIRouter(tags=["floor"])

MODE_FEATURE = {"CHEF_ACCEPT": "CHEF_ACCEPT_MODE", "AUTO_ASSIGN": "AUTO_ROUTING", "ADMIN_PRINT": "ADMIN_PRINT_MODE"}
OPEN_STATUSES = ("PLACED", "CONFIRMED", "ASSIGNED", "PREPARING", "READY")


async def current_outlet(ctx: Ctx) -> Outlet:
    """v1: one outlet per hotel is surfaced in the UI; the schema already supports many."""
    return (await ctx.db.execute(select(Outlet).order_by(Outlet.created_at).limit(1))).scalar_one()


class SettingsOut(BaseModel):
    outlet_id: UUID
    name: str
    kitchen_mode: str
    qr_confirm_required: bool
    max_queue_orders: int
    serve_mode: str
    pickup_alert_minutes: int | None
    available_modes: list[str]


class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kitchen_mode: str | None = Field(default=None, pattern="^(CHEF_ACCEPT|AUTO_ASSIGN|ADMIN_PRINT)$")
    qr_confirm_required: bool | None = None
    max_queue_orders: int | None = Field(default=None, ge=1, le=10)
    serve_mode: str | None = Field(default=None, pattern="^(SERVER|COUNTER)$")
    # 1-60 minutes; send 0 to turn the alert off (null means "leave unchanged", like every field here).
    pickup_alert_minutes: int | None = Field(default=None, ge=0, le=60)


def _settings_out(ctx: Ctx, o: Outlet) -> SettingsOut:
    modes = [m for m, f in MODE_FEATURE.items() if ctx.entitlements["features"].get(f)]
    return SettingsOut(
        outlet_id=o.id,
        name=o.name,
        kitchen_mode=o.kitchen_mode,
        qr_confirm_required=o.qr_confirm_required,
        max_queue_orders=o.max_queue_orders,
        serve_mode=o.serve_mode,
        pickup_alert_minutes=o.pickup_alert_minutes,
        available_modes=modes,
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings_(ctx: Ctx = HotelCtx()) -> SettingsOut:
    return _settings_out(ctx, await current_outlet(ctx))


@router.put("/settings", response_model=SettingsOut)
async def put_settings(body: SettingsIn, ctx: Ctx = HotelCtx("settings.manage")) -> SettingsOut:
    o = await current_outlet(ctx)
    before = _settings_out(ctx, o).model_dump(mode="json")
    if body.kitchen_mode:
        ctx.require_feature(MODE_FEATURE[body.kitchen_mode])  # validated on write, re-checked on use
        o.kitchen_mode = body.kitchen_mode
    for f in ("name", "qr_confirm_required", "max_queue_orders", "serve_mode"):
        v = getattr(body, f)
        if v is not None:
            setattr(o, f, v)
    if body.pickup_alert_minutes is not None:
        o.pickup_alert_minutes = body.pickup_alert_minutes or None  # 0 = off
    out = _settings_out(ctx, o)
    await ctx.audit("settings.update", "outlet", o.id, before=before, after=out.model_dump(mode="json"))
    return out


class TableOut(BaseModel):
    id: UUID
    label: str
    area: str | None
    seats: int
    status: str
    qr_token: str
    qr_url: str
    open_orders: int = 0
    # Derived (docs/03 "Table visits"): OCCUPIED while orders are open, BILL_PENDING while served food is unpaid,
    # SEATED when guests sat but haven't ordered, then CLEANING / RESERVED holds, else FREE.
    state: str = "FREE"
    seated_at: datetime | None = None
    guests: int | None = None
    hold_note: str | None = None
    unbilled_orders: int = 0
    unbilled_total_minor: int = 0  # unbilled served orders + issued-but-unpaid bills; computed by the server


class TableIn(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    area: str | None = Field(default=None, max_length=40)
    seats: int = Field(default=4, ge=1, le=40)


class TablePatch(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=20)
    area: str | None = Field(default=None, max_length=40)
    seats: int | None = Field(default=None, ge=1, le=40)
    status: str | None = Field(default=None, pattern="^(ACTIVE|INACTIVE)$")


def table_state(t: DiningTable, open_orders: int, owes: bool) -> str:
    if open_orders:
        return "OCCUPIED"
    if owes:
        return "BILL_PENDING"
    if t.seated_at is not None:
        return "SEATED"
    return t.hold_state or "FREE"


def _qr_url(token: str) -> str:
    base = get_settings().public_diner_url.rstrip("/")
    return f"{base}/{token}" if base else f"/#/t/{token}"


def _table_out(t: DiningTable, open_orders: int = 0) -> TableOut:
    return TableOut(
        id=t.id,
        label=t.label,
        area=t.area,
        seats=t.seats,
        status=t.status,
        qr_token=t.qr_token,
        qr_url=_qr_url(t.qr_token),
        open_orders=open_orders,
        seated_at=t.seated_at,
        guests=t.guests,
        hold_note=t.hold_note,
        state=table_state(t, open_orders, False),
    )


@router.get("/tables", response_model=list[TableOut])
async def list_tables(ctx: Ctx = HotelCtx("order.view")) -> list[TableOut]:
    tables = (await ctx.db.execute(select(DiningTable).order_by(DiningTable.area, DiningTable.label))).scalars().all()
    from sqlalchemy import func

    counts = dict(
        (
            await ctx.db.execute(
                select(Order.table_id, func.count()).where(Order.status.in_(OPEN_STATUSES)).group_by(Order.table_id)
            )
        ).all()
    )
    # Served but not on a bill yet.
    unbilled = {
        tid: (n, total)
        for tid, n, total in (
            await ctx.db.execute(
                select(Order.table_id, func.count(), func.coalesce(func.sum(Order.total_minor), 0))
                .where(Order.status == "SERVED", Order.bill_id.is_(None))
                .group_by(Order.table_id)
            )
        ).all()
    }
    # On a bill that is issued but not paid.
    owing = dict(
        (
            await ctx.db.execute(
                select(Bill.table_id, func.coalesce(func.sum(Bill.total_minor), 0))
                .where(Bill.status == "ISSUED")
                .group_by(Bill.table_id)
            )
        ).all()
    )
    out = []
    for t in tables:
        row = _table_out(t, counts.get(t.id, 0))
        n, total = unbilled.get(t.id, (0, 0))
        row.unbilled_orders = n
        row.unbilled_total_minor = int(total) + int(owing.get(t.id, 0))
        row.state = table_state(t, row.open_orders, bool(row.unbilled_total_minor or n))
        out.append(row)
    return out


@router.post("/tables", response_model=TableOut, status_code=201)
async def create_table(body: TableIn, ctx: Ctx = HotelCtx("table.manage")) -> TableOut:
    await ctx.require_limit("MAX_TABLES", count(DiningTable))
    o = await current_outlet(ctx)
    if (await ctx.db.execute(select(DiningTable.id).where(DiningTable.label == body.label))).first():
        raise conflict("label_taken", "A table with this label exists")
    t = DiningTable(
        hotel_id=ctx.hotel_id,
        outlet_id=o.id,
        label=body.label,
        area=body.area,
        seats=body.seats,
        qr_token=new_opaque_token(18),
    )
    ctx.db.add(t)
    await ctx.db.flush()
    await ctx.audit("table.create", "table", t.id, after={"label": t.label})
    return _table_out(t)


@router.patch("/tables/{table_id}", response_model=TableOut)
async def patch_table(table_id: UUID, body: TablePatch, ctx: Ctx = HotelCtx("table.manage")) -> TableOut:
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    for f in ("label", "area", "seats", "status"):
        v = getattr(body, f)
        if v is not None:
            setattr(t, f, v)
    await ctx.audit("table.update", "table", t.id, after=body.model_dump(exclude_none=True))
    return _table_out(t)


@router.post("/tables/{table_id}/rotate-qr", response_model=TableOut)
async def rotate_qr(table_id: UUID, ctx: Ctx = HotelCtx("table.manage")) -> TableOut:
    """Invalidate a leaked or defaced QR (docs/08). The old printed code stops working."""
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    t.qr_token = new_opaque_token(18)
    await ctx.audit("table.rotate_qr", "table", t.id)
    return _table_out(t)


@router.delete("/tables/{table_id}", status_code=204)
async def delete_table(table_id: UUID, ctx: Ctx = HotelCtx("table.manage")) -> None:
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    if (await ctx.db.execute(select(Order.id).where(Order.table_id == t.id).limit(1))).first():
        raise bad_request("table_has_orders", "Tables with order history can be deactivated, not deleted")
    await ctx.db.delete(t)
    await ctx.audit("table.delete", "table", table_id)


class QrPrintIn(BaseModel):
    paper_mm: int | None = Field(default=None, description="58 or 80 to also get ESC/POS bytes")
    # Only used when PUBLIC_DINER_URL is not configured (dev/preview): the web origin the codes should open.
    origin: str | None = Field(default=None, max_length=200, pattern=r"^https?://[^\s/]+$")


def _absolute(url: str, origin: str | None) -> str:
    if url.startswith("http"):
        return url
    if not origin:
        raise bad_request("public_url_missing", "Set PUBLIC_DINER_URL so printed QR codes open the right site")
    return origin.rstrip("/") + url


async def _qr_doc(ctx: Ctx, tables: list[DiningTable], body: QrPrintIn) -> dict:
    import base64

    from app.core.config import get_settings
    from app.models import Hotel, HotelBranding
    from app.printing import escpos
    from app.printing.documents import table_qr_blocks

    s = get_settings()
    hotel = await ctx.db.get(Hotel, ctx.hotel_id)
    brand = await ctx.db.get(HotelBranding, ctx.hotel_id)
    logo = brand.logo_png if brand else None
    address = brand.address_line if brand else None
    powered_by = f"Powered by {s.powered_by_name}" + (f" | {s.powered_by_contact}" if s.powered_by_contact else "")
    tickets = []
    for t in tables:
        url = _absolute(_qr_url(t.qr_token), body.origin)
        tickets.append(
            {
                "table_id": str(t.id),
                "label": t.label,
                "area": t.area,
                "url": url,
                "blocks": table_qr_blocks(
                    hotel_name=hotel.name,
                    address_line=address,
                    has_logo=bool(logo),
                    table_label=t.label,
                    area=t.area,
                    url=url,
                    powered_by=powered_by,
                ),
            }
        )
    out: dict = {
        "hotel": {
            "name": hotel.name,
            "address_line": address,
            "logo_b64": base64.b64encode(logo).decode() if logo else None,
        },
        "powered_by": {"name": s.powered_by_name, "contact": s.powered_by_contact or None},
        "tickets": tickets,
    }
    if body.paper_mm:
        out["escpos_b64"] = base64.b64encode(
            b"".join(escpos.render(tk["blocks"], body.paper_mm, logo_png=logo) for tk in tickets)
        ).decode()
    return out


@router.post("/tables/{table_id}/qr-print")
async def print_table_qr(table_id: UUID, body: QrPrintIn, ctx: Ctx = HotelCtx("table.manage")) -> dict:
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    return await _qr_doc(ctx, [t], body)


@router.post("/tables/qr-print")
async def print_all_qr(body: QrPrintIn, ctx: Ctx = HotelCtx("table.manage")) -> dict:
    tables = (
        (
            await ctx.db.execute(
                select(DiningTable).where(DiningTable.status == "ACTIVE").order_by(DiningTable.area, DiningTable.label)
            )
        )
        .scalars()
        .all()
    )
    return await _qr_doc(ctx, list(tables), body)


class SeatIn(BaseModel):
    guests: int | None = Field(default=None, ge=1, le=99)


@router.post("/tables/{table_id}/seat", response_model=TableOut)
async def seat(table_id: UUID, body: SeatIn, ctx: Ctx = HotelCtx("order.create")) -> TableOut:
    """Guests sit down (or the count changes): the visit's timer starts at the first seating and the count is what
    the bill will record as covers. Seating clears a Reserved or Cleaning hold."""
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    if t.seated_at is None:
        t.seated_at = now()
    t.guests = body.guests or t.guests
    t.hold_state, t.hold_note = None, None
    await ctx.audit("table.seat", "table", t.id, after={"guests": t.guests})
    ctx.emit(outlet_channel(t.outlet_id), {"type": "table.updated", "table_id": str(t.id)})
    return _table_out(t)


class HoldIn(BaseModel):
    """FREE clears a hold (the table is clean, or the reservation is off). RESERVED takes a short note."""

    state: str = Field(pattern="^(FREE|RESERVED|CLEANING)$")
    note: str | None = Field(default=None, max_length=80)


@router.post("/tables/{table_id}/hold", response_model=TableOut)
async def hold(table_id: UUID, body: HoldIn, ctx: Ctx = HotelCtx("order.create")) -> TableOut:
    t = await ctx.db.get(DiningTable, table_id)
    if not t:
        raise not_found("Table")
    if t.seated_at is not None and body.state != "FREE":
        raise conflict("table_in_use", "Guests are at this table")
    if body.state == "FREE" and t.seated_at is not None:
        # Guests left without ordering: end the visit.
        busy = (
            await ctx.db.execute(
                select(Order.id)
                .where(Order.table_id == t.id, Order.bill_id.is_(None), Order.status != "CANCELLED")
                .limit(1)
            )
        ).first()
        if busy:
            raise conflict("table_in_use", "This table has orders; bill it first")
        t.seated_at, t.guests = None, None
    t.hold_state = None if body.state == "FREE" else body.state
    t.hold_note = (body.note or "").strip() or None if body.state == "RESERVED" else None
    await ctx.audit("table.hold", "table", t.id, after={"state": body.state, "note": t.hold_note})
    ctx.emit(outlet_channel(t.outlet_id), {"type": "table.updated", "table_id": str(t.id)})
    return _table_out(t)


# ---------- move / merge (docs/03 "Table visits", docs/14) ----------

LIVE_ORDERS = ("PLACED", "CONFIRMED", "ASSIGNED", "PREPARING", "READY", "SERVED")


async def _visit_orders(ctx: Ctx, t: DiningTable) -> list[Order]:
    return list(
        (
            await ctx.db.execute(
                select(Order).where(Order.table_id == t.id, Order.bill_id.is_(None), Order.status.in_(LIVE_ORDERS))
            )
        ).scalars()
    )


async def _check_unbilled(ctx: Ctx, t: DiningTable) -> None:
    """Only before billing: an issued invoice never changes table, and nothing paid is ever merged."""
    open_bill = (
        await ctx.db.execute(select(Bill.invoice_no).where(Bill.table_id == t.id, Bill.status == "ISSUED").limit(1))
    ).scalar()
    if open_bill:
        raise conflict(
            "table_has_bill",
            f"Table {t.label} has bill {open_bill}; settle or cancel it first",
            {"table": t.label, "invoice_no": open_bill},
        )


async def _bring(ctx: Ctx, src: DiningTable, dst: DiningTable) -> int:
    """Move one visit's orders, QR diner sessions, guests and timer from src to dst; src turns Cleaning."""
    orders = await _visit_orders(ctx, src)
    for o in orders:
        o.table_id = dst.id
    # Diners' phones keep ordering and tracking on the new table.
    await ctx.db.execute(
        update(TableSession)
        .where(TableSession.table_id == src.id, TableSession.closed_at.is_(None))
        .values(table_id=dst.id)
    )
    if src.seated_at and (dst.seated_at is None or src.seated_at < dst.seated_at):
        dst.seated_at = src.seated_at
    if src.guests:
        dst.guests = (dst.guests or 0) + src.guests
    if dst.seated_at is None and orders:
        dst.seated_at = min(o.created_at for o in orders)
    dst.hold_state, dst.hold_note = None, None
    src.seated_at, src.guests, src.hold_state, src.hold_note = None, None, "CLEANING", None
    return len(orders)


class MoveIn(BaseModel):
    to_table_id: UUID


@router.post("/tables/{table_id}/move", response_model=TableOut)
async def move(table_id: UUID, body: MoveIn, ctx: Ctx = HotelCtx("order.create")) -> TableOut:
    """The guests changed tables: their whole visit goes to a free table (Free, Reserved or Cleaning)."""
    src = await ctx.db.get(DiningTable, table_id, with_for_update=True)
    dst = await ctx.db.get(DiningTable, body.to_table_id, with_for_update=True)
    if not src or not dst:
        raise not_found("Table")
    if src.id == dst.id:
        raise bad_request("same_table", "Pick another table")
    if dst.status != "ACTIVE" or dst.seated_at is not None or await _visit_orders(ctx, dst):
        raise conflict("table_in_use", f"Table {dst.label} has guests; merge instead", {"table": dst.label})
    await _check_unbilled(ctx, src)
    await _check_unbilled(ctx, dst)
    if src.seated_at is None and not await _visit_orders(ctx, src):
        raise bad_request("nothing_to_move", f"Nobody is seated at table {src.label}")
    moved = await _bring(ctx, src, dst)
    await ctx.audit("table.move", "table", src.id, after={"from": src.label, "to": dst.label, "orders": moved})
    for t in (src, dst):
        ctx.emit(outlet_channel(t.outlet_id), {"type": "table.updated", "table_id": str(t.id)})
    return _table_out(dst, sum(o.status != "SERVED" for o in await _visit_orders(ctx, dst)))


class MergeIn(BaseModel):
    from_table_ids: list[UUID] = Field(min_length=1, max_length=10)


@router.post("/tables/{table_id}/merge", response_model=TableOut)
async def merge(table_id: UUID, body: MergeIn, ctx: Ctx = HotelCtx("order.create")) -> TableOut:
    """One party across several tables: their visits join this table, so they get one bill (split it by guest
    later if they want)."""
    dst = await ctx.db.get(DiningTable, table_id, with_for_update=True)
    if not dst:
        raise not_found("Table")
    ids = list(dict.fromkeys(body.from_table_ids))
    if table_id in ids:
        raise bad_request("same_table", "Pick other tables to merge in")
    srcs = [await ctx.db.get(DiningTable, i, with_for_update=True) for i in ids]
    if any(s is None for s in srcs):
        raise not_found("Table")
    for t in (dst, *srcs):
        await _check_unbilled(ctx, t)
    moved = 0
    for s in srcs:
        moved += await _bring(ctx, s, dst)
    if dst.seated_at is None:
        raise bad_request("nothing_to_move", "None of these tables has guests")
    labels = [s.label for s in srcs]
    await ctx.audit("table.merge", "table", dst.id, after={"into": dst.label, "from": labels, "orders": moved})
    for t in (dst, *srcs):
        ctx.emit(outlet_channel(t.outlet_id), {"type": "table.updated", "table_id": str(t.id)})
    return _table_out(dst, sum(o.status != "SERVED" for o in await _visit_orders(ctx, dst)))
