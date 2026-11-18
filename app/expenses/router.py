"""Hotel expenses (added 2026-11-18 at the owner's request). Money in paise; totals computed here."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import extract, func, select

from app.core.deps import Ctx, HotelCtx, PlatformCtx
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.models import Expense, ExpenseCategory, User

router = APIRouter(tags=["expenses"])


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class ExpenseIn(BaseModel):
    category_id: UUID
    amount_minor: int = Field(gt=0, le=10_000_000_000)
    spent_on: date
    vendor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    payment_method: str = Field(default="CASH", pattern="^(CASH|UPI|CARD|BANK)$")


class ExpensePatch(BaseModel):
    category_id: UUID | None = None
    amount_minor: int | None = Field(default=None, gt=0, le=10_000_000_000)
    spent_on: date | None = None
    vendor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    payment_method: str | None = Field(default=None, pattern="^(CASH|UPI|CARD|BANK)$")


def _category_out(c: ExpenseCategory, used: int = 0) -> dict:
    return {
        "id": str(c.id),
        "name": c.name,
        "scope": "GLOBAL" if c.hotel_id is None else "HOTEL",
        "is_active": c.is_active,
        "used": used,
    }


@router.get("/expense-categories")
async def categories(ctx: Ctx = HotelCtx("expense.view", feature="EXPENSES")) -> list[dict]:
    """Global categories (super admin) plus this hotel's own. RLS returns exactly those two sets."""
    used = dict((await ctx.db.execute(select(Expense.category_id, func.count()).group_by(Expense.category_id))).all())
    rows = (
        await ctx.db.execute(
            select(ExpenseCategory)
            .where(ExpenseCategory.is_active)
            .order_by(ExpenseCategory.hotel_id.is_not(None), ExpenseCategory.name)
        )
    ).scalars()
    return [_category_out(c, used.get(c.id, 0)) for c in rows]


async def _name_taken(db, name: str, exclude: UUID | None = None) -> bool:
    q = select(ExpenseCategory.id).where(func.lower(ExpenseCategory.name) == name.strip().lower())
    if exclude:
        q = q.where(ExpenseCategory.id != exclude)
    return (await db.execute(q)).first() is not None


@router.post("/expense-categories", status_code=201)
async def add_category(body: CategoryIn, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")) -> dict:
    """A hotel's own category; visible only to this hotel. Clashes with a global name are refused."""
    if await _name_taken(ctx.db, body.name):
        raise conflict("category_exists", "A category with this name already exists")
    c = ExpenseCategory(hotel_id=ctx.hotel_id, name=body.name.strip())
    ctx.db.add(c)
    await ctx.db.flush()
    await ctx.audit("expense_category.create", "expense_category", c.id, after={"name": c.name})
    return _category_out(c)


@router.patch("/expense-categories/{category_id}")
async def rename_category(
    category_id: UUID, body: CategoryIn, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")
) -> dict:
    c = await ctx.db.get(ExpenseCategory, category_id)
    if not c:
        raise not_found("Category")
    if c.hotel_id is None:
        raise forbidden("global_category", "Global categories are managed by the platform")
    if await _name_taken(ctx.db, body.name, exclude=c.id):
        raise conflict("category_exists", "A category with this name already exists")
    before = c.name
    c.name = body.name.strip()
    await ctx.audit(
        "expense_category.rename", "expense_category", c.id, before={"name": before}, after={"name": c.name}
    )
    return _category_out(c)


@router.delete("/expense-categories/{category_id}", status_code=204)
async def delete_category(category_id: UUID, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")) -> None:
    c = await ctx.db.get(ExpenseCategory, category_id)
    if not c:
        raise not_found("Category")
    if c.hotel_id is None:
        raise forbidden("global_category", "Global categories are managed by the platform")
    if (await ctx.db.execute(select(Expense.id).where(Expense.category_id == c.id).limit(1))).first():
        raise conflict("category_in_use", "Move this category's expenses first")
    await ctx.audit("expense_category.delete", "expense_category", c.id, before={"name": c.name})
    await ctx.db.delete(c)


# ---------------------------------------------------- platform: global categories --

platform = APIRouter(prefix="/platform/expense-categories", tags=["platform"])


class GlobalCategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_active: bool | None = None


@platform.get("")
async def list_global(ctx: Ctx = PlatformCtx) -> list[dict]:
    used = dict((await ctx.db.execute(select(Expense.category_id, func.count()).group_by(Expense.category_id))).all())
    rows = (
        await ctx.db.execute(
            select(ExpenseCategory).where(ExpenseCategory.hotel_id.is_(None)).order_by(ExpenseCategory.name)
        )
    ).scalars()
    return [_category_out(c, used.get(c.id, 0)) for c in rows]


@platform.post("", status_code=201)
async def add_global(body: CategoryIn, ctx: Ctx = PlatformCtx) -> dict:
    """Shows up for every hotel immediately. A hotel that already has its own category with this name keeps it."""
    taken = (
        await ctx.db.execute(
            select(ExpenseCategory.id).where(
                ExpenseCategory.hotel_id.is_(None), func.lower(ExpenseCategory.name) == body.name.strip().lower()
            )
        )
    ).first()
    if taken:
        raise conflict("category_exists", "A global category with this name already exists")
    c = ExpenseCategory(hotel_id=None, name=body.name.strip())
    ctx.db.add(c)
    await ctx.db.flush()
    await ctx.audit("expense_category.global_create", "expense_category", c.id, after={"name": c.name})
    return _category_out(c)


@platform.patch("/{category_id}")
async def patch_global(category_id: UUID, body: GlobalCategoryPatch, ctx: Ctx = PlatformCtx) -> dict:
    """Deactivating hides it from new expenses; past expenses keep their category (never deleted while in use)."""
    c = await ctx.db.get(ExpenseCategory, category_id)
    if not c or c.hotel_id is not None:
        raise not_found("Global category")
    before = {"name": c.name, "is_active": c.is_active}
    if body.name is not None:
        c.name = body.name.strip()
    if body.is_active is not None:
        c.is_active = body.is_active
    await ctx.audit(
        "expense_category.global_update",
        "expense_category",
        c.id,
        before=before,
        after={"name": c.name, "is_active": c.is_active},
    )
    return _category_out(c)


def _month(month: str) -> tuple[int, int]:
    try:
        y, m = (int(x) for x in month.split("-"))
        assert 1 <= m <= 12
        return y, m
    except Exception as e:
        raise bad_request("bad_month", "Use YYYY-MM") from e


@router.get("/expenses")
async def list_expenses(
    month: str = Query(pattern=r"^\d{4}-\d{2}$"), ctx: Ctx = HotelCtx("expense.view", feature="EXPENSES")
) -> dict:
    y, m = _month(month)
    in_month = (extract("year", Expense.spent_on) == y, extract("month", Expense.spent_on) == m)
    rows = (
        await ctx.db.execute(
            select(Expense, ExpenseCategory.name, User.name)
            .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .outerjoin(User, User.id == Expense.created_by)
            .where(*in_month)
            .order_by(Expense.spent_on.desc(), Expense.created_at.desc())
        )
    ).all()
    by_cat = (
        await ctx.db.execute(
            select(ExpenseCategory.name, func.sum(Expense.amount_minor))
            .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .where(*in_month)
            .group_by(ExpenseCategory.name)
            .order_by(func.sum(Expense.amount_minor).desc())
        )
    ).all()
    by_day = (
        await ctx.db.execute(
            select(Expense.spent_on, func.sum(Expense.amount_minor))
            .where(*in_month)
            .group_by(Expense.spent_on)
            .order_by(Expense.spent_on)
        )
    ).all()
    by_cat = [(n, int(v)) for n, v in by_cat]  # SUM(bigint) is numeric in Postgres; paise are ints
    total = sum(v for _, v in by_cat)
    return {
        "month": month,
        "total_minor": total,
        "by_category": [
            {"name": n, "total_minor": v, "share_bp": (v * 10_000 // total) if total else 0} for n, v in by_cat
        ],
        "by_day": [{"date": d.isoformat(), "total_minor": int(v)} for d, v in by_day],
        "items": [
            {
                "id": str(e.id),
                "category_id": str(e.category_id),
                "category": cat,
                "amount_minor": e.amount_minor,
                "spent_on": e.spent_on.isoformat(),
                "vendor": e.vendor,
                "note": e.note,
                "payment_method": e.payment_method,
                "created_by": who,
            }
            for e, cat, who in rows
        ],
    }


@router.post("/expenses", status_code=201)
async def add_expense(body: ExpenseIn, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")) -> dict:
    cat = await ctx.db.get(ExpenseCategory, body.category_id)  # RLS: own or global only
    if not cat or not cat.is_active:
        raise not_found("Category")
    e = Expense(hotel_id=ctx.hotel_id, created_by=ctx.user_id, **body.model_dump())
    ctx.db.add(e)
    await ctx.db.flush()
    await ctx.audit("expense.create", "expense", e.id, after=body.model_dump(mode="json"))
    return {"id": str(e.id)}


@router.patch("/expenses/{expense_id}")
async def patch_expense(
    expense_id: UUID, body: ExpensePatch, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")
) -> dict:
    e = await ctx.db.get(Expense, expense_id)
    if not e:
        raise not_found("Expense")
    before = {"amount_minor": e.amount_minor, "spent_on": e.spent_on.isoformat()}
    for f, v in body.model_dump(exclude_none=True).items():
        setattr(e, f, v)
    await ctx.audit(
        "expense.update", "expense", e.id, before=before, after=body.model_dump(mode="json", exclude_none=True)
    )
    return {"id": str(e.id)}


@router.delete("/expenses/{expense_id}", status_code=204)
async def delete_expense(expense_id: UUID, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")) -> None:
    e = await ctx.db.get(Expense, expense_id)
    if not e:
        raise not_found("Expense")
    await ctx.audit(
        "expense.delete", "expense", e.id, before={"amount_minor": e.amount_minor, "spent_on": e.spent_on.isoformat()}
    )
    await ctx.db.delete(e)
