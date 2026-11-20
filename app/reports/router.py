"""Sales reports (docs/14 "Reports", docs/13 section "Reports").

One source of truth: issued bills, their lines and their payments. Every report states its basis, and the money
ladder always reconciles: gross - discount = taxable; taxable + tax + service charge (+ round off) = bill total.
Dates are the hotel's own calendar days (its timezone). All sums are integer paise, computed here.
"""

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from fastapi.responses import Response
from sqlalchemy import Date, case, cast, func, select

from app.billing.service import REPLACED_MARK, split_gst, tax_by_rate
from app.core.deps import Ctx, HotelCtx
from app.core.errors import bad_request
from app.models import AuditLog, Bill, BillLine, BillPayment, DiningTable, Expense, ExpenseCategory, Hotel, Order, User

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

    def local_date(self, ts: datetime) -> date:
        """A timestamp's hotel-local calendar date (same day boundary as `on`)."""
        return ts.astimezone(ZoneInfo(self.tz)).date()

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
                func.coalesce(func.sum(Bill.guests), 0),
                func.coalesce(func.sum(case((Bill.guests.is_not(None), Bill.total_minor), else_=0)), 0),
            )
            .where(*p.on(Bill.created_at))
            .group_by(day, Bill.status, replaced)
        )
    ).all()
    acc: dict[date, dict] = {d: defaultdict(int) for d in _dates(p)}
    for d, status, is_replaced, n, gross, disc, tax, cgst, sgst, svc, total, guests, covered in rows:
        a = acc[d]
        if status in LIVE:
            a["bills"] += n
            a["covers"] += int(guests)
            a["covered"] += int(covered)  # total of the bills that recorded guests (takeaway has none)
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
            # Covers: guests recorded once per visit (docs/14); average spend per guest, where guests were counted.
            "covers": a["covers"],
            "avg_per_cover_minor": a["covered"] // a["covers"] if a["covers"] else None,
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


NOT_RECORDED = "Not recorded"  # bills issued before categories were kept on bill lines (docs/14)


async def items_data(ctx: Ctx, p: Period) -> dict:
    """Item-wise and category-wise sales from the same bill lines, so categories add up to the item totals."""
    category = func.coalesce(BillLine.category_name, NOT_RECORDED)
    rows = (
        await ctx.db.execute(
            select(
                BillLine.name,
                category,
                func.sum(BillLine.qty),
                func.sum(BillLine.line_total_minor),
                func.sum(BillLine.discount_minor),
            )
            .join(Bill, Bill.id == BillLine.bill_id)
            .where(*p.on(Bill.created_at), Bill.status.in_(LIVE))
            .group_by(BillLine.name, category)
            .order_by(func.sum(BillLine.line_total_minor).desc(), BillLine.name)
        )
    ).all()
    items = [
        {
            "name": name,
            "category": cat,
            "qty": int(qty),
            "gross_minor": int(gross),
            "discount_minor": int(disc),
            "taxable_minor": int(gross) - int(disc),
        }
        for name, cat, qty, gross, disc in rows
    ]
    keys = ("qty", "gross_minor", "discount_minor", "taxable_minor")
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: dict.fromkeys(("items", *keys), 0))
    for i in items:
        c = by_cat[i["category"]]
        c["items"] += 1
        for k in keys:
            c[k] += i[k]
    totals = {k: sum(i[k] for i in items) for k in keys}
    categories = [
        {
            "category": name,
            **c,
            "share_pct": round(c["taxable_minor"] * 100 / totals["taxable_minor"], 1) if totals["taxable_minor"] else 0,
        }
        for name, c in sorted(by_cat.items(), key=lambda kv: (-kv[1]["taxable_minor"], kv[0]))
    ]
    return {
        **p.head("items"),
        "basis": BASIS + "; item names and categories as they were when billed",
        "items": items,
        "categories": categories,
        "totals": totals,
    }


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
    b2c: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    b2b: list[dict] = []  # GSTR-1 B2B: one row per invoice and rate
    service = 0
    for b in bills:
        if b.status not in LIVE:
            continue
        lines = lines_by_bill[b.id]
        taxable: dict[int, int] = defaultdict(int)
        for ln in lines:
            taxable[ln.tax_rate_bp] += ln.line_total_minor - ln.discount_minor
        tax_rates = tax_by_rate(b.tax_minor, lines)
        for rate in sorted(set(taxable) | set(tax_rates)):
            tax = tax_rates.get(rate, 0)
            c, s = split_gst(tax)  # per bill, exactly as printed, then summed
            row = {"taxable_minor": taxable.get(rate, 0), "tax_minor": tax, "cgst_minor": c, "sgst_minor": s}
            for k, v in row.items():
                rates[rate][k] += v
                if not b.buyer_gstin:
                    b2c[rate][k] += v
            if b.buyer_gstin:
                b2b.append(
                    {
                        "invoice_no": b.invoice_no,
                        "date": p.local_date(b.created_at).isoformat(),
                        "buyer_gstin": b.buyer_gstin,
                        "buyer_name": b.buyer_name,
                        "invoice_total_minor": b.total_minor,
                        "rate_bp": rate,
                        **row,
                    }
                )
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
        "supply": "Intra-state (CGST + SGST); B2B invoices listed one by one, the rest is B2C",
        "b2b": b2b,
        "b2c_by_rate": [
            {"rate_bp": r, **{k: v[k] for k in ("taxable_minor", "cgst_minor", "sgst_minor", "tax_minor")}}
            for r, v in sorted(b2c.items())
        ],
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


# ---------- Profit and loss by month (docs/14 "Profit and loss") ----------

MAX_MONTHS = 12


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


async def pnl_period(ctx: Ctx, start: date | None, end: date | None) -> tuple[str, date, list[date]]:
    """Whole months from `start`'s month to `end`'s month (default: the last 6 months to this one), at most 12."""
    tz = (await ctx.db.execute(select(Hotel.timezone))).scalar_one()
    today = (await ctx.db.execute(select(cast(func.timezone(tz, func.now()), Date)))).scalar_one()
    last = _month_start(end or today)
    first = _month_start(start) if start else _month_start(last - timedelta(days=150))
    if first > last:
        raise bad_request("bad_range", "The start is after the end")
    months = [first]
    while months[-1] < last:
        months.append(_next_month(months[-1]))
    if len(months) > MAX_MONTHS:
        raise bad_request("range_too_long", f"Pick at most {MAX_MONTHS} months", {"max_months": MAX_MONTHS})
    return tz, today, months


async def pnl_data(ctx: Ctx, start: date | None, end: date | None) -> dict:
    """Sales before GST + service charge - expenses, per month. GST collected is shown, never counted as income."""
    tz, today, months = await pnl_period(ctx, start, end)
    db = ctx.db
    first, after = months[0], _next_month(months[-1])
    bill_day = cast(func.timezone(tz, Bill.created_at), Date)
    bill_month = cast(func.date_trunc("month", bill_day), Date)
    sales = {
        m: (int(n), int(taxable), int(service), int(tax))
        for m, n, taxable, service, tax in (
            await db.execute(
                select(
                    bill_month,
                    func.count(),
                    func.sum(Bill.subtotal_minor - Bill.discount_minor),
                    func.sum(Bill.service_charge_minor),
                    func.sum(Bill.tax_minor),
                )
                .where(bill_day >= first, bill_day < after, Bill.status.in_(LIVE))
                .group_by(bill_month)
            )
        ).all()
    }
    exp_month = cast(func.date_trunc("month", Expense.spent_on), Date)
    expenses: dict[date, dict[str, int]] = defaultdict(dict)
    for m, name, amount in (
        await db.execute(
            select(exp_month, ExpenseCategory.name, func.sum(Expense.amount_minor))
            .join(ExpenseCategory, ExpenseCategory.id == Expense.category_id)
            .where(Expense.spent_on >= first, Expense.spent_on < after)
            .group_by(exp_month, ExpenseCategory.name)
        )
    ).all():
        expenses[m][name] = int(amount)

    def column(bills: int, taxable: int, service: int, tax: int, spent: dict[str, int]) -> dict:
        income = taxable + service
        total_exp = sum(spent.values())
        profit = income - total_exp
        return {
            "bills": bills,
            "sales_minor": taxable,  # before GST, after discounts: the same taxable value as the sales report
            "service_charge_minor": service,
            "income_minor": income,
            "expenses": [{"category": k, "amount_minor": v} for k, v in sorted(spent.items(), key=lambda kv: -kv[1])],
            "expenses_minor": total_exp,
            "operating_profit_minor": profit,
            "margin_pct": round(profit * 100 / income, 1) if income else None,
            "gst_collected_minor": tax,  # owed to the government: not income
        }

    cols = []
    for m in months:
        n, taxable, service, tax = sales.get(m, (0, 0, 0, 0))
        cols.append(
            {
                "month": m.strftime("%Y-%m"),
                "so_far": m == _month_start(today),  # the current month is not complete
                **column(n, taxable, service, tax, expenses.get(m, {})),
            }
        )
    every: dict[str, int] = defaultdict(int)
    for m in months:
        for k, v in expenses.get(m, {}).items():
            every[k] += v
    totals = column(
        sum(c["bills"] for c in cols),
        sum(c["sales_minor"] for c in cols),
        sum(c["service_charge_minor"] for c in cols),
        sum(c["gst_collected_minor"] for c in cols),
        dict(every),
    )
    return {
        "report": "pnl",
        "from": first.isoformat(),
        "to": (after - timedelta(days=1)).isoformat(),
        "timezone": tz,
        "basis": "Sales = issued bills before GST (after discounts); expenses on the day they were spent. "
        "Food cost is only what was entered as expenses (no stock tracking yet)",
        "months": cols,
        "totals": totals,
    }


@router.get("/reports/sales")
async def sales(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await sales_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/items")
async def items(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await items_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/gst")
async def gst(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    return await gst_data(ctx, await _period(ctx, date_from, date_to))


@router.get("/reports/pnl")
async def pnl(date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")) -> dict:
    """Profit and loss by month: whole months covering `from`..`to` (default the last 6), at most 12."""
    return await pnl_data(ctx, date_from, date_to)


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
    if kind == "categories":
        d = await items_data(ctx, p)
        money = ("gross_minor", "discount_minor", "taxable_minor")
        rows = [
            [c["category"], c["items"], c["qty"], *(rupees(c[k]) for k in money), f"{c['share_pct']:g}%"]
            for c in d["categories"]
        ]
        t = d["totals"]
        rows.append(["Total", len(d["items"]), t["qty"], *(rupees(t[k]) for k in money), "100%" if t["qty"] else ""])
        return _csv(d, d["basis"], ["Category", "Items", "Qty", "Gross", "Discount", "Taxable", "Share"], rows)
    if kind == "gst":
        d = await gst_data(ctx, p)
        keys = ("taxable_minor", "cgst_minor", "sgst_minor", "tax_minor")
        rows = [[f"{r['rate_bp'] / 100:g}%", d["sac"], *(rupees(r[k]) for k in keys)] for r in d["by_rate"]]
        rows.append(["Total", "", *(rupees(d["totals"][k]) for k in keys)])
        if d["b2b"]:  # GSTR-1 B2B: one row per invoice and rate; B2C below is the rest
            money = ["Taxable", "CGST", "SGST", "Total tax"]
            rows += [[], ["B2B invoices", "Date", "Buyer GSTIN", "Buyer", "Invoice value", "Rate", *money]]
            rows += [
                [
                    x["invoice_no"],
                    x["date"],
                    x["buyer_gstin"],
                    x["buyer_name"],
                    rupees(x["invoice_total_minor"]),
                    f"{x['rate_bp'] / 100:g}%",
                    *(rupees(x[k]) for k in keys),
                ]
                for x in d["b2b"]
            ]
            rows += [[], ["B2C by rate", "SAC", *money]]
            rows += [[f"{r['rate_bp'] / 100:g}%", d["sac"], *(rupees(r[k]) for k in keys)] for r in d["b2c_by_rate"]]
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
    raise bad_request(
        "unknown_report", "Unknown report", {"kinds": ["sales", "items", "categories", "gst", "staff", "register"]}
    )


def pnl_csv(d: dict) -> str:
    """One column per month and a total column; one row per line of the statement."""
    cols = [*d["months"], {"month": "Total", "so_far": False, **d["totals"]}]
    cats = [e["category"] for e in d["totals"]["expenses"]]
    head = ["", *(c["month"] + (" (so far)" if c["so_far"] else "") for c in cols)]

    def line(label: str, get) -> list:
        return [label, *(get(c) for c in cols)]

    rows = [
        line("Bills", lambda c: c["bills"]),
        line("Sales before GST", lambda c: rupees(c["sales_minor"])),
        line("Service charge", lambda c: rupees(c["service_charge_minor"])),
        line("Income", lambda c: rupees(c["income_minor"])),
        *[
            line(
                f"Expense: {k}",
                lambda c, k=k: rupees(next((e["amount_minor"] for e in c["expenses"] if e["category"] == k), 0)),
            )
            for k in cats
        ],
        line("Expenses", lambda c: rupees(c["expenses_minor"])),
        line("Operating profit", lambda c: rupees(c["operating_profit_minor"])),
        line("Margin", lambda c: "" if c["margin_pct"] is None else f"{c['margin_pct']:g}%"),
        [],
        line("GST collected (owed to the government, not income)", lambda c: rupees(c["gst_collected_minor"])),
    ]
    head_info = {"report": "pnl", "from": d["from"], "to": d["to"], "timezone": d["timezone"]}
    return _csv(head_info, d["basis"], head, rows)


@router.get("/reports/{kind}.csv", response_class=Response)
async def export_csv(
    kind: str, date_from: date | None = From, date_to: date | None = To, ctx: Ctx = HotelCtx("report.view")
) -> Response:
    if kind == "pnl":  # whole months (up to 12), not the 92-day day range of the other reports
        d = await pnl_data(ctx, date_from, date_to)
        body, name = pnl_csv(d), f"pnl_{d['from'][:7]}_{d['to'][:7]}.csv"
    else:
        p = await _period(ctx, date_from, date_to)
        body = await csv_text(ctx, kind, p)
        name = f"{kind}_{p.start.isoformat()}_{p.end.isoformat()}.csv"
    # A byte-order mark so Excel opens rupee names and Telugu/Hindi item names correctly.
    return Response(
        "﻿" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
