import base64
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import or_, select

from app.billing import service
from app.core.deps import Ctx, HotelCtx
from app.core.errors import not_found
from app.core.listing import ListParams
from app.core.security import now
from app.iam.permissions import LOCKED_ROLE
from app.models import Bill, DiningTable, Hotel, PrintLog, Role
from app.printing import escpos

router = APIRouter(tags=["bills"])


class DiscountIn(BaseModel):
    """Either `percent_bp` (1000 = 10%) or `amount_minor` (paise), and why. Computed and capped on the server."""

    percent_bp: int | None = Field(default=None, ge=1, le=10_000)
    amount_minor: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def _one_kind(self) -> "DiscountIn":
        if (self.percent_bp is None) == (self.amount_minor is None):
            raise ValueError("Give either percent_bp or amount_minor")
        return self


class BillIn(BaseModel):
    """A table's bill for this visit (`table_id`), or one takeaway order's bill (`order_id`)."""

    table_id: UUID | None = None
    order_id: UUID | None = None
    include_unserved: bool = False
    # With an unpaid bill already open, a discount re-issues it (the old one is cancelled, never edited).
    discount: DiscountIn | None = None

    @model_validator(mode="after")
    def _one_target(self) -> "BillIn":
        if (self.table_id is None) == (self.order_id is None):
            raise ValueError("Give either table_id or order_id")
        return self


class SettleIn(BaseModel):
    method: str = Field(pattern="^(CASH|UPI|CARD)$")


class VoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class PrintIn(BaseModel):
    paper_mm: int | None = None


async def max_discount_bp(ctx: Ctx) -> int:
    """The signed-in person's role limit. Hotel admins can always give any discount (their role is locked)."""
    if ctx.principal.role_code == LOCKED_ROLE:
        return 10_000
    role = (await ctx.db.execute(select(Role).where(Role.code == ctx.principal.role_code))).scalar_one_or_none()
    return role.max_discount_bp if role else 0


async def _get(ctx: Ctx, bill_id: UUID) -> Bill:
    b = await ctx.db.get(Bill, bill_id, with_for_update=True)
    if not b:
        raise not_found("Bill")
    return b


@router.post("/bills", status_code=201)
async def create(body: BillIn, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    discount = None
    if body.discount:
        discount = service.DiscountSpec(
            percent_bp=body.discount.percent_bp,
            amount_minor=body.discount.amount_minor,
            reason=body.discount.reason.strip(),
            by_user=ctx.user_id,
            max_bp=await max_discount_bp(ctx),
        )
    if body.order_id:
        b, replaced = await service.create_order_bill(
            ctx.db,
            hotel_id=ctx.hotel_id,
            order_id=body.order_id,
            user_id=ctx.user_id,
            include_unserved=body.include_unserved,
            discount=discount,
        )
    else:
        b, replaced = await service.create_bill(
            ctx.db,
            hotel_id=ctx.hotel_id,
            table_id=body.table_id,
            user_id=ctx.user_id,
            include_unserved=body.include_unserved,
            discount=discount,
        )
    after = {"invoice_no": b.invoice_no, "total_minor": b.total_minor, "replaced": replaced}
    if b.discount_minor:
        after |= {"discount_minor": b.discount_minor, "discount_reason": b.discount_reason}
    await ctx.audit("bill.create", "bill", b.id, after=after)
    ctx.emit(f"outlet:{b.outlet_id}", {"type": "bill.created", "table_id": str(b.table_id)})
    return {**await service.bill_json(ctx.db, b), "replaced_invoice_nos": replaced}


@router.get("/bills")
async def list_bills(
    scope: str = Query("today", pattern="^(today|open|all)$"),
    table_id: UUID | None = None,
    status: str | None = Query(None, pattern="^(ISSUED|PAID|VOID)$"),
    page: ListParams = Depends(),
    ctx: Ctx = HotelCtx("bill.manage"),
) -> list[dict]:
    """The bills register, newest first. `from`/`to` (hotel-local days, max 31), `q` (invoice number or table),
    `status`, `limit`/`offset`."""
    q = select(Bill).order_by(Bill.created_at.desc(), Bill.id).limit(page.limit).offset(page.offset)
    if page.date_from:
        hotel = await ctx.db.get(Hotel, ctx.hotel_id)
        q = q.where(*page.dates(Bill.created_at, hotel.timezone))
    if status:
        q = q.where(Bill.status == status)
    if page.q:
        tables = select(DiningTable.id).where(DiningTable.label.ilike(f"%{page.q}%"))
        q = q.where(or_(Bill.invoice_no.ilike(f"%{page.q}%"), Bill.table_id.in_(tables)))
    if scope == "today":
        q = q.where(Bill.created_at >= now() - timedelta(hours=24))
    elif scope == "open":
        q = q.where(Bill.status == "ISSUED")
    if table_id:
        q = q.where(Bill.table_id == table_id)
    return [await service.bill_json(ctx.db, b) for b in (await ctx.db.execute(q)).scalars()]


@router.get("/bills/{bill_id}")
async def get(bill_id: UUID, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    return await service.bill_json(ctx.db, await _get(ctx, bill_id))


@router.post("/bills/{bill_id}/print")
async def print_bill(bill_id: UUID, body: PrintIn, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    """Every print is logged; the second and later copies say DUPLICATE."""
    b = await _get(ctx, bill_id)
    b.print_count += 1
    ctx.db.add(
        PrintLog(hotel_id=ctx.hotel_id, kind="BILL", bill_id=b.id, copy_no=b.print_count, printed_by=ctx.user_id)
    )
    out = await service.bill_json(ctx.db, b, copy_no=b.print_count)
    if body.paper_mm:
        out["escpos_b64"] = base64.b64encode(escpos.render(out["blocks"], body.paper_mm)).decode()
    return out


@router.post("/bills/{bill_id}/settle")
async def settle(bill_id: UUID, body: SettleIn, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    """Pay whatever is still due, in one method."""
    b = await _get(ctx, bill_id)
    await service.settle(ctx.db, b, body.method, ctx.user_id)
    await ctx.audit("bill.settle", "bill", b.id, after={"method": body.method, "total_minor": b.total_minor})
    ctx.emit(f"outlet:{b.outlet_id}", {"type": "bill.paid", "table_id": str(b.table_id)})
    return await service.bill_json(ctx.db, b)


class PaymentIn(BaseModel):
    method: str = Field(pattern="^(CASH|UPI|CARD)$")
    amount_minor: int = Field(ge=1)
    reference: str | None = Field(default=None, max_length=60)


@router.post("/bills/{bill_id}/payments")
async def add_payment(bill_id: UUID, body: PaymentIn, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    """One part of the payment (cash + UPI, or a guest's share). The bill is paid when the parts reach the total."""
    b = await _get(ctx, bill_id)
    p = await service.add_payment(
        ctx.db, b, method=body.method, amount_minor=body.amount_minor, reference=body.reference, user_id=ctx.user_id
    )
    await ctx.audit("bill.payment", "bill", b.id, after={"method": p.method, "amount_minor": p.amount_minor})
    if b.status == "PAID":
        ctx.emit(f"outlet:{b.outlet_id}", {"type": "bill.paid", "table_id": str(b.table_id)})
    return await service.bill_json(ctx.db, b)


@router.delete("/bills/{bill_id}/payments/{payment_id}")
async def remove_payment(bill_id: UUID, payment_id: UUID, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    """Undo a part entered by mistake, while the bill is still unpaid. Audited."""
    b = await _get(ctx, bill_id)
    await service.remove_payment(ctx.db, b, payment_id)
    await ctx.audit("bill.payment_removed", "bill", b.id, after={"payment_id": str(payment_id)})
    return await service.bill_json(ctx.db, b)


@router.get("/bills/{bill_id}/split")
async def split(bill_id: UUID, parts: int = Query(ge=2, le=20), ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    """Equal shares of what is still due, adding up to the paisa. Each guest then pays theirs as a part payment."""
    b = await _get(ctx, bill_id)
    due = b.total_minor - await service.paid_minor(ctx.db, b) if b.status == "ISSUED" else 0
    return {"due_minor": due, "shares_minor": service.split_equally(due, parts)}


@router.post("/bills/{bill_id}/void")
async def void(bill_id: UUID, body: VoidIn, ctx: Ctx = HotelCtx("bill.void")) -> dict:
    b = await _get(ctx, bill_id)
    await service.void(ctx.db, b, body.reason)
    await ctx.audit("bill.void", "bill", b.id, reason=body.reason, after={"invoice_no": b.invoice_no})
    return await service.bill_json(ctx.db, b)
