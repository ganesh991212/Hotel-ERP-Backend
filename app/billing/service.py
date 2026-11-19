"""Table bills. Totals come from the orders' server-computed amounts; nothing is re-priced here."""

from collections import defaultdict
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, not_found
from app.core.security import now
from app.models import Bill, BillLine, DiningTable, Hotel, HotelTaxProfile, Order, OrderItem, TableSession
from app.ordering.pricing import bp_of
from app.printing.documents import bill_blocks

DOC_TYPE = {"REGULAR": "TAX_INVOICE", "COMPOSITION": "BILL_OF_SUPPLY", "UNREGISTERED": "BILL"}


def financial_year(at: datetime) -> str:
    """Indian FY: April to March. 2026-11-18 -> '2026-27'."""
    y = at.year if at.month >= 4 else at.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def invoice_no(fy: str, number: int) -> str:
    """'INV/26-27/00001' (15 chars; GST allows up to 16, unique per FY)."""
    return f"INV/{fy[2:4]}-{fy[5:7]}/{number:05d}"


def split_gst(tax_minor: int) -> tuple[int, int]:
    """Intra-state dine-in: CGST + SGST halves. The odd paisa goes to CGST so the parts always sum exactly."""
    cgst = (tax_minor + 1) // 2
    return cgst, tax_minor - cgst


async def _unbilled(db: AsyncSession, table_id: UUID) -> list[Order]:
    q = (
        select(Order)
        .where(Order.table_id == table_id, Order.bill_id.is_(None), Order.status.not_in(("CANCELLED", "PLACED")))
        .order_by(Order.created_at)
        .with_for_update()
    )
    return list((await db.execute(q)).scalars().all())


async def create_bill(
    db: AsyncSession, *, hotel_id: UUID, table_id: UUID, user_id: UUID, include_unserved: bool
) -> tuple[Bill, list[str]]:
    """One bill per table visit (docs/03 "Billing a table visit"). If the table already has an issued, unpaid
    bill and the guest ordered more, that bill is cancelled (a GST invoice cannot be edited; the cancelled number
    stays in the register) and one new bill covers everything. Returns the bill and the invoice numbers it
    replaced."""
    table = await db.get(DiningTable, table_id)
    if not table:
        raise not_found("Table")
    # One bill number at a time per hotel: sequential numbering must have no gaps or duplicates.
    await db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"bill:{hotel_id}"})
    if not await _unbilled(db, table_id):
        raise bad_request("nothing_to_bill", "This table has no orders to bill")
    open_bills = (
        (await db.execute(select(Bill).where(Bill.table_id == table_id, Bill.status == "ISSUED").with_for_update()))
        .scalars()
        .all()
    )
    orders = await _unbilled(db, table_id)
    already = [o for b in open_bills for o in (await db.execute(select(Order).where(Order.bill_id == b.id))).scalars()]
    unserved = sorted(o.number for o in [*orders, *already] if o.status != "SERVED")
    if unserved and not include_unserved:
        # The client warns ("still being prepared: bill anyway?") and retries with include_unserved.
        raise conflict("orders_not_served", "Some orders are not served yet", {"orders": unserved})
    replaced = []
    for old in open_bills:
        await void(db, old, "Items added: replaced by a new bill")
        replaced.append(old)
    orders = await _unbilled(db, table_id)

    profile = (await db.execute(select(HotelTaxProfile))).scalar_one_or_none()
    hotel = await db.get(Hotel, hotel_id)
    fy = financial_year(now().astimezone(ZoneInfo(hotel.timezone)))
    number = (await db.execute(select(func.coalesce(func.max(Bill.number), 0) + 1).where(Bill.fy == fy))).scalar_one()

    items = (
        (
            await db.execute(
                select(OrderItem).where(OrderItem.order_id.in_([o.id for o in orders]), OrderItem.status != "CANCELLED")
            )
        )
        .scalars()
        .all()
    )
    grouped: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for it in items:
        key = (it.name, it.unit_price_minor, it.tax_rate_bp)
        grouped[key][0] += it.qty
        grouped[key][1] += it.line_total_minor
    tax = sum(bp_of(it.line_total_minor, it.tax_rate_bp) for it in items)  # same per-line rounding as pricing
    subtotal = sum(o.subtotal_minor for o in orders)
    service = sum(o.service_charge_minor for o in orders)
    total = sum(o.total_minor for o in orders)
    assert tax == sum(o.tax_minor for o in orders) and subtotal + tax + service == total, "bill must equal orders"
    cgst, sgst = split_gst(tax)

    bill = Bill(
        hotel_id=hotel_id,
        outlet_id=table.outlet_id,
        table_id=table.id,
        fy=fy,
        number=number,
        invoice_no=invoice_no(fy, number),
        doc_type=DOC_TYPE[profile.gst_registration if profile else "UNREGISTERED"],
        status="ISSUED",
        subtotal_minor=subtotal,
        tax_minor=tax,
        cgst_minor=cgst,
        sgst_minor=sgst,
        service_charge_minor=service,
        total_minor=total,
        created_by=user_id,
    )
    db.add(bill)
    await db.flush()
    for (name, price, rate), (qty, line_total) in grouped.items():
        db.add(
            BillLine(
                hotel_id=hotel_id,
                bill_id=bill.id,
                name=name,
                qty=qty,
                unit_price_minor=price,
                tax_rate_bp=rate,
                line_total_minor=line_total,
            )
        )
    for o in orders:
        o.bill_id = bill.id
    for old in replaced:
        old.void_reason = f"Items added: replaced by {bill.invoice_no}"
    await db.flush()
    return bill, [old.invoice_no for old in replaced]


async def bill_json(db: AsyncSession, bill: Bill, copy_no: int | None = None) -> dict:
    lines = (
        (await db.execute(select(BillLine).where(BillLine.bill_id == bill.id).order_by(BillLine.name))).scalars().all()
    )
    hotel = await db.get(Hotel, bill.hotel_id)
    profile = await db.get(HotelTaxProfile, bill.hotel_id)
    table = await db.get(DiningTable, bill.table_id) if bill.table_id else None
    orders = (await db.execute(select(Order.number).where(Order.bill_id == bill.id).order_by(Order.number))).scalars()
    by_rate: dict[int, int] = defaultdict(int)
    for ln in lines:
        if ln.tax_rate_bp:
            by_rate[ln.tax_rate_bp] += bp_of(ln.line_total_minor, ln.tax_rate_bp)
    # Rate-group taxes are recomputed from summed lines for display; reconcile any rounding into the largest group
    # so the printed groups always add up to the bill's stored tax.
    if by_rate:
        drift = bill.tax_minor - sum(by_rate.values())
        by_rate[max(by_rate, key=lambda r: by_rate[r])] += drift
    groups = [
        {"rate_bp": r, "tax_minor": t, "cgst_minor": split_gst(t)[0], "sgst_minor": split_gst(t)[1]}
        for r, t in sorted(by_rate.items())
    ]
    data = {
        "id": str(bill.id),
        "invoice_no": bill.invoice_no,
        "fy": bill.fy,
        "number": bill.number,
        "doc_type": bill.doc_type,
        "status": bill.status,
        "table_label": table.label if table else None,
        "table_id": str(bill.table_id) if bill.table_id else None,
        "subtotal_minor": bill.subtotal_minor,
        "tax_minor": bill.tax_minor,
        "cgst_minor": bill.cgst_minor,
        "sgst_minor": bill.sgst_minor,
        "service_charge_minor": bill.service_charge_minor,
        "total_minor": bill.total_minor,
        "payment_method": bill.payment_method,
        "paid_at": bill.paid_at.isoformat() if bill.paid_at else None,
        "print_count": bill.print_count,
        "void_reason": bill.void_reason,
        "created_at": bill.created_at.isoformat() if bill.created_at else None,
        "orders": list(orders),
        "tax_groups": groups,
        "lines": [
            {
                "name": ln.name,
                "qty": ln.qty,
                "unit_price_minor": ln.unit_price_minor,
                "tax_rate_bp": ln.tax_rate_bp,
                "line_total_minor": ln.line_total_minor,
            }
            for ln in lines
        ],
    }
    hotel_info = {
        "name": hotel.name,
        "legal_name": hotel.legal_name,
        "city": hotel.city,
        "gstin": profile.gstin if profile and profile.gst_registration != "UNREGISTERED" else None,
    }
    data["blocks"] = bill_blocks(
        hotel=hotel_info,
        bill=data,
        lines=data["lines"],
        tax_groups=groups,
        tz=hotel.timezone,
        copy_no=copy_no if copy_no is not None else bill.print_count + 1,
        created_at=bill.created_at,
    )
    return data


async def settle(db: AsyncSession, bill: Bill, method: str) -> None:
    if bill.status != "ISSUED":
        raise conflict("bad_bill_state", f"Bill is {bill.status}")
    bill.status, bill.payment_method, bill.paid_at = "PAID", method, now()
    # Payment ends the table's QR sessions (docs/08): the next scan starts fresh.
    if bill.table_id:
        await db.execute(
            update(TableSession)
            .where(TableSession.table_id == bill.table_id, TableSession.closed_at.is_(None))
            .values(closed_at=now())
        )


async def void(db: AsyncSession, bill: Bill, reason: str) -> None:
    if bill.status == "VOID":
        raise conflict("bad_bill_state", "Bill is already void")
    bill.status, bill.void_reason = "VOID", reason
    await db.execute(update(Order).where(Order.bill_id == bill.id).values(bill_id=None))
