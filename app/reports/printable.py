"""Reports as print blocks (docs/14 "Reports as PDF"): the same data as the screen and the CSV, laid out in the
block format the app already renders for bills. English on purpose, like every printed document (docs/07)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.printing.documents import rs

METHOD_LABELS = (("CASH", "Cash"), ("UPI", "UPI"), ("CARD", "Card"))

TITLES = {
    "sales": "Sales by day",
    "items": "Item-wise sales",
    "categories": "Sales by category",
    "gst": "GST summary",
    "pnl": "Profit and loss",
    "staff": "Staff",
}


def _pct(bp: int) -> str:
    return f"{bp / 100:g}%"  # display only


def header(hotel: str, kind: str, d: dict, generated: datetime) -> list[tuple]:
    tz = d["timezone"]
    return [
        ("title", hotel),
        ("center", TITLES[kind]),
        ("center", f"{d['from']} to {d['to']} ({tz})"),
        ("small", d["basis"]),
        ("small", "Generated " + generated.astimezone(ZoneInfo(tz)).strftime("%d %b %Y %H:%M")),
        ("divider",),
    ]


def sales(d: dict) -> list[tuple]:
    t = d["totals"]
    out: list[tuple] = [
        ("row", f"Bills: {t['bills']}", f"Covers: {t['covers']}"),
        ("row", "Gross (menu value)", rs(t["gross_minor"])),
        ("row", "Discounts", "-" + rs(t["discount_minor"])),
        ("row", "Taxable value", rs(t["taxable_minor"])),
        ("row", "CGST", rs(t["cgst_minor"])),
        ("row", "SGST", rs(t["sgst_minor"])),
    ]
    if t["other_tax_minor"]:
        out.append(("row", "Other tax", rs(t["other_tax_minor"])))
    if t["service_charge_minor"]:
        out.append(("row", "Service charge", rs(t["service_charge_minor"])))
    if t["round_off_minor"]:
        out.append(("row", "Round off", rs(t["round_off_minor"])))
    out += [
        ("total", "Bill total", rs(t["total_minor"])),
        ("row", "Paid", rs(t["paid_minor"])),
        ("row", f"Unpaid ({t['unpaid_bills']} bills)", rs(t["unpaid_minor"])),
        ("row", f"Cancelled ({t['cancelled_bills']} bills, kept apart)", rs(t["cancelled_minor"])),
        ("divider",),
        ("bold", "Money received"),
        *[("row", label, rs(t["received"][m.lower() + "_minor"])) for m, label in METHOD_LABELS],
        ("row", "Total received", rs(t["received_minor"])),
    ]
    if d["unbilled"]["orders"]:
        out.append(("small", f"Not billed yet: {d['unbilled']['orders']} orders, {rs(d['unbilled']['value_minor'])}"))
    if len(d["days"]) > 1:
        out += [("divider",), ("bold", "By day")]
        out += [("row", f"{x['date']}  ({x['bills']} bills)", rs(x["total_minor"])) for x in d["days"]]
    return out


def items(d: dict, with_items: bool = True) -> list[tuple]:
    out: list[tuple] = [("bold", "By category")]
    for c in d["categories"]:
        out.append(("row", f"{c['category']}  ({c['qty']} sold, {c['share_pct']:g}%)", rs(c["taxable_minor"])))
    if with_items:
        out += [("divider",), ("bold", "By item")]
        for i in d["items"]:
            out.append(("row", f"{i['qty']} x {i['name']}", rs(i["taxable_minor"])))
    t = d["totals"]
    out += [("divider",), ("total", f"Taxable value ({t['qty']} sold)", rs(t["taxable_minor"]))]
    return out


def gst(d: dict) -> list[tuple]:
    out: list[tuple] = [("bold", f"By rate (SAC {d['sac']})")]
    for r in d["by_rate"]:
        out += [
            ("row", f"GST {_pct(r['rate_bp'])}: taxable", rs(r["taxable_minor"])),
            ("row", "   CGST + SGST", f"{rs(r['cgst_minor'])} + {rs(r['sgst_minor'])}"),
        ]
    t = d["totals"]
    out += [("row", "Taxable value", rs(t["taxable_minor"])), ("total", "Total GST", rs(t["tax_minor"]))]
    if d.get("b2b"):
        out += [("divider",), ("bold", "Company (B2B) bills")]
        for x in d["b2b"]:
            out += [
                ("row", f"{x['invoice_no']}  {x['date']}", rs(x["tax_minor"])),
                (
                    "small",
                    f"{x['buyer_name']}, GSTIN {x['buyer_gstin']}, GST {_pct(x['rate_bp'])}, "
                    f"taxable {rs(x['taxable_minor'])}",
                ),
            ]
    out += [("divider",), ("bold", "Documents issued")]
    for x in d["documents"]:
        out.append(
            (
                "text",
                f"{x['doc_type']} {x['fy']}: {x['from_no']} to {x['to_no']}, "
                f"{x['count']} issued, {x['cancelled']} cancelled, {x['net']} net",
            )
        )
    return out


def pnl(d: dict) -> list[tuple]:
    out: list[tuple] = []
    cols = [*d["months"], {"month": "Total", "so_far": False, **d["totals"]}]
    for c in cols:
        title = c["month"] + (" (so far)" if c["so_far"] else "")
        out += [("bold", title), ("row", f"Sales before GST ({c['bills']} bills)", rs(c["sales_minor"]))]
        if c["service_charge_minor"]:
            out.append(("row", "Service charge", rs(c["service_charge_minor"])))
        out += [("row", f"   {e['category']}", "-" + rs(e["amount_minor"])) for e in c["expenses"]]
        out.append(("row", "Expenses", "-" + rs(c["expenses_minor"])))
        margin = "" if c["margin_pct"] is None else f" ({c['margin_pct']:g}% of sales)"
        word = "Operating profit" if c["operating_profit_minor"] >= 0 else "Operating loss"
        out += [
            ("total", word + margin, rs(c["operating_profit_minor"])),
            ("small", f"GST collected {rs(c['gst_collected_minor'])}: owed to the government, not income"),
            ("divider",),
        ]
    return out


def staff(d: dict) -> list[tuple]:
    out: list[tuple] = []
    for s in d["staff"]:
        out += [
            ("bold", s["name"]),
            ("row", f"Bills ({s['bills']})", rs(s["billed_minor"])),
            ("row", "Money received", rs(s["received_minor"])),
            ("row", f"Discounts given ({s['discounts']})", rs(s["discount_minor"])),
            ("row", "Bills cancelled by hand", str(s["cancellations"])),
            ("divider",),
        ]
    return out or [("text", "No staff activity in these dates")]


def blocks(kind: str, d: dict) -> list[tuple]:
    return {
        "sales": sales,
        "items": items,
        "categories": lambda x: items(x, with_items=False),
        "gst": gst,
        "pnl": pnl,
        "staff": staff,
    }[kind](d)
