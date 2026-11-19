"""Table bills. Totals come from the orders' server-computed amounts; nothing is re-priced here."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.security import now
from app.models import Bill, BillLine, BillPayment, DiningTable, Hotel, HotelTaxProfile, Order, OrderItem, TableSession
from app.ordering.pricing import apply_discount, bp_of, discount_bp_of, discount_minor_for
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


@dataclass(frozen=True)
class DiscountSpec:
    """A requested bill discount: `percent_bp` (1000 = 10%) or `amount_minor`, why, by whom, and the most this
    person's role may give (basis points of the subtotal)."""

    percent_bp: int | None
    amount_minor: int | None
    reason: str
    by_user: UUID
    max_bp: int


async def _unbilled(db: AsyncSession, table_id: UUID) -> list[Order]:
    q = (
        select(Order)
        .where(Order.table_id == table_id, Order.bill_id.is_(None), Order.status.not_in(("CANCELLED", "PLACED")))
        .order_by(Order.created_at)
        .with_for_update()
    )
    return list((await db.execute(q)).scalars().all())


async def create_bill(
    db: AsyncSession,
    *,
    hotel_id: UUID,
    table_id: UUID,
    user_id: UUID,
    include_unserved: bool,
    discount: DiscountSpec | None = None,
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
    has_new = bool(await _unbilled(db, table_id))
    open_bills = (
        (await db.execute(select(Bill).where(Bill.table_id == table_id, Bill.status == "ISSUED").with_for_update()))
        .scalars()
        .all()
    )
    # Re-billing with nothing new is only meaningful to apply a discount to the open bill.
    if not has_new and not (discount and open_bills):
        raise bad_request("nothing_to_bill", "This table has no orders to bill")
    orders = await _unbilled(db, table_id)
    already = [o for b in open_bills for o in (await db.execute(select(Order).where(Order.bill_id == b.id))).scalars()]
    unserved = sorted(o.number for o in [*orders, *already] if o.status != "SERVED")
    if unserved and not include_unserved:
        # The client warns ("still being prepared: bill anyway?") and retries with include_unserved.
        raise conflict("orders_not_served", "Some orders are not served yet", {"orders": unserved})
    replaced = []
    why = "Items added" if has_new else "Discount applied"
    for old in open_bills:
        await void(db, old, f"{why}: replaced by a new bill", keep_payments=True)
        replaced.append(old)
    orders = await _unbilled(db, table_id)
    bill = await _issue(
        db,
        hotel_id=hotel_id,
        outlet_id=table.outlet_id,
        table_id=table.id,
        orders=orders,
        user_id=user_id,
        discount=discount,
    )
    for old in replaced:
        old.void_reason = f"{why}: replaced by {bill.invoice_no}"
    await _carry_payments(db, replaced, bill)
    await db.flush()
    return bill, [old.invoice_no for old in replaced]


async def create_order_bill(
    db: AsyncSession,
    *,
    hotel_id: UUID,
    order_id: UUID,
    user_id: UUID,
    include_unserved: bool,
    discount: DiscountSpec | None = None,
) -> tuple[Bill, list[str]]:
    """Bill one takeaway / counter order (no table, so no table visit). Usually paid at the counter before or at
    pickup; billing while it is still cooking warns like a table bill."""
    await db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"bill:{hotel_id}"})
    o = await db.get(Order, order_id, with_for_update=True)
    if not o:
        raise not_found("Order")
    if o.table_id is not None:
        raise bad_request("table_order", "Bill table orders by table: one bill per table visit")
    old_bill = await db.get(Bill, o.bill_id, with_for_update=True) if o.bill_id else None
    if old_bill is not None and not (discount and old_bill.status == "ISSUED"):
        raise conflict("already_billed", "This order already has a bill")
    if o.status in ("CANCELLED", "PLACED"):
        raise conflict("bad_order_state", f"Order is {o.status}")
    if o.status != "SERVED" and not include_unserved:
        raise conflict("orders_not_served", "This order is not handed over yet", {"orders": [o.number]})
    if old_bill is not None:  # applying a discount to the unpaid bill: cancel it, issue a new one
        await void(db, old_bill, "Discount applied: replaced by a new bill", keep_payments=True)
    bill = await _issue(
        db, hotel_id=hotel_id, outlet_id=o.outlet_id, table_id=None, orders=[o], user_id=user_id, discount=discount
    )
    if old_bill is not None:
        old_bill.void_reason = f"Discount applied: replaced by {bill.invoice_no}"
        await _carry_payments(db, [old_bill], bill)
    return bill, [old_bill.invoice_no] if old_bill is not None else []


async def _issue(
    db: AsyncSession,
    *,
    hotel_id: UUID,
    outlet_id: UUID,
    table_id: UUID | None,
    orders: list[Order],
    user_id: UUID,
    discount: DiscountSpec | None = None,
) -> Bill:
    """Issue one GST bill covering exactly these orders. Caller holds the hotel's bill-number lock."""
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
    keys = list(grouped)
    shares = [0] * len(keys)
    discount_minor = 0
    if discount is not None:
        discount_minor = discount_minor_for(
            subtotal, percent_bp=discount.percent_bp, amount_minor=discount.amount_minor
        )
        if discount_minor <= 0:
            raise bad_request("bad_discount", "The discount must be more than zero")
        if discount_bp_of(discount_minor, subtotal) > discount.max_bp:
            raise forbidden(
                "discount_limit",
                f"Your role can give at most {discount.max_bp / 100:g}% off",
                {"max_bp": discount.max_bp},
            )
        # Service charge was on; it now applies to the discounted subtotal at the hotel's rate.
        sc_bp = (profile.service_charge_bp if profile else 0) if service else 0
        d = apply_discount([(grouped[k][1], k[2]) for k in keys], discount_minor, sc_bp)
        shares, tax, service, total = d.line_discounts, d.tax_minor, d.service_charge_minor, d.total_minor
    cgst, sgst = split_gst(tax)

    bill = Bill(
        hotel_id=hotel_id,
        outlet_id=outlet_id,
        table_id=table_id,
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
        discount_minor=discount_minor,
        discount_reason=discount.reason if discount else None,
        discount_by=discount.by_user if discount else None,
        created_by=user_id,
    )
    db.add(bill)
    await db.flush()
    for (name, price, rate), share in zip(keys, shares, strict=True):
        qty, line_total = grouped[(name, price, rate)]
        db.add(
            BillLine(
                hotel_id=hotel_id,
                bill_id=bill.id,
                name=name,
                qty=qty,
                unit_price_minor=price,
                tax_rate_bp=rate,
                line_total_minor=line_total,
                discount_minor=share,
            )
        )
    for o in orders:
        o.bill_id = bill.id
    await db.flush()
    return bill


async def _customer(db: AsyncSession, bill: Bill) -> str | None:
    """Takeaway bills name the guest (from the order); table bills do not."""
    if bill.table_id:
        return None
    return (await db.execute(select(Order.customer_name).where(Order.bill_id == bill.id).limit(1))).scalar()


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
            # Discounted bills are taxed on each line's discounted value (exactly as stored).
            by_rate[ln.tax_rate_bp] += bp_of(ln.line_total_minor - ln.discount_minor, ln.tax_rate_bp)
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
        "customer_name": await _customer(db, bill),
        "subtotal_minor": bill.subtotal_minor,
        "discount_minor": bill.discount_minor,
        "discount_reason": bill.discount_reason,
        "taxable_minor": bill.subtotal_minor - bill.discount_minor,
        "tax_minor": bill.tax_minor,
        "cgst_minor": bill.cgst_minor,
        "sgst_minor": bill.sgst_minor,
        "service_charge_minor": bill.service_charge_minor,
        "total_minor": bill.total_minor,
        "payment_method": bill.payment_method,
        "payments": [
            {
                "id": str(p.id),
                "method": p.method,
                "amount_minor": p.amount_minor,
                "reference": p.reference,
                "at": p.created_at.isoformat() if p.created_at else None,
            }
            for p in (
                await db.execute(
                    select(BillPayment).where(BillPayment.bill_id == bill.id).order_by(BillPayment.created_at)
                )
            ).scalars()
        ],
        "paid_minor": await paid_minor(db, bill),
        "due_minor": 0 if bill.status != "ISSUED" else bill.total_minor - await paid_minor(db, bill),
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
                "discount_minor": ln.discount_minor,
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


async def paid_minor(db: AsyncSession, bill: Bill) -> int:
    q = select(func.coalesce(func.sum(BillPayment.amount_minor), 0)).where(BillPayment.bill_id == bill.id)
    return int((await db.execute(q)).scalar_one())


async def add_payment(
    db: AsyncSession, bill: Bill, *, method: str, amount_minor: int | None, reference: str | None, user_id: UUID
) -> BillPayment:
    """One part of the payment (docs/03 "Payments"). `amount_minor` None = whatever is still due. When the parts
    reach the total the bill is paid; paying more than is due is refused (give change outside the app)."""
    if bill.status != "ISSUED":
        raise conflict("bad_bill_state", f"Bill is {bill.status}")
    due = bill.total_minor - await paid_minor(db, bill)
    amount = due if amount_minor is None else amount_minor
    if amount <= 0 or due <= 0:
        raise bad_request("bad_amount", "Nothing is due on this bill")
    if amount > due:
        raise conflict("overpayment", "That is more than is due on this bill", {"due_minor": due})
    p = BillPayment(
        hotel_id=bill.hotel_id,
        bill_id=bill.id,
        method=method,
        amount_minor=amount,
        reference=reference,
        received_by=user_id,
    )
    db.add(p)
    await db.flush()
    if amount == due:
        await _mark_paid(db, bill)
    return p


async def remove_payment(db: AsyncSession, bill: Bill, payment_id: UUID) -> None:
    """Undo a part entered by mistake. Only while the bill is unpaid: a paid bill's takings are final."""
    if bill.status != "ISSUED":
        raise conflict("bad_bill_state", f"Bill is {bill.status}")
    p = await db.get(BillPayment, payment_id)
    if p is None or p.bill_id != bill.id:
        raise not_found("Payment")
    await db.delete(p)
    await db.flush()


def split_equally(total_minor: int, parts: int) -> list[int]:
    """Equal shares that add up to the paisa; the first shares take the leftover paise."""
    base, extra = divmod(total_minor, parts)
    return [base + 1 if i < extra else base for i in range(parts)]


async def _carry_payments(db: AsyncSession, old_bills: list[Bill], new_bill: Bill) -> None:
    """A replaced bill's payments move to its replacement, so money taken is never lost with a cancelled bill."""
    if not old_bills:
        return
    await db.execute(
        update(BillPayment).where(BillPayment.bill_id.in_([b.id for b in old_bills])).values(bill_id=new_bill.id)
    )
    paid = await paid_minor(db, new_bill)
    if paid > new_bill.total_minor:
        raise conflict(
            "payments_exceed_total",
            "More has been paid than the new total; remove a payment first",
            {"paid_minor": paid, "total_minor": new_bill.total_minor},
        )
    if paid and paid == new_bill.total_minor:
        await _mark_paid(db, new_bill)


async def settle(db: AsyncSession, bill: Bill, method: str, user_id: UUID | None = None) -> None:
    """Pay whatever is still due in one method (the common case)."""
    await add_payment(db, bill, method=method, amount_minor=None, reference=None, user_id=user_id)


async def _mark_paid(db: AsyncSession, bill: Bill) -> None:
    methods = set((await db.execute(select(BillPayment.method).where(BillPayment.bill_id == bill.id))).scalars())
    bill.status, bill.paid_at = "PAID", now()
    bill.payment_method = methods.pop() if len(methods) == 1 else "MIXED"
    # Payment ends the table's QR sessions (docs/08): the next scan starts fresh.
    if bill.table_id:
        await db.execute(
            update(TableSession)
            .where(TableSession.table_id == bill.table_id, TableSession.closed_at.is_(None))
            .values(closed_at=now())
        )


async def void(db: AsyncSession, bill: Bill, reason: str, *, keep_payments: bool = False) -> None:
    if bill.status == "VOID":
        raise conflict("bad_bill_state", "Bill is already void")
    if not keep_payments and bill.status == "ISSUED" and await paid_minor(db, bill):
        raise conflict("has_payments", "Part of this bill is paid; remove those payments first")
    bill.status, bill.void_reason = "VOID", reason
    await db.execute(update(Order).where(Order.bill_id == bill.id).values(bill_id=None))
