"""B2B bills: the buyer's GSTIN on the tax invoice (docs/03 "B2B invoices", docs/14)."""

import pytest

from app.platform.gstin import checksum_char
from tests.conftest import h, make_hotel

API = "/api/v1"


def gstin(first14: str) -> str:
    return first14 + checksum_char(first14)


HOTEL = gstin("36ABCDE1234F1Z")
BUYER = gstin("36PQRST5678K1Z")  # same state (36)
OTHER_STATE = gstin("29PQRST5678K1Z")


@pytest.fixture
async def hb(client, root):
    return await make_hotel(client, root, plan="PRO", gst_registration="REGULAR", gstin=HOTEL)


async def _served(client, hp, **qty) -> dict:
    body = {"table_id": hp["table"]["id"], "items": [{"item_id": hp["items"][n], "qty": q} for n, q in qty.items()]}
    o = (await client.post(f"{API}/orders", headers=h(hp["Kiran"]), json=body)).json()
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hp["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hp["Kiran"]))
    return o


def _bill(client, hp, **extra):
    return client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"], **extra})


BUYER_IN = {"name": "Acme Foods Pvt Ltd", "gstin": BUYER.lower(), "address": "Plot 4, Hitech City, Hyderabad"}


async def test_buyer_on_the_tax_invoice_and_kept_when_more_food_comes(client, hb):
    await _served(client, hb, Biryani=1)
    first = (await _bill(client, hb)).json()
    assert first["buyer"] is None
    # Added to the unpaid bill: re-issued (an issued invoice is never edited).
    r = await _bill(client, hb, buyer=BUYER_IN)
    assert r.status_code == 201, r.text
    b2b = r.json()
    assert b2b["buyer"] == {"name": "Acme Foods Pvt Ltd", "gstin": BUYER, "address": BUYER_IN["address"]}
    assert b2b["replaced_invoice_nos"] == [first["invoice_no"]] and b2b["total_minor"] == first["total_minor"]
    printed = (await client.post(f"{API}/bills/{b2b['id']}/print", headers=h(hb["admin"]), json={})).json()
    text = " ".join(str(x) for x in printed["blocks"])
    assert f"GSTIN: {BUYER}" in text and "Bill to: Acme Foods Pvt Ltd" in text and "TAX INVOICE" in text
    # More food: the new bill keeps the buyer without asking again.
    await _served(client, hb, Chai=1)
    again = (await _bill(client, hb)).json()
    assert again["buyer"]["gstin"] == BUYER and again["replaced_invoice_nos"] == [b2b["invoice_no"]]
    # A company bill is not split by items (the guest bills would lose the buyer).
    lines = [{"key": "x", "qty": [1, 0]}]
    split = await client.post(
        f"{API}/bills/split",
        headers=h(hb["admin"]),
        json={"table_id": hb["table"]["id"], "guests": 2, "lines": lines},
    )
    assert split.status_code == 409 and split.json()["code"] == "bill_has_buyer"

    # GST report: this invoice is B2B, on its own line; nothing left in B2C.
    gst = (await client.get(f"{API}/reports/gst", headers=h(hb["admin"]))).json()
    assert [x["invoice_no"] for x in gst["b2b"]] == [again["invoice_no"]]
    row = gst["b2b"][0]
    assert row["buyer_gstin"] == BUYER and row["taxable_minor"] == again["taxable_minor"]
    assert row["cgst_minor"] + row["sgst_minor"] == again["tax_minor"]
    assert gst["b2c_by_rate"] == [] and gst["totals"]["tax_minor"] == again["tax_minor"]
    csv = (await client.get(f"{API}/reports/gst.csv", headers=h(hb["admin"]))).text
    assert "B2B invoices" in csv and BUYER in csv


async def test_wrong_gstins_are_refused_before_anything_is_cancelled(client, hb):
    await _served(client, hb, Naan=1)
    first = (await _bill(client, hb)).json()
    bad = [
        ({**BUYER_IN, "gstin": BUYER[:-1] + ("A" if BUYER[-1] != "A" else "B")}, "bad_gstin"),  # check digit
        ({**BUYER_IN, "gstin": HOTEL}, "bad_gstin"),  # the hotel's own
        ({**BUYER_IN, "gstin": OTHER_STATE}, "interstate_b2b"),  # may need IGST (docs/14)
    ]
    for buyer, code in bad:
        r = await _bill(client, hb, buyer=buyer)
        assert r.status_code == 400 and r.json()["code"] == code, r.text
    bills = (await client.get(f"{API}/bills/{first['id']}", headers=h(hb["admin"]))).json()
    assert bills["status"] == "ISSUED", "the open bill was not cancelled by a refused request"


async def test_only_a_gst_registered_hotel_issues_b2b_bills(client, root):
    plain = await make_hotel(client, root)
    await _served(client, plain, Chai=1)
    r = await _bill(client, plain, buyer=BUYER_IN)
    assert r.status_code == 400 and r.json()["code"] == "not_tax_invoice"


async def test_a_takeaway_bill_takes_the_buyer_by_its_order(client, hb):
    body = {"takeaway": True, "items": [{"item_id": hb["items"]["Chai"], "qty": 2}]}
    o = (await client.post(f"{API}/orders", headers=h(hb["Kiran"]), json=body)).json()
    first = (
        await client.post(f"{API}/bills", headers=h(hb["admin"]), json={"order_id": o["id"], "include_unserved": True})
    ).json()
    assert first["order_ids"] == [o["id"]], "a bill opened on its own knows which order to re-issue by"
    r = await client.post(
        f"{API}/bills",
        headers=h(hb["admin"]),
        json={"order_id": first["order_ids"][0], "include_unserved": True, "buyer": BUYER_IN},
    )
    assert r.status_code == 201, r.text
    assert r.json()["buyer"]["gstin"] == BUYER and r.json()["replaced_invoice_nos"] == [first["invoice_no"]]
