"""Sales reports (docs/14 "Reports", docs/13 section "Reports").

One source of truth: issued bills, their lines and their payments. Every report states its basis, and the money
ladder always reconciles: gross - discount = taxable; taxable + tax + service charge (+ round off) = bill total.
Dates are the hotel's own calendar days (its timezone). All sums are integer paise, computed here.
"""

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import Date, cast, func, select

from app.billing.service import REPLACED_MARK, split_gst, tax_by_rate
from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request
from app.models import AuditLog, Bill, BillLine, BillPayment, DiningTable, Hotel, Order, User

router = APIRouter(tags=["reports"])

MAX_DAYS = 92  # a quarter: enough for GST returns, small enough to answer quickly
LIVE = ("ISSUED", "PAID")  # a cancelled (VOID) bill is never sales
METHODS = ("CASH", "UPI", "CARD")
BASIS = "Issued bills on the hotel's calendar day; cancelled bills excluded and listed separately"
SAC_RESTAURANT = "996331"  # GST services code for restaurant service


class Period:
    def __init__(self, tz: str, start: date, end: date):
        self.tz, self.start, self.end = tz, start, end

    def on(self, column):
        """Filter a timestamp column to the period, using the hotel's local day."""
        local = self.day(column)
        return [local >= self.start, local <= self.end]

    def day(self, column):
        return cast(func.timezone(self.tz, column), Date)

    def head(self, title: str) -> dict:
        return {"report": title, "from": self.start.isoformat(), "to": self.end.isoformat(), "timezone": self.tz}


async def _period(ctx: Ctx, start: date | None, end: date | None) -> Period:
    hotel = await ctx.db.get(Hotel, ctx.hotel_id)
    tz = hotel.timezone
    today = (await ctx.db.execute(select(cast(func.timezone(tz, func.now()), Date)))).scalar_one()
    start = start or end or today
    end = end or start
    if end < start:
        raise bad_request("bad_range", "'to' is before 'from'")
    if (end - start).days >= MAX_DAYS:
        raise bad_request("range_too_long", f"Pick at most {MAX_DAYS} days", {"max_days": MAX_DAYS})
    return Period(tz, start, end)


def _dates(p: Period) -> list[date]:
    return [p.start + timedelta(days=i) for i in range((p.end - p.start).days + 1)]


def _ladder(gross: int, discount: int, tax: int, cgst: int, sgst: int, service: int, total: int) -> dict:
    taxable = gross - discount
    return {
        "gross_minor": gross,
        "discount_minor": discount,
        "taxable_minor": taxable,
        "cgst_minor": cgst,
        "sgst_minor": sgst,
        "other_tax_minor": tax - cgst - sgst,
        "service_charge_minor": service,
        "round_off_minor": total - taxable - tax - service,  # 0 today; shown so any drift is visible, not hidden
        "total_minor": total,
    }


# ---------- Sales by day ----------


async def sales_data(ctx: Ctx, p: Period) -> dict:
    db = ctx.db
    day = p.day(Bill.created_at)
    replaced = func.coalesce(Bill.void_reason, "").contains(REPLACED_MARK)
    rows = (
        await db.execute(
            select(
                day,
                Bill.status,
                replaced,
                func.count(),
                func.sum(Bill.subtotal_minor),
                func.sum(Bill.discount_minor),
                func.sum(Bill.tax_minor),
                func.sum(Bill.cgst_minor),
                func.sum(Bill.sgst_minor),
                func.sum(Bill.service_charge_minor),
                func.sum(Bill.total_minor),
            )
            .where(*p.on(Bill.created_at))
            .group_by(day, Bill.status, replaced)
        )
    ).all()
    acc: dict[date, dict] = {d: defaultdict(int) for d in _dates(p)}
    for d, status, is_replaced, n, gross, disc, tax, cgst, sgst, svc, total in rows:
        a = acc[d]
        if status in LIVE:
            a["bills"] += n
            for k, v in (("g", gross), ("d", disc), ("t", tax), ("c", cgst), ("s", sgst), ("v", svc), ("T", total)):
                a[k] += int(v)
            a["paid" if status == "PAID" else "unpaid"] += int(total)
            if status == "ISSUED":
                a["unpaid_bills"] += n
        elif is_replaced:
            a["replaced_bills"] += n
        else:
            a["cancelled_bills"] += n
            a["cancelled"] += int(total)

    # Money actually received, by the day it was received (part payments included).
    pay_day = p.day(BillPayment.created_at)
    pays = (
        await db.execute(
            select(pay_day, BillPayment.method, func.sum(BillPayment.amount_minor))
            .where(*p.on(BillPayment.created_at))
            .group_by(pay_day, BillPayment.method)
        )
    ).all()
    received: dict[date, dict[str, int]] = {d: dict.fromkeys(METHODS, 0) for d in _dates(p)}
    for d, method, amount in pays:
        received[d][method] = received[d].get(method, 0) + int(amount)

    def row(a: dict, r: dict[str, int]) -> dict:
        return {
            "bills": a["bills"],
            **_ladder(a["g"], a["d"], a["t"], a["c"], a["s"], a["v"], a["T"]),
            "paid_minor": a["paid"],
            "unpaid_minor": a["unpaid"],
            "unpaid_bills": a["unpaid_bills"],
            "cancelled_bills": a["cancelled_bills"],
            "cancelled_minor": a["cancelled"],
            "replaced_bills": a["replaced_bills"],
            "received": {m.lower() + "_minor": r.get(m, 0) for m in METHODS},
            "received_minor": sum(r.values()),
        }

    days = [{"date": d.isoformat(), **row(acc[d], received[d])} for d in _dates(p)]
    total_acc: dict = defaultdict(int)
    total_rec: dict[str, int] = defaultdict(int)
    for d in _dates(p):
        for k, v in acc[d].items():
            total_acc[k] += v
        for k, v in received[d].items():
            total_rec[k] += v

    # Food served or ordered in the period that is on no bill yet: a warning, never counted as sales.
    unbilled_n, unbilled_v = (
        await db.execute(
            select(func.count(), func.coalesce(func.sum(Order.total_minor), 0)).where(
                *p.on(Order.created_at), Order.status != "CANCELLED", Order.bill_id.is_(None)
            )
        )
    ).one()
    return {
        **p.head("sales"),
        "basis": BASIS,
        "days": days,
        "totals": row(total_acc, total_rec),
        "unbilled": {"orders": int(unbilled_n), "value_minor": int(unbilled_v)},
    }


# ---------- Items ----------


async def items_data(ctx: Ctx, p: Period) -> dict:
    rows = (
        await ctx.db.execute(
            select(
                BillLine.name,
                func.sum(BillLine.qty),
                func.sum(BillLine.line_total_minor),
                func.sum(BillLine.discount_minor),
            )
            .join(Bill, Bill.id == BillLine.bill_id)
            .where(*p.on(Bill.created_at), Bill.status.in_(LIVE))
            .group_by(BillLine.name)
            .order_by(func.sum(BillLine.line_total_minor).desc(), BillLine.name)
        )
    ).all()
    items = [
        {
            "name": name,
            "qty": int(qty),
            "gross_minor": int(gross),
            "discount_minor": int(disc),
            "taxable_minor": int(gross) - int(disc),
        }
        for name, qty, gross, disc in rows
    ]
    totals = {k: sum(i[k] for i in items) for k in ("qty", "gross_minor", "discount_minor", "taxable_minor")}
    return {**p.head("items"), "basis": BASIS + "; item names as printed on the bill", "items": items, "totals": totals}


# ---------- GST summary ----------


async def gst_data(ctx: Ctx, p: Period) -> dict:
    db = ctx.db
    bills = (
        (await db.execute(select(Bill).where(*p.on(Bill.created_at)).order_by(Bill.fy, Bill.number))).scalars().all()
    )
    live_ids = [b.id for b in bills if b.status in LIVE]
    lines_by_bill: dict[UUID, list] = defaultdict(list)
    if live_ids:
        for ln in (await db.execute(select(BillLine).where(BillLine.bill_id.in_(live_ids)))).scalars():
            lines_by_bill[ln.bill_id].append(ln)

    rates: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    service = 0
    for b in bills:
        if b.status not in LIVE:
            continue
        lines = lines_by_bill[b.id]
        for ln in lines:
            rates[ln.tax_rate_bp]["taxable_minor"] += ln.line_total_minor - ln.discount_minor
        for rate, tax in tax_by_rate(b.tax_minor, lines).items():
            c, s = split_gst(tax)  # per bill, exactly as printed, then summed
            rates[rate]["tax_minor"] += tax
            rates[rate]["cgst_minor"] += c
            rates[rate]["sgst_minor"] += s
        service += b.service_charge_minor

    by_rate = [
        {
            "rate_bp": r,
            "taxable_minor": v["taxable_minor"],
            "cgst_minor": v["cgst_minor"],
            "sgst_minor": v["sgst_minor"],
            "tax_minor": v["tax_minor"],
        }
        for r, v in sorted(rates.items())
    ]
    # Documents issued (GSTR-1 table 13): number range, total, cancelled. Replaced bills are cancelled documents too.
    docs: dict[tuple[str, str], dict] = {}
    for b in bills:
        d = docs.setdefault((b.doc_type, b.fy), {"from_no": b.invoice_no, "count": 0, "cancelled": 0})
        d["to_no"] = b.invoice_no
        d["count"] += 1
        d["cancelled"] += b.status == "VOID"
    return {
        **p.head("gst"),
        "basis": BASIS + "; tax per rate exactly as printed on each bill",
        "sac": SAC_RESTAURANT,
        "supply": "B2C, intra-state (CGST + SGST)",
        "by_rate": by_rate,
        "totals": {k: sum(r[k] for r in by_rate) for k in ("taxable_minor", "cgst_minor", "sgst_minor", "tax_minor")},
        "service_charge_minor": service,
        "documents": [
            {"doc_type": t, "fy": fy, **v, "net": v["count"] - v["cancelled"]} for (t, fy), v in sorted(docs.items())
        ],
    }


# ---------- Staff ----------


async def staff_data(ctx: Ctx, p: Period) -> dict:
    db = ctx.db
    people: dict[UUID, dict] = defaultdict(
        lambda: {
            "bills": 0,
            "billed_minor": 0,
            "discounts": 0,
            "discount_minor": 0,
            "received_minor": 0,
            "received": {m.lower() + "_minor": 0 for m in METHODS},
            "cancellations": 0,
        }
    )
    live = [*p.on(Bill.created_at), Bill.status.in_(LIVE)]
    for uid, n, total in await db.execute(
        select(Bill.created_by, func.count(), func.sum(Bill.total_minor)).where(*live).group_by(Bill.created_by)
    ):
        if uid:
            people[uid]["bills"] += n
            people[uid]["billed_minor"] += int(total)
    for uid, n, disc in await db.execute(
        select(Bill.discount_by, func.count(), func.sum(Bill.discount_minor))
        .where(*live, Bill.discount_minor > 0)
        .group_by(Bill.discount_by)
    ):
        if uid:
            people[uid]["discounts"] += n
            people[uid]["discount_minor"] += int(disc)
    for uid, method, amount in await db.execute(
        select(BillPayment.received_by, BillPayment.method, func.sum(BillPayment.amount_minor))
        .where(*p.on(BillPayment.created_at))
        .group_by(BillPayment.received_by, BillPayment.method)
    ):
        if uid:
            people[uid]["received"][method.lower() + "_minor"] += int(amount)
            people[uid]["received_minor"] += int(amount)
    # Cancellations a person made by hand (replacements happen automatically and are not counted).
    for uid, n in await db.execute(
        select(AuditLog.actor_user_id, func.count())
        .where(*p.on(AuditLog.at), AuditLog.action == "bill.void")
        .group_by(AuditLog.actor_user_id)
    ):
        if uid:
            people[uid]["cancellations"] += n

    names = {}
    if people:
        names = dict((await db.execute(select(User.id, User.name).where(User.id.in_(list(people))))).all())
    staff = sorted(
        ({"user_id": str(uid), "name": names.get(uid, "?"), **v} for uid, v in people.items()),
        key=lambda s: (-s["billed_minor"], -s["received_minor"], s["name"]),
    )
    return {**p.head("staff"), "basis": BASIS + "; money received by who took it", "staff": staff}


# ---------- Bills register (CSV only) ----------


async def register_rows(ctx: Ctx, p: Period) -> list[dict]:
    db = ctx.db
    paid = (
        select(BillPayment.bill_id, func.sum(BillPayment.amount_minor).label("paid"))
        .group_by(BillPayment.bill_id)
        .subquery()
    )
    local = func.timezone(p.tz, Bill.created_at)
    rows = (
        await db.execute(
            select(Bill, DiningTable.label, func.coalesce(paid.c.paid, 0), local)
            .outerjoin(DiningTable, DiningTable.id == Bill.table_id)
            .outerjoin(paid, paid.c.bill_id == Bill.id)
            .where(*p.on(Bill.created_at))
            .order_by(Bill.fy, Bill.number)
        )
    ).all()
    out = []
    for b, label, paid_minor, at in rows:
        out.append(
            {
                "invoice_no": b.invoice_no,
                "date": at.date().isoformat(),
                "time": at.strftime("%H:%M"),
                "place": f"Table {label}" if label else "Takeaway",
                "doc_type": b.doc_type,
                "status": b.status,
                **_ladder(
                    b.subtotal_minor,
                    b.discount_minor,
                    b.tax_minor,
                    b.cgst_minor,
                    b.sgst_minor,
                    b.service_charge_minor,
                    b.total_minor,
                ),
                "paid_minor": int(paid_minor),
                "payment_method": b.payment_method or "",
                "note": b.void_reason or b.discount_reason or "",
            }
        )
    return out


# ---------- Routes ----------

From = Query(None, alias="from")
To = Query(None, alias="to")


@router.get("/reports/sales")
async def sales(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await sales_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/items")
async def items(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await items_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/gst")
async def gst(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await gst_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/staff")
async def staff(
    date_from: date | None = From,
    date_to: date | None = To,
    ctx: Ctx = HotelCtx("report.view", feature="REPORTS_ADVANCED"),
) -> dict:
    return await staff_data(ctx, await _period(ctx, date_from, date_to))


# ---------- CSV ----------


def rupees(minor: int) -> str:
    """Integer paise to '1234.50' text, without floats."""
    sign = "-" if minor < 0 else ""
    m = abs(minor)
    return f"{sign}{m // 100}.{m % 100:02d}"


def _csv(head: dict, basis: str, header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Report", head["report"]])
    w.writerow(["From", head["from"], "To", head["to"], "Time zone", head["timezone"]])
    w.writerow(["Basis", basis])
    w.writerow(["Amounts", "Rupees"])
    w.writerow([])
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


LADDER = [
    ("Gross", "gross_minor"),
    ("Discount", "discount_minor"),
    ("Taxable", "taxable_minor"),
    ("CGST", "cgst_minor"),
    ("SGST", "sgst_minor"),
    ("Other tax", "other_tax_minor"),
    ("Service charge", "service_charge_minor"),
    ("Round off", "round_off_minor"),
    ("Total", "total_minor"),
]


async def csv_text(ctx: Ctx, kind: str, p: Period) -> str:
    if kind == "sales":
        d = await sales_data(ctx, p)
        header = ["Date", "Bills", *[h for h, _ in LADDER], "Paid", "Unpaid", "Cancelled bills", "Cancelled value"]
        header += [m.title() + " received" for m in METHODS] + ["Received"]

        def line(label: str, r: dict) -> list:
            return [
                label,
                r["bills"],
                *[rupees(r[k]) for _, k in LADDER],
                rupees(r["paid_minor"]),
                rupees(r["unpaid_minor"]),
                r["cancelled_bills"],
                rupees(r["cancelled_minor"]),
                *[rupees(r["received"][m.lower() + "_minor"]) for m in METHODS],
                rupees(r["received_minor"]),
            ]

        return _csv(d, d["basis"], header, [line(r["date"], r) for r in d["days"]] + [line("Total", d["totals"])])
    if kind == "items":
        d = await items_data(ctx, p)
        rows = [
            [i["name"], i["qty"], *(rupees(i[k]) for k in ("gross_minor", "discount_minor", "taxable_minor"))]
            for i in d["items"]
        ]
        t = d["totals"]
        rows.append(["Total", t["qty"], *(rupees(t[k]) for k in ("gross_minor", "discount_minor", "taxable_minor"))])
        return _csv(d, d["basis"], ["Item", "Qty", "Gross", "Discount", "Taxable"], rows)
    if kind == "gst":
        d = await gst_data(ctx, p)
        keys = ("taxable_minor", "cgst_minor", "sgst_minor", "tax_minor")
        rows = [[f"{r['rate_bp'] / 100:g}%", d["sac"], *(rupees(r[k]) for k in keys)] for r in d["by_rate"]]
        rows.append(["Total", "", *(rupees(d["totals"][k]) for k in keys)])
        rows += [[], ["Documents", "FY", "From", "To", "Total", "Cancelled", "Net"]]
        rows += [
            [x["doc_type"], x["fy"], x["from_no"], x["to_no"], x["count"], x["cancelled"], x["net"]]
            for x in d["documents"]
        ]
        return _csv(d, d["basis"], ["GST rate", "SAC", "Taxable", "CGST", "SGST", "Total tax"], rows)
    if kind == "staff":
        ctx.require_feature("REPORTS_ADVANCED")
        d = await staff_data(ctx, p)
        rows = [
            [
                s["name"],
                s["bills"],
                rupees(s["billed_minor"]),
                s["discounts"],
                rupees(s["discount_minor"]),
                *[rupees(s["received"][m.lower() + "_minor"]) for m in METHODS],
                rupees(s["received_minor"]),
                s["cancellations"],
            ]
            for s in d["staff"]
        ]
        header = ["Staff", "Bills", "Billed", "Discounts", "Discount value"]
        header += [m.title() + " received" for m in METHODS] + ["Received", "Cancellations"]
        return _csv(d, d["basis"], header, rows)
    if kind == "register":
        rows = await register_rows(ctx, p)
        header = [
            "Invoice",
            "Date",
            "Time",
            "Place",
            "Type",
            "Status",
            *[h for h, _ in LADDER],
            "Paid",
            "Method",
            "Note",
        ]
        body = [
            [
                r["invoice_no"],
                r["date"],
                r["time"],
                r["place"],
                r["doc_type"],
                r["status"],
                *[rupees(r[k]) for _, k in LADDER],
                rupees(r["paid_minor"]),
                r["payment_method"],
                r["note"],
            ]
            for r in rows
        ]
        basis = "Every bill issued on the hotel's calendar day, including cancelled ones (status VOID)"
        return _csv(p.head("register"), basis, header, body)
    raise bad_request("unknown_report", "Unknown report", {"kinds": ["sales", "items", "gst", "staff", "register"]})


@router.get("/reports/{kind}.csv", response_class=Response)
async def export_csv(
    kind: str, date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")
) -> Response:
    p = await _period(ctx, date_from, date_to)
    body = await csv_text(ctx, kind, p)
    name = f"{kind}_{p.start.isoformat()}_{p.end.isoformat()}.csv"
    # A byte-order mark so Excel opens rupee names and Telugu/Hindi item names correctly.
    return Response(
        "﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
