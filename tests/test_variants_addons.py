"""Menu variants (half/full) and add-ons (docs/03 "Variants and add-ons", docs/14)."""

import pytest

from app.ordering.pricing import AddonPick, addons_label, bp_of, unit_price
from app.platform.gstin import checksum_char
from tests.conftest import h, make_hotel

API = "/api/v1"


def test_unit_price_and_label():
    picks = (AddonPick("r", "Raita", 3000), AddonPick("c", "Cheese", 4000, qty=2))
    assert unit_price(18000, picks) == 29000
    assert addons_label(picks) == "Raita, 2 x Cheese"
    assert addons_label([{"name": "Raita", "qty": 1}]) == "Raita"


@pytest.fixture
async def hm(client, root):
    """GST hotel; Biryani has Half/Full, an optional Extras group (0..3) and a required Bread choice (1..1)."""
    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    hp = await make_hotel(client, root, gst_registration="REGULAR", gstin=g)
    a = h(hp["admin"])
    bid = hp["items"]["Biryani"]
    v = await client.put(
        f"{API}/items/{bid}/variants",
        headers=a,
        json={"variants": [{"name": "Half", "price_minor": 18000}, {"name": "Full", "price_minor": 28000}]},
    )
    assert v.status_code == 200, v.text
    extras = (
        await client.post(
            f"{API}/addon-groups",
            headers=a,
            json={
                "name": "Extras",
                "min_select": 0,
                "max_select": 3,
                "addons": [{"name": "Raita", "price_minor": 3000}, {"name": "Cheese", "price_minor": 4000}],
            },
        )
    ).json()
    bread = (
        await client.post(
            f"{API}/addon-groups",
            headers=a,
            json={"name": "Bread", "min_select": 1, "max_select": 1, "addons": [{"name": "Roti"}, {"name": "Naan"}]},
        )
    ).json()
    r = await client.put(f"{API}/items/{bid}/addon-groups", headers=a, json={"group_ids": [extras["id"]]})
    assert r.status_code == 200, r.text
    ids = {x["name"]: x["id"] for x in v.json()}
    ad = {x["name"]: x["id"] for x in extras["addons"]}
    return {
        **hp,
        "half": ids["Half"],
        "full": ids["Full"],
        "raita": ad["Raita"],
        "cheese": ad["Cheese"],
        "extras": extras,
        "bread": bread,
    }


def _order(client, hm, lines, who="Kiran"):
    return client.post(f"{API}/orders", headers=h(hm[who]), json={"table_id": hm["table"]["id"], "items": lines})


async def test_half_with_add_ons_and_full_on_one_order(client, hm):
    items = (await client.get(f"{API}/items", headers=h(hm["Kiran"]))).json()
    biryani = next(i for i in items if i["name"] == "Biryani")
    assert [v["name"] for v in biryani["variants"]] == ["Half", "Full"]
    assert [g["name"] for g in biryani["addon_groups"]] == ["Extras"]
    groups = (await client.get(f"{API}/addon-groups", headers=h(hm["admin"]))).json()
    assert next(g for g in groups if g["name"] == "Extras")["used_by"] == ["Biryani"], "where a group is used"

    bid = hm["items"]["Biryani"]
    r = await _order(
        client,
        hm,
        [
            {
                "item_id": bid,
                "qty": 2,
                "variant_id": hm["half"],
                "note": "less spicy",
                "addons": [{"addon_id": hm["raita"]}, {"addon_id": hm["cheese"], "qty": 2}],
            },
            {"item_id": bid, "qty": 1, "variant_id": hm["full"]},
        ],
    )
    assert r.status_code == 201, r.text
    o = r.json()
    half, full = o["items"]
    # Server prices: Half 180 + Raita 30 + 2 x Cheese 40 = 290 a plate.
    assert half["name"] == "Biryani (Half)" and half["variant_name"] == "Half" and half["unit_price_minor"] == 29000
    assert half["line_total_minor"] == 58000 and half["note"] == "less spicy"
    assert [(a["name"], a["qty"], a["price_minor"]) for a in half["addons"]] == [
        ("Raita", 1, 3000),
        ("Cheese", 2, 4000),
    ]
    assert full["name"] == "Biryani (Full)" and full["unit_price_minor"] == 28000 and full["note"] is None
    # Add-ons are taxed at the dish's rate (composite supply).
    assert o["tax_minor"] == bp_of(58000, 500) + bp_of(28000, 500) > 0

    # The kitchen ticket shows the add-ons under the dish.
    kot = (
        await client.post(f"{API}/orders/{o['id']}/kot", headers=h(hm["admin"]), json={"send_to_kitchen": False})
    ).json()
    text = " ".join(str(x) for x in kot["blocks"])
    assert "2 x Biryani (Half)" in text and "+ Raita" in text and "+ 2 x Cheese" in text

    # A later price change never alters the order or its bill (snapshot).
    await client.put(
        f"{API}/items/{bid}/variants",
        headers=h(hm["admin"]),
        json={
            "variants": [
                {"id": hm["half"], "name": "Half", "price_minor": 99900},
                {"id": hm["full"], "name": "Full", "price_minor": 28000},
            ]
        },
    )
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hm["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hm["Kiran"]))
    bill = (await client.post(f"{API}/bills", headers=h(hm["admin"]), json={"table_id": hm["table"]["id"]})).json()
    lines = {ln["name"]: ln for ln in bill["lines"]}
    assert lines["Biryani (Half) + Raita, 2 x Cheese"]["unit_price_minor"] == 29000
    assert lines["Biryani (Full)"]["line_total_minor"] == 28000
    assert bill["subtotal_minor"] == o["subtotal_minor"]


@pytest.mark.parametrize(
    ("make_line", "code"),
    [
        (lambda m: {"variant_id": None}, "variant_required"),  # Biryani needs a size
        (lambda m: {"variant_id": m["half"], "addons": [{"addon_id": m["cheese"], "qty": 4}]}, "addon_count"),  # max 3
        (lambda m: {"variant_id": m["half"], "addons": [{"addon_id": m["bread"]["addons"][0]["id"]}]}, "bad_addon"),
        (
            lambda m: {"variant_id": m["half"], "addons": [{"addon_id": m["raita"]}, {"addon_id": m["raita"]}]},
            "bad_addon",
        ),  # same add-on twice: use qty instead
    ],
)
async def test_choices_are_checked_on_the_server(client, hm, make_line, code):
    r = await _order(client, hm, [{"item_id": hm["items"]["Biryani"], "qty": 1, **make_line(hm)}])
    assert r.status_code == 400 and r.json()["code"] == code, r.text


async def test_required_group_sold_out_and_no_sizes(client, hm):
    a = h(hm["admin"])
    bid = hm["items"]["Biryani"]
    # Make Bread required on Biryani: an order without a bread choice is refused.
    await client.put(
        f"{API}/items/{bid}/addon-groups", headers=a, json={"group_ids": [hm["extras"]["id"], hm["bread"]["id"]]}
    )
    r = await _order(client, hm, [{"item_id": bid, "qty": 1, "variant_id": hm["half"]}])
    assert r.json()["code"] == "addon_count" and r.json()["details"]["group"] == "Bread"
    roti = hm["bread"]["addons"][0]["id"]
    ok = await _order(
        client, hm, [{"item_id": bid, "qty": 1, "variant_id": hm["half"], "addons": [{"addon_id": roti}]}]
    )
    assert ok.status_code == 201, ok.text
    # A chef can mark the half sold out; the full is still on.
    s = await client.post(
        f"{API}/variants/{hm['half']}/availability", headers=h(hm["Ravi"]), json={"is_available": False}
    )
    assert s.status_code == 200
    r = await _order(client, hm, [{"item_id": bid, "qty": 1, "variant_id": hm["half"], "addons": [{"addon_id": roti}]}])
    assert r.status_code == 409 and r.json()["code"] == "item_unavailable"
    # A size on an item that has none is refused.
    r = await _order(client, hm, [{"item_id": hm["items"]["Chai"], "qty": 1, "variant_id": hm["half"]}])
    assert r.json()["code"] == "bad_variant"
    # Servers cannot edit the menu.
    r = await client.put(f"{API}/items/{bid}/variants", headers=h(hm["Kiran"]), json={"variants": []})
    assert r.status_code == 403


async def test_diners_never_see_sold_out_choices(client, hm):
    qr = hm["table"]["qr_token"]
    bid = hm["items"]["Biryani"]

    async def biryani():
        m = (await client.get(f"{API}/d/{qr}")).json()
        return next(i for c in m["categories"] for i in c["items"] if i["id"] == bid)

    b = await biryani()
    assert [v["name"] for v in b["variants"]] == ["Half", "Full"] and b["is_available"]
    await client.post(f"{API}/variants/{hm['half']}/availability", headers=h(hm["admin"]), json={"is_available": False})
    await client.post(f"{API}/addons/{hm['raita']}/availability", headers=h(hm["admin"]), json={"is_available": False})
    b = await biryani()
    assert [v["name"] for v in b["variants"]] == ["Full"]
    assert [x["name"] for x in b["addon_groups"][0]["addons"]] == ["Cheese"]
    await client.post(f"{API}/variants/{hm['full']}/availability", headers=h(hm["admin"]), json={"is_available": False})
    b = await biryani()
    assert b["is_available"] is False, "every size sold out = the dish is sold out"


async def test_sizes_need_two_and_other_hotels_cannot_touch_them(client, root, hm):
    bid = hm["items"]["Biryani"]
    one = await client.put(
        f"{API}/items/{bid}/variants", headers=h(hm["admin"]), json={"variants": [{"name": "Only", "price_minor": 1}]}
    )
    assert one.status_code == 422
    other = await make_hotel(client, root)
    r = await client.put(f"{API}/items/{bid}/variants", headers=h(other["admin"]), json={"variants": []})
    assert r.status_code == 404
    assert (await client.get(f"{API}/addon-groups", headers=h(other["admin"]))).json() == []
