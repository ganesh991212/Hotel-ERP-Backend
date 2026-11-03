"""Outlets: the restaurants, bars and room-service points inside a hotel.

Kitchen settings are validated against the plan on write (docs/05: "validated on write and
re-checked at use"). Phase 4 re-checks them when routing, in case the plan was downgraded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.deps import HotelContext, HotelCtx, require_permission
from app.api.schemas import Strict
from app.core.errors import conflict, not_found, unprocessable
from app.services.audit import write_hotel_audit
from app.services.entitlements import Entitlements, enforce_limit, feature_denied

router = APIRouter(prefix="/outlets", tags=["outlets"])
ManageOutlets = Annotated[HotelContext, Depends(require_permission("outlet.manage"))]

OutletKind = Literal["restaurant", "bar", "cafe", "room_service", "banquet"]
KitchenMode = Literal["admin_print", "auto_assign", "chef_accept"]
KITCHEN_MODE_FEATURE: dict[str, str] = {
    "admin_print": "ADMIN_PRINT_MODE",
    "auto_assign": "AUTO_ROUTING",
    "chef_accept": "CHEF_ACCEPT_MODE",
}


class OutletOut(BaseModel):
    id: uuid.UUID
    name: str
    code: str
    kind: OutletKind
    status: Literal["active", "archived"]
    kitchen_mode: KitchenMode
    require_accept: bool
    max_queue_orders: int
    rating_band_minutes: int
    qr_confirm_required: bool
    ratings_enabled: bool
    business_day_cutoff: time
    created_at: datetime
    updated_at: datetime


class _OutletFields(Strict):
    kitchen_mode: KitchenMode | None = None
    require_accept: bool | None = None
    max_queue_orders: int | None = Field(default=None, ge=1, le=20)
    rating_band_minutes: int | None = Field(default=None, ge=0, le=30)
    qr_confirm_required: bool | None = None
    ratings_enabled: bool | None = None
    business_day_cutoff: time | None = Field(
        default=None, description="Orders before this local time belong to the previous day"
    )


class OutletCreate(_OutletFields):
    name: str = Field(min_length=2, max_length=120)
    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,23}$")
    kind: OutletKind = "restaurant"


class OutletPatch(_OutletFields):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    kind: OutletKind | None = None


_COLUMNS = (
    "id, name, code, kind, status, kitchen_mode, require_accept, max_queue_orders, "
    "rating_band_minutes, qr_confirm_required, ratings_enabled, business_day_cutoff, "
    "created_at, updated_at"
)


def _check_plan(ent: Entitlements, fields: dict[str, object]) -> None:
    mode = fields.get("kitchen_mode")
    if isinstance(mode, str) and not ent.allows(KITCHEN_MODE_FEATURE[mode]):
        raise feature_denied(KITCHEN_MODE_FEATURE[mode])
    if fields.get("ratings_enabled") is True and not ent.allows("RATINGS"):
        raise feature_denied("RATINGS")


def _default_mode(ent: Entitlements) -> str:
    for mode, feature in KITCHEN_MODE_FEATURE.items():
        if ent.allows(feature):
            return mode
    raise feature_denied("ADMIN_PRINT_MODE")


def _visible_filter(ctx: HotelContext) -> tuple[str, dict[str, object]]:
    """Staff limited to some outlets only see those outlets."""
    if ctx.outlet_ids is None:
        return "", {}
    return " AND id = ANY(:mine)", {"mine": list(ctx.outlet_ids)}


async def _get(ctx: HotelContext, outlet_id: uuid.UUID, *, for_update: bool = False) -> OutletOut:
    extra, params = _visible_filter(ctx)
    lock = " FOR UPDATE" if for_update else ""
    row = (
        (
            await ctx.db.execute(
                text(
                    f"SELECT {_COLUMNS} FROM outlet WHERE hotel_id = :h AND id = :id{extra}{lock}"  # noqa: S608
                ),
                {"h": ctx.hotel_id, "id": outlet_id, **params},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise not_found("Outlet not found")
    return OutletOut(**row)


_COUNT_ACTIVE = "SELECT count(*) FROM outlet WHERE hotel_id = :h AND status = 'active'"


@router.get("", response_model=list[OutletOut])
async def list_outlets(
    ctx: HotelCtx,
    status_filter: Annotated[
        Literal["active", "archived"] | None, Query(alias="status")
    ] = "active",
) -> list[OutletOut]:
    extra, params = _visible_filter(ctx)
    if status_filter:
        extra += " AND status = :st"
        params["st"] = status_filter
    rows = (
        (
            await ctx.db.execute(
                text(f"SELECT {_COLUMNS} FROM outlet WHERE hotel_id = :h{extra} ORDER BY name"),  # noqa: S608
                {"h": ctx.hotel_id, **params},
            )
        )
        .mappings()
        .all()
    )
    return [OutletOut(**r) for r in rows]


@router.post("", response_model=OutletOut, status_code=status.HTTP_201_CREATED)
async def create_outlet(body: OutletCreate, ctx: ManageOutlets) -> OutletOut:
    ent = await ctx.entitlements()
    fields = body.model_dump(exclude_none=True)
    _check_plan(ent, fields)
    fields.setdefault("kitchen_mode", _default_mode(ent))
    await enforce_limit(ctx.db, ent, "MAX_OUTLETS", _COUNT_ACTIVE, {"h": ctx.hotel_id})
    cols = ", ".join(fields)
    vals = ", ".join(f":{k}" for k in fields)
    try:
        async with ctx.db.begin_nested():
            new_id = (
                await ctx.db.execute(
                    text(f"INSERT INTO outlet (hotel_id, {cols}) VALUES (:h, {vals}) RETURNING id"),  # noqa: S608
                    {"h": ctx.hotel_id, **fields},
                )
            ).scalar_one()
    except IntegrityError as exc:
        raise conflict("OUTLET_CODE_TAKEN", "Another outlet already uses this code") from exc
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="outlet.created",
        entity_type="outlet",
        entity_id=new_id,
        actor_user_id=ctx.user_id,
        after=fields,
        ip=ctx.ip,
    )
    return await _get(ctx, new_id)


@router.get("/{outlet_id}", response_model=OutletOut)
async def get_outlet(outlet_id: uuid.UUID, ctx: HotelCtx) -> OutletOut:
    return await _get(ctx, outlet_id)


@router.patch("/{outlet_id}", response_model=OutletOut)
async def update_outlet(outlet_id: uuid.UUID, body: OutletPatch, ctx: ManageOutlets) -> OutletOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise unprocessable("NOTHING_TO_UPDATE", "No fields given")
    if any(v is None for v in changes.values()):
        raise unprocessable("NULL_NOT_ALLOWED", "Fields cannot be set to null")
    _check_plan(await ctx.entitlements(), changes)
    before = await _get(ctx, outlet_id, for_update=True)
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    await ctx.db.execute(
        text(f"UPDATE outlet SET {sets} WHERE hotel_id = :h AND id = :id"),  # noqa: S608
        {**changes, "h": ctx.hotel_id, "id": outlet_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="outlet.updated",
        entity_type="outlet",
        entity_id=outlet_id,
        actor_user_id=ctx.user_id,
        before={k: getattr(before, k) for k in changes},
        after=changes,
        ip=ctx.ip,
    )
    return await _get(ctx, outlet_id)


async def _set_status(ctx: HotelContext, outlet_id: uuid.UUID, new: str) -> OutletOut:
    current = await _get(ctx, outlet_id, for_update=True)
    if current.status == new:
        return current
    if new == "active":
        await enforce_limit(
            ctx.db, await ctx.entitlements(), "MAX_OUTLETS", _COUNT_ACTIVE, {"h": ctx.hotel_id}
        )
    await ctx.db.execute(
        text("UPDATE outlet SET status = :s WHERE hotel_id = :h AND id = :id"),
        {"s": new, "h": ctx.hotel_id, "id": outlet_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="outlet.archived" if new == "archived" else "outlet.restored",
        entity_type="outlet",
        entity_id=outlet_id,
        actor_user_id=ctx.user_id,
        ip=ctx.ip,
    )
    return await _get(ctx, outlet_id)


@router.post("/{outlet_id}/archive", response_model=OutletOut)
async def archive_outlet(outlet_id: uuid.UUID, ctx: ManageOutlets) -> OutletOut:
    """Outlets are archived, never deleted: orders and bills keep pointing at them."""
    return await _set_status(ctx, outlet_id, "archived")


@router.post("/{outlet_id}/restore", response_model=OutletOut)
async def restore_outlet(outlet_id: uuid.UUID, ctx: ManageOutlets) -> OutletOut:
    return await _set_status(ctx, outlet_id, "active")
