"""Super admin API (docs/06 Platform) and plan billing via Razorpay subscriptions (docs/05 Lifecycle)."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.deps import Ctx, HotelCtx, PlatformCtx
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.security import now
from app.models import (
    AuditLog,
    DiningTable,
    EntitlementOverride,
    Hotel,
    Order,
    Plan,
    PlanFeature,
    Subscription,
    SubscriptionInvoice,
    User,
    WebhookEvent,
)
from app.platform.features import BOOL_FEATURES, LIMITS
from app.platform.gstin import is_valid_gstin
from app.platform.lifecycle import effective_status
from app.platform.provision import provision_hotel

router = APIRouter(prefix="/platform", tags=["platform"])
billing = APIRouter(tags=["billing"])


def _eff(sub: Subscription | None) -> str:
    if not sub:
        return "SUSPENDED"
    return effective_status(sub.status, now(), sub.trial_ends_at, sub.current_period_end, get_settings().grace_days)


def _monthly_minor(plan: Plan) -> int:
    return plan.price_minor if plan.interval == "MONTHLY" else plan.price_minor // 12


# ------------------------------------------------------------------- summary --


@router.get("/summary")
async def summary(ctx: Ctx = PlatformCtx) -> dict:
    db = ctx.db
    subs = (await db.execute(select(Subscription, Plan).join(Plan, Plan.id == Subscription.plan_id))).all()
    by_status: dict[str, int] = {}
    mrr = 0
    by_plan: dict[str, int] = {}
    trials_ending = 0
    for sub, plan in subs:
        st = _eff(sub)
        by_status[st] = by_status.get(st, 0) + 1
        by_plan[plan.name] = by_plan.get(plan.name, 0) + 1
        if st == "ACTIVE":
            mrr += _monthly_minor(plan)
        if st == "TRIAL" and sub.trial_ends_at and sub.trial_ends_at < now() + timedelta(days=7):
            trials_ending += 1
    since = now() - timedelta(days=30)
    orders_30d = (await db.execute(select(func.count()).select_from(Order).where(Order.created_at >= since))).scalar()
    collected = (
        await db.execute(
            select(func.coalesce(func.sum(SubscriptionInvoice.amount_minor), 0)).where(
                SubscriptionInvoice.status == "PAID", SubscriptionInvoice.created_at >= since
            )
        )
    ).scalar()
    # Sign-ups per month, last 6 months.
    month = func.date_trunc("month", Hotel.created_at)
    signups = (
        await db.execute(
            select(month, func.count())
            .where(Hotel.created_at >= now() - timedelta(days=183))
            .group_by(month)
            .order_by(month)
        )
    ).all()
    return {
        "hotels_total": len(subs),
        "by_status": by_status,
        "by_plan": by_plan,
        "mrr_minor": mrr,
        "collected_30d_minor": int(collected),
        "orders_30d": orders_30d,
        "trials_ending_7d": trials_ending,
        "signups": [{"month": m.date().isoformat(), "count": c} for m, c in signups],
    }


# --------------------------------------------------------------------- plans --


class PlanOut(BaseModel):
    id: UUID
    code: str
    name: str
    price_minor: int
    interval: str
    is_placeholder: bool
    is_active: bool
    features: dict[str, bool]
    limits: dict[str, int | None]


class PlanPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    price_minor: int | None = Field(default=None, ge=0)
    is_active: bool | None = None
    features: dict[str, bool] | None = None
    limits: dict[str, int | None] | None = None


async def _plan_out(db, plan: Plan) -> PlanOut:
    rows = (await db.execute(select(PlanFeature).where(PlanFeature.plan_id == plan.id))).scalars().all()
    m = {r.feature_key: r for r in rows}
    return PlanOut(
        id=plan.id,
        code=plan.code,
        name=plan.name,
        price_minor=plan.price_minor,
        interval=plan.interval,
        is_placeholder=plan.is_placeholder,
        is_active=plan.is_active,
        features={k: bool(m[k].bool_value) if k in m else False for k in BOOL_FEATURES},
        limits={k: (m[k].int_limit if k in m else 0) for k in LIMITS},
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(ctx: Ctx = PlatformCtx) -> list[PlanOut]:
    plans = (await ctx.db.execute(select(Plan).order_by(Plan.sort))).scalars().all()
    return [await _plan_out(ctx.db, p) for p in plans]


@router.patch("/plans/{plan_id}", response_model=PlanOut)
async def patch_plan(plan_id: UUID, body: PlanPatch, ctx: Ctx = PlatformCtx) -> PlanOut:
    """Price changes apply to new subscriptions; existing subscribers keep theirs until renewal (docs/05)."""
    plan = await ctx.db.get(Plan, plan_id)
    if not plan:
        raise not_found("Plan")
    before = (await _plan_out(ctx.db, plan)).model_dump(mode="json")
    if body.name is not None:
        plan.name = body.name
    if body.is_active is not None:
        plan.is_active = body.is_active
    if body.price_minor is not None and body.price_minor != plan.price_minor:
        plan.price_minor = body.price_minor
        plan.is_placeholder = False
        plan.razorpay_plan_id = None  # Razorpay plans are immutable; a new one is created on next checkout.
    for key, val in (body.features or {}).items():
        if key not in BOOL_FEATURES:
            raise bad_request("unknown_feature", f"Unknown feature {key}")
        row = await ctx.db.get(PlanFeature, (plan.id, key))
        if row:
            row.bool_value = val
        else:
            ctx.db.add(PlanFeature(plan_id=plan.id, feature_key=key, bool_value=val))
    for key, val in (body.limits or {}).items():
        if key not in LIMITS:
            raise bad_request("unknown_limit", f"Unknown limit {key}")
        row = await ctx.db.get(PlanFeature, (plan.id, key))
        if row:
            row.int_limit = val
        else:
            ctx.db.add(PlanFeature(plan_id=plan.id, feature_key=key, int_limit=val))
    await ctx.db.flush()
    out = await _plan_out(ctx.db, plan)
    await ctx.audit("plan.update", "plan", plan.id, before=before, after=out.model_dump(mode="json"))
    return out


# -------------------------------------------------------------------- hotels --


class AdminIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class HotelIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    legal_name: str | None = Field(default=None, max_length=160)
    city: str | None = Field(default=None, max_length=80)
    contact_phone: str | None = Field(default=None, max_length=20)
    plan_code: str
    gst_registration: str = Field(default="UNREGISTERED", pattern="^(REGULAR|COMPOSITION|UNREGISTERED)$")
    gstin: str | None = None
    state_code: str | None = Field(default=None, pattern=r"^\d{2}$")
    service_charge_bp: int = Field(default=0, ge=0, le=2000)
    admin: AdminIn

    @model_validator(mode="after")
    def _gst(self):
        if self.gst_registration in ("REGULAR", "COMPOSITION"):
            if not self.gstin or not is_valid_gstin(self.gstin):
                raise ValueError("A valid GSTIN is required for registered hotels")
            self.gstin = self.gstin.upper()
            self.state_code = self.gstin[:2]
        return self


class HotelOut(BaseModel):
    id: UUID
    name: str
    city: str | None
    contact_email: str | None
    status: str
    plan_code: str | None
    plan_name: str | None
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    tables: int
    orders_30d: int
    created_at: datetime


async def _hotel_rows(db, hotel_id: UUID | None = None) -> list[HotelOut]:
    since = now() - timedelta(days=30)
    tables = select(DiningTable.hotel_id, func.count().label("n")).group_by(DiningTable.hotel_id).subquery()
    orders = (
        select(Order.hotel_id, func.count().label("n"))
        .where(Order.created_at >= since)
        .group_by(Order.hotel_id)
        .subquery()
    )
    q = (
        select(Hotel, Subscription, Plan, tables.c.n, orders.c.n)
        .outerjoin(Subscription, Subscription.hotel_id == Hotel.id)
        .outerjoin(Plan, Plan.id == Subscription.plan_id)
        .outerjoin(tables, tables.c.hotel_id == Hotel.id)
        .outerjoin(orders, orders.c.hotel_id == Hotel.id)
        .order_by(Hotel.created_at.desc())
    )
    if hotel_id:
        q = q.where(Hotel.id == hotel_id)
    out = []
    for h, sub, plan, nt, no in (await db.execute(q)).all():
        out.append(
            HotelOut(
                id=h.id,
                name=h.name,
                city=h.city,
                contact_email=h.contact_email,
                status=_eff(sub),
                plan_code=plan.code if plan else None,
                plan_name=plan.name if plan else None,
                trial_ends_at=sub.trial_ends_at if sub else None,
                current_period_end=sub.current_period_end if sub else None,
                tables=nt or 0,
                orders_30d=no or 0,
                created_at=h.created_at,
            )
        )
    return out


@router.get("/hotels", response_model=list[HotelOut])
async def list_hotels(ctx: Ctx = PlatformCtx) -> list[HotelOut]:
    return await _hotel_rows(ctx.db)


@router.post("/hotels", response_model=HotelOut, status_code=201)
async def create_hotel(body: HotelIn, ctx: Ctx = PlatformCtx) -> HotelOut:
    plan = (await ctx.db.execute(select(Plan).where(Plan.code == body.plan_code))).scalar_one_or_none()
    if not plan or not plan.is_active:
        raise bad_request("unknown_plan", "Plan not found or inactive")
    if (await ctx.db.execute(select(User.id).where(User.email == body.admin.email.lower()))).first():
        raise conflict("email_taken", "This email is already registered")
    hotel, _ = await provision_hotel(
        ctx.db,
        name=body.name,
        plan=plan,
        admin_name=body.admin.name,
        admin_email=body.admin.email,
        admin_password=body.admin.password,
        legal_name=body.legal_name,
        city=body.city,
        contact_phone=body.contact_phone,
        gst_registration=body.gst_registration,
        gstin=body.gstin,
        state_code=body.state_code,
        service_charge_bp=body.service_charge_bp,
    )
    ctx.db.add(
        AuditLog(
            hotel_id=hotel.id,
            actor_user_id=ctx.user_id,
            action="hotel.create",
            entity="hotel",
            entity_id=str(hotel.id),
            after={"name": hotel.name, "plan": plan.code},
        )
    )
    return (await _hotel_rows(ctx.db, hotel.id))[0]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


async def _set_status(ctx: Ctx, hotel_id: UUID, status: str, reason: str) -> HotelOut:
    sub = (await ctx.db.execute(select(Subscription).where(Subscription.hotel_id == hotel_id))).scalar_one_or_none()
    hotel = await ctx.db.get(Hotel, hotel_id)
    if not sub or not hotel:
        raise not_found("Hotel")
    before = sub.status
    if status == "ACTIVE" and before == "SUSPENDED":
        # Manual resume (comp): extend 30 days, audited.
        sub.status = "ACTIVE"
        sub.current_period_end = max(sub.current_period_end or now(), now()) + timedelta(days=30)
    else:
        sub.status = status
    hotel.status = "SUSPENDED" if status == "SUSPENDED" else "ACTIVE"
    ctx.db.add(
        AuditLog(
            hotel_id=hotel_id,
            actor_user_id=ctx.user_id,
            action=f"hotel.{status.lower()}",
            entity="hotel",
            entity_id=str(hotel_id),
            before={"status": before},
            after={"status": sub.status},
            reason=reason,
        )
    )
    await ctx.db.flush()
    return (await _hotel_rows(ctx.db, hotel_id))[0]


@router.post("/hotels/{hotel_id}/suspend", response_model=HotelOut)
async def suspend(hotel_id: UUID, body: ReasonIn, ctx: Ctx = PlatformCtx) -> HotelOut:
    return await _set_status(ctx, hotel_id, "SUSPENDED", body.reason)


@router.post("/hotels/{hotel_id}/resume", response_model=HotelOut)
async def resume(hotel_id: UUID, body: ReasonIn, ctx: Ctx = PlatformCtx) -> HotelOut:
    return await _set_status(ctx, hotel_id, "ACTIVE", body.reason)


class AssignPlanIn(BaseModel):
    plan_code: str
    reason: str = Field(min_length=3, max_length=500)


@router.post("/hotels/{hotel_id}/subscription", response_model=HotelOut)
async def assign_plan(hotel_id: UUID, body: AssignPlanIn, ctx: Ctx = PlatformCtx) -> HotelOut:
    sub = (await ctx.db.execute(select(Subscription).where(Subscription.hotel_id == hotel_id))).scalar_one_or_none()
    plan = (await ctx.db.execute(select(Plan).where(Plan.code == body.plan_code))).scalar_one_or_none()
    if not sub or not plan:
        raise not_found("Hotel or plan")
    before = str(sub.plan_id)
    sub.plan_id = plan.id
    ctx.db.add(
        AuditLog(
            hotel_id=hotel_id,
            actor_user_id=ctx.user_id,
            action="hotel.plan",
            entity="subscription",
            entity_id=str(sub.id),
            before={"plan_id": before},
            after={"plan": plan.code},
            reason=body.reason,
        )
    )
    await ctx.db.flush()
    return (await _hotel_rows(ctx.db, hotel_id))[0]


class OverrideIn(BaseModel):
    feature_key: str
    bool_value: bool | None = None
    int_limit: int | None = None
    clear: bool = False
    reason: str = Field(min_length=3, max_length=500)


@router.get("/hotels/{hotel_id}/overrides")
async def list_overrides(hotel_id: UUID, ctx: Ctx = PlatformCtx) -> list[dict]:
    rows = (await ctx.db.execute(select(EntitlementOverride).where(EntitlementOverride.hotel_id == hotel_id))).scalars()
    return [
        {"feature_key": r.feature_key, "bool_value": r.bool_value, "int_limit": r.int_limit, "reason": r.reason}
        for r in rows
    ]


@router.put("/hotels/{hotel_id}/overrides")
async def set_override(hotel_id: UUID, body: OverrideIn, ctx: Ctx = PlatformCtx) -> list[dict]:
    if body.feature_key not in BOOL_FEATURES and body.feature_key not in LIMITS:
        raise bad_request("unknown_feature", "Unknown feature")
    row = await ctx.db.get(EntitlementOverride, (hotel_id, body.feature_key))
    if body.clear:
        if row:
            await ctx.db.delete(row)
    elif row:
        row.bool_value, row.int_limit, row.reason, row.set_by = (
            body.bool_value,
            body.int_limit,
            body.reason,
            ctx.user_id,
        )
    else:
        ctx.db.add(
            EntitlementOverride(
                hotel_id=hotel_id,
                feature_key=body.feature_key,
                bool_value=body.bool_value,
                int_limit=body.int_limit,
                reason=body.reason,
                set_by=ctx.user_id,
            )
        )
    ctx.db.add(
        AuditLog(
            hotel_id=hotel_id,
            actor_user_id=ctx.user_id,
            action="hotel.override",
            entity="override",
            entity_id=body.feature_key,
            after=body.model_dump(),
            reason=body.reason,
        )
    )
    await ctx.db.flush()
    return await list_overrides(hotel_id, ctx)


# ------------------------------------------------- hotel-side billing (tenant) --


@billing.get("/billing/subscription")
async def my_subscription(ctx: Ctx = HotelCtx("settings.manage")) -> dict:
    sub = (await ctx.db.execute(select(Subscription))).scalar_one()
    plan = await ctx.db.get(Plan, sub.plan_id)
    plans = (await ctx.db.execute(select(Plan).where(Plan.is_active).order_by(Plan.sort))).scalars().all()
    invoices = (
        await ctx.db.execute(select(SubscriptionInvoice).order_by(SubscriptionInvoice.created_at.desc()).limit(24))
    ).scalars()
    return {
        "status": ctx.entitlements["status"],
        "plan": {"code": plan.code, "name": plan.name, "price_minor": plan.price_minor, "interval": plan.interval},
        "trial_ends_at": sub.trial_ends_at,
        "current_period_end": sub.current_period_end,
        "checkout_url": sub.checkout_url,
        "payments_enabled": bool(get_settings().razorpay_key_id),
        "plans": [
            {"code": p.code, "name": p.name, "price_minor": p.price_minor, "interval": p.interval} for p in plans
        ],
        "invoices": [
            {
                "id": str(i.id),
                "amount_minor": i.amount_minor,
                "status": i.status,
                "created_at": i.created_at,
                "period_end": i.period_end,
            }
            for i in invoices
        ],
    }


class CheckoutIn(BaseModel):
    plan_code: str


async def _razorpay(method: str, path: str, payload: dict) -> dict:
    s = get_settings()
    async with httpx.AsyncClient(
        base_url="https://api.razorpay.com/v1", timeout=20, auth=(s.razorpay_key_id, s.razorpay_key_secret)
    ) as c:
        r = await c.request(method, path, json=payload)
    if r.status_code >= 300:
        raise ApiError(
            502, "payment_provider_error", "Payment provider rejected the request", {"status": r.status_code}
        )
    return r.json()


@billing.post("/billing/subscription/checkout")
async def checkout(body: CheckoutIn, ctx: Ctx = HotelCtx("settings.manage")) -> dict:
    """Creates a Razorpay subscription and returns its hosted payment page. Activation arrives by webhook."""
    if not get_settings().razorpay_key_id:
        raise ApiError(503, "payments_not_configured", "Online payment is not configured yet")
    sub = (await ctx.db.execute(select(Subscription))).scalar_one()
    hotel_id, sub_id = ctx.hotel_id, sub.id
    async with platform_session() as pdb:  # plans.razorpay_plan_id is platform-owned
        plan = (await pdb.execute(select(Plan).where(Plan.code == body.plan_code, Plan.is_active))).scalar_one_or_none()
        if not plan:
            raise bad_request("unknown_plan", "Plan not found")
        if not plan.razorpay_plan_id:
            rp = await _razorpay(
                "POST",
                "/plans",
                {
                    "period": "monthly" if plan.interval == "MONTHLY" else "yearly",
                    "interval": 1,
                    "item": {"name": plan.name, "amount": plan.price_minor, "currency": "INR"},
                },
            )
            plan.razorpay_plan_id = rp["id"]
        rs = await _razorpay(
            "POST",
            "/subscriptions",
            {
                "plan_id": plan.razorpay_plan_id,
                "total_count": 120 if plan.interval == "MONTHLY" else 10,
                "customer_notify": 1,
                "notes": {"hotel_id": str(hotel_id), "plan_code": plan.code},
            },
        )
        psub = await pdb.get(Subscription, sub_id)
        psub.razorpay_sub_id, psub.checkout_url = rs["id"], rs.get("short_url")
        psub.plan_id = plan.id
    await ctx.audit("subscription.checkout", "subscription", sub_id, after={"plan": body.plan_code})
    return {"checkout_url": rs.get("short_url"), "razorpay_subscription_id": rs["id"]}


def verify_razorpay_signature(raw: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@billing.post("/webhooks/razorpay", status_code=200)
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    x_razorpay_event_id: str | None = Header(default=None),
) -> dict:
    raw = await request.body()
    if not verify_razorpay_signature(raw, x_razorpay_signature, get_settings().razorpay_webhook_secret):
        raise ApiError(401, "bad_signature", "Signature check failed")
    payload = json.loads(raw)
    event = payload.get("event", "")
    event_id = x_razorpay_event_id or payload.get("id") or hashlib.sha256(raw).hexdigest()
    async with platform_session() as db:
        if await db.get(WebhookEvent, event_id):
            return {"status": "duplicate"}  # idempotent on event id
        db.add(WebhookEvent(id=event_id, provider="razorpay", event=event))
        ent = (payload.get("payload", {}).get("subscription") or {}).get("entity") or {}
        sub = None
        if ent.get("id"):
            sub = (
                await db.execute(select(Subscription).where(Subscription.razorpay_sub_id == ent["id"]))
            ).scalar_one_or_none()
        if not sub:
            return {"status": "ignored"}
        hotel = await db.get(Hotel, sub.hotel_id)
        period_end = datetime.fromtimestamp(ent["current_end"], UTC) if ent.get("current_end") else None
        if event in ("subscription.activated", "subscription.charged", "subscription.resumed"):
            sub.status, hotel.status = "ACTIVE", "ACTIVE"
            if period_end:
                sub.current_period_end = period_end
            pay = (payload["payload"].get("payment") or {}).get("entity")
            if event == "subscription.charged" and pay:
                exists = (
                    await db.execute(
                        select(SubscriptionInvoice.id).where(SubscriptionInvoice.provider_ref == pay["id"])
                    )
                ).first()
                if not exists:
                    db.add(
                        SubscriptionInvoice(
                            hotel_id=sub.hotel_id,
                            subscription_id=sub.id,
                            amount_minor=int(pay["amount"]),
                            status="PAID",
                            provider_ref=pay["id"],
                            period_end=period_end,
                        )
                    )
        elif event in ("subscription.pending", "subscription.halted"):
            sub.status = "PAST_DUE"
        elif event in ("subscription.cancelled", "subscription.completed"):
            sub.status = "CANCELLED"
        db.add(
            AuditLog(
                hotel_id=sub.hotel_id,
                action=f"webhook.{event}",
                entity="subscription",
                entity_id=str(sub.id),
                after={"status": sub.status},
            )
        )
    return {"status": "ok"}
