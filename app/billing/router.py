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
from app.models import Bill, DiningTable, Hotel, PrintLog
from app.printing import escpos

router = APIRouter(tags=["bills"])


class BillIn(BaseModel):
    """A table's bill for this visit (`table_id`), or one takeaway order's bill (`order_id`)."""

    table_id: UUID | None = None
    order_id: UUID | None = None
    include_unserved: bool = False

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


async def _get(ctx: Ctx, bill_id: UUID) -> Bill:
    b = await ctx.db.get(Bill, bill_id, with_for_update=True)
    if not b:
        raise not_found("Bill")
    return b


@router.post("/bills", status_code=201)
async def create(body: BillIn, ctx: Ctx = HotelCtx("bill.manage")) -> dict:
    if body.order_id:
        b = await service.create_order_bill(
            ctx.db,
            hotel_id=ctx.hotel_id,
            order_id=body.order_id,
            user_id=ctx.user_id,
            include_unserved=body.include_unserved,
        )
        replaced: list[str] = []
    else:
        b, replaced = await service.create_bill(
            ctx.db,
            hotel_id=ctx.hotel_id,
            table_id=body.table_id,
            user_id=ctx.user_id,
            include_unserved=body.include_unserved,
        )
    after = {"invoice_no": b.invoice_no, "total_minor": b.total_minor, "replaced": replaced}
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
    b = await _get(ctx, bill_id)
    await service.settle(ctx.db, b, body.method)
    await ctx.audit("bill.settle", "bill", b.id, after={"method": body.method, "total_minor": b.total_minor})
    ctx.emit(f"outlet:{b.outlet_id}", {"type": "bill.paid", "table_id": str(b.table_id)})
    return await service.bill_json(ctx.db, b)


@router.post("/bills/{bill_id}/void")
async def void(bill_id: UUID, body: VoidIn, ctx: Ctx = HotelCtx("bill.void")) -> dict:
    b = await _get(ctx, bill_id)
    await service.void(ctx.db, b, body.reason)
    await ctx.audit("bill.void", "bill", b.id, reason=body.reason, after={"invoice_no": b.invoice_no})
    return await service.bill_json(ctx.db, b)
