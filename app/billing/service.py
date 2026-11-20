"""Table bills. Totals come from the orders' server-computed amounts; nothing is re-priced here."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.security import now
from app.models import Bill, BillLine, BillPayment, DiningTable, Hotel, HotelTaxProfile, Order, OrderItem, TableSession
from app.ordering.pricing import addons_label, apply_discount, bp_of, discount_bp_of, discount_minor_for
from app.printing.documents import bill_blocks

DOC_TYPE = {"REGULAR": "TAX_INVOICE", "COMPOSITION": "BILL_OF_SUPPLY", "UNREGISTERED": "BILL"}

# A bill cancelled only because a newer bill for the same food replaced it. Reports keep these apart from
# real cancellations (docs/14 "Reports").
REPLACED_MARK = ": replaced by "


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
        # Guest bills of a split stay as they are; food ordered after a split gets a bill of its own.
        (
            await db.execute(
                select(Bill)
                .where(Bill.table_id == table_id, Bill.status == "ISSUED", Bill.split_group_id.is_(None))
                .with_for_update()
            )
        )
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
        await void(db, old, f"{why}{REPLACED_MARK}a new bill", keep_payments=True)
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
        old.void_reason = f"{why}{REPLACED_MARK}{bill.invoice_no}"
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
        await void(db, old_bill, f"Discount applied{REPLACED_MARK}a new bill", keep_payments=True)
    bill = await _issue(
        db, hotel_id=hotel_id, outlet_id=o.outlet_id, table_id=None, orders=[o], user_id=user_id, discount=discount
    )
    if old_bill is not None:
        old_bill.void_reason = f"Discount applied{REPLACED_MARK}{bill.invoice_no}"
        await _carry_payments(db, [old_bill], bill)
    return bill, [old_bill.invoice_no] if old_bill is not None else []


# ---------- Split by items (docs/03 "Split by items", docs/14) ----------


def line_key(name: str, unit_price_minor: int, tax_rate_bp: int) -> str:
    """Stable id of a grouped bill line (same dish, price and GST rate), used by the split screen."""
    return f"{name}|{unit_price_minor}|{tax_rate_bp}"


@dataclass(frozen=True)
class SplitSpec:
    """How one grouped line is shared out: whole units per guest (`qty`, one number per guest), or the whole line
    shared equally by some guests (`shared`, guest numbers from 1)."""

    key: str
    qty: list[int] | None = None
    shared: list[int] | None = None


@dataclass(frozen=True)
class GuestLine:
    name: str
    qty: int
    unit_price_minor: int
    tax_rate_bp: int
    line_total_minor: int


def apportion(total_minor: int, weights: list[int]) -> list[int]:
    """Split an amount by whole-number weights, to the paisa (largest remainder; ties go to the earlier guest)."""
    whole = sum(weights)
    base = [total_minor * w // whole for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(total_minor * weights[i] % whole), i))
    for i in order[: total_minor - sum(base)]:
        base[i] += 1
    return base


def allocate(
    grouped: dict[tuple[str, int, int], tuple[int, int]], guests: int, specs: list[SplitSpec]
) -> list[list[GuestLine]]:
    """Pure: give every grouped line (name, price, rate) -> (qty, total) to guests exactly as `specs` say. Every
    line must be given out in full and every guest must get something; values add up to the paisa."""
    by_key = {line_key(*k): k for k in grouped}
    seen = [x.key for x in specs]
    if sorted(seen) != sorted(by_key):
        raise conflict(
            "lines_changed",
            "The table's items changed; open the split again",
            {"missing": sorted(set(by_key) - set(seen)), "unknown": sorted(set(seen) - set(by_key))},
        )
    out: list[list[GuestLine]] = [[] for _ in range(guests)]
    for spec in specs:
        name, price, rate = by_key[spec.key]
        qty, total = grouped[(name, price, rate)]
        if (spec.qty is None) == (spec.shared is None):
            raise bad_request("bad_split", f"Give either qty or shared for {name}")
        if spec.qty is not None:
            if len(spec.qty) != guests or any(q < 0 for q in spec.qty) or sum(spec.qty) != qty:
                raise bad_request("bad_split", f"{name}: give out all {qty} exactly", {"key": spec.key, "qty": qty})
            for g, (q, value) in enumerate(zip(spec.qty, apportion(total, spec.qty), strict=True)):
                if q:
                    out[g].append(GuestLine(name, q, price, rate, value))
        else:
            who = sorted(set(spec.shared or []))
            if len(who) < 2 or who[0] < 1 or who[-1] > guests or len(who) != len(spec.shared or []):
                raise bad_request("bad_split", f"{name}: share between two or more different guests", {"key": spec.key})
            label = f"{qty} x {name}" if qty > 1 else name
            for g, value in zip(who, split_equally(total, len(who)), strict=True):
                # A share is one line priced at its part of the value: qty x price = line total still holds.
                out[g - 1].append(GuestLine(f"{label} (1/{len(who)} share)", 1, value, rate, value))
    empty = [g + 1 for g, lines in enumerate(out) if not lines]
    if empty:
        raise bad_request("guest_empty", "Every guest needs at least one item", {"guests": empty})
    return out


async def _grouped(db: AsyncSession, orders: list[Order]) -> tuple[dict[tuple, list[int]], list[OrderItem]]:
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
        # Add-ons are part of the line's name, so "Biryani (Half) + Raita" and "+ Salad" never merge.
        name = f"{it.name} + {addons_label(it.addons)}" if it.addons else it.name
        if it.price_rule:  # happy-hour sales stay their own line (and their own row in the item report)
            name = f"{name} ({it.price_rule})"
        key = (name, it.unit_price_minor, it.tax_rate_bp)
        grouped[key][0] += it.qty
        grouped[key][1] += it.line_total_minor
    return grouped, list(items)


async def _split_open_bill(db: AsyncSession, table_id: UUID) -> Bill | None:
    """The table's ordinary unpaid bill, if any (a split replaces it)."""
    return (
        await db.execute(
            select(Bill)
            .where(Bill.table_id == table_id, Bill.status == "ISSUED", Bill.split_group_id.is_(None))
            .with_for_update()
        )
    ).scalar_one_or_none()


async def split_preview(db: AsyncSession, table_id: UUID) -> dict:
    """What a split would share out: the visit's food not yet on a guest bill (unbilled + the ordinary open bill)."""
    if not await db.get(DiningTable, table_id):
        raise not_found("Table")
    open_bill = await _split_open_bill(db, table_id)
    orders = await _unbilled(db, table_id)
    if open_bill:
        orders += list((await db.execute(select(Order).where(Order.bill_id == open_bill.id))).scalars())
    grouped, _ = await _grouped(db, orders) if orders else ({}, [])
    return {
        "table_id": str(table_id),
        "lines": [
            {
                "key": line_key(*k),
                "name": k[0],
                "qty": v[0],
                "unit_price_minor": k[1],
                "tax_rate_bp": k[2],
                "line_total_minor": v[1],
            }
            for k, v in sorted(grouped.items())
        ],
        "unserved": sorted(o.number for o in orders if o.status != "SERVED"),
        "open_bill": {
            "id": str(open_bill.id),
            "invoice_no": open_bill.invoice_no,
            "discount_minor": open_bill.discount_minor,
        }
        if open_bill
        else None,
        "open_bill_paid_minor": await paid_minor(db, open_bill) if open_bill else 0,
    }


async def split_by_items(
    db: AsyncSession,
    *,
    hotel_id: UUID,
    table_id: UUID,
    user_id: UUID,
    guests: int,
    specs: list[SplitSpec],
    include_unserved: bool,
) -> tuple[list[Bill], list[str]]:
    """One GST bill per guest for this table visit. Each bill has its own number and its tax is calculated on its
    own lines (never a divided total). An ordinary unpaid bill for the visit is cancelled and replaced, so the same
    food is never on two live bills. Returns the guest bills and the invoice numbers replaced."""
    table = await db.get(DiningTable, table_id)
    if not table:
        raise not_found("Table")
    await db.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"bill:{hotel_id}"})
    open_bill = await _split_open_bill(db, table_id)
    if open_bill and await paid_minor(db, open_bill):
        raise conflict("has_payments", "Part of this bill is paid; remove those payments first")
    if open_bill and open_bill.discount_minor:
        # Splitting would drop the discount (guests would pay more). Discounts per guest bill come later.
        raise conflict(
            "bill_discounted",
            "This bill has a discount; cancel it before splitting by items",
            {"invoice_no": open_bill.invoice_no},
        )
    orders = await _unbilled(db, table_id)
    already = []
    if open_bill:
        already = list((await db.execute(select(Order).where(Order.bill_id == open_bill.id))).scalars())
    if not orders and not already:
        raise bad_request("nothing_to_bill", "This table has no orders to bill")
    unserved = sorted(o.number for o in [*orders, *already] if o.status != "SERVED")
    if unserved and not include_unserved:
        raise conflict("orders_not_served", "Some orders are not served yet", {"orders": unserved})
    grouped, _ = await _grouped(db, [*orders, *already])
    parts = allocate({k: (v[0], v[1]) for k, v in grouped.items()}, guests, specs)  # validate before cancelling

    if open_bill:
        await void(db, open_bill, f"Split by items{REPLACED_MARK}guest bills")
    orders = await _unbilled(db, table_id)
    profile = (await db.execute(select(HotelTaxProfile))).scalar_one_or_none()
    # Service charge only if the visit's orders carried it; then each guest pays it on their own subtotal.
    sc_bp = (profile.service_charge_bp if profile else 0) if any(o.service_charge_minor for o in orders) else 0
    bills: list[Bill] = []
    for g, lines in enumerate(parts, start=1):
        subtotal = sum(x.line_total_minor for x in lines)
        tax = sum(bp_of(x.line_total_minor, x.tax_rate_bp) for x in lines)  # per line, recalculated
        service = bp_of(subtotal, sc_bp)
        cgst, sgst = split_gst(tax)
        fy, number = await _next_number(db, hotel_id)
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
            total_minor=subtotal + tax + service,
            guest_no=g,
            guest_count=guests,
            created_by=user_id,
        )
        # The first guest bill anchors the group (it refers to itself); the visit's orders point to it.
        bill.id = uuid4()
        bill.split_group_id = bills[0].id if bills else bill.id
        db.add(bill)
        await db.flush()
        for x in lines:
            db.add(
                BillLine(
                    hotel_id=hotel_id,
                    bill_id=bill.id,
                    name=x.name,
                    qty=x.qty,
                    unit_price_minor=x.unit_price_minor,
                    tax_rate_bp=x.tax_rate_bp,
                    line_total_minor=x.line_total_minor,
                )
            )
        bills.append(bill)
    for o in orders:
        o.bill_id = bills[0].id
    if open_bill:
        open_bill.void_reason = f"Split by items{REPLACED_MARK}{', '.join(b.invoice_no for b in bills)}"
    await db.flush()
    return bills, [open_bill.invoice_no] if open_bill else []


async def _group(db: AsyncSession, bill: Bill) -> list[Bill]:
    if not bill.split_group_id:
        return [bill]
    q = select(Bill).where(Bill.split_group_id == bill.split_group_id).order_by(Bill.guest_no).with_for_update()
    return list((await db.execute(q)).scalars())


async def _next_number(db: AsyncSession, hotel_id: UUID) -> tuple[str, int]:
    """Next invoice number in the hotel's financial year. Caller holds the hotel's bill-number lock."""
    hotel = await db.get(Hotel, hotel_id)
    fy = financial_year(now().astimezone(ZoneInfo(hotel.timezone)))
    number = (await db.execute(select(func.coalesce(func.max(Bill.number), 0) + 1).where(Bill.fy == fy))).scalar_one()
    return fy, number


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
    fy, number = await _next_number(db, hotel_id)
    grouped, items = await _grouped(db, orders)
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


async def _split_json(db: AsyncSession, bill: Bill) -> dict | None:
    """For a guest bill: which guest it is and every guest bill of the visit (to see who still owes)."""
    if not bill.split_group_id:
        return None
    rows = (
        await db.execute(select(Bill).where(Bill.split_group_id == bill.split_group_id).order_by(Bill.guest_no))
    ).scalars()
    siblings = []
    for b in rows:
        paid = await paid_minor(db, b)
        siblings.append(
            {
                "id": str(b.id),
                "invoice_no": b.invoice_no,
                "guest_no": b.guest_no,
                "status": b.status,
                "total_minor": b.total_minor,
                "due_minor": 0 if b.status != "ISSUED" else b.total_minor - paid,
            }
        )
    return {
        "group_id": str(bill.split_group_id),
        "guest_no": bill.guest_no,
        "guest_count": bill.guest_count,
        "bills": siblings,
    }


def tax_by_rate(tax_minor: int, lines) -> dict[int, int]:
    """A bill's tax per GST rate. The printed bill and the GST report both use this, so they always agree."""
    by_rate: dict[int, int] = defaultdict(int)
    for ln in lines:
        if ln.tax_rate_bp:
            # Discounted bills are taxed on each line's discounted value (exactly as stored).
            by_rate[ln.tax_rate_bp] += bp_of(ln.line_total_minor - ln.discount_minor, ln.tax_rate_bp)
    # Rate-group taxes are recomputed from summed lines; reconcile any rounding into the largest group
    # so the groups always add up to the bill's stored tax.
    if by_rate:
        drift = tax_minor - sum(by_rate.values())
        by_rate[max(by_rate, key=lambda r: by_rate[r])] += drift
    return dict(by_rate)


async def bill_json(db: AsyncSession, bill: Bill, copy_no: int | None = None) -> dict:
    lines = (
        (await db.execute(select(BillLine).where(BillLine.bill_id == bill.id).order_by(BillLine.name))).scalars().all()
    )
    hotel = await db.get(Hotel, bill.hotel_id)
    profile = await db.get(HotelTaxProfile, bill.hotel_id)
    table = await db.get(DiningTable, bill.table_id) if bill.table_id else None
    anchor = bill.split_group_id or bill.id
    orders = (await db.execute(select(Order.number).where(Order.bill_id == anchor).order_by(Order.number))).scalars()
    by_rate = tax_by_rate(bill.tax_minor, lines)
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
        "split": await _split_json(db, bill),
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
    # Paying the table's last open bill ends its QR sessions (docs/08): the next scan starts fresh. With guest bills
    # (split by items) the table stays open until every guest has paid.
    still_owed = (
        await db.execute(
            select(func.count()).where(Bill.table_id == bill.table_id, Bill.status == "ISSUED", Bill.id != bill.id)
        )
    ).scalar_one()
    if bill.table_id and not still_owed:
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
    group = await _group(db, bill)
    if len(group) > 1:
        # Guest bills share the visit's orders: cancel all of them or none (then split or bill again).
        if any(b.status == "PAID" for b in group) or any([await paid_minor(db, b) for b in group]):
            raise conflict("split_partly_paid", "A guest has already paid; this split cannot be cancelled")
        for b in group:
            if b.status != "VOID":
                b.status, b.void_reason = "VOID", reason
        await db.execute(update(Order).where(Order.bill_id == bill.split_group_id).values(bill_id=None))
        return
    bill.status, bill.void_reason = "VOID", reason
    await db.execute(update(Order).where(Order.bill_id == bill.id).values(bill_id=None))
