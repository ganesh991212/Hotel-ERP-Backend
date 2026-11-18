"""The signed-in hotel's own profile, GST details and plan entitlements."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.api.deps import HotelContext, HotelCtx, require_permission
from app.api.schemas import EntitlementsOut, Strict, TaxProfileIn, TaxProfileOut, tax_profile_out
from app.core.errors import not_found, unprocessable
from app.services.audit import write_hotel_audit

router = APIRouter(prefix="/hotel", tags=["hotel"])
ManageHotel = Annotated[HotelContext, Depends(require_permission("hotel.manage"))]


class HotelProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    legal_name: str | None
    timezone: str
    currency: str
    status: str
    plan_code: str | None
    subscription_status: str | None
    current_period_end: datetime | None


class HotelProfilePatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)


async def _profile(ctx: HotelContext) -> HotelProfileOut:
    # `hotel` is a platform table: always filtered by the id from the token.
    row = (
        (
            await ctx.db.execute(
                text(
                    "SELECT h.id, h.name, h.slug, h.legal_name, h.timezone, h.currency, h.status, "
                    "p.code AS plan_code, s.status AS subscription_status, s.current_period_end "
                    "FROM hotel h LEFT JOIN subscription s ON s.hotel_id = h.id AND s.is_current "
                    "LEFT JOIN plan p ON p.id = s.plan_id WHERE h.id = :h"
                ),
                {"h": ctx.hotel_id},
            )
        )
        .mappings()
        .one()
    )
    return HotelProfileOut(**row)


@router.get("", response_model=HotelProfileOut)
async def get_profile(ctx: HotelCtx) -> HotelProfileOut:
    return await _profile(ctx)


@router.patch("", response_model=HotelProfileOut)
async def update_profile(body: HotelProfilePatch, ctx: ManageHotel) -> HotelProfileOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise unprocessable("NOTHING_TO_UPDATE", "No fields given")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    await ctx.db.execute(
        text(f"UPDATE hotel SET {sets} WHERE id = :h"),  # noqa: S608 - keys from the model
        {**changes, "h": ctx.hotel_id},
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="hotel.profile_updated",
        entity_type="hotel",
        entity_id=ctx.hotel_id,
        actor_user_id=ctx.user_id,
        after=changes,
        ip=ctx.ip,
    )
    return await _profile(ctx)


@router.get("/tax-profile", response_model=TaxProfileOut)
async def get_tax_profile(ctx: HotelCtx) -> TaxProfileOut:
    row = (
        (
            await ctx.db.execute(
                text("SELECT * FROM hotel_tax_profile WHERE hotel_id = :h"), {"h": ctx.hotel_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise not_found("No tax profile yet")
    return tax_profile_out(row)


@router.put("/tax-profile", response_model=TaxProfileOut)
async def put_tax_profile(body: TaxProfileIn, ctx: ManageHotel) -> TaxProfileOut:
    """GST details decide the billing document: tax invoice (regular), bill of supply
    (composition) or plain bill (unregistered). The GSTIN checksum is verified."""
    before = (
        (
            await ctx.db.execute(
                text(
                    "SELECT gst_registration, gstin, state_code, service_charge_bp "
                    "FROM hotel_tax_profile WHERE hotel_id = :h"
                ),
                {"h": ctx.hotel_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    row = (
        (
            await ctx.db.execute(
                text(
                    "INSERT INTO hotel_tax_profile (hotel_id, gst_registration, gstin, legal_name, "
                    "state_code, service_charge_applies, service_charge_bp, invoice_prefix, fy_start_month) "
                    "VALUES (:h, :gst_registration, :gstin, :legal_name, :state_code, "
                    ":service_charge_applies, :service_charge_bp, :invoice_prefix, :fy_start_month) "
                    "ON CONFLICT (hotel_id) DO UPDATE SET gst_registration = EXCLUDED.gst_registration, "
                    "gstin = EXCLUDED.gstin, legal_name = EXCLUDED.legal_name, "
                    "state_code = EXCLUDED.state_code, "
                    "service_charge_applies = EXCLUDED.service_charge_applies, "
                    "service_charge_bp = EXCLUDED.service_charge_bp, "
                    "invoice_prefix = EXCLUDED.invoice_prefix, fy_start_month = EXCLUDED.fy_start_month "
                    "RETURNING *"
                ),
                {"h": ctx.hotel_id, **body.model_dump()},
            )
        )
        .mappings()
        .one()
    )
    await write_hotel_audit(
        ctx.db,
        hotel_id=ctx.hotel_id,
        action="hotel.tax_profile_saved",
        entity_type="hotel_tax_profile",
        entity_id=ctx.hotel_id,
        actor_user_id=ctx.user_id,
        before=dict(before) if before else None,
        after={
            "gst_registration": body.gst_registration,
            "gstin": body.gstin,
            "state_code": body.state_code,
            "service_charge_bp": body.service_charge_bp,
        },
        ip=ctx.ip,
    )
    return tax_profile_out(row)


@router.get("/entitlements", response_model=EntitlementsOut)
async def get_entitlements(ctx: HotelCtx) -> EntitlementsOut:
    """What the plan allows. The app uses this to hide or explain locked features; the
    server enforces them regardless."""
    ent = await ctx.entitlements()
    return EntitlementsOut(
        plan_code=ent.plan_code,
        subscription_status=ent.subscription_status,
        version=ent.version,
        flags=dict(ent.flags),
        limits=dict(ent.limits),
    )
