from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.catalog.options import options_for, with_options
from app.core.deps import Ctx, HotelCtx, count
from app.core.errors import bad_request, not_found
from app.floor.router import current_outlet, outlet_channel
from app.models import MenuCategory, MenuItem, OrderItem

router = APIRouter(tags=["catalog"])


class CategoryOut(BaseModel):
    id: UUID
    name: str
    sort: int


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sort: int = 0


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


class ItemPatch(BaseModel):
    category_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_veg: bool | None = None
    price_minor: int | None = Field(default=None, ge=0, le=100_000_000)
    est_prep_minutes: int | None = Field(default=None, ge=1, le=240)
    sort: int | None = None


class AvailabilityIn(BaseModel):
    is_available: bool


def _item_out(i: MenuItem) -> ItemOut:
    return ItemOut.model_validate(i, from_attributes=True)


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(ctx: Ctx = HotelCtx()) -> list[CategoryOut]:
    rows = (await ctx.db.execute(select(MenuCategory).order_by(MenuCategory.sort, MenuCategory.name))).scalars()
    return [CategoryOut(id=c.id, name=c.name, sort=c.sort) for c in rows]


@router.post("/categories", response_model=CategoryOut, status_code=201)
async def create_category(body: CategoryIn, ctx: Ctx = HotelCtx("menu.manage")) -> CategoryOut:
    o = await current_outlet(ctx)
    c = MenuCategory(hotel_id=ctx.hotel_id, outlet_id=o.id, name=body.name, sort=body.sort)
    ctx.db.add(c)
    await ctx.db.flush()
    await ctx.audit("category.create", "category", c.id, after={"name": c.name})
    return CategoryOut(id=c.id, name=c.name, sort=c.sort)


@router.put("/categories/{category_id}", response_model=CategoryOut)
async def update_category(category_id: UUID, body: CategoryIn, ctx: Ctx = HotelCtx("menu.manage")) -> CategoryOut:
    c = await ctx.db.get(MenuCategory, category_id)
    if not c:
        raise not_found("Category")
    c.name, c.sort = body.name, body.sort
    return CategoryOut(id=c.id, name=c.name, sort=c.sort)


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
    rows = (await ctx.db.execute(select(MenuItem).order_by(MenuItem.sort, MenuItem.name))).scalars()
    opts = await options_for(ctx.db)
    return [ItemOut(**with_options(_item_out(i).model_dump(), opts, i.id)) for i in rows]


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
    await ctx.db.delete(i)
