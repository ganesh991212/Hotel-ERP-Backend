"""Takeaway / counter orders (docs/03 "Takeaway orders", docs/13 section 6)."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture(scope="module")
async def ht(client, root):
    return await make_hotel(client, root)


async def _takeaway(client, ht, **extra):
    body = {"takeaway": True, "items": [{"item_id": ht["items"]["Naan"], "qty": 2}], **extra}
    return await client.post(f"{API}/orders", headers=h(ht["Kiran"]), json=body)


async def test_takeaway_order_through_kitchen_handover_and_bill(client, ht):
    r = await _takeaway(client, ht, customer_name="Ramesh", customer_phone="98480 22338")
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["channel"] == "TAKEAWAY" and o["table"] is None
    assert o["customer_name"] == "Ramesh" and o["customer_phone"] == "98480 22338"

    # The kitchen ticket says TAKEAWAY and who it is for.
    kot = (
        await client.post(f"{API}/orders/{o['id']}/kot", headers=h(ht["admin"]), json={"send_to_kitchen": False})
    ).json()
    kot_text = " ".join(str(x) for x in kot["blocks"])
    assert "TAKEAWAY" in kot_text and "Ramesh" in kot_text and "Table" not in kot_text

    # Same kitchen flow as a table order.
    board = (await client.get(f"{API}/kitchen/board", headers=h(ht["Ravi"]))).json()
    assert o["id"] in [x["id"] for x in board["up_for_grabs"]]
    for step in ("accept", "ready"):
        assert (await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(ht["Ravi"]))).status_code == 200

    # Billed per order; still being handed over -> warn, then allow.
    warn = await client.post(f"{API}/bills", headers=h(ht["admin"]), json={"order_id": o["id"]})
    assert warn.status_code == 409 and warn.json()["code"] == "orders_not_served"
    bill = await client.post(
        f"{API}/bills", headers=h(ht["admin"]), json={"order_id": o["id"], "include_unserved": True}
    )
    assert bill.status_code == 201, bill.text
    b = bill.json()
    assert b["table_id"] is None and b["customer_name"] == "Ramesh" and b["orders"] == [o["number"]]
    assert b["total_minor"] == o["total_minor"]
    again = await client.post(f"{API}/bills", headers=h(ht["admin"]), json={"order_id": o["id"]})
    assert again.status_code == 409 and again.json()["code"] == "already_billed"

    # Handed over = served.
    assert (await client.post(f"{API}/orders/{o['id']}/serve", headers=h(ht["Kiran"]))).json()["status"] == "SERVED"

    printed = (await client.post(f"{API}/bills/{b['id']}/print", headers=h(ht["admin"]), json={})).json()
    text = " ".join(str(x) for x in printed["blocks"])
    assert "Takeaway" in text and "Ramesh" in text and "Table" not in text


async def test_takeaway_never_touches_table_state_and_is_searchable(client, ht):
    o = (await _takeaway(client, ht, customer_name="Lakshmi Devi")).json()
    tables = (await client.get(f"{API}/tables", headers=h(ht["admin"]))).json()
    assert all(t["open_orders"] == 0 or t["id"] == ht["table"]["id"] for t in tables)
    found = (await client.get(f"{API}/orders?scope=all&q=lakshmi", headers=h(ht["admin"]))).json()
    assert [x["id"] for x in found] == [o["id"]]
    await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(ht["admin"]), json={"reason": "test"})


async def test_takeaway_guards(client, ht):
    both = await client.post(
        f"{API}/orders",
        headers=h(ht["Kiran"]),
        json={"takeaway": True, "table_id": ht["table"]["id"], "items": [{"item_id": ht["items"]["Naan"], "qty": 1}]},
    )
    assert both.status_code == 422, "a table order or a takeaway, not both"
    neither = await client.post(
        f"{API}/orders", headers=h(ht["Kiran"]), json={"items": [{"item_id": ht["items"]["Naan"], "qty": 1}]}
    )
    assert neither.status_code == 422
    assert (await _takeaway(client, ht, customer_phone="call me")).status_code == 422
    # A chef cannot take orders.
    r = await client.post(
        f"{API}/orders",
        headers=h(ht["Ravi"]),
        json={"takeaway": True, "items": [{"item_id": ht["items"]["Naan"], "qty": 1}]},
    )
    assert r.status_code == 403
    # Table orders are billed per table visit, not per order.
    t = await client.post(
        f"{API}/orders",
        headers=h(ht["Kiran"]),
        json={"table_id": ht["table"]["id"], "items": [{"item_id": ht["items"]["Naan"], "qty": 1}]},
    )
    r = await client.post(f"{API}/bills", headers=h(ht["admin"]), json={"order_id": t.json()["id"]})
    assert r.status_code == 400 and r.json()["code"] == "table_order"
    await client.post(f"{API}/orders/{t.json()['id']}/cancel", headers=h(ht["admin"]), json={"reason": "test"})
