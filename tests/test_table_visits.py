"""Table visits: guests (covers), time seated, Reserved / Cleaning (docs/03 "Table visits", docs/14)."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def ht(client, root):
    return await make_hotel(client, root)


async def _table(client, hp):
    return next(
        t for t in (await client.get(f"{API}/tables", headers=h(hp["admin"]))).json() if t["id"] == hp["table"]["id"]
    )


def _seat(client, hp, guests=None, who="Kiran"):
    return client.post(f"{API}/tables/{hp['table']['id']}/seat", headers=h(hp[who]), json={"guests": guests})


def _hold(client, hp, state, note=None, who="Kiran"):
    return client.post(
        f"{API}/tables/{hp['table']['id']}/hold", headers=h(hp[who]), json={"state": state, "note": note}
    )


async def _order(client, hp, qty=2):
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
    return o


async def test_a_visit_from_seating_to_clean(client, ht):
    r = await _seat(client, ht, 4)
    assert r.status_code == 200 and r.json()["state"] == "SEATED" and r.json()["guests"] == 4
    seated_at = r.json()["seated_at"]
    assert (await _hold(client, ht, "RESERVED")).json()["code"] == "table_in_use"
    await _order(client, ht)
    t = await _table(client, ht)
    assert t["state"] == "BILL_PENDING" and t["seated_at"] == seated_at, "the timer started at seating"
    bill = (await client.post(f"{API}/bills", headers=h(ht["admin"]), json={"table_id": ht["table"]["id"]})).json()
    assert bill["guests"] == 4
    await client.post(f"{API}/bills/{bill['id']}/settle", headers=h(ht["admin"]), json={"method": "CASH"})
    t = await _table(client, ht)
    assert t["state"] == "CLEANING" and t["seated_at"] is None and t["guests"] is None
    assert (await _hold(client, ht, "FREE")).json()["state"] == "FREE"
    # Covers in the report: 4 guests, average spend per guest from the server.
    rep = (await client.get(f"{API}/reports/sales", headers=h(ht["admin"]))).json()["totals"]
    assert rep["covers"] == 4 and rep["avg_per_cover_minor"] == bill["total_minor"] // 4


async def test_reserved_then_the_first_order_seats_the_table(client, ht):
    r = await _hold(client, ht, "RESERVED", "8 pm, 4 people")
    assert r.json()["state"] == "RESERVED" and r.json()["hold_note"] == "8 pm, 4 people"
    await client.post(
        f"{API}/orders",
        headers=h(ht["Kiran"]),
        json={"table_id": ht["table"]["id"], "items": [{"item_id": ht["items"]["Chai"], "qty": 1}]},
    )
    t = await _table(client, ht)
    assert t["state"] == "OCCUPIED" and t["seated_at"] is not None and t["hold_note"] is None


async def test_guests_who_leave_without_ordering_free_the_table(client, ht):
    await _seat(client, ht, 2)
    assert (await _hold(client, ht, "FREE")).json()["state"] == "FREE"
    assert (await _table(client, ht))["seated_at"] is None


async def test_split_counts_guests_once(client, ht):
    await _seat(client, ht, 3)
    await _order(client, ht, qty=3)
    pv = (
        await client.get(f"{API}/bills/split-preview", headers=h(ht["admin"]), params={"table_id": ht["table"]["id"]})
    ).json()
    r = await client.post(
        f"{API}/bills/split",
        headers=h(ht["admin"]),
        json={"table_id": ht["table"]["id"], "guests": 3, "lines": [{"key": pv["lines"][0]["key"], "qty": [1, 1, 1]}]},
    )
    assert [b["guests"] for b in r.json()["bills"]] == [3, None, None]
    rep = (await client.get(f"{API}/reports/sales", headers=h(ht["admin"]))).json()["totals"]
    assert rep["covers"] == 3, "a split never multiplies the guests"


async def test_permissions_and_isolation(client, root, ht):
    assert (await _seat(client, ht, 2, who="Ravi")).status_code == 403, "chefs don't seat tables"
    assert (await _seat(client, ht, 0)).status_code == 422
    other = await make_hotel(client, root)
    r = await client.post(f"{API}/tables/{ht['table']['id']}/seat", headers=h(other["admin"]), json={"guests": 2})
    assert r.status_code == 404
