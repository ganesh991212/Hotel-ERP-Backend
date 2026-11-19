"""'My work' dashboard: each person sees their own numbers by role, with the team figure for context."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture(scope="module")
async def hm(client, root):
    return await make_hotel(client, root)


async def _mine(client, who, period="today") -> dict:
    r = await client.get(f"{API}/dashboard/me?period={period}", headers=h(who))
    assert r.status_code == 200, r.text
    return {s["kind"]: s for s in r.json()["sections"]}


async def _full_order(client, hm, chef="Ravi", server="Kiran") -> dict:
    """Waiter takes it, chef cooks it, waiter serves it, admin bills and settles it."""
    r = await client.post(
        f"{API}/orders",
        headers=h(hm[server]),
        json={"table_id": hm["table"]["id"], "items": [{"item_id": hm["items"]["Naan"], "qty": 3}]},
    )
    assert r.status_code == 201, r.text
    o = r.json()
    for step in ("accept", "ready"):
        assert (await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hm[chef]))).status_code == 200
    assert (await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hm[server]))).status_code == 200
    bill = (await client.post(f"{API}/bills", headers=h(hm["admin"]), json={"table_id": hm["table"]["id"]})).json()
    r = await client.post(f"{API}/bills/{bill['id']}/settle", headers=h(hm["admin"]), json={"method": "UPI"})
    assert r.status_code == 200, r.text
    return {**o, "bill": bill}


async def test_each_role_sees_its_own_work(client, hm):
    assert set(await _mine(client, hm["Ravi"])) == {"cooking"}, "a chef sees cooking only"
    assert "cooking" not in await _mine(client, hm["Kiran"]), "a waiter does not see cooking"
    assert "serving" in await _mine(client, hm["Kiran"])
    assert {"cooking", "serving", "billing"} <= set(await _mine(client, hm["admin"])), "admin does everything"

    before_ravi, before_sita = (
        (await _mine(client, hm["Ravi"]))["cooking"],
        (await _mine(client, hm["Sita"]))["cooking"],
    )
    before_kiran = (await _mine(client, hm["Kiran"]))["serving"]
    before_bills = (await _mine(client, hm["admin"]))["billing"]

    o = await _full_order(client, hm)

    ravi = (await _mine(client, hm["Ravi"]))["cooking"]
    assert ravi["orders_cooked"] == before_ravi["orders_cooked"] + 1
    assert ravi["items_cooked"] == before_ravi["items_cooked"] + 3
    assert ravi["avg_prep_minutes"] is not None and ravi["team_avg_prep_minutes"] is not None
    assert (await _mine(client, hm["Sita"]))["cooking"]["orders_cooked"] == before_sita["orders_cooked"], (
        "another chef's work is not counted as mine"
    )

    kiran = (await _mine(client, hm["Kiran"]))["serving"]
    assert kiran["orders_served"] == before_kiran["orders_served"] + 1
    assert kiran["orders_taken"] == before_kiran["orders_taken"] + 1
    assert kiran["orders_taken_minor"] == before_kiran["orders_taken_minor"] + o["total_minor"]
    assert kiran["tables_served"] >= 1

    bills = (await _mine(client, hm["admin"]))["billing"]
    assert bills["bills_raised"] == before_bills["bills_raised"] + 1
    assert bills["collected_by_method_minor"]["UPI"] >= o["bill"]["total_minor"]
    assert bills["collected_minor"] == sum(bills["collected_by_method_minor"].values())


async def test_week_includes_today_and_other_hotels_never_count(client, root, hm):
    today = (await _mine(client, hm["Ravi"]))["cooking"]["orders_cooked"]
    week = await client.get(f"{API}/dashboard/me?period=week", headers=h(hm["Ravi"]))
    assert week.json()["from"] < week.json()["to"]
    assert {s["kind"]: s for s in week.json()["sections"]}["cooking"]["orders_cooked"] >= today

    other = await make_hotel(client, root)
    await _full_order(client, other)
    assert (await _mine(client, hm["Ravi"]))["cooking"]["orders_cooked"] == today, "another hotel's orders"
    assert (await client.get(f"{API}/dashboard/me?period=year", headers=h(hm["Ravi"]))).status_code == 422
