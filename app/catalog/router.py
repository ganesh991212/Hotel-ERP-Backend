from datetime import time
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.catalog.options import options_for, station_for_items, station_json, with_options
from app.catalog.price_rules import hotel_now, offers_now
from app.core.deps import Ctx, HotelCtx, count
from app.core.errors import bad_request, not_found
from app.floor.router import current_outlet, outlet_channel
from app.models import MenuCategory, MenuItem, MenuItemComponent, OrderItem
from app.ordering.pricing import in_window

router = APIRouter(tags=["catalog"])


class CategoryOut(BaseModel):
    id: UUID
    name: str
    sort: int
    # Serving hours (docs/03 "Serving hours"): Monday = 1 ... Sunday = 64; no times = all day.
    days: int = 127
    available_from: str | None = None
    available_to: str | None = None
    open_now: bool = True
    station_id: UUID | None = None  # its dishes' kitchen station (docs/03 "Kitchen stations")


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort: int = 0
    days: int = Field(default=127, ge=1, le=127)
    available_from: time | None = None
    available_to: time | None = None
    station_id: UUID | None = None

    @model_validator(mode="after")
    def _hours(self) -> "CategoryIn":
        if (self.available_from is None) != (self.available_to is None):
            raise ValueError("Give both serving times, or neither (served all day)")
        if self.available_from is not None and self.available_from == self.available_to:
            raise ValueError("The start and end times must differ")
        return self


def category_out(c: MenuCategory, at) -> CategoryOut:
    return CategoryOut(
        id=c.id,
        name=c.name,
        sort=c.sort,
        days=c.days,
        available_from=c.available_from.strftime("%H:%M") if c.available_from else None,
        available_to=c.available_to.strftime("%H:%M") if c.available_to else None,
        open_now=in_window(c.days, c.available_from, c.available_to, at),
        station_id=c.station_id,
    )


class ItemOut(BaseModel):
    id: UUID
    category_id: UUID
    name: str
    description: str | None
    is_veg: bool
    price_minor: int
    tax_code: str
    est_prep_minutes: int
    is_available: bool
    sort: int
    # Sizes and add-on groups (docs/03 "Variants and add-ons"); managed through /items/{id}/variants and
    # /items/{id}/addon-groups.
    variants: list[dict] = []
    addon_groups: list[dict] = []
    components: list[dict] = []  # a combo's dishes (docs/03 "Combos"); empty for ordinary items
    # A happy-hour rule live right now for this dish (a badge; the price comes from the quote).
    offer: dict | None = None
    station_id: UUID | None = None  # the dish's own station (overrides its category's)
    station: dict | None = None  # where it actually goes: own, else category's; None = main kitchen


class ItemIn(BaseModel):
    category_id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_veg: bool = True
    price_minor: int = Field(ge=0, le=100_000_000)
    tax_code: str = Field(default="FOOD", max_length=20)
    est_prep_minutes: int = Field(default=10, ge=1, le=240)
    is_available: bool = True
    sort: int = 0
    station_id: UUID | None = None


class ItemPatch(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_veg: bool | None = None
    price_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    est_prep_minutes: int | None = Field(default=None, ge=1, le=240)
    sort: int | None = None
    station_id: UUID | None = None  # send null to follow the category again


class AvailabilityIn(BaseModel):
    is_available: bool


def _item_out(i: MenuItem) -> ItemOut:
    return ItemOut.model_validate(i, from_attributes=True)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(ctx: Ctx = HotelCtx()) -> list[CategoryOut]:
    rows = (await ctx.db.execute(select(MenuCategory).order_by(MenuCategory.sort, MenuCategory.name))).scalars()
    at = await hotel_now(ctx.db)
    return [category_out(c, at) for c in rows]


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, ctx: Ctx = HotelCtx("menu.manage")) -> CategoryOut:
    o = await current_outlet(ctx)
    c = MenuCategory(hotel_id=ctx.hotel_id, outlet_id=o.id, **body.model_dump())
    ctx.db.add(c)
    await ctx.db.flush()
    await ctx.audit("category.create", "category", c.id, after={"name": c.name})
    return category_out(c, await hotel_now(ctx.db))


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def update_category(category_id: UUID, body: CategoryIn, ctx: Ctx = HotelCtx("menu.manage")) -> CategoryOut:
    c = await ctx.db.get(MenuCategory, category_id)
    if not c:
        raise not_found("Category")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    await ctx.db.flush()
    after = {"name": c.name, "days": c.days, "from": str(c.available_from), "to": str(c.available_to)}
    await ctx.audit("category.update", "category", c.id, after=after)
    return category_out(c, await hotel_now(ctx.db))


@router.delete("/categories/{category_id}", status_code=204)
async def delete_category(category_id: UUID, ctx: Ctx = HotelCtx("menu.manage")) -> None:
    c = await ctx.db.get(MenuCategory, category_id)
    if not c:
        raise not_found("Category")
    if (await ctx.db.execute(select(MenuItem.id).where(MenuItem.category_id == c.id).limit(1))).first():
        raise bad_request("category_not_empty", "Move or delete the items first")
    await ctx.db.delete(c)


@router.get("/items", response_model=list[ItemOut])
async def list_items(ctx: Ctx = HotelCtx()) -> list[ItemOut]:
    rows = (await ctx.db.execute(select(MenuItem).order_by(MenuItem.sort, MenuItem.name))).scalars().all()
    opts = await options_for(ctx.db)
    offers = await offers_now(ctx.db, rows)
    where = await station_for_items(ctx.db, list(rows))
    return [
        ItemOut(
            **{
                **with_options(_item_out(i).model_dump(), opts, i.id),
                "offer": offers.get(i.id),
                "station": station_json(where[i.id]) if where[i.id] else None,
            }
        )
        for i in rows
    ]


@router.post("/items", response_model=ItemOut, status_code=201)
async def create_item(body: ItemIn, ctx: Ctx = HotelCtx("menu.manage")) -> ItemOut:
    await ctx.require_limit("MAX_MENU_ITEMS", count(MenuItem))
    if not await ctx.db.get(MenuCategory, body.category_id):
        raise not_found("Category")
    i = MenuItem(hotel_id=ctx.hotel_id, **body.model_dump())
    ctx.db.add(i)
    await ctx.db.flush()
    await ctx.audit("item.create", "item", i.id, after={"name": i.name, "price_minor": i.price_minor})
    return _item_out(i)


@router.patch("/items/{item_id}", response_model=ItemOut)
async def patch_item(item_id: UUID, body: ItemPatch, ctx: Ctx = HotelCtx("menu.manage")) -> ItemOut:
    i = await ctx.db.get(MenuItem, item_id)
    if not i:
        raise not_found("Item")
    before = {"price_minor": i.price_minor, "name": i.name}
    for f, v in body.model_dump(exclude_none=True).items():
        setattr(i, f, v)
    if "station_id" in body.model_fields_set:  # null is meaningful: back to the category's station
        i.station_id = body.station_id
    await ctx.audit("item.update", "item", i.id, before=before, after={"price_minor": i.price_minor, "name": i.name})
    return _item_out(i)


@router.post("/items/{item_id}/availability", response_model=ItemOut)
async def set_availability(item_id: UUID, body: AvailabilityIn, ctx: Ctx = HotelCtx("menu.availability")) -> ItemOut:
    i = await ctx.db.get(MenuItem, item_id)
    if not i:
        raise not_found("Item")
    i.is_available = body.is_available
    o = await current_outlet(ctx)
    ctx.emit(outlet_channel(o.id), {"type": "menu.availability", "item_id": str(i.id), "is_available": i.is_available})
    await ctx.audit("item.availability", "item", i.id, after={"is_available": i.is_available})
    return _item_out(i)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(item_id: UUID, ctx: Ctx = HotelCtx("menu.manage")) -> None:
    i = await ctx.db.get(MenuItem, item_id)
    if not i:
        raise not_found("Item")
    if (await ctx.db.execute(select(OrderItem.id).where(OrderItem.item_id == i.id).limit(1))).first():
        raise bad_request("item_has_orders", "Items with order history can be marked unavailable, not deleted")
    in_combo = (
        (
            await ctx.db.execute(
                select(MenuItem.name)
                .join(MenuItemComponent, MenuItemComponent.combo_item_id == MenuItem.id)
                .where(MenuItemComponent.component_item_id == i.id)
            )
        )
        .scalars()
        .all()
    )
    if in_combo:
        raise bad_request("item_in_combo", "Remove it from its combos first", {"combos": list(in_combo)})
    await ctx.db.delete(i)
