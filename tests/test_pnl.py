"""Profit and loss by month (docs/14): sales before GST + service charge - expenses; GST is not income."""

from datetime import date

import pytest

from app.platform.gstin import checksum_char
from tests.conftest import h, make_hotel
from tests.test_day_close import _paid_bill

API = "/api/v1"


@pytest.fixture
async def hp(client, root):
    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    return await make_hotel(client, root, plan="PRO", gst_registration="REGULAR", gstin=g)


async def _expense(client, hp, amount: int, day: date, category: int = 0) -> None:
    a = h(hp["admin"])
    cats = (await client.get(f"{API}/expense-categories", headers=a)).json()
    r = await client.post(
        f"{API}/expenses",
        headers=a,
        json={
            "category_id": cats[category]["id"],
            "amount_minor": amount,
            "spent_on": str(day),
            "payment_method": "CASH",
        },
    )
    assert r.status_code in (200, 201), r.text


async def test_profit_is_sales_before_gst_minus_expenses_per_month(client, hp):
    a = h(hp["admin"])
    bill = await _paid_bill(client, hp, "CASH")
    assert bill["tax_minor"] > 0, "a GST bill, so the GST line matters"
    d0 = (await client.get(f"{API}/reports/pnl", headers=a)).json()
    today = date.fromisoformat(d0["to"]).replace(day=1)  # this month
    last_month = (today.replace(day=1) - date.resolution * 1).replace(day=10)
    await _expense(client, hp, 5000, today)
    await _expense(client, hp, 2500, today, category=1)
    await _expense(client, hp, 9000, last_month)

    d = (await client.get(f"{API}/reports/pnl", headers=a)).json()
    assert len(d["months"]) == 6 and d["months"][-1]["so_far"] and not d["months"][0]["so_far"]
    now = d["months"][-1]
    assert now["sales_minor"] == bill["taxable_minor"], "before GST, after discounts"
    assert now["gst_collected_minor"] == bill["tax_minor"]
    assert now["income_minor"] == bill["taxable_minor"] + bill["service_charge_minor"]
    assert now["expenses_minor"] == 7500 and [e["amount_minor"] for e in now["expenses"]] == [5000, 2500]
    assert now["operating_profit_minor"] == now["income_minor"] - 7500
    assert now["margin_pct"] == round(now["operating_profit_minor"] * 100 / now["income_minor"], 1)
    prev = d["months"][-2]
    assert prev["income_minor"] == 0 and prev["operating_profit_minor"] == -9000 and prev["margin_pct"] is None
    assert d["totals"]["expenses_minor"] == 16500 and d["totals"]["sales_minor"] == bill["taxable_minor"]

    # The Overview's month net uses the same rule (it used to subtract expenses from totals that included GST).
    dash = (await client.get(f"{API}/dashboard/summary", headers=a)).json()
    assert dash["net_month_minor"] == now["operating_profit_minor"]
    assert dash["revenue_month_minor"] - dash["income_month_minor"] == bill["tax_minor"]

    csv = (await client.get(f"{API}/reports/pnl.csv", headers=a)).text
    assert "Operating profit" in csv and "(so far)" in csv and "not income" in csv


async def test_range_is_whole_months_at_most_twelve(client, hp):
    a = h(hp["admin"])
    d = (await client.get(f"{API}/reports/pnl", headers=a, params={"from": "2026-01-15", "to": "2026-03-02"})).json()
    assert [m["month"] for m in d["months"]] == ["2026-01", "2026-02", "2026-03"]
    assert d["from"] == "2026-01-01" and d["to"] == "2026-03-31"
    r = await client.get(f"{API}/reports/pnl", headers=a, params={"from": "2025-01-01", "to": "2026-03-01"})
    assert r.status_code == 400 and r.json()["code"] == "range_too_long"
    r = await client.get(f"{API}/reports/pnl", headers=h(hp["Kiran"]))
    assert r.status_code == 403, "a waiter does not see the P&L"
