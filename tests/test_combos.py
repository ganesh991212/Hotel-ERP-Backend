"""Combos (docs/03 "Combos", docs/14)."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hc(client, root):
    """A Thali combo (1 Biryani + 2 Naan) at ₹300 with a required free drink choice."""
    hp = await make_hotel(client, root)
    a = h(hp["admin"])
    cat = (await client.get(f"{API}/categories", headers=a)).json()[0]["id"]
    combo = (
        await client.post(f"{API}/items", headers=a, json={"category_id": cat, "name": "Thali", "price_minor": 30000})
    ).json()
    r = await client.put(
        f"{API}/items/{combo['id']}/components",
        headers=a,
        json={"components": [{"item_id": hp["items"]["Biryani"]}, {"item_id": hp["items"]["Naan"], "qty": 2}]},
    )
    assert r.status_code == 200, r.text
    drink = (
        await client.post(
            f"{API}/addon-groups",
            headers=a,
            json={
                "name": "Drink",
                "min_select": 1,
                "max_select": 1,
                "addons": [{"name": "Chai"}, {"name": "Lassi", "price_minor": 2000}],
            },
        )
    ).json()
    await client.put(f"{API}/items/{combo['id']}/addon-groups", headers=a, json={"group_ids": [drink["id"]]})
    return {**hp, "combo": combo["id"], "drink": {x["name"]: x["id"] for x in drink["addons"]}}


async def _order(client, hc, qty=1, drink="Chai"):
    return await client.post(
        f"{API}/orders",
        headers=h(hc["Kiran"]),
        json={
            "table_id": hc["table"]["id"],
            "items": [{"item_id": hc["combo"], "qty": qty, "addons": [{"addon_id": hc["drink"][drink]}]}],
        },
    )


async def test_combo_is_one_price_and_the_kitchen_sees_every_dish(client, hc):
    items = (await client.get(f"{API}/items", headers=h(hc["Kiran"]))).json()
    thali = next(i for i in items if i["name"] == "Thali")
    assert [(c["name"], c["qty"]) for c in thali["components"]] == [("Biryani", 1), ("Naan", 2)]
    assert thali["est_prep_minutes"] == 20, "the slowest dish sets the combo's time"

    r = await _order(client, hc, qty=2, drink="Lassi")
    assert r.status_code == 201, r.text
    line = r.json()["items"][0]
    assert line["unit_price_minor"] == 30000 + 2000 and line["line_total_minor"] == 64000
    assert [(c["name"], c["qty"]) for c in line["components"]] == [("Biryani", 1), ("Naan", 2)]
    kot = (
        await client.post(f"{API}/orders/{r.json()['id']}/kot", headers=h(hc["admin"]), json={"send_to_kitchen": False})
    ).json()
    text = " ".join(str(x) for x in kot["blocks"])
    assert "2 x Thali" in text and "- 2 x Biryani" in text and "- 4 x Naan" in text and "+ Lassi" in text


async def test_combo_sold_out_while_a_dish_is(client, hc):
    await client.post(
        f"{API}/items/{hc['items']['Naan']}/availability", headers=h(hc["Ravi"]), json={"is_available": False}
    )
    r = await _order(client, hc)
    assert r.status_code == 409 and r.json()["code"] == "item_unavailable" and "Naan" in r.json()["message"]
    menu = (await client.get(f"{API}/d/{hc['table']['qr_token']}")).json()
    thali = next(i for c in menu["categories"] for i in c["items"] if i["id"] == hc["combo"])
    assert thali["is_available"] is False


async def test_combo_rules(client, root, hc):
    a = h(hc["admin"])
    bid = hc["items"]["Biryani"]

    def put(item, comps, who=a):
        return client.put(f"{API}/items/{item}/components", headers=who, json={"components": comps})

    assert (await put(hc["combo"], [{"item_id": hc["combo"]}, {"item_id": bid}])).json()["code"] == "bad_combo"
    assert (await put(hc["combo"], [{"item_id": bid}])).json()["code"] == "bad_combo", "one dish is not a combo"
    assert (await put(hc["combo"], [{"item_id": bid}, {"item_id": bid}])).json()["code"] == "bad_combo"
    # No combos inside combos, either way round.
    assert (await put(hc["items"]["Chai"], [{"item_id": hc["combo"]}, {"item_id": bid}])).json()["code"] == "bad_combo"
    assert (await put(bid, [{"item_id": hc["items"]["Chai"], "qty": 2}])).json()["code"] == "bad_combo"
    # A dish in a combo can't be deleted out from under it.
    d = await client.delete(f"{API}/items/{hc['items']['Naan']}", headers=a)
    assert d.status_code == 400 and d.json()["code"] == "item_in_combo" and d.json()["details"]["combos"] == ["Thali"]
    assert (await put(hc["combo"], [], who=h(hc["Kiran"]))).status_code == 403
    other = await make_hotel(client, root)
    assert (await put(hc["combo"], [], who=h(other["admin"]))).status_code == 404
    # Empty = an ordinary item again.
    assert (await put(hc["combo"], [])).json() == []
