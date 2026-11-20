"""Variants (half/full) and add-on groups (docs/03 "Variants and add-ons", docs/14).

Prices live here and only here; orders snapshot what was chosen (app/ordering/service.py `_choose`).
"""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, not_found
from app.floor.router import current_outlet, outlet_channel
from app.models import Addon, AddonGroup, MenuItem, MenuItemAddonGroup, MenuItemComponent, MenuItemVariant

router = APIRouter(tags=["catalog"])


# ---------- shapes ----------


def variant_json(v: MenuItemVariant) -> dict:
    return {"id": str(v.id), "name": v.name, "price_minor": v.price_minor, "is_available": v.is_available}


def addon_json(a: Addon) -> dict:
    return {"id": str(a.id), "name": a.name, "price_minor": a.price_minor, "is_available": a.is_available}


def group_json(g: AddonGroup, addons: list[Addon]) -> dict:
    return {
        "id": str(g.id),
        "name": g.name,
        "min_select": g.min_select,
        "max_select": g.max_select,
        "addons": [addon_json(a) for a in addons],
    }


async def options_for(db: AsyncSession) -> dict[UUID, dict]:
    """Every item's variants and add-on groups in four queries. Staff see sold-out choices greyed; diners get
    `for_diner` (one list for both, no separate visibility flag to forget)."""
    out: dict[UUID, dict] = {}
    vq = select(MenuItemVariant).order_by(MenuItemVariant.sort, MenuItemVariant.price_minor)
    for v in (await db.execute(vq)).scalars():
        out.setdefault(v.item_id, {"variants": [], "addon_groups": []})["variants"].append(variant_json(v))
    groups = {g.id: g for g in (await db.execute(select(AddonGroup))).scalars()}
    addons: dict[UUID, list[Addon]] = {}
    for a in (await db.execute(select(Addon).order_by(Addon.sort, Addon.name))).scalars():
        addons.setdefault(a.group_id, []).append(a)
    comps = (
        await db.execute(
            select(MenuItemComponent, MenuItem)
            .join(MenuItem, MenuItem.id == MenuItemComponent.component_item_id)
            .order_by(MenuItemComponent.sort)
        )
    ).all()
    for c, part in comps:
        out.setdefault(c.combo_item_id, {"variants": [], "addon_groups": []}).setdefault("components", []).append(
            {"item_id": str(part.id), "name": part.name, "qty": c.qty, "is_available": part.is_available}
        )
    links = (await db.execute(select(MenuItemAddonGroup).order_by(MenuItemAddonGroup.sort))).scalars()
    for ln in links:
        g = groups[ln.group_id]
        out.setdefault(ln.item_id, {"variants": [], "addon_groups": []})["addon_groups"].append(
            group_json(g, addons.get(g.id, []))
        )
    return out


def for_diner(item: dict) -> dict:
    """Hide sold-out sizes and add-ons from diners. If every size is sold out, the dish itself is sold out (else the
    diner would see a dish with no sizes and be refused at checkout)."""
    variants = [v for v in item["variants"] if v["is_available"]]
    groups = [{**g, "addons": [a for a in g["addons"] if a["is_available"]]} for g in item["addon_groups"]]
    sold_out = bool(item["variants"]) and not variants
    # A combo is sold out while any of its dishes is (the server refuses it anyway).
    sold_out = sold_out or any(not c["is_available"] for c in item.get("components", []))
    return {**item, "variants": variants, "addon_groups": groups, "is_available": item["is_available"] and not sold_out}


def with_options(item: dict, opts: dict[UUID, dict], item_id: UUID) -> dict:
    o = opts.get(item_id, {})
    return {
        **item,
        "variants": o.get("variants", []),
        "addon_groups": o.get("addon_groups", []),
        "components": o.get("components", []),
    }


# ---------- variants ----------


class VariantIn(BaseModel):
    id: UUID | None = None  # keep an existing variant (its id stays the same)
    name: str = Field(min_length=1, max_length=40)
    price_minor: int = Field(ge=0, le=100_000_000)
    is_available: bool = True


class VariantsIn(BaseModel):
    """The item's full list of sizes, in order. Empty = no sizes (the item's own price applies). Two or more
    otherwise: a single "variant" is just the price."""

    variants: list[VariantIn] = Field(max_length=10)

    @model_validator(mode="after")
    def _check(self) -> "VariantsIn":
        if len(self.variants) == 1:
            raise ValueError("Give two or more sizes, or none")
        names = [v.name.strip().lower() for v in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("Each size needs a different name")
        return self


async def _item(ctx: Ctx, item_id: UUID) -> MenuItem:
    i = await ctx.db.get(MenuItem, item_id)
    if not i:
        raise not_found("Item")
    return i


@router.put("/items/{item_id}/variants")
async def set_variants(item_id: UUID, body: VariantsIn, ctx: Ctx = HotelCtx("menu.manage")) -> list[dict]:
    """Replace the item's sizes. Orders already placed keep their own copy, so editing never changes them."""
    await _item(ctx, item_id)
    existing = {
        v.id: v
        for v in (await ctx.db.execute(select(MenuItemVariant).where(MenuItemVariant.item_id == item_id))).scalars()
    }
    keep = {v.id for v in body.variants if v.id}
    if keep - set(existing):
        raise bad_request("unknown_variant", "A size does not belong to this item")
    for vid, v in existing.items():
        if vid not in keep:
            await ctx.db.delete(v)
    await ctx.db.flush()
    out = []
    for sort, v in enumerate(body.variants):
        row = existing.get(v.id) if v.id else None
        if row is None:
            row = MenuItemVariant(hotel_id=ctx.hotel_id, item_id=item_id)
            ctx.db.add(row)
        row.name, row.price_minor, row.is_available, row.sort = v.name.strip(), v.price_minor, v.is_available, sort
        out.append(row)
    await ctx.db.flush()
    await ctx.audit("item.variants", "item", item_id, after={"variants": [[v.name, v.price_minor] for v in out]})
    return [variant_json(v) for v in out]


class AvailabilityIn(BaseModel):
    is_available: bool


@router.post("/variants/{variant_id}/availability")
async def variant_availability(
    variant_id: UUID, body: AvailabilityIn, ctx: Ctx = HotelCtx("menu.availability")
) -> dict:
    """Half sold out, full still on (kitchen staff can do this, like item availability)."""
    v = await ctx.db.get(MenuItemVariant, variant_id)
    if not v:
        raise not_found("Size")
    v.is_available = body.is_available
    o = await current_outlet(ctx)
    ctx.emit(outlet_channel(o.id), {"type": "menu.availability", "item_id": str(v.item_id)})
    await ctx.audit(
        "variant.availability", "item", v.item_id, after={"variant": v.name, "is_available": v.is_available}
    )
    return variant_json(v)


# ---------- add-on groups ----------


class AddonIn(BaseModel):
    id: UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    price_minor: int = Field(default=0, ge=0, le=100_000_000)
    is_available: bool = True


class GroupIn(BaseModel):
    """`min_select`..`max_select` picks in total ("Extras": 0..3; "Choose your bread": 1..1)."""

    name: str = Field(min_length=1, max_length=60)
    min_select: int = Field(default=0, ge=0, le=20)
    max_select: int = Field(default=1, ge=1, le=20)
    addons: list[AddonIn] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _check(self) -> "GroupIn":
        if self.min_select > self.max_select:
            raise ValueError("The minimum cannot be more than the maximum")
        return self


async def _group_out(db: AsyncSession, g: AddonGroup) -> dict:
    addons = list((await db.execute(select(Addon).where(Addon.group_id == g.id).order_by(Addon.sort))).scalars())
    used_by = (
        await db.execute(
            select(MenuItem.name)
            .join(MenuItemAddonGroup, MenuItemAddonGroup.item_id == MenuItem.id)
            .where(MenuItemAddonGroup.group_id == g.id)
            .order_by(MenuItem.name)
        )
    ).scalars()
    # Where the group is used, so owners can change or delete it safely (docs/14, a common complaint elsewhere).
    return {**group_json(g, addons), "used_by": list(used_by)}


async def _write_addons(ctx: Ctx, g: AddonGroup, items: list[AddonIn]) -> None:
    existing = {a.id: a for a in (await ctx.db.execute(select(Addon).where(Addon.group_id == g.id))).scalars()}
    keep = {a.id for a in items if a.id}
    if keep - set(existing):
        raise bad_request("unknown_addon", "An add-on does not belong to this group")
    for aid, a in existing.items():
        if aid not in keep:
            await ctx.db.delete(a)
    await ctx.db.flush()
    for sort, a in enumerate(items):
        row = existing.get(a.id) if a.id else None
        if row is None:
            row = Addon(hotel_id=ctx.hotel_id, group_id=g.id)
            ctx.db.add(row)
        row.name, row.price_minor, row.is_available, row.sort = a.name.strip(), a.price_minor, a.is_available, sort
    await ctx.db.flush()


@router.get("/addon-groups")
async def list_groups(ctx: Ctx = HotelCtx()) -> list[dict]:
    groups = (await ctx.db.execute(select(AddonGroup).order_by(AddonGroup.sort, AddonGroup.name))).scalars().all()
    return [await _group_out(ctx.db, g) for g in groups]


@router.post("/addon-groups", status_code=201)
async def create_group(body: GroupIn, ctx: Ctx = HotelCtx("menu.manage")) -> dict:
    n = (await ctx.db.execute(select(func.count()).select_from(AddonGroup))).scalar_one()
    g = AddonGroup(
        hotel_id=ctx.hotel_id, name=body.name.strip(), min_select=body.min_select, max_select=body.max_select, sort=n
    )
    ctx.db.add(g)
    await ctx.db.flush()
    await _write_addons(ctx, g, body.addons)
    await ctx.audit("addon_group.create", "addon_group", g.id, after={"name": g.name})
    return await _group_out(ctx.db, g)


@router.put("/addon-groups/{group_id}")
async def update_group(group_id: UUID, body: GroupIn, ctx: Ctx = HotelCtx("menu.manage")) -> dict:
    g = await ctx.db.get(AddonGroup, group_id)
    if not g:
        raise not_found("Add-on group")
    g.name, g.min_select, g.max_select = body.name.strip(), body.min_select, body.max_select
    await _write_addons(ctx, g, body.addons)
    await ctx.audit("addon_group.update", "addon_group", g.id, after={"name": g.name})
    return await _group_out(ctx.db, g)


@router.delete("/addon-groups/{group_id}", status_code=204)
async def delete_group(group_id: UUID, ctx: Ctx = HotelCtx("menu.manage")) -> None:
    """Allowed even while items use it (they simply stop offering it); orders keep their own copy."""
    g = await ctx.db.get(AddonGroup, group_id)
    if not g:
        raise not_found("Add-on group")
    await ctx.audit("addon_group.delete", "addon_group", g.id, before={"name": g.name})
    await ctx.db.delete(g)


@router.post("/addons/{addon_id}/availability")
async def addon_availability(addon_id: UUID, body: AvailabilityIn, ctx: Ctx = HotelCtx("menu.availability")) -> dict:
    a = await ctx.db.get(Addon, addon_id)
    if not a:
        raise not_found("Add-on")
    a.is_available = body.is_available
    o = await current_outlet(ctx)
    ctx.emit(outlet_channel(o.id), {"type": "menu.availability", "addon_id": str(a.id)})
    await ctx.audit("addon.availability", "addon", a.id, after={"name": a.name, "is_available": a.is_available})
    return addon_json(a)


class ItemGroupsIn(BaseModel):
    group_ids: list[UUID] = Field(max_length=10)


@router.put("/items/{item_id}/addon-groups")
async def set_item_groups(item_id: UUID, body: ItemGroupsIn, ctx: Ctx = HotelCtx("menu.manage")) -> list[dict]:
    """Which add-on groups this item offers, in the order shown."""
    await _item(ctx, item_id)
    ids = list(dict.fromkeys(body.group_ids))
    groups = {g.id: g for g in (await ctx.db.execute(select(AddonGroup).where(AddonGroup.id.in_(ids)))).scalars()}
    if len(groups) != len(ids):
        raise not_found("Add-on group")
    await ctx.db.execute(delete(MenuItemAddonGroup).where(MenuItemAddonGroup.item_id == item_id))
    for sort, gid in enumerate(ids):
        ctx.db.add(MenuItemAddonGroup(hotel_id=ctx.hotel_id, item_id=item_id, group_id=gid, sort=sort))
    await ctx.db.flush()
    await ctx.audit("item.addon_groups", "item", item_id, after={"groups": [groups[g].name for g in ids]})
    return [await _group_out(ctx.db, groups[g]) for g in ids]


# ---------- combos ----------


class ComponentIn(BaseModel):
    item_id: UUID
    qty: int = Field(default=1, ge=1, le=20)


class ComponentsIn(BaseModel):
    """The combo's dishes. Empty = an ordinary item again. At least two dishes (or one dish twice) otherwise."""

    components: list[ComponentIn] = Field(max_length=12)


@router.put("/items/{item_id}/components")
async def set_components(item_id: UUID, body: ComponentsIn, ctx: Ctx = HotelCtx("menu.manage")) -> list[dict]:
    """Make an item a combo (docs/03 "Combos"). Its price, GST code and add-on groups stay its own; its preparation
    time becomes the slowest dish's. Orders already placed keep their own copy."""
    combo = await _item(ctx, item_id)
    ids = [c.item_id for c in body.components]
    if len(set(ids)) != len(ids):
        raise bad_request("bad_combo", "List each dish once and use its quantity")
    if item_id in ids:
        raise bad_request("bad_combo", "A combo can't contain itself")
    if body.components and sum(c.qty for c in body.components) < 2:
        raise bad_request("bad_combo", "A combo needs at least two dishes")
    parts = {i.id: i for i in (await ctx.db.execute(select(MenuItem).where(MenuItem.id.in_(ids)))).scalars()}
    if len(parts) != len(ids):
        raise not_found("Item")
    nested = (
        await ctx.db.execute(
            select(MenuItemComponent.combo_item_id).where(MenuItemComponent.combo_item_id.in_(ids)).limit(1)
        )
    ).first()
    used_in = (
        await ctx.db.execute(
            select(MenuItemComponent.id).where(MenuItemComponent.component_item_id == item_id).limit(1)
        )
    ).first()
    if body.components and (nested or used_in):
        raise bad_request("bad_combo", "No combos inside combos")
    await ctx.db.execute(delete(MenuItemComponent).where(MenuItemComponent.combo_item_id == item_id))
    for sort, c in enumerate(body.components):
        ctx.db.add(
            MenuItemComponent(
                hotel_id=ctx.hotel_id, combo_item_id=item_id, component_item_id=c.item_id, qty=c.qty, sort=sort
            )
        )
    if body.components:
        combo.est_prep_minutes = max(parts[c.item_id].est_prep_minutes for c in body.components)
    await ctx.db.flush()
    after = {"components": [[parts[c.item_id].name, c.qty] for c in body.components]}
    await ctx.audit("item.components", "item", item_id, after=after)
    return [
        {
            "item_id": str(c.item_id),
            "name": parts[c.item_id].name,
            "qty": c.qty,
            "is_available": parts[c.item_id].is_available,
        }
        for c in body.components
    ]
