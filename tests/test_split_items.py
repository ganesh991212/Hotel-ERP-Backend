"""Split by items: one GST bill per guest (docs/03 "Split by items", docs/14)."""

import pytest

from app.billing.service import REPLACED_MARK, SplitSpec, allocate, apportion, line_key
from app.core.errors import ApiError
from app.ordering.pricing import bp_of
from app.platform.gstin import checksum_char
from tests.conftest import h, make_hotel

API = "/api/v1"


# ---------- pure ----------


def test_apportion_adds_up_to_the_paisa():
    assert apportion(100_00, [1, 1, 1]) == [33_34, 33_33, 33_33]
    assert apportion(28_000, [2, 1]) == [18_667, 9_333]
    assert sum(apportion(123_457, [3, 0, 4])) == 123_457 and apportion(123_457, [3, 0, 4])[1] == 0


def test_allocate_units_and_shares():
    grouped = {("Naan", 6000, 500): (3, 18_000), ("Chai", 4000, 500): (1, 4_000)}
    parts = allocate(
        grouped,
        3,
        [
            SplitSpec(line_key("Naan", 6000, 500), qty=[1, 2, 0]),
            SplitSpec(line_key("Chai", 4000, 500), shared=[1, 2, 3]),
        ],
    )
    assert [sum(x.line_total_minor for x in p) for p in parts] == [6_000 + 1_334, 12_000 + 1_333, 1_333]
    assert parts[2][0].name == "Chai (1/3 share)" and parts[2][0].qty == 1
    assert all(x.qty * x.unit_price_minor == x.line_total_minor for p in parts for x in p)


@pytest.mark.parametrize(
    ("specs", "code"),
    [
        ([SplitSpec("Naan|6000|500", qty=[1, 1])], "bad_split"),  # one unit not given out
        ([SplitSpec("Naan|6000|500", qty=[3, 0])], "guest_empty"),  # guest 2 gets nothing
        ([SplitSpec("Naan|6000|500", shared=[1, 1])], "bad_split"),  # same guest twice
        ([SplitSpec("Naan|6000|500", shared=[1, 3])], "bad_split"),  # no guest 3
        ([SplitSpec("Roti|1000|500", qty=[1, 2])], "lines_changed"),  # the table changed under the screen
    ],
)
def test_allocate_refuses_anything_not_exact(specs, code):
    with pytest.raises(ApiError) as e:
        allocate({("Naan", 6000, 500): (3, 18_000)}, 2, specs)
    assert e.value.code == code


# ---------- API ----------


@pytest.fixture
async def hp(client, root):
    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    return await make_hotel(client, root, gst_registration="REGULAR", gstin=g)


async def _served(client, hp, items: dict[str, int]) -> dict:
    body = {"table_id": hp["table"]["id"], "items": [{"item_id": hp["items"][n], "qty": q} for n, q in items.items()]}
    o = (await client.post(f"{API}/orders", headers=h(hp["Kiran"]), json=body)).json()
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hp["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hp["Kiran"]))
    return o


def _split(client, hp, lines, guests=3, who="admin"):
    return client.post(
        f"{API}/bills/split",
        headers=h(hp[who]),
        json={"table_id": hp["table"]["id"], "guests": guests, "lines": lines},
    )


async def _preview(client, hp) -> dict:
    r = await client.get(f"{API}/bills/split-preview", headers=h(hp["admin"]), params={"table_id": hp["table"]["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def _table_state(tables, hp):
    return next(t for t in tables if t["id"] == hp["table"]["id"])["state"]


async def test_three_guests_get_their_own_gst_bills(client, hp):
    o = await _served(client, hp, {"Biryani": 2, "Naan": 3, "Chai": 1})
    whole = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()

    pv = await _preview(client, hp)
    assert pv["open_bill"]["invoice_no"] == whole["invoice_no"] and pv["unserved"] == []
    key = {ln["name"]: ln["key"] for ln in pv["lines"]}
    r = await _split(
        client,
        hp,
        [
            {"key": key["Biryani"], "qty": [1, 1, 0]},
            {"key": key["Naan"], "qty": [1, 1, 1]},
            {"key": key["Chai"], "shared": [1, 2, 3]},
        ],
    )
    assert r.status_code == 201, r.text
    out = r.json()
    bills = out["bills"]
    # The ordinary bill is cancelled and replaced: the same food is never on two live bills.
    assert out["replaced_invoice_nos"] == [whole["invoice_no"]]
    old = (await client.get(f"{API}/bills/{whole['id']}", headers=h(hp["admin"]))).json()
    assert old["status"] == "VOID" and REPLACED_MARK in old["void_reason"]
    # Own numbers, in order; guest k of 3; every guest bill lists the visit's order.
    assert [b["number"] for b in bills] == [whole["number"] + i for i in (1, 2, 3)]
    assert [b["split"]["guest_no"] for b in bills] == [1, 2, 3] and bills[0]["split"]["guest_count"] == 3
    assert all(b["orders"] == [o["number"]] for b in bills)
    # Food value is shared to the paisa; GST is recalculated on each bill's own lines (never a divided total).
    assert sum(b["subtotal_minor"] for b in bills) == whole["subtotal_minor"]
    for b in bills:
        assert b["tax_minor"] == sum(bp_of(ln["line_total_minor"], ln["tax_rate_bp"]) for ln in b["lines"]) > 0
        assert b["cgst_minor"] + b["sgst_minor"] == b["tax_minor"]
        assert b["total_minor"] == b["subtotal_minor"] + b["tax_minor"] + b["service_charge_minor"]
    assert "Chai (1/3 share)" in [ln["name"] for ln in bills[2]["lines"]]
    printed = (await client.post(f"{API}/bills/{bills[1]['id']}/print", headers=h(hp["admin"]), json={})).json()
    assert "Guest 2 of 3" in " ".join(str(x) for x in printed["blocks"])

    # Guest 1 pays: the table still owes; once everyone has paid it is free.
    await client.post(f"{API}/bills/{bills[0]['id']}/settle", headers=h(hp["admin"]), json={"method": "UPI"})
    tables = (await client.get(f"{API}/tables", headers=h(hp["admin"]))).json()
    assert _table_state(tables, hp) == "BILL_PENDING"
    g1 = (await client.get(f"{API}/bills/{bills[0]['id']}", headers=h(hp["admin"]))).json()
    assert [s["status"] for s in g1["split"]["bills"]] == ["PAID", "ISSUED", "ISSUED"]
    # A split with a paid guest cannot be cancelled.
    v = await client.post(f"{API}/bills/{bills[1]['id']}/void", headers=h(hp["admin"]), json={"reason": "Mistake"})
    assert v.status_code == 409 and v.json()["code"] == "split_partly_paid"
    for b in bills[1:]:
        await client.post(f"{API}/bills/{b['id']}/settle", headers=h(hp["admin"]), json={"method": "CASH"})
    tables = (await client.get(f"{API}/tables", headers=h(hp["admin"]))).json()
    assert _table_state(tables, hp) == "FREE"

    # Reports count three bills and one replaced.
    t = (await client.get(f"{API}/reports/sales", headers=h(hp["admin"]))).json()["totals"]
    assert t["bills"] == 3 and t["replaced_bills"] == 1 and t["paid_minor"] == sum(b["total_minor"] for b in bills)


async def test_food_ordered_after_a_split_gets_its_own_bill(client, hp):
    await _served(client, hp, {"Naan": 2})
    key = (await _preview(client, hp))["lines"][0]["key"]
    bills = (await _split(client, hp, [{"key": key, "qty": [1, 1]}], guests=2)).json()["bills"]
    await _served(client, hp, {"Chai": 1})
    pv = await _preview(client, hp)
    assert [ln["name"] for ln in pv["lines"]] == ["Chai"], "only the new food is left to share out"
    extra = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()
    assert extra["replaced_invoice_nos"] == [] and extra["split"] is None
    for b in bills:
        assert (await client.get(f"{API}/bills/{b['id']}", headers=h(hp["admin"]))).json()["status"] == "ISSUED"


async def test_an_unpaid_split_is_cancelled_as_a_whole_and_can_be_redone(client, hp):
    await _served(client, hp, {"Naan": 2})
    key = (await _preview(client, hp))["lines"][0]["key"]
    bills = (await _split(client, hp, [{"key": key, "qty": [1, 1]}], guests=2)).json()["bills"]
    v = await client.post(f"{API}/bills/{bills[1]['id']}/void", headers=h(hp["admin"]), json={"reason": "Wrong guests"})
    assert v.status_code == 200, v.text
    for b in bills:
        assert (await client.get(f"{API}/bills/{b['id']}", headers=h(hp["admin"]))).json()["status"] == "VOID"
    again = await _split(client, hp, [{"key": key, "shared": [1, 2]}], guests=2)
    assert again.status_code == 201, again.text
    assert [b["total_minor"] for b in again.json()["bills"]] == [b["total_minor"] for b in bills]


async def test_refusals(client, root, hp):
    await _served(client, hp, {"Naan": 2})
    key = (await _preview(client, hp))["lines"][0]["key"]
    assert (await _split(client, hp, [{"key": key, "qty": [2, 0]}], guests=2)).json()["code"] == "guest_empty"
    assert (await _split(client, hp, [{"key": key, "qty": [1, 0]}], guests=2)).json()["code"] == "bad_split"
    assert (await _split(client, hp, [{"key": "Old|1|0", "qty": [1, 1]}], guests=2)).json()["code"] == "lines_changed"
    # A chef cannot bill; a paid-in-part ordinary bill must be cleared first.
    assert (await _split(client, hp, [{"key": key, "qty": [1, 1]}], guests=2, who="Ravi")).status_code == 403
    b = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()
    await client.post(
        f"{API}/bills/{b['id']}/payments", headers=h(hp["admin"]), json={"method": "CASH", "amount_minor": 100}
    )
    assert (await _split(client, hp, [{"key": key, "qty": [1, 1]}], guests=2)).json()["code"] == "has_payments"
    # A discounted bill is not split (the discount would be lost); it must be cancelled first.
    paid = (await client.get(f"{API}/bills/{b['id']}", headers=h(hp["admin"]))).json()["payments"][0]["id"]
    await client.delete(f"{API}/bills/{b['id']}/payments/{paid}", headers=h(hp["admin"]))
    disc = {"table_id": hp["table"]["id"], "discount": {"percent_bp": 1000, "reason": "Regular guest"}}
    assert (await client.post(f"{API}/bills", headers=h(hp["admin"]), json=disc)).status_code == 201
    r = await _split(client, hp, [{"key": key, "qty": [1, 1]}], guests=2)
    assert r.status_code == 409 and r.json()["code"] == "bill_discounted"
    # Another hotel cannot see this table.
    other = await make_hotel(client, root)
    r = await client.get(
        f"{API}/bills/split-preview", headers=h(other["admin"]), params={"table_id": hp["table"]["id"]}
    )
    assert r.status_code == 404
