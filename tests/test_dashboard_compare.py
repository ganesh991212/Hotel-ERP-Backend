"""Overview: vs the same weekday last week (up to the same time) and today's payment mix (docs/14)."""

import os
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import h, make_hotel
from tests.test_day_close import _paid_bill

API = "/api/v1"


async def _shift(bill_id: str, interval: timedelta) -> None:
    """Move a bill (and its payments) back in time; the app itself never backdates."""
    eng = create_async_engine(os.environ["MIGRATION_DATABASE_URL"])
    async with eng.begin() as c:
        for table, col in (("bills", "id"), ("bill_payments", "bill_id")):
            await c.execute(
                text(f"UPDATE {table} SET created_at = created_at - CAST(:i AS interval) WHERE {col} = :b"),
                {"i": interval, "b": bill_id},
            )
    await eng.dispose()


@pytest.fixture
async def hw(client, root):
    return await make_hotel(client, root)


async def _summary(client, hp) -> dict:
    return (await client.get(f"{API}/dashboard/summary", headers=h(hp["admin"]))).json()


async def test_no_sales_last_week_means_no_percentage(client, hw):
    cash = await _paid_bill(client, hw, "CASH")
    d = await _summary(client, hw)
    v = d["vs_last_week"]
    assert v["last_week_minor"] == 0 and v["change_pct"] is None  # never "+inf%" or "-100%"
    assert v["change_minor"] == cash["total_minor"] == d["revenue_today_minor"]


async def test_compares_up_to_the_same_time_last_week(client, hw):
    earlier = await _paid_bill(client, hw, "UPI", qty=2)  # last week, before this time of day: counted
    later = await _paid_bill(client, hw, "CARD", qty=3)  # last week, after this time of day: not counted yet
    today = await _paid_bill(client, hw, "CASH", qty=4)
    await _shift(earlier["id"], timedelta(days=7, minutes=1))
    await _shift(later["id"], timedelta(days=6, hours=23))  # one hour later in the day than now, a week ago
    d = await _summary(client, hw)
    v = d["vs_last_week"]
    if v["until"][11:16] < "00:02" or v["until"][11:16] >= "23:00":
        pytest.skip("too close to midnight for this fixed shift")
    last = earlier["total_minor"]
    assert v["last_week_minor"] == last
    assert v["change_minor"] == today["total_minor"] - last
    assert v["change_pct"] == round((today["total_minor"] - last) * 100 / last, 1)


async def test_payment_mix_is_money_received_today(client, hw):
    cash = await _paid_bill(client, hw, "CASH")
    upi = await _paid_bill(client, hw, "UPI", qty=1)
    old = await _paid_bill(client, hw, "CARD", qty=1)
    await _shift(old["id"], timedelta(days=8))  # received on another day: not in today's mix
    mix = (await _summary(client, hw))["payment_mix"]
    by = {m["method"]: m for m in mix["methods"]}
    assert [m["method"] for m in mix["methods"]] == ["CASH", "UPI", "CARD"]
    assert by["CASH"]["amount_minor"] == cash["total_minor"] and by["UPI"]["amount_minor"] == upi["total_minor"]
    assert by["CARD"]["amount_minor"] == 0 and by["CARD"]["share_pct"] == 0
    assert mix["total_minor"] == cash["total_minor"] + upi["total_minor"]
    # Same numbers as the Day close screen.
    dc = (await client.get(f"{API}/day-close", headers=h(hw["admin"]))).json()
    assert dc["cash_sales_minor"] == by["CASH"]["amount_minor"] and dc["upi_minor"] == by["UPI"]["amount_minor"]
