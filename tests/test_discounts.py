"""Bill discounts with a per-role maximum (docs/03 "Discounts", docs/13 section 10)."""

import pytest

from app.ordering.pricing import bp_of
from tests.conftest import h, login, make_hotel

API = "/api/v1"


@pytest.fixture
async def hd(client, root):
    """Fresh hotel per test, with a cashier."""
    hotel = await make_hotel(client, root)
    roles = {r["code"]: r for r in (await client.get(f"{API}/roles", headers=h(hotel["admin"]))).json()}
    email = f"cash-{hotel['table']['id'][:8]}@hotel.example.com"
    r = await client.post(
        f"{API}/users",
        headers=h(hotel["admin"]),
        json={"name": "Cara", "email": email, "password": "cashier-pass-1", "role_id": roles["CASHIER"]["id"]},
    )
    assert r.status_code == 201, r.text
    return {**hotel, "roles": roles, "Cara": await login(client, email, "cashier-pass-1")}


async def _served_table(client, hd, qty=10) -> dict:
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hd["Kiran"]),
            json={"table_id": hd["table"]["id"], "items": [{"item_id": hd["items"]["Naan"], "qty": qty}]},
        )
    ).json()
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hd["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hd["Kiran"]))
    return o


def _bill(client, hd, who, **discount):
    body = {"table_id": hd["table"]["id"]}
    if discount:
        body["discount"] = discount
    return client.post(f"{API}/bills", headers=h(hd[who]), json=body)


async def test_discount_lowers_the_taxable_value_and_prints(client, root):
    """GST-registered hotel: the discount must reduce CGST/SGST, not just the total."""
    from app.platform.gstin import checksum_char

    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    hd = await make_hotel(client, root, gst_registration="REGULAR", gstin=g)
    o = await _served_table(client, hd)
    assert o["tax_minor"] > 0, "the test needs a hotel that charges GST"
    r = await _bill(client, hd, "admin", percent_bp=1000, reason="Regular guest")
    assert r.status_code == 201, r.text
    b = r.json()
    sub = o["subtotal_minor"]
    assert b["discount_minor"] == bp_of(sub, 1000) and b["taxable_minor"] == sub - b["discount_minor"]
    # GST is charged on each line's discounted value.
    expected_tax = sum(bp_of(ln["line_total_minor"] - ln["discount_minor"], ln["tax_rate_bp"]) for ln in b["lines"])
    assert b["tax_minor"] == expected_tax < o["tax_minor"]
    assert b["cgst_minor"] + b["sgst_minor"] == b["tax_minor"]
    assert b["total_minor"] == b["taxable_minor"] + b["tax_minor"] + b["service_charge_minor"]
    assert sum(gp["tax_minor"] for gp in b["tax_groups"]) == b["tax_minor"], "printed tax groups add up"
    assert sum(ln["discount_minor"] for ln in b["lines"]) == b["discount_minor"]
    assert b["discount_reason"] == "Regular guest"

    printed = (await client.post(f"{API}/bills/{b['id']}/print", headers=h(hd["admin"]), json={})).json()
    text = " ".join(str(x) for x in printed["blocks"])
    assert "Discount" in text and "Taxable value" in text and "Regular guest" in text


async def test_role_limits_are_enforced_on_the_server(client, hd):
    await _served_table(client, hd)
    # Cashier default limit: 10%.
    over = await _bill(client, hd, "Cara", percent_bp=1500, reason="Asked nicely")
    assert over.status_code == 403 and over.json()["code"] == "discount_limit"
    assert over.json()["details"]["max_bp"] == 1000
    # A fixed amount is checked as a share of the subtotal, rounded against the giver.
    sub = (await client.get(f"{API}/orders?scope=attention", headers=h(hd["admin"]))).json()[0]["subtotal_minor"]
    just_over = await _bill(client, hd, "Cara", amount_minor=bp_of(sub, 1000) + 1, reason="Rounding try")
    assert just_over.status_code == 403
    ok = await _bill(client, hd, "Cara", percent_bp=1000, reason="Loyalty")
    assert ok.status_code == 201, ok.text
    # A server's role (0%) cannot give any discount.
    r = await _bill(client, hd, "Kiran", percent_bp=100, reason="Friend")
    assert r.status_code == 403 and r.json()["details"]["max_bp"] == 0


async def test_admin_sets_each_roles_limit(client, hd):
    cashier = hd["roles"]["CASHIER"]
    assert cashier["max_discount_bp"] == 1000
    assert (await client.get(f"{API}/me", headers=h(hd["Cara"]))).json()["max_discount_bp"] == 1000
    assert (await client.get(f"{API}/me", headers=h(hd["admin"]))).json()["max_discount_bp"] == 10_000
    assert hd["roles"]["HOTEL_ADMIN"]["max_discount_bp"] == 10_000
    r = await client.put(
        f"{API}/roles/{cashier['id']}/discount-limit", headers=h(hd["admin"]), json={"max_discount_bp": 2500}
    )
    assert r.status_code == 200 and r.json()["max_discount_bp"] == 2500
    locked = await client.put(
        f"{API}/roles/{hd['roles']['HOTEL_ADMIN']['id']}/discount-limit",
        headers=h(hd["admin"]),
        json={"max_discount_bp": 0},
    )
    assert locked.status_code == 403
    nope = await client.put(
        f"{API}/roles/{cashier['id']}/discount-limit", headers=h(hd["Cara"]), json={"max_discount_bp": 10_000}
    )
    assert nope.status_code == 403, "a cashier cannot raise their own limit"
    await _served_table(client, hd)
    assert (await _bill(client, hd, "Cara", percent_bp=2500, reason="Festival")).status_code == 201


async def test_discount_after_printing_reissues_the_unpaid_bill(client, hd):
    o = await _served_table(client, hd)
    first = (await _bill(client, hd, "admin")).json()
    assert first["discount_minor"] == 0
    r = await _bill(client, hd, "admin", amount_minor=5000, reason="Late food")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["replaced_invoice_nos"] == [first["invoice_no"]] and b["orders"] == [o["number"]]
    assert b["discount_minor"] == 5000
    old = (await client.get(f"{API}/bills/{first['id']}", headers=h(hd["admin"]))).json()
    assert old["status"] == "VOID" and old["void_reason"].startswith("Discount applied")
    # Nothing new and no discount: still nothing to bill.
    assert (await _bill(client, hd, "admin")).status_code == 400


async def test_takeaway_discount(client, hd):
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hd["Kiran"]),
            json={"takeaway": True, "items": [{"item_id": hd["items"]["Naan"], "qty": 4}]},
        )
    ).json()
    body = {"order_id": o["id"], "include_unserved": True}
    first = (await client.post(f"{API}/bills", headers=h(hd["Cara"]), json=body)).json()
    again = await client.post(
        f"{API}/bills", headers=h(hd["Cara"]), json={**body, "discount": {"percent_bp": 500, "reason": "Pickup"}}
    )
    assert again.status_code == 201, again.text
    assert again.json()["replaced_invoice_nos"] == [first["invoice_no"]]
    assert again.json()["discount_minor"] == bp_of(o["subtotal_minor"], 500)


async def test_bad_discounts_are_refused(client, hd):
    await _served_table(client, hd)
    both = await _bill(client, hd, "admin", percent_bp=100, amount_minor=100, reason="x-x-x")
    assert both.status_code == 422
    no_reason = await client.post(
        f"{API}/bills", headers=h(hd["admin"]), json={"table_id": hd["table"]["id"], "discount": {"percent_bp": 100}}
    )
    assert no_reason.status_code == 422
    assert (await _bill(client, hd, "admin", percent_bp=10_001, reason="too much")).status_code == 422
