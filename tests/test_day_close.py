"""Day close / cash drawer (docs/03 "Day close", docs/14)."""

from datetime import date, timedelta

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


async def _paid_bill(client, hp, method, amount=None, qty=5):
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hp["Kiran"]),
            json={"table_id": hp["table"]["id"], "items": [{"item_id": hp["items"]["Naan"], "qty": qty}]},
        )
    ).json()
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hp["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hp["Kiran"]))
    b = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()
    await client.post(f"{API}/bills/{b['id']}/settle", headers=h(hp["admin"]), json={"method": method})
    return b


@pytest.fixture
async def hd(client, root):
    return await make_hotel(client, root)


async def _today(client, hp) -> date:
    return date.fromisoformat((await client.get(f"{API}/day-close", headers=h(hp["admin"]))).json()["date"])


async def test_count_against_what_every_device_recorded(client, hd):
    a = h(hd["admin"])
    cash = await _paid_bill(client, hd, "CASH")
    upi = await _paid_bill(client, hd, "UPI", qty=2)
    cats = (await client.get(f"{API}/expense-categories", headers=a)).json()
    await client.post(
        f"{API}/expenses",
        headers=a,
        json={
            "category_id": cats[0]["id"],
            "amount_minor": 2000,
            "spent_on": str(await _today(client, hd)),
            "payment_method": "CASH",
        },
    )
    p = (await client.get(f"{API}/day-close", headers=a)).json()
    assert p["cash_sales_minor"] == cash["total_minor"] and p["upi_minor"] == upi["total_minor"]
    assert p["cash_paid_out_minor"] == 2000 and p["carried_float_minor"] is None and p["closed"] is None

    # First close ever: the opening float is typed once. Expected = float + cash sales - cash paid out.
    expected = 50_000 + cash["total_minor"] - 2000
    short = {"counted_minor": expected - 1_000, "float_kept_minor": 50_000, "opening_float_minor": 50_000}
    r = await client.post(f"{API}/day-close", headers=a, json=short)
    assert r.status_code == 400 and r.json()["code"] == "note_required"
    r = await client.post(f"{API}/day-close", headers=a, json={**short, "note": "Change given twice"})
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["expected_minor"] == expected and c["difference_minor"] == -1_000 and c["result"] == "SHORT"
    assert c["deposit_minor"] == expected - 1_000 - 50_000 and c["closed_by"] == "Admin"
    again = await client.post(f"{API}/day-close", headers=a, json={**short, "note": "again"})
    assert again.status_code == 409 and again.json()["code"] == "already_closed"

    # Cash taken after the close is shown on it, never folded in.
    late = await _paid_bill(client, hd, "CASH", qty=1)
    hist = (await client.get(f"{API}/day-closes", headers=a)).json()
    assert hist["closes"][0]["cash_after_close_minor"] == late["total_minor"]
    assert hist["closes"][0]["counted_minor"] == expected - 1_000
    assert hist["short_days"] == 1 and hist["net_difference_minor"] == -1_000


async def test_float_carries_over_and_cannot_be_retyped(client, hd):
    a = h(hd["admin"])
    today = await _today(client, hd)
    y = today - timedelta(days=1)
    r = await client.post(
        f"{API}/day-close",
        headers=a,
        json={"date": str(y), "counted_minor": 30_000, "float_kept_minor": 20_000, "opening_float_minor": 30_000},
    )
    assert r.status_code == 201 and r.json()["result"] == "EXACT" and r.json()["deposit_minor"] == 10_000
    p = (await client.get(f"{API}/day-close", headers=a)).json()
    assert p["carried_float_minor"] == 20_000 and p["expected_minor"] == 20_000
    # Typing a different opening float today is ignored: yesterday's kept cash is today's float.
    r = await client.post(f"{API}/day-close", headers=a, json={"counted_minor": 20_000, "opening_float_minor": 99_999})
    assert r.json()["opening_float_minor"] == 20_000 and r.json()["result"] == "EXACT"


async def test_rules_permissions_and_isolation(client, root, hd):
    a = h(hd["admin"])
    future = str(await _today(client, hd) + timedelta(days=1))
    r = await client.post(f"{API}/day-close", headers=a, json={"date": future, "counted_minor": 0})
    assert r.status_code == 400 and r.json()["code"] == "future_day"
    r = await client.post(f"{API}/day-close", headers=a, json={"counted_minor": 100, "float_kept_minor": 200})
    assert r.status_code == 400 and r.json()["code"] == "bad_float"
    # Servers don't count the drawer.
    assert (await client.get(f"{API}/day-close", headers=h(hd["Kiran"]))).status_code == 403
    await client.post(f"{API}/day-close", headers=a, json={"counted_minor": 0})
    other = await make_hotel(client, root)
    assert (await client.get(f"{API}/day-closes", headers=h(other["admin"]))).json()["closes"] == []
