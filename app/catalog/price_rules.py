"""Happy hour price rules (docs/03 "Happy hour", docs/14). Applied in ordering.service.quote for every channel."""

from datetime import datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx, HotelCtx
from app.core.errors import not_found
from app.core.security import now
from app.models import Hotel, MenuCategory, MenuItem, PriceRule
from app.ordering import pricing

router = APIRouter(tags=["catalog"])


def rule_row(r: PriceRule) -> pricing.PriceRuleRow:
    cats = frozenset(str(c) for c in r.category_ids) if r.category_ids is not None else None
    return pricing.PriceRuleRow(r.name, r.percent_bp, r.days, r.start_time, r.end_time, cats)


async def hotel_now(db: AsyncSession) -> datetime:
    tz = (await db.execute(select(Hotel.timezone))).scalar_one()
    return now().astimezone(ZoneInfo(tz)).replace(tzinfo=None)


async def offers_now(db: AsyncSession, items: list[MenuItem]) -> dict[UUID, dict]:
    """The rule live right now for each dish, for a badge ("Happy hour -20%"); the price itself is the quote's."""
    rules = [rule_row(r) for r in (await db.execute(select(PriceRule).where(PriceRule.is_active))).scalars()]
    if not rules:
        return {}
    at = await hotel_now(db)
    out = {}
    for i in items:
        r = pricing.best_rule(rules, at, str(i.category_id))
        if r:
            out[i.id] = {"name": r.name, "percent_bp": r.percent_bp, "until": r.end.strftime("%H:%M")}
    return out


def rule_json(r: PriceRule) -> dict:
    return {
        "id": str(r.id),
        "name": r.name,
        "percent_bp": r.percent_bp,
        "days": r.days,
        "start_time": r.start_time.strftime("%H:%M"),
        "end_time": r.end_time.strftime("%H:%M"),
        "category_ids": [str(c) for c in r.category_ids] if r.category_ids is not None else None,
        "is_active": r.is_active,
    }


class RuleIn(BaseModel):
    """`days`: Monday = 1, Tuesday = 2, Wednesday = 4 ... Sunday = 64 (127 = every day). Times are the hotel's local
    times; an end before the start crosses midnight. `category_ids` null = the whole menu."""

    name: str = Field(min_length=1, max_length=60)
    percent_bp: int = Field(ge=1, le=9000)
    days: int = Field(default=127, ge=1, le=127)
    start_time: time
    end_time: time
    category_ids: list[UUID] | None = Field(default=None, max_length=100)
    is_active: bool = True

    @model_validator(mode="after")
    def _window(self) -> "RuleIn":
        if self.start_time == self.end_time:
            raise ValueError("The start and end times must differ")
        if self.category_ids is not None and not self.category_ids:
            raise ValueError("Pick at least one category, or leave it empty for the whole menu")
        return self


async def _check_categories(ctx: Ctx, ids: list[UUID] | None) -> None:
    if ids:
        found = set((await ctx.db.execute(select(MenuCategory.id).where(MenuCategory.id.in_(ids)))).scalars())
        if found != set(ids):
            raise not_found("Category")


@router.get("/price-rules")
async def list_rules(ctx: Ctx = HotelCtx()) -> list[dict]:
    rules = (await ctx.db.execute(select(PriceRule).order_by(PriceRule.start_time, PriceRule.name))).scalars()
    return [rule_json(r) for r in rules]


@router.post("/price-rules", status_code=201)
async def create_rule(body: RuleIn, ctx: Ctx = HotelCtx("menu.manage")) -> dict:
    await _check_categories(ctx, body.category_ids)
    r = PriceRule(hotel_id=ctx.hotel_id, **body.model_dump())
    ctx.db.add(r)
    await ctx.db.flush()
    await ctx.audit("price_rule.create", "price_rule", r.id, after=rule_json(r))
    return rule_json(r)


@router.put("/price-rules/{rule_id}")
async def update_rule(rule_id: UUID, body: RuleIn, ctx: Ctx = HotelCtx("menu.manage")) -> dict:
    r = await ctx.db.get(PriceRule, rule_id)
    if not r:
        raise not_found("Price rule")
    await _check_categories(ctx, body.category_ids)
    before = rule_json(r)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    await ctx.db.flush()
    await ctx.audit("price_rule.update", "price_rule", r.id, before=before, after=rule_json(r))
    return rule_json(r)


@router.delete("/price-rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: UUID, ctx: Ctx = HotelCtx("menu.manage")) -> None:
    """Orders already placed keep the price they were charged."""
    r = await ctx.db.get(PriceRule, rule_id)
    if not r:
        raise not_found("Price rule")
    await ctx.audit("price_rule.delete", "price_rule", r.id, before=rule_json(r))
    await ctx.db.delete(r)
