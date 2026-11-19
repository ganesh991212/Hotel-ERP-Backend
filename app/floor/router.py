from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import Ctx, HotelCtx, count
from app.core.errors import bad_request, conflict, not_found
from app.core.security import new_opaque_token
from app.models import Bill, DiningTable, Order, Outlet
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
    # Derived (docs/04): OCCUPIED while orders are open, BILL_PENDING while served food is unpaid, else FREE.
    state: str = "FREE"
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
        row.state = "OCCUPIED" if row.open_orders else "BILL_PENDING" if row.unbilled_total_minor or n else "FREE"
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
