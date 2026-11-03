"""Super admin: plans, feature catalogue, hotels, subscriptions, entitlement overrides.

Platform tables are global, so these run on a session with no hotel context. Where a
hotel's own (RLS) rows must be written, as in onboarding, that hotel's context is set
explicitly inside the same transaction, so the whole operation is atomic.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import RowMapping, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.deps import PlatformContext, PlatformCtx
from app.api.schemas import (
    EntitlementsOut,
    InviteOut,
    Page,
    Strict,
    TaxProfileIn,
    TaxProfileOut,
    tax_profile_out,
)
from app.core.errors import AppError, conflict, not_found, unprocessable
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.core.permissions import HOTEL_ADMIN_ROLE_ID
from app.db.session import set_context
from app.services import entitlements as ent_service
from app.services.audit import write_hotel_audit, write_platform_audit
from app.services.staff import add_member, find_or_create_user, issue_invite

router = APIRouter(prefix="/platform", tags=["platform"])

FeatureValue = bool | int | None


# --- plans and features ---------------------------------------------------------------
class FeatureOut(BaseModel):
    key: str
    kind: Literal["flag", "limit"]
    description: str
    default_flag: bool | None
    default_limit: int | None


class PlanOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    price_monthly_minor: int
    price_annual_minor: int
    currency: str
    is_placeholder: bool
    is_active: bool
    sort_order: int
    features: dict[str, FeatureValue]


class PlanCreate(Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    price_monthly_minor: int = Field(ge=0, description="Paise")
    price_annual_minor: int = Field(ge=0, description="Paise")
    is_placeholder: bool = True
    is_active: bool = True
    sort_order: int = 0


class PlanPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    price_monthly_minor: int | None = Field(default=None, ge=0)
    price_annual_minor: int | None = Field(default=None, ge=0)
    is_placeholder: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class PlanFeaturesIn(Strict):
    """Set features on a plan. A limit of ``null`` means unlimited. Keys in ``remove`` fall
    back to the catalogue default."""

    set: dict[str, FeatureValue] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)


async def _catalog(db: AsyncConnection) -> dict[str, str]:
    rows = (await db.execute(text("SELECT key, kind FROM feature_catalog"))).all()
    return {k: v for k, v in rows}


def _check_value(key: str, kind: str | None, value: FeatureValue) -> tuple[bool | None, int | None]:
    if kind is None:
        raise unprocessable("UNKNOWN_FEATURE", f"Unknown feature {key}", {"feature": key})
    if kind == "flag":
        if not isinstance(value, bool):
            raise unprocessable(
                "FEATURE_VALUE_INVALID", f"{key} is a flag: use true or false", {"feature": key}
            )
        return value, None
    if isinstance(value, bool) or (value is not None and value < 0):
        raise unprocessable(
            "FEATURE_VALUE_INVALID",
            f"{key} is a limit: use a number >= 0 or null for unlimited",
            {"feature": key},
        )
    return None, value


async def _plans(db: AsyncConnection, plan_id: uuid.UUID | None = None) -> list[PlanOut]:
    where = "WHERE p.id = :id" if plan_id else ""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT p.*, COALESCE(jsonb_object_agg(pf.feature_key, "  # noqa: S608
                    "  CASE WHEN fc.kind = 'flag' THEN to_jsonb(pf.flag_value) "
                    "       ELSE to_jsonb(pf.limit_value) END) "
                    "  FILTER (WHERE pf.feature_key IS NOT NULL), '{}'::jsonb) AS features "
                    "FROM plan p LEFT JOIN plan_feature pf ON pf.plan_id = p.id "
                    "LEFT JOIN feature_catalog fc ON fc.key = pf.feature_key "
                    f"{where} GROUP BY p.id ORDER BY p.sort_order, p.code"
                ),
                {"id": plan_id},
            )
        )
        .mappings()
        .all()
    )
    return [
        PlanOut(
            **{k: r[k] for k in PlanOut.model_fields if k != "features"},
            features=dict(r["features"]),
        )
        for r in rows
    ]


@router.get("/features", response_model=list[FeatureOut])
async def list_features(ctx: PlatformCtx) -> list[FeatureOut]:
    rows = (
        (
            await ctx.db.execute(
                text(
                    "SELECT key, kind, description, default_flag, default_limit "
                    "FROM feature_catalog ORDER BY sort_order"
                )
            )
        )
        .mappings()
        .all()
    )
    return [FeatureOut(**r) for r in rows]


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(ctx: PlatformCtx) -> list[PlanOut]:
    return await _plans(ctx.db)


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: PlanCreate, ctx: PlatformCtx) -> PlanOut:
    try:
        async with ctx.db.begin_nested():
            plan_id = (
                await ctx.db.execute(
                    text(
                        "INSERT INTO plan (code, name, description, price_monthly_minor, "
                        "price_annual_minor, is_placeholder, is_active, sort_order) VALUES "
                        "(:code, :name, :description, :price_monthly_minor, :price_annual_minor, "
                        ":is_placeholder, :is_active, :sort_order) RETURNING id"
                    ),
                    body.model_dump(),
                )
            ).scalar_one()
    except IntegrityError as exc:
        raise conflict("PLAN_CODE_TAKEN", "A plan with this code already exists") from exc
    await write_platform_audit(
        ctx.db,
        action="plan.created",
        entity_type="plan",
        entity_id=plan_id,
        actor_user_id=ctx.user_id,
        after=body.model_dump(),
        ip=ctx.ip,
    )
    return (await _plans(ctx.db, plan_id))[0]


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def update_plan(plan_id: uuid.UUID, body: PlanPatch, ctx: PlatformCtx) -> PlanOut:
    """Edit name, prices or flags. Existing subscribers keep their snapshot price until
    their subscription is changed (grandfathering)."""
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise unprocessable("NOTHING_TO_UPDATE", "No fields given")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    before = (
        (
            await ctx.db.execute(
                text(
                    "SELECT price_monthly_minor, price_annual_minor, is_active, is_placeholder "
                    "FROM plan WHERE id = :id FOR UPDATE"
                ),
                {"id": plan_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if before is None:
        raise not_found("Plan not found")
    await ctx.db.execute(
        text(f"UPDATE plan SET {sets} WHERE id = :id"),  # noqa: S608 - keys from the model
        {**changes, "id": plan_id},
    )
    await write_platform_audit(
        ctx.db,
        action="plan.updated",
        entity_type="plan",
        entity_id=plan_id,
        actor_user_id=ctx.user_id,
        before=dict(before),
        after=changes,
        ip=ctx.ip,
    )
    return (await _plans(ctx.db, plan_id))[0]


@router.put("/plans/{plan_id}/features", response_model=PlanOut)
async def set_plan_features(plan_id: uuid.UUID, body: PlanFeaturesIn, ctx: PlatformCtx) -> PlanOut:
    db = ctx.db
    if (
        await db.execute(text("SELECT 1 FROM plan WHERE id = :id"), {"id": plan_id})
    ).first() is None:
        raise not_found("Plan not found")
    catalog = await _catalog(db)
    rows = []
    for key, value in body.set.items():
        flag, limit = _check_value(key, catalog.get(key), value)
        rows.append({"p": plan_id, "k": key, "f": flag, "l": limit})
    for key in body.remove:
        if key not in catalog:
            raise unprocessable("UNKNOWN_FEATURE", f"Unknown feature {key}", {"feature": key})
    if rows:
        await db.execute(
            text(
                "INSERT INTO plan_feature (plan_id, feature_key, flag_value, limit_value) "
                "VALUES (:p, :k, :f, :l) ON CONFLICT (plan_id, feature_key) "
                "DO UPDATE SET flag_value = EXCLUDED.flag_value, limit_value = EXCLUDED.limit_value"
            ),
            rows,
        )
    if body.remove:
        await db.execute(
            text("DELETE FROM plan_feature WHERE plan_id = :p AND feature_key = ANY(:keys)"),
            {"p": plan_id, "keys": body.remove},
        )
    await write_platform_audit(
        db,
        action="plan.features_updated",
        entity_type="plan",
        entity_id=plan_id,
        actor_user_id=ctx.user_id,
        after={"set": body.set, "remove": body.remove},
        ip=ctx.ip,
    )
    return (await _plans(db, plan_id))[0]


# --- hotels ---------------------------------------------------------------------------
class SubscriptionOut(BaseModel):
    plan_code: str
    status: str
    billing_interval: str
    price_minor: int
    started_at: datetime
    current_period_end: datetime


class HotelOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    legal_name: str | None
    timezone: str
    status: str
    status_reason: str | None
    created_at: datetime
    subscription: SubscriptionOut | None


class HotelDetailOut(HotelOut):
    tax_profile: TaxProfileOut | None
    member_count: int
    outlet_count: int


class AdminIn(Strict):
    full_name: str = Field(min_length=2, max_length=200)
    login: str = Field(min_length=3, max_length=320, description="Email or mobile number")


class OutletSeed(Strict):
    name: str = Field(default="Main Restaurant", min_length=2, max_length=120)
    code: str = Field(default="MAIN", pattern=r"^[A-Z0-9][A-Z0-9_-]{0,23}$")


def _valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("unknown timezone") from exc
    return value


class HotelCreate(Strict):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
    legal_name: str | None = Field(default=None, max_length=200)
    timezone: str = "Asia/Kolkata"
    plan_code: str = "starter"
    billing_interval: Literal["monthly", "annual"] = "monthly"
    trial_days: int = Field(default=14, ge=0, le=90, description="0 starts a paid period")
    tax_profile: TaxProfileIn
    admin: AdminIn
    first_outlet: OutletSeed = Field(default_factory=OutletSeed)

    _tz = field_validator("timezone")(_valid_timezone)


class HotelCreatedOut(BaseModel):
    hotel: HotelDetailOut
    admin_membership_id: uuid.UUID
    admin_invite: InviteOut


class HotelPatch(Strict):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return None if value is None else _valid_timezone(value)


class ReasonIn(Strict):
    reason: str = Field(min_length=3, max_length=500)


class SubscriptionIn(Strict):
    plan_code: str
    billing_interval: Literal["monthly", "annual"] = "monthly"
    status: Literal["trial", "active"] = "active"
    period_days: int | None = Field(
        default=None,
        ge=1,
        le=800,
        description="Defaults to 14 for trial, 30 monthly, 365 annual",
    )
    note: str | None = Field(default=None, max_length=500)


class OverrideIn(Strict):
    value: FeatureValue = Field(description="true/false for flags; number or null for limits")
    reason: str = Field(min_length=3, max_length=500)


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "hotel"
    return f"{base}-{secrets.token_hex(2)}"


_HOTEL_SELECT = """
    SELECT h.id, h.name, h.slug, h.legal_name, h.timezone, h.status, h.status_reason,
           h.created_at, p.code AS plan_code, s.status AS sub_status, s.billing_interval,
           s.price_minor, s.started_at, s.current_period_end
      FROM hotel h
      LEFT JOIN subscription s ON s.hotel_id = h.id AND s.is_current
      LEFT JOIN plan p ON p.id = s.plan_id
"""


def _hotel_out(row: RowMapping) -> HotelOut:
    sub = None
    if row["plan_code"] is not None:
        sub = SubscriptionOut(
            plan_code=row["plan_code"],
            status=row["sub_status"],
            billing_interval=row["billing_interval"],
            price_minor=row["price_minor"],
            started_at=row["started_at"],
            current_period_end=row["current_period_end"],
        )
    return HotelOut(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        legal_name=row["legal_name"],
        timezone=row["timezone"],
        status=row["status"],
        status_reason=row["status_reason"],
        created_at=row["created_at"],
        subscription=sub,
    )


async def _hotel_detail(ctx: PlatformContext, hotel_id: uuid.UUID) -> HotelDetailOut:
    db = ctx.db
    row = (
        (await db.execute(text(_HOTEL_SELECT + " WHERE h.id = :id"), {"id": hotel_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise not_found("Hotel not found")
    # Reading this hotel's own (RLS) rows: set its context, explicitly and audibly.
    await set_context(db, hotel_id=hotel_id)
    tax = (
        (
            await db.execute(
                text("SELECT * FROM hotel_tax_profile WHERE hotel_id = :h"), {"h": hotel_id}
            )
        )
        .mappings()
        .one_or_none()
    )
    counts = (
        (
            await db.execute(
                text(
                    "SELECT (SELECT count(*) FROM membership WHERE hotel_id = :h "
                    "        AND status <> 'disabled') AS members, "
                    "       (SELECT count(*) FROM outlet WHERE hotel_id = :h "
                    "        AND status = 'active') AS outlets"
                ),
                {"h": hotel_id},
            )
        )
        .mappings()
        .one()
    )
    await set_context(db)
    base = _hotel_out(row)
    return HotelDetailOut(
        **base.model_dump(),
        tax_profile=tax_profile_out(tax) if tax is not None else None,
        member_count=counts["members"],
        outlet_count=counts["outlets"],
    )


@router.get("/hotels", response_model=Page[HotelOut])
async def list_hotels(
    ctx: PlatformCtx,
    status_filter: Annotated[
        Literal["active", "suspended", "cancelled"] | None, Query(alias="status")
    ] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[HotelOut]:
    clauses: list[str] = []
    params: dict[str, object] = {"lim": limit + 1}
    if status_filter:
        clauses.append("h.status = :st")
        params["st"] = status_filter
    if q:
        clauses.append("(h.name ILIKE :q OR h.slug ILIKE :q)")
        params["q"] = f"%{q}%"
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        clauses.append("(h.created_at, h.id) < (:cs, :ci)")
        params.update(cs=stamp, ci=row_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = (
        (
            await ctx.db.execute(
                text(_HOTEL_SELECT + where + " ORDER BY h.created_at DESC, h.id DESC LIMIT :lim"),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = [_hotel_out(r) for r in rows[:limit]]
    nxt = encode_cursor(items[-1].created_at, items[-1].id) if len(rows) > limit else None
    return Page[HotelOut](items=items, next_cursor=nxt)


async def _plan_for(db: AsyncConnection, code: str) -> dict[str, object]:
    plan = (
        (
            await db.execute(
                text(
                    "SELECT id, price_monthly_minor, price_annual_minor, is_active FROM plan "
                    "WHERE code = :c"
                ),
                {"c": code},
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None or not plan["is_active"]:
        raise unprocessable("UNKNOWN_PLAN", f"No active plan {code!r}")
    return dict(plan)


async def _start_subscription(
    db: AsyncConnection,
    *,
    hotel_id: uuid.UUID,
    plan_code: str,
    interval: str,
    sub_status: str,
    period_days: int | None,
    actor: uuid.UUID,
    note: str | None,
) -> None:
    plan = await _plan_for(db, plan_code)
    days = period_days or (14 if sub_status == "trial" else 365 if interval == "annual" else 30)
    price = plan["price_annual_minor"] if interval == "annual" else plan["price_monthly_minor"]
    await db.execute(
        text(
            "UPDATE subscription SET is_current = false, ended_at = now() "
            "WHERE hotel_id = :h AND is_current"
        ),
        {"h": hotel_id},
    )
    await db.execute(
        text(
            "INSERT INTO subscription (hotel_id, plan_id, status, billing_interval, price_minor, "
            "current_period_end, created_by_user_id, note) VALUES (:h, :p, :st, :bi, :pr, "
            "now() + make_interval(days => :d), :by, :n)"
        ),
        {
            "h": hotel_id,
            "p": plan["id"],
            "st": sub_status,
            "bi": interval,
            "pr": price,
            "d": days,
            "by": actor,
            "n": note,
        },
    )


@router.post("/hotels", response_model=HotelCreatedOut, status_code=status.HTTP_201_CREATED)
async def onboard_hotel(body: HotelCreate, ctx: PlatformCtx) -> HotelCreatedOut:
    """Create a hotel with its subscription, tax profile, first outlet and admin invite,
    in one transaction. The invite token is returned once, to share with the admin."""
    db = ctx.db
    slug = body.slug or _slugify(body.name)
    try:
        async with db.begin_nested():
            hotel_id: uuid.UUID = (
                await db.execute(
                    text(
                        "INSERT INTO hotel (name, slug, legal_name, timezone) "
                        "VALUES (:n, :s, :l, :tz) RETURNING id"
                    ),
                    {"n": body.name, "s": slug, "l": body.legal_name, "tz": body.timezone},
                )
            ).scalar_one()
    except IntegrityError as exc:
        raise conflict("SLUG_TAKEN", "Another hotel already uses this slug") from exc

    await _start_subscription(
        db,
        hotel_id=hotel_id,
        plan_code=body.plan_code,
        interval=body.billing_interval,
        sub_status="trial" if body.trial_days > 0 else "active",
        period_days=body.trial_days or None,
        actor=ctx.user_id,
        note="onboarding",
    )
    await set_context(db, hotel_id=hotel_id)
    tax = body.tax_profile
    await db.execute(
        text(
            "INSERT INTO hotel_tax_profile (hotel_id, gst_registration, gstin, legal_name, "
            "state_code, service_charge_applies, service_charge_bp, invoice_prefix, "
            "fy_start_month) VALUES (:h, :gst_registration, :gstin, :legal_name, :state_code, "
            ":service_charge_applies, :service_charge_bp, :invoice_prefix, :fy_start_month)"
        ),
        {"h": hotel_id, **tax.model_dump()},
    )
    ent = await ent_service.load(db, hotel_id, 0)
    if ent.limit("MAX_OUTLETS") != 0:
        kitchen_mode = next(
            (
                m
                for m, f in (
                    ("admin_print", "ADMIN_PRINT_MODE"),
                    ("auto_assign", "AUTO_ROUTING"),
                    ("chef_accept", "CHEF_ACCEPT_MODE"),
                )
                if ent.allows(f)
            ),
            "admin_print",
        )
        await db.execute(
            text("INSERT INTO outlet (hotel_id, name, code, kitchen_mode) VALUES (:h, :n, :c, :k)"),
            {
                "h": hotel_id,
                "n": body.first_outlet.name,
                "c": body.first_outlet.code,
                "k": kitchen_mode,
            },
        )
    user_id = await find_or_create_user(db, full_name=body.admin.full_name, login=body.admin.login)
    membership_id = await add_member(
        db,
        hotel_id=hotel_id,
        user_id=user_id,
        role_id=HOTEL_ADMIN_ROLE_ID,
        outlet_ids=None,
        invited_by=ctx.user_id,
    )
    token, expires_at = await issue_invite(
        db, hotel_id=hotel_id, user_id=user_id, created_by=ctx.user_id
    )
    await write_hotel_audit(
        db,
        hotel_id=hotel_id,
        action="hotel.onboarded",
        entity_type="hotel",
        entity_id=hotel_id,
        actor_user_id=ctx.user_id,
        actor_kind="platform",
        after={"plan_code": body.plan_code, "slug": slug},
        ip=ctx.ip,
    )
    await set_context(db)
    await write_platform_audit(
        db,
        action="hotel.onboarded",
        entity_type="hotel",
        entity_id=hotel_id,
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        after={"plan_code": body.plan_code, "slug": slug},
        ip=ctx.ip,
    )
    return HotelCreatedOut(
        hotel=await _hotel_detail(ctx, hotel_id),
        admin_membership_id=membership_id,
        admin_invite=InviteOut(token=token, expires_at=expires_at),
    )


@router.get("/hotels/{hotel_id}", response_model=HotelDetailOut)
async def get_hotel(hotel_id: uuid.UUID, ctx: PlatformCtx) -> HotelDetailOut:
    return await _hotel_detail(ctx, hotel_id)


@router.patch("/hotels/{hotel_id}", response_model=HotelDetailOut)
async def update_hotel(hotel_id: uuid.UUID, body: HotelPatch, ctx: PlatformCtx) -> HotelDetailOut:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise unprocessable("NOTHING_TO_UPDATE", "No fields given")
    sets = ", ".join(f"{k} = :{k}" for k in changes)
    done = (
        await ctx.db.execute(
            text(f"UPDATE hotel SET {sets} WHERE id = :id RETURNING id"),  # noqa: S608
            {**changes, "id": hotel_id},
        )
    ).scalar_one_or_none()
    if done is None:
        raise not_found("Hotel not found")
    await write_platform_audit(
        ctx.db,
        action="hotel.updated",
        entity_type="hotel",
        entity_id=hotel_id,
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        after=changes,
        ip=ctx.ip,
    )
    return await _hotel_detail(ctx, hotel_id)


async def _set_status(
    ctx: PlatformContext,
    hotel_id: uuid.UUID,
    new: str,
    reason: str | None,
    allowed_from: tuple[str, ...],
    action: str,
) -> HotelDetailOut:
    old = (
        await ctx.db.execute(
            text("SELECT status FROM hotel WHERE id = :id FOR UPDATE"), {"id": hotel_id}
        )
    ).scalar()
    if old is None:
        raise not_found("Hotel not found")
    if old not in allowed_from:
        raise conflict("INVALID_STATUS_CHANGE", f"Hotel is {old}", {"status": old})
    await ctx.db.execute(
        text("UPDATE hotel SET status = :s, status_reason = :r WHERE id = :id"),
        {"s": new, "r": reason, "id": hotel_id},
    )
    await write_platform_audit(
        ctx.db,
        action=action,
        entity_type="hotel",
        entity_id=hotel_id,
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        before={"status": old},
        after={"status": new},
        note=reason,
        ip=ctx.ip,
    )
    return await _hotel_detail(ctx, hotel_id)


@router.post("/hotels/{hotel_id}/suspend", response_model=HotelDetailOut)
async def suspend_hotel(hotel_id: uuid.UUID, body: ReasonIn, ctx: PlatformCtx) -> HotelDetailOut:
    """Suspended hotels stay readable (for export) but every write is refused."""
    return await _set_status(
        ctx, hotel_id, "suspended", body.reason, ("active",), "hotel.suspended"
    )


@router.post("/hotels/{hotel_id}/resume", response_model=HotelDetailOut)
async def resume_hotel(hotel_id: uuid.UUID, ctx: PlatformCtx) -> HotelDetailOut:
    return await _set_status(ctx, hotel_id, "active", None, ("suspended",), "hotel.resumed")


@router.put("/hotels/{hotel_id}/subscription", response_model=HotelDetailOut)
async def change_subscription(
    hotel_id: uuid.UUID, body: SubscriptionIn, ctx: PlatformCtx
) -> HotelDetailOut:
    """Move a hotel to a plan. The current subscription is closed and a new one starts at
    the plan's current price. Downgrades never delete data (docs/05)."""
    if (
        await ctx.db.execute(
            text("SELECT 1 FROM hotel WHERE id = :id FOR UPDATE"), {"id": hotel_id}
        )
    ).first() is None:
        raise not_found("Hotel not found")
    await _start_subscription(
        ctx.db,
        hotel_id=hotel_id,
        plan_code=body.plan_code,
        interval=body.billing_interval,
        sub_status=body.status,
        period_days=body.period_days,
        actor=ctx.user_id,
        note=body.note,
    )
    await write_platform_audit(
        ctx.db,
        action="subscription.changed",
        entity_type="subscription",
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        after=body.model_dump(),
        ip=ctx.ip,
    )
    return await _hotel_detail(ctx, hotel_id)


@router.get("/hotels/{hotel_id}/entitlements", response_model=EntitlementsOut)
async def hotel_entitlements(hotel_id: uuid.UUID, ctx: PlatformCtx) -> EntitlementsOut:
    version = (
        await ctx.db.execute(
            text("SELECT entitlements_version FROM hotel WHERE id = :id"), {"id": hotel_id}
        )
    ).scalar()
    if version is None:
        raise not_found("Hotel not found")
    ent = await ent_service.load(ctx.db, hotel_id, int(version))
    return EntitlementsOut(
        plan_code=ent.plan_code,
        subscription_status=ent.subscription_status,
        version=ent.version,
        flags=dict(ent.flags),
        limits=dict(ent.limits),
        sources=dict(ent.sources),
    )


@router.put("/hotels/{hotel_id}/overrides/{feature_key}", response_model=EntitlementsOut)
async def set_override(
    hotel_id: uuid.UUID, feature_key: str, body: OverrideIn, ctx: PlatformCtx
) -> EntitlementsOut:
    """Grant or restrict one feature for one hotel, whatever its plan says. Audited."""
    db = ctx.db
    if (
        await db.execute(text("SELECT 1 FROM hotel WHERE id = :id"), {"id": hotel_id})
    ).first() is None:
        raise not_found("Hotel not found")
    catalog = await _catalog(db)
    flag, limit = _check_value(feature_key, catalog.get(feature_key), body.value)
    await db.execute(
        text(
            "INSERT INTO hotel_entitlement_override (hotel_id, feature_key, flag_value, "
            "limit_value, reason, set_by_user_id) VALUES (:h, :k, :f, :l, :r, :by) "
            "ON CONFLICT (hotel_id, feature_key) DO UPDATE SET flag_value = EXCLUDED.flag_value, "
            "limit_value = EXCLUDED.limit_value, reason = EXCLUDED.reason, "
            "set_by_user_id = EXCLUDED.set_by_user_id"
        ),
        {
            "h": hotel_id,
            "k": feature_key,
            "f": flag,
            "l": limit,
            "r": body.reason,
            "by": ctx.user_id,
        },
    )
    await write_platform_audit(
        db,
        action="entitlement.override_set",
        entity_type="hotel",
        entity_id=hotel_id,
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        after={"feature": feature_key, "value": body.value},
        note=body.reason,
        ip=ctx.ip,
    )
    return await hotel_entitlements(hotel_id, ctx)


@router.delete("/hotels/{hotel_id}/overrides/{feature_key}", response_model=EntitlementsOut)
async def remove_override(
    hotel_id: uuid.UUID, feature_key: str, ctx: PlatformCtx
) -> EntitlementsOut:
    removed = (
        await ctx.db.execute(
            text(
                "DELETE FROM hotel_entitlement_override WHERE hotel_id = :h AND feature_key = :k "
                "RETURNING feature_key"
            ),
            {"h": hotel_id, "k": feature_key},
        )
    ).scalar_one_or_none()
    if removed is None:
        raise not_found("No override for this feature")
    await write_platform_audit(
        ctx.db,
        action="entitlement.override_removed",
        entity_type="hotel",
        entity_id=hotel_id,
        hotel_id=hotel_id,
        actor_user_id=ctx.user_id,
        after={"feature": feature_key},
        ip=ctx.ip,
    )
    return await hotel_entitlements(hotel_id, ctx)


class PlatformAuditOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    hotel_id: uuid.UUID | None
    before_json: dict[str, object] | None
    after_json: dict[str, object] | None
    note: str | None
    occurred_at: datetime


@router.get("/audit", response_model=Page[PlatformAuditOut])
async def platform_audit(
    ctx: PlatformCtx,
    hotel_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[PlatformAuditOut]:
    clauses: list[str] = []
    params: dict[str, object] = {"lim": limit + 1}
    if hotel_id:
        clauses.append("hotel_id = :h")
        params["h"] = hotel_id
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        clauses.append("(occurred_at, id) < (:cs, :ci)")
        params.update(cs=stamp, ci=row_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = (
        (
            await ctx.db.execute(
                text(
                    "SELECT id, actor_user_id, action, entity_type, entity_id, hotel_id, before_json, "  # noqa: S608
                    f"after_json, note, occurred_at FROM platform_audit_log{where} "
                    "ORDER BY occurred_at DESC, id DESC LIMIT :lim"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = [PlatformAuditOut(**r) for r in rows[:limit]]
    nxt = encode_cursor(items[-1].occurred_at, items[-1].id) if len(rows) > limit else None
    return Page[PlatformAuditOut](items=items, next_cursor=nxt)


__all__ = ["AppError", "router"]
