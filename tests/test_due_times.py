"""Target time per dish and "Delayed" (docs/14): due times fixed when ordered; on-time share on the Overview."""

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hd(client, root):
    return await make_hotel(client, root)


async def _order(client, hp, **qty) -> dict:
    body = {"table_id": hp["table"]["id"], "items": [{"item_id": hp["items"][n], "qty": q} for n, q in qty.items()]}
    r = await client.post(f"{API}/orders", headers=h(hp["Kiran"]), json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_each_dish_is_due_by_its_own_prep_time_and_the_order_by_the_slowest(client, hd):
    o = await _order(client, hd, Biryani=1, Chai=2)  # 20 and 4 minutes of prep
    placed = datetime.fromisoformat(o["created_at"])
    due = {i["name"]: datetime.fromisoformat(i["due_at"]) for i in o["items"]}
    pickup = timedelta(minutes=5)  # the outlet's default chef pick-up time counts: the guest waits from ordering
    assert abs(due["Biryani"] - (placed + pickup + timedelta(minutes=20))) < timedelta(seconds=5)
    assert abs(due["Chai"] - (placed + pickup + timedelta(minutes=4))) < timedelta(seconds=5)
    assert o["due_at"] == max(i["due_at"] for i in o["items"])
    # A later menu change does not move an order already placed (snapshot).
    await client.patch(f"{API}/items/{hd['items']['Biryani']}", headers=h(hd["admin"]), json={"est_prep_minutes": 45})
    again = (await client.get(f"{API}/orders/{o['id']}", headers=h(hd["admin"]))).json()
    assert again["due_at"] == o["due_at"]


async def test_overview_counts_orders_ready_on_time(client, hd):
    fast = await _order(client, hd, Chai=1)
    late = await _order(client, hd, Naan=1)
    for o in (fast, late):
        for step in ("accept", "ready"):
            r = await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hd["Ravi"]))
            assert r.status_code == 200, r.text
    # Pretend the Naan's due time had passed before it was ready (the app never edits due times).
    eng = create_async_engine(os.environ["MIGRATION_DATABASE_URL"])
    async with eng.begin() as c:
        sql = "UPDATE orders SET due_at = ready_at - interval '3 minutes' WHERE id = :i"
        await c.execute(text(sql), {"i": late["id"]})
    await eng.dispose()
    t = (await client.get(f"{API}/dashboard/summary", headers=h(hd["admin"]))).json()["on_time"]
    assert t == {"ready": 2, "on_time": 1, "pct": 50}
