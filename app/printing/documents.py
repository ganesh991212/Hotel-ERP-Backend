"""Builds KOT and bill print blocks (see escpos.py). Money is formatted here, on the server, from paise."""

from datetime import datetime
from zoneinfo import ZoneInfo


def rs(minor: int) -> str:
    """Paise -> 'Rs. 1,23,456.78' using integer string slicing (Indian grouping, no floats)."""
    neg = minor < 0
    d = str(abs(minor)).rjust(3, "0")
    rupees, paise = d[:-2], d[-2:]
    if len(rupees) > 3:
        head, last3 = rupees[:-3], rupees[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        rupees = ",".join(groups) + "," + last3
    return f"{'-' if neg else ''}Rs. {rupees}.{paise}"


def _local(ts: datetime | None, tz: str) -> str:
    if ts is None:
        return ""
    return ts.astimezone(ZoneInfo(tz)).strftime("%d %b %Y %H:%M")


CHANNEL = {"QR": "QR order", "WAITER": "Server", "ADMIN": "Admin", "TAKEAWAY": "Takeaway"}


def kot_blocks(
    *, hotel_name: str, order: dict, tz: str, copy_no: int, printed_by: str | None, created_at: datetime | None
) -> list[tuple]:
    table = (order.get("table") or {}).get("label")
    where = f"Table {table}" if table else "TAKEAWAY"
    blocks: list[tuple] = [
        ("title", "KOT" if copy_no <= 1 else f"KOT REPRINT {copy_no}"),
        ("center", hotel_name),
        ("divider",),
        ("total", f"Order #{order['number']}", where),
        ("row", _local(created_at, tz), CHANNEL.get(order["channel"], order["channel"])),
        ("divider",),
    ]
    for it in order["items"]:
        if it.get("status") == "CANCELLED":
            continue
        blocks.append(("bold", f"{it['qty']} x {it['name']}"))
        if it.get("note"):
            blocks.append(("text", f"   > {it['note']}"))
    blocks.append(("divider",))
    if not table and order.get("customer_name"):
        blocks.append(("bold", f"For: {order['customer_name']}"))
    if order.get("note"):
        blocks.append(("text", f"NOTE: {order['note']}"))
    if order.get("chef"):
        blocks.append(("text", f"Chef: {order['chef']['name']}"))
    if printed_by:
        blocks.append(("text", f"Printed by {printed_by}"))
    blocks += [("feed", 1), ("cut",)]
    return blocks


def table_qr_blocks(
    *,
    hotel_name: str,
    address_line: str | None,
    has_logo: bool,
    table_label: str,
    area: str | None,
    url: str,
    powered_by: str,
) -> list[tuple]:
    """Branded table card: logo + hotel name, one-line address, QR, table name, how to use it, powered-by footer.
    English only (thermal printers have no Indic fonts; see docs/07)."""
    blocks: list[tuple] = [("logo",)] if has_logo else []
    blocks.append(("title", hotel_name))
    if address_line:
        blocks.append(("center", address_line))
    blocks += [
        ("feed", 1),
        ("qr", url),
        ("feed", 1),
        ("badge", f"Table {table_label}"),
        ("center", "Scan to order" + (f"  -  {area}" if area else "")),
        ("divider",),
        ("small", powered_by),
        ("feed", 1),
        ("cut",),
    ]
    return blocks


DOC_TITLE = {"TAX_INVOICE": "TAX INVOICE", "BILL_OF_SUPPLY": "BILL OF SUPPLY", "BILL": "BILL"}


def bill_blocks(
    *,
    hotel: dict,
    bill: dict,
    lines: list[dict],
    tax_groups: list[dict],
    tz: str,
    copy_no: int,
    created_at: datetime | None,
) -> list[tuple]:
    blocks: list[tuple] = [("title", hotel["name"])]
    if hotel.get("legal_name"):
        blocks.append(("center", hotel["legal_name"]))
    if hotel.get("city"):
        blocks.append(("center", hotel["city"]))
    if hotel.get("gstin"):
        blocks.append(("center", f"GSTIN: {hotel['gstin']}"))
    blocks.append(("center", DOC_TITLE[bill["doc_type"]] + ("" if copy_no <= 1 else " - DUPLICATE")))
    if bill["status"] == "VOID":
        blocks.append(("title", "VOID"))
    blocks += [
        ("divider",),
        (
            "row",
            f"Bill No: {bill['invoice_no']}",
            f"Table {bill['table_label']}" if bill.get("table_label") else "Takeaway",
        ),
        ("row", _local(created_at, tz), bill.get("customer_name") or ""),
        ("divider",),
        ("row", "Item", "Amount"),
        ("divider",),
    ]
    for ln in lines:
        blocks.append(("text", ln["name"]))
        blocks.append(("row", f"   {ln['qty']} x {rs(ln['unit_price_minor'])}", rs(ln["line_total_minor"])))
    blocks += [("divider",), ("row", "Subtotal", rs(bill["subtotal_minor"]))]
    if bill.get("discount_minor"):
        blocks.append(("row", "Discount", "-" + rs(bill["discount_minor"])))
        if bill.get("discount_reason"):
            blocks.append(("small", f"  ({bill['discount_reason']})"))
        blocks.append(("row", "Taxable value", rs(bill["taxable_minor"])))
    for g in tax_groups:
        half = g["rate_bp"] / 200  # display only: 500 bp -> "2.5%"
        pct = f"{half:g}%"
        blocks.append(("row", f"CGST @ {pct}", rs(g["cgst_minor"])))
        blocks.append(("row", f"SGST @ {pct}", rs(g["sgst_minor"])))
    if bill["service_charge_minor"]:
        blocks.append(("row", "Service charge", rs(bill["service_charge_minor"])))
    blocks += [("divider",), ("total", "TOTAL", rs(bill["total_minor"])), ("divider",)]
    if bill["status"] == "PAID":
        payments = bill.get("payments") or []
        if len(payments) > 1:
            for pay in payments:
                blocks.append(("row", f"Paid by {pay['method']}", rs(pay["amount_minor"])))
        else:
            blocks.append(("row", "Paid by", bill.get("payment_method") or ""))
    if bill["doc_type"] == "BILL_OF_SUPPLY":
        blocks.append(("text", "Composition taxable person, not eligible to collect tax on supplies."))
    blocks += [("center", "Thank you! Please visit again."), ("feed", 1), ("cut",)]
    return blocks
