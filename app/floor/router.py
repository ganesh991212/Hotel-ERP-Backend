from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import Ctx, HotelCtx, count
from app.core.errors import bad_request, conflict, not_found
from app.core.security import new_opaque_token
from app.models import DiningTable, Order, Outlet
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
    available_modes: list[str]


class SettingsIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    kitchen_mode: str | None = Field(default=None, pattern="^(CHEF_ACCEPT|AUTO_ASSIGN|ADMIN_PRINT)$")
    qr_confirm_required: bool | None = None
    max_queue_orders: int | None = Field(default=None, ge=1, le=10)


def _settings_out(ctx: Ctx, o: Outlet) -> SettingsOut:
    modes = [m for m, f in MODE_FEATURE.items() if ctx.entitlements["features"].get(f)]
    return SettingsOut(
        outlet_id=o.id,
        name=o.name,
        kitchen_mode=o.kitchen_mode,
        qr_confirm_required=o.qr_confirm_required,
        max_queue_orders=o.max_queue_orders,
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
    for f in ("name", "qr_confirm_required", "max_queue_orders"):
        v = getattr(body, f)
        if v is not None:
            setattr(o, f, v)
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
    return f"{get_settings().public_diner_url.rstrip('/')}/{token}"


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
    return [_table_out(t, counts.get(t.id, 0)) for t in tables]


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
