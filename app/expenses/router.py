"""Hotel expenses (added 2026-11-18 at the owner's request). Money in paise; totals computed here."""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import extract, func, select

from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request, conflict, not_found
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


@router.get("/expense-categories")
async def categories(ctx: Ctx = HotelCtx("expense.view", feature="EXPENSES")) -> list[dict]:
    rows = (await ctx.db.execute(select(ExpenseCategory).order_by(ExpenseCategory.name))).scalars()
    return [{"id": str(c.id), "name": c.name} for c in rows]


@router.post("/expense-categories", status_code=201)
async def add_category(body: CategoryIn, ctx: Ctx = HotelCtx("expense.manage", feature="EXPENSES")) -> dict:
    if (await ctx.db.execute(select(ExpenseCategory.id).where(ExpenseCategory.name == body.name))).first():
        raise conflict("category_exists", "This category exists")
    c = ExpenseCategory(hotel_id=ctx.hotel_id, name=body.name)
    ctx.db.add(c)
    await ctx.db.flush()
    return {"id": str(c.id), "name": c.name}


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
    if not await ctx.db.get(ExpenseCategory, body.category_id):
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
