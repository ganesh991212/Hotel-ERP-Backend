"""Kitchen stations: split tickets and screens (docs/03 "Kitchen stations", docs/14)."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hs(client, root):
    """Bar station on a Drinks category (Chai); Tandoor station set on the Naan dish itself; Biryani has none."""
    hp = await make_hotel(client, root)
    a = h(hp["admin"])
    bar = (await client.post(f"{API}/stations", headers=a, json={"name": "Bar"})).json()
    tandoor = (await client.post(f"{API}/stations", headers=a, json={"name": "Tandoor"})).json()
    drinks = (
        await client.post(f"{API}/categories", headers=a, json={"name": "Drinks", "station_id": bar["id"]})
    ).json()
    await client.patch(f"{API}/items/{hp['items']['Chai']}", headers=a, json={"category_id": drinks["id"]})
    await client.patch(f"{API}/items/{hp['items']['Naan']}", headers=a, json={"station_id": tandoor["id"]})
    return {**hp, "bar": bar, "tandoor": tandoor}


async def test_each_dish_shows_where_it_goes(client, hs):
    items = {i["name"]: i for i in (await client.get(f"{API}/items", headers=h(hs["Kiran"]))).json()}
    assert items["Chai"]["station"]["name"] == "Bar", "from its category"
    assert items["Naan"]["station"]["name"] == "Tandoor", "its own setting wins"
    assert items["Biryani"]["station"] is None, "no station: the main kitchen"


async def test_one_ticket_per_station_and_the_order_keeps_its_route(client, hs):
    a = h(hs["admin"])
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hs["Kiran"]),
            json={
                "table_id": hs["table"]["id"],
                "items": [
                    {"item_id": hs["items"]["Biryani"], "qty": 1},
                    {"item_id": hs["items"]["Naan"], "qty": 2},
                    {"item_id": hs["items"]["Chai"], "qty": 2},
                ],
            },
        )
    ).json()
    assert [(i["name"], (i["station"] or {}).get("name")) for i in o["items"]] == [
        ("Biryani", None),
        ("Naan", "Tandoor"),
        ("Chai", "Bar"),
    ]
    kot = (
        await client.post(f"{API}/orders/{o['id']}/kot", headers=a, json={"send_to_kitchen": False, "paper_mm": 80})
    ).json()
    assert all(t["escpos_b64"] for t in kot["tickets"]), "network printers get each slip as raw bytes"
    tickets = {t["station"]: " ".join(str(b) for b in t["blocks"]) for t in kot["tickets"]}
    assert list(tickets) == ["Kitchen", "Tandoor", "Bar"]
    assert "Biryani" in tickets["Kitchen"] and "Naan" not in tickets["Kitchen"]
    assert "2 x Chai" in tickets["Bar"] and "Biryani" not in tickets["Bar"] and "KOT - BAR" in tickets["Bar"]
    assert all(f"Order #{o['number']}" in t for t in tickets.values()), "every ticket names the order and table"
    full = " ".join(str(b) for b in kot["blocks"])
    assert "Biryani" in full and "Chai" in full and "Naan" in full
    # Moving Chai to another station later doesn't move the ticket already sent.
    await client.patch(f"{API}/items/{hs['items']['Chai']}", headers=a, json={"station_id": hs["tandoor"]["id"]})
    again = (await client.get(f"{API}/orders/{o['id']}", headers=a)).json()
    assert [i["station"]["name"] for i in again["items"] if i["name"] == "Chai"] == ["Bar"]


async def test_single_station_orders_print_one_ticket(client, hs):
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hs["Kiran"]),
            json={"table_id": hs["table"]["id"], "items": [{"item_id": hs["items"]["Biryani"], "qty": 1}]},
        )
    ).json()
    kot = (
        await client.post(f"{API}/orders/{o['id']}/kot", headers=h(hs["admin"]), json={"send_to_kitchen": False})
    ).json()
    assert kot["tickets"] == [] and kot["blocks"]


async def test_station_rules_permissions_and_isolation(client, root, hs):
    a = h(hs["admin"])
    assert (await client.post(f"{API}/stations", headers=a, json={"name": "bar"})).json()["code"] == "station_exists"
    assert (await client.post(f"{API}/stations", headers=a, json={"name": "Kitchen"})).json()[
        "code"
    ] == "station_exists"
    assert (await client.post(f"{API}/stations", headers=h(hs["Kiran"]), json={"name": "Grill"})).status_code == 403
    # Deleting a station sends its dishes back to the main kitchen; null on a dish follows the category again.
    assert (await client.delete(f"{API}/stations/{hs['tandoor']['id']}", headers=a)).status_code == 204
    items = {i["name"]: i for i in (await client.get(f"{API}/items", headers=a)).json()}
    assert items["Naan"]["station"] is None
    await client.patch(f"{API}/items/{hs['items']['Chai']}", headers=a, json={"station_id": None})
    items = {i["name"]: i for i in (await client.get(f"{API}/items", headers=a)).json()}
    assert items["Chai"]["station"]["name"] == "Bar"
    other = await make_hotel(client, root)
    assert (await client.get(f"{API}/stations", headers=h(other["admin"]))).json() == []
    r = await client.put(f"{API}/stations/{hs['bar']['id']}", headers=h(other["admin"]), json={"name": "Mine"})
    assert r.status_code == 404
