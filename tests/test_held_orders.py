"""Hold / resume (docs/03 "Hold / resume", docs/14): a cart on the server, re-priced, consumed once."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hh(client, root):
    return await make_hotel(client, root)


def _items(hp, **qty):
    return [{"item_id": hp["items"][n], "qty": q} for n, q in qty.items()]


async def _orders(client, hp) -> list:
    return (await client.get(f"{API}/orders", headers=h(hp["admin"]), params={"scope": "today"})).json()


async def test_hold_is_not_an_order_and_resume_sends_it_once(client, hh):
    k = h(hh["Kiran"])
    r = await client.post(
        f"{API}/held-orders",
        headers=k,
        json={"items": _items(hh, Biryani=1, Chai=2), "customer_name": " Ravi K ", "note": "less spicy"},
    )
    assert r.status_code == 201, r.text
    held = r.json()
    assert held["customer_name"] == "Ravi K" and held["table"] is None and held["held_by"]
    assert held["quote"]["total_minor"] > 0 and held["problem"] is None
    assert await _orders(client, hh) == [], "a hold never reaches the kitchen"
    # The takeaway list shows it; a table's list does not.
    assert [x["id"] for x in (await client.get(f"{API}/held-orders?takeaway=true", headers=k)).json()] == [held["id"]]
    tid = hh["table"]["id"]
    assert (await client.get(f"{API}/held-orders?table_id={tid}", headers=k)).json() == []

    body = {"takeaway": True, "items": held["items"], "held_order_id": held["id"], "client_ref": "resume-1"}
    o = await client.post(f"{API}/orders", headers=k, json=body)
    assert o.status_code == 201, o.text
    assert (await client.get(f"{API}/held-orders", headers=k)).json() == [], "sending consumes the hold"
    # Another device resuming the same hold is refused, not sent twice.
    again = await client.post(f"{API}/orders", headers=k, json={**body, "client_ref": "resume-2"})
    assert again.status_code == 409 and again.json()["code"] == "hold_gone"
    assert len(await _orders(client, hh)) == 1


async def test_prices_are_todays_and_sold_out_dishes_are_flagged(client, hh):
    a, k = h(hh["admin"]), h(hh["Kiran"])
    tid = hh["table"]["id"]
    held = (
        await client.post(f"{API}/held-orders", headers=k, json={"table_id": tid, "items": _items(hh, Naan=2)})
    ).json()
    assert held["table"]["id"] == tid
    before = held["quote"]["subtotal_minor"]
    await client.patch(f"{API}/items/{hh['items']['Naan']}", headers=a, json={"price_minor": 7000})
    now = (await client.get(f"{API}/held-orders?table_id={tid}", headers=k)).json()[0]
    assert now["quote"]["subtotal_minor"] == 14000 != before, "never yesterday's price"
    await client.post(f"{API}/items/{hh['items']['Naan']}/availability", headers=a, json={"is_available": False})
    stale = (await client.get(f"{API}/held-orders?table_id={tid}", headers=k)).json()[0]
    assert stale["quote"] is None and stale["problem"]["code"] == "item_unavailable"
    # Close the day counts holds still waiting.
    assert (await client.get(f"{API}/day-close", headers=a)).json()["held_orders"] == 1
    # Discarding is allowed once; a second discard says it is gone.
    assert (await client.delete(f"{API}/held-orders/{held['id']}", headers=k)).status_code == 204
    gone = await client.delete(f"{API}/held-orders/{held['id']}", headers=k)
    assert gone.status_code == 409 and gone.json()["code"] == "hold_gone"
    # A cart that cannot be priced is not held at all.
    bad = await client.post(f"{API}/held-orders", headers=k, json={"items": _items(hh, Naan=1)})
    assert bad.status_code == 409 and bad.json()["code"] == "item_unavailable"


async def test_holds_need_permission_and_stay_in_their_hotel(client, root, hh):
    held = (await client.post(f"{API}/held-orders", headers=h(hh["Kiran"]), json={"items": _items(hh, Chai=1)})).json()
    chef = await client.post(f"{API}/held-orders", headers=h(hh["Ravi"]), json={"items": _items(hh, Chai=1)})
    assert chef.status_code == 403, "a chef does not take orders"
    other = await make_hotel(client, root)
    assert (await client.get(f"{API}/held-orders", headers=h(other["admin"]))).json() == []
    r = await client.delete(f"{API}/held-orders/{held['id']}", headers=h(other["admin"]))
    assert r.status_code == 409, "another hotel cannot see or discard it"
    r = await client.post(
        f"{API}/orders",
        headers=h(other["Kiran"]),
        json={"takeaway": True, "items": _items(other, Chai=1), "held_order_id": held["id"]},
    )
    assert r.status_code == 409 and r.json()["code"] == "hold_gone"
    assert len((await client.get(f"{API}/held-orders", headers=h(hh["admin"]))).json()) == 1
