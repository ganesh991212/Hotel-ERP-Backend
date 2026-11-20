"""Cash handover between shifts (docs/03 "Cash handover", docs/14): blind count, each shift answers for its own time."""

import pytest

from tests.conftest import h, make_hotel
from tests.test_day_close import _paid_bill

API = "/api/v1"


@pytest.fixture
async def hh(client, root):
    return await make_hotel(client, root)


async def _uid(client, hp, name):
    users = (await client.get(f"{API}/users", headers=h(hp["admin"]))).json()
    return next(u["id"] for u in users if u["name"] == name)


async def test_blind_count_per_shift_and_the_day_close_lists_them(client, hh):
    a = h(hh["admin"])
    kiran = await _uid(client, hh, "Kiran")
    start = (await client.get(f"{API}/handover", headers=a)).json()
    assert start["needs_opening_float"] and "expected_minor" not in start, "blind: nothing to copy from"

    morning = await _paid_bill(client, hh, "CASH")
    upi = await _paid_bill(client, hh, "UPI", qty=1)
    # The morning cashier hands over Rs 50 short.
    counted = 100_000 + morning["total_minor"] - 5000
    r = await client.post(
        f"{API}/handover", headers=a, json={"counted_minor": counted, "taken_by": kiran, "opening_float_minor": 100_000}
    )
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["expected_minor"] == 100_000 + morning["total_minor"]
    assert first["difference_minor"] == -5000 and first["result"] == "SHORT"
    assert first["upi_minor"] == upi["total_minor"] and first["taken_by"] == "Kiran"
    # The note comes after the count, once; the count never changes.
    n = await client.post(f"{API}/handover/{first['id']}/note", headers=a, json={"note": "Gave change twice"})
    assert n.json()["note"] == "Gave change twice" and n.json()["counted_minor"] == counted
    again = await client.post(f"{API}/handover/{first['id']}/note", headers=a, json={"note": "edit"})
    assert again.json()["code"] == "note_exists"

    # The next shift starts from what was counted, so it answers only for its own sales.
    evening = await _paid_bill(client, hh, "CASH", qty=2)
    second = (
        await client.post(
            f"{API}/handover",
            headers=h(hh["admin"]),
            json={"counted_minor": counted + evening["total_minor"], "taken_by": kiran},
        )
    ).json()
    assert second["opening_minor"] == counted and second["cash_in_minor"] == evening["total_minor"]
    assert second["result"] == "EXACT", "the morning shortage is not blamed on the evening"

    dc = (await client.get(f"{API}/day-close", headers=a)).json()
    assert [x["result"] for x in dc["handovers"]] == ["SHORT", "EXACT"]


async def test_refusals(client, hh):
    a = h(hh["admin"])
    users = (await client.get(f"{API}/users", headers=a)).json()
    admin_id = next(u["id"] for u in users if u["name"] not in ("Ravi", "Sita", "Kiran"))
    r = await client.post(f"{API}/handover", headers=a, json={"counted_minor": 0, "taken_by": admin_id})
    assert r.status_code == 400 and r.json()["code"] == "same_person", "hand over to someone else"
    waiter = await client.post(
        f"{API}/handover", headers=h(hh["Kiran"]), json={"counted_minor": 0, "taken_by": admin_id}
    )
    assert waiter.status_code == 403
