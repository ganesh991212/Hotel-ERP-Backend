"""One bill per table visit (docs/03 "Billing a table visit")."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hb(client, root):
    """A fresh hotel per test: each test owns its table's whole visit."""
    return await make_hotel(client, root)


async def _order(client, hb, item="Naan", qty=2) -> dict:
    r = await client.post(
        f"{API}/orders",
        headers=h(hb["Kiran"]),
        json={"table_id": hb["table"]["id"], "items": [{"item_id": hb["items"][item], "qty": qty}]},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _cook(client, hb, o, serve=True) -> None:
    for step in ("accept", "ready"):
        assert (await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hb["Ravi"]))).status_code == 200
    if serve:
        assert (await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hb["Kiran"]))).status_code == 200


async def _bill(client, hb, **extra):
    return await client.post(f"{API}/bills", headers=h(hb["admin"]), json={"table_id": hb["table"]["id"], **extra})


async def _get_bill(client, hb, bill_id):
    return (await client.get(f"{API}/bills/{bill_id}", headers=h(hb["admin"]))).json()


async def test_items_added_after_bill_replace_it_with_one_bill(client, hb):
    """Regression: the guest's second round got a separate bill."""
    first = await _order(client, hb)
    await _cook(client, hb, first)
    b1 = (await _bill(client, hb)).json()
    assert b1["orders"] == [first["number"]]

    second = await _order(client, hb, item="Chai", qty=2)
    await _cook(client, hb, second)
    r = await _bill(client, hb)
    assert r.status_code == 201, r.text
    b2 = r.json()
    assert b2["replaced_invoice_nos"] == [b1["invoice_no"]]
    assert b2["orders"] == sorted([first["number"], second["number"]]), "one bill for the whole visit"
    assert b2["total_minor"] == first["total_minor"] + second["total_minor"]
    old = await _get_bill(client, hb, b1["id"])
    assert old["status"] == "VOID" and b2["invoice_no"] in old["void_reason"], "cancelled, pointing to its replacement"
    assert b2["number"] == b1["number"] + 1, "numbering stays gapless; the cancelled number stays in the register"

    open_bills = (await client.get(f"{API}/bills?scope=open", headers=h(hb["admin"]))).json()
    assert [b["id"] for b in open_bills] == [b2["id"]]


async def test_billing_while_cooking_warns_then_allows(client, hb):
    served = await _order(client, hb)
    await _cook(client, hb, served)
    cooking = await _order(client, hb, item="Chai", qty=1)
    assert (await client.post(f"{API}/orders/{cooking['id']}/accept", headers=h(hb["Ravi"]))).status_code == 200

    warn = await _bill(client, hb)
    assert warn.status_code == 409 and warn.json()["code"] == "orders_not_served"
    assert warn.json()["details"]["orders"] == [cooking["number"]], "the client names what is still cooking"

    ok = await _bill(client, hb, include_unserved=True)
    assert ok.status_code == 201 and set(ok.json()["orders"]) == {served["number"], cooking["number"]}


async def test_cancelling_a_billed_order_cancels_the_unpaid_bill(client, hb):
    served = await _order(client, hb)
    await _cook(client, hb, served)
    cooking = await _order(client, hb, item="Chai", qty=1)
    bill = (await _bill(client, hb, include_unserved=True)).json()

    r = await client.post(f"{API}/orders/{cooking['id']}/cancel", headers=h(hb["admin"]), json={"reason": "guest left"})
    assert r.status_code == 200, r.text
    assert (await _get_bill(client, hb, bill["id"]))["status"] == "VOID", "the bill included the cancelled order"
    again = (await _bill(client, hb)).json()
    assert again["orders"] == [served["number"]] and again["total_minor"] == served["total_minor"]


async def test_after_payment_the_next_order_starts_a_new_visit(client, hb):
    o = await _order(client, hb)
    await _cook(client, hb, o)
    paid = (await _bill(client, hb)).json()
    r = await client.post(f"{API}/bills/{paid['id']}/settle", headers=h(hb["admin"]), json={"method": "CASH"})
    assert r.status_code == 200

    nothing = await _bill(client, hb)
    assert nothing.status_code == 400 and nothing.json()["code"] == "nothing_to_bill"

    nxt = await _order(client, hb)
    await _cook(client, hb, nxt)
    new = (await _bill(client, hb)).json()
    assert new["replaced_invoice_nos"] == [] and new["orders"] == [nxt["number"]]
    assert (await _get_bill(client, hb, paid["id"]))["status"] == "PAID", "a paid bill is never replaced"


async def test_paid_bill_orders_cannot_be_cancelled(client, hb):
    o = await _order(client, hb)
    await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hb["Ravi"]))
    bill = (await _bill(client, hb, include_unserved=True)).json()
    await client.post(f"{API}/bills/{bill['id']}/settle", headers=h(hb["admin"]), json={"method": "UPI"})
    r = await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(hb["admin"]), json={"reason": "test"})
    assert r.status_code == 409 and r.json()["code"] == "on_paid_bill"


async def test_paid_bill_can_be_printed_again_as_duplicate(client, hb):
    o = await _order(client, hb)
    await _cook(client, hb, o)
    bill = (await _bill(client, hb)).json()
    await client.post(f"{API}/bills/{bill['id']}/settle", headers=h(hb["admin"]), json={"method": "CASH"})
    first = (await client.post(f"{API}/bills/{bill['id']}/print", headers=h(hb["admin"]), json={})).json()
    again = (await client.post(f"{API}/bills/{bill['id']}/print", headers=h(hb["admin"]), json={})).json()
    titles = lambda doc: " ".join(str(b) for b in doc["blocks"])  # noqa: E731
    assert "DUPLICATE" not in titles(first) and "DUPLICATE" in titles(again)


# ----------------------------------------------------------------- long lists --


async def test_orders_and_bills_lists_by_date_search_and_pages(client, hb):
    from datetime import date, timedelta

    orders = []
    for _ in range(3):
        o = await _order(client, hb, qty=1)
        await _cook(client, hb, o)
        orders.append(o)
    bill = (await _bill(client, hb)).json()
    today = (await client.get(f"{API}/dashboard/summary", headers=h(hb["admin"]))).json()["date"]  # hotel-local
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()

    def get(path):
        return client.get(f"{API}{path}", headers=h(hb["admin"]))

    day = (await get(f"/orders?scope=all&from={today}&to={today}")).json()
    assert {o["id"] for o in orders} <= {o["id"] for o in day}
    assert (await get(f"/orders?scope=all&from={yesterday}&to={yesterday}")).json() == []

    # Pages: newest first, no overlap, nothing lost.
    p1 = (await get(f"/orders?scope=all&from={today}&limit=2&offset=0")).json()
    p2 = (await get(f"/orders?scope=all&from={today}&limit=2&offset=2")).json()
    assert len(p1) == 2 and not {o["id"] for o in p1} & {o["id"] for o in p2}
    assert p1[0]["number"] > p1[1]["number"]

    # Search by order number (with or without #) and by table.
    n = orders[1]["number"]
    assert [o["number"] for o in (await get(f"/orders?scope=all&q=%23{n}")).json()] == [n]
    by_table = (await get(f"/orders?scope=all&q={hb['table']['label']}")).json()
    assert len(by_table) >= 3

    bills = (await get(f"/bills?scope=all&from={today}&to={today}")).json()
    assert [b["id"] for b in bills] == [bill["id"]]
    assert (await get(f"/bills?scope=all&q={bill['invoice_no'][-5:]}")).json()[0]["id"] == bill["id"]
    assert (await get("/bills?scope=all&status=PAID")).json() == []

    too_long = await get("/orders?scope=all&from=2026-01-01&to=2026-03-01")
    assert too_long.status_code == 400 and too_long.json()["code"] == "range_too_long"
    backwards = await get(f"/bills?scope=all&from={today}&to={yesterday}")
    assert backwards.status_code == 400 and backwards.json()["code"] == "bad_range"
