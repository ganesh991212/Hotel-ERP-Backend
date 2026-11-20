"""Part payments and equal split (docs/03 "Payments", docs/13 section 10)."""

import uuid

import pytest
from sqlalchemy import text

from app.billing.service import split_equally
from app.core.db import tenant_session
from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hp(client, root):
    return await make_hotel(client, root)


async def _billed(client, hp, qty=7) -> dict:
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
    return (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()


def _pay(client, hp, bill, method, amount, who="admin"):
    return client.post(
        f"{API}/bills/{bill['id']}/payments", headers=h(hp[who]), json={"method": method, "amount_minor": amount}
    )


def test_equal_shares_add_up_to_the_paisa():
    assert split_equally(100_00, 3) == [33_34, 33_33, 33_33]
    assert sum(split_equally(123_457, 7)) == 123_457


async def test_cash_plus_upi_pays_the_bill(client, hp):
    b = await _billed(client, hp)
    total = b["total_minor"]
    first = await _pay(client, hp, b, "CASH", 10_000)
    assert first.status_code == 200, first.text
    part = first.json()
    assert part["status"] == "ISSUED" and part["paid_minor"] == 10_000 and part["due_minor"] == total - 10_000
    over = await _pay(client, hp, b, "UPI", total)
    assert over.status_code == 409 and over.json()["code"] == "overpayment"
    assert over.json()["details"]["due_minor"] == total - 10_000
    done = (await _pay(client, hp, b, "UPI", total - 10_000)).json()
    assert done["status"] == "PAID" and done["payment_method"] == "MIXED" and done["due_minor"] == 0
    assert [p["method"] for p in done["payments"]] == ["CASH", "UPI"]
    # Paid: the visit is over; the table waits to be cleared (docs/03 "Table visits"), and no more payments are taken.
    tables = (await client.get(f"{API}/tables", headers=h(hp["admin"]))).json()
    assert next(t for t in tables if t["id"] == hp["table"]["id"])["state"] == "CLEANING"
    assert (await _pay(client, hp, b, "CASH", 1)).status_code == 409
    printed = (await client.post(f"{API}/bills/{b['id']}/print", headers=h(hp["admin"]), json={})).json()
    text_ = " ".join(str(x) for x in printed["blocks"])
    assert "Paid by CASH" in text_ and "Paid by UPI" in text_


async def test_split_equally_then_each_guest_pays(client, hp):
    b = await _billed(client, hp, qty=5)
    split = (await client.get(f"{API}/bills/{b['id']}/split?parts=3", headers=h(hp["admin"]))).json()
    assert sum(split["shares_minor"]) == split["due_minor"] == b["total_minor"]
    for share, method in zip(split["shares_minor"], ["UPI", "UPI", "CARD"], strict=True):
        last = (await _pay(client, hp, b, method, share)).json()
    assert last["status"] == "PAID"
    assert (await client.get(f"{API}/bills/{b['id']}/split?parts=1", headers=h(hp["admin"]))).status_code == 422


async def test_a_mistaken_part_can_be_removed_only_while_unpaid(client, hp):
    b = await _billed(client, hp)
    part = (await _pay(client, hp, b, "CARD", 5_000)).json()
    pid = part["payments"][0]["id"]
    # A bill with money taken cannot simply be voided.
    v = await client.post(f"{API}/bills/{b['id']}/void", headers=h(hp["admin"]), json={"reason": "wrong table"})
    assert v.status_code == 409 and v.json()["code"] == "has_payments"
    r = await client.delete(f"{API}/bills/{b['id']}/payments/{pid}", headers=h(hp["admin"]))
    assert r.status_code == 200 and r.json()["paid_minor"] == 0
    await client.post(f"{API}/bills/{b['id']}/settle", headers=h(hp["admin"]), json={"method": "CASH"})
    paid = (await client.get(f"{API}/bills/{b['id']}", headers=h(hp["admin"]))).json()
    assert paid["status"] == "PAID" and paid["payment_method"] == "CASH"
    gone = await client.delete(f"{API}/bills/{b['id']}/payments/{paid['payments'][0]['id']}", headers=h(hp["admin"]))
    assert gone.status_code == 409, "a paid bill's takings are final"


async def test_money_moves_to_the_replacement_bill(client, hp):
    b = await _billed(client, hp, qty=10)
    await _pay(client, hp, b, "CASH", 20_000)
    # Guest orders more: the bill is replaced, and the cash already taken comes along.
    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hp["Kiran"]),
            json={"table_id": hp["table"]["id"], "items": [{"item_id": hp["items"]["Chai"], "qty": 1}]},
        )
    ).json()
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hp["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hp["Kiran"]))
    nb = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"]})).json()
    assert nb["replaced_invoice_nos"] == [b["invoice_no"]]
    assert nb["paid_minor"] == 20_000 and nb["due_minor"] == nb["total_minor"] - 20_000
    old = (await client.get(f"{API}/bills/{b['id']}", headers=h(hp["admin"]))).json()
    assert old["payments"] == [], "nothing is left behind on the cancelled bill"
    # A discount that would make the new total smaller than what was already paid is refused.
    r = await client.post(
        f"{API}/bills",
        headers=h(hp["admin"]),
        json={"table_id": hp["table"]["id"], "discount": {"percent_bp": 9000, "reason": "Test big"}},
    )
    assert r.status_code == 409 and r.json()["code"] == "payments_exceed_total"


async def test_payments_are_isolated_per_hotel(client, root, hp):
    b = await _billed(client, hp)
    await _pay(client, hp, b, "CASH", 1_000)
    other = await make_hotel(client, root)
    async with tenant_session(uuid.UUID(other["hotel"]["id"])) as db:
        seen = (await db.execute(text("select count(*) from bill_payments"))).scalar()
    assert seen == 0, "hotel B must not see hotel A's payments"
    r = await client.get(f"{API}/bills/{b['id']}", headers=h(other["admin"]))
    assert r.status_code == 404
