"""Order workflow (docs/04 "Order workflow"): settings, confirmed-order alerts, table bill state, counter mode."""

import pytest

from app.realtime.hub import hub, outlet_channel
from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture(scope="module")
async def hw(client, root):
    return await make_hotel(client, root)


async def _order(client, hw, item="Naan", qty=1) -> dict:
    qr = hw["table"]["qr_token"]
    tok = (await client.post(f"{API}/d/{qr}/session")).json()["session_token"]
    r = await client.post(
        f"{API}/d/orders", headers={"X-Diner-Token": tok}, json={"items": [{"item_id": hw["items"][item], "qty": qty}]}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _cook(client, hw, order_id: str) -> dict:
    assert (await client.post(f"{API}/orders/{order_id}/accept", headers=h(hw["Ravi"]))).status_code == 200
    r = await client.post(f"{API}/orders/{order_id}/ready", headers=h(hw["Ravi"]))
    assert r.status_code == 200, r.text
    return r.json()


async def _table(client, hw) -> dict:
    tables = (await client.get(f"{API}/tables", headers=h(hw["admin"]))).json()
    return next(t for t in tables if t["id"] == hw["table"]["id"])


async def test_workflow_settings_round_trip_and_permissions(client, hw):
    s = (await client.get(f"{API}/settings", headers=h(hw["admin"]))).json()
    assert s["serve_mode"] == "SERVER" and s["pickup_alert_minutes"] == 5  # defaults

    r = await client.put(
        f"{API}/settings", headers=h(hw["admin"]), json={"serve_mode": "COUNTER", "pickup_alert_minutes": 12}
    )
    assert r.status_code == 200 and r.json()["serve_mode"] == "COUNTER" and r.json()["pickup_alert_minutes"] == 12
    off = await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"pickup_alert_minutes": 0})
    assert off.json()["pickup_alert_minutes"] is None, "0 turns the alert off"

    for bad in ({"serve_mode": "DRONE"}, {"pickup_alert_minutes": 61}, {"pickup_alert_minutes": -1}):
        assert (await client.put(f"{API}/settings", headers=h(hw["admin"]), json=bad)).status_code == 422
    # Only settings.manage may change the workflow.
    assert (
        await client.put(f"{API}/settings", headers=h(hw["Kiran"]), json={"serve_mode": "SERVER"})
    ).status_code == 403

    await client.put(
        f"{API}/settings", headers=h(hw["admin"]), json={"serve_mode": "SERVER", "pickup_alert_minutes": 5}
    )


async def test_confirmed_qr_order_is_announced_as_new_kitchen_work(client, hw):
    """Regression: confirming a QR order emitted 'order.updated', which neither the phone push nor the in-app
    sound treated as new work, so chefs were never alerted."""
    await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"qr_confirm_required": True})
    try:
        o = await _order(client, hw)
        assert o["status"] == "PLACED"
        me = (await client.get(f"{API}/me", headers=h(hw["admin"]))).json()
        q = hub.subscribe(outlet_channel(me["outlet"]["id"]))
        assert (await client.post(f"{API}/orders/{o['id']}/confirm", headers=h(hw["Kiran"]))).status_code == 200
        events = []
        while not q.empty():
            events.append(q.get_nowait())
        assert any(e["type"] == "order.confirmed" and e["order"]["id"] == o["id"] for e in events), events
    finally:
        await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"qr_confirm_required": False})
        await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(hw["admin"]), json={"reason": "test"})


async def test_served_table_is_bill_pending_until_paid(client, hw):
    o = await _order(client, hw, qty=2)
    assert (await _table(client, hw))["state"] == "OCCUPIED"

    await _cook(client, hw, o["id"])
    assert (await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hw["Kiran"]))).status_code == 200

    # Regression: a served, unpaid table used to show as FREE and the order vanished from "Needs attention".
    t = await _table(client, hw)
    assert t["state"] == "BILL_PENDING" and t["unbilled_orders"] == 1 and t["unbilled_total_minor"] == o["total_minor"]
    attention = (await client.get(f"{API}/orders?scope=attention", headers=h(hw["admin"]))).json()
    assert o["id"] in [x["id"] for x in attention]
    assert o["id"] not in [x["id"] for x in (await client.get(f"{API}/orders", headers=h(hw["admin"]))).json()]

    bill = (await client.post(f"{API}/bills", headers=h(hw["admin"]), json={"table_id": hw["table"]["id"]})).json()
    t = await _table(client, hw)
    assert t["state"] == "BILL_PENDING" and t["unbilled_orders"] == 0
    assert t["unbilled_total_minor"] == bill["total_minor"], "issued but unpaid bill still owed"

    r = await client.post(f"{API}/bills/{bill['id']}/settle", headers=h(hw["admin"]), json={"method": "UPI"})
    assert r.status_code == 200, r.text
    t = await _table(client, hw)
    assert t["state"] == "FREE" and t["unbilled_total_minor"] == 0
    attention = (await client.get(f"{API}/orders?scope=attention", headers=h(hw["admin"]))).json()
    assert o["id"] not in [x["id"] for x in attention]


async def test_orders_list_filters_for_the_orders_screen(client, hw):
    served = (await client.get(f"{API}/orders?scope=today&status=SERVED", headers=h(hw["admin"]))).json()
    assert served and all(x["status"] == "SERVED" for x in served)
    by_table = (await client.get(f"{API}/orders?scope=all&table_id={hw['table']['id']}", headers=h(hw["admin"]))).json()
    assert by_table and all(x["table"]["id"] == hw["table"]["id"] for x in by_table)
    assert (await client.get(f"{API}/orders?status=EATEN", headers=h(hw["admin"]))).status_code == 422


async def test_counter_mode_ready_means_collected(client, hw):
    await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"serve_mode": "COUNTER"})
    try:
        before = (await client.get(f"{API}/dashboard/summary", headers=h(hw["admin"]))).json()["timings"]
        o = await _order(client, hw)
        done = await _cook(client, hw, o["id"])
        assert done["status"] == "SERVED", "diner collects: no separate Served step"
        assert done["served_at"] == done["ready_at"]
        assert (await _table(client, hw))["state"] == "BILL_PENDING"
        menu = (await client.get(f"{API}/d/{hw['table']['qr_token']}")).json()
        assert menu["outlet"]["serve_mode"] == "COUNTER", "the diner page says 'collect at the counter'"
        me = (await client.get(f"{API}/me", headers=h(hw["Kiran"]))).json()
        assert me["outlet"]["serve_mode"] == "COUNTER"
        after = (await client.get(f"{API}/dashboard/summary", headers=h(hw["admin"]))).json()["timings"]
        # Counter orders have no serving step, so they must not drag the serving average to zero.
        assert after["serving"]["count"] == before["serving"]["count"]
        assert after["preparation"]["count"] == before["preparation"]["count"] + 1
    finally:
        await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"serve_mode": "SERVER"})


# ------------------------------------------------------------------ no-pickup alert --


async def _age(order_id: str, minutes: int) -> None:
    """Pretend the order was confirmed `minutes` ago."""
    from sqlalchemy import text

    from app.core.db import platform_session

    async with platform_session() as db:
        await db.execute(
            text("update orders set confirmed_at = now() - make_interval(mins => :m) where id = :o"),
            {"m": minutes, "o": order_id},
        )


async def test_unpicked_order_alerts_admins_and_chefs_once_and_only_its_hotel(client, root, hw):
    from app.kitchen.overdue import sweep

    other = await make_hotel(client, root)  # a second hotel whose staff must never get this alert
    for who, tok in (
        (hw["admin"], "tok-admin-hw-0001"),
        (hw["Ravi"], "tok-chef-hw-00001"),
        (other["admin"], "tok-admin-other-1"),
    ):
        r = await client.post(f"{API}/me/devices", headers=h(who), json={"token": tok, "platform": "android"})
        assert r.status_code == 200, r.text
    await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"pickup_alert_minutes": 5})

    fresh = await _order(client, hw)
    late = await _order(client, hw)
    await _age(late["id"], 6)
    events = await sweep()

    overdue = [e for ch, e in events if ch != "push"]
    assert [e["order"]["id"] for e in overdue] == [late["id"]], "only the order past the limit"
    assert overdue[0]["type"] == "order.pickup_overdue" and overdue[0]["minutes"] == 5
    assert overdue[0]["order"]["pickup_alerted_at"], "screens can flag the order as overdue"
    pushed = [e for ch, e in events if ch == "push"]
    assert len(pushed) == 1
    tokens = {t for t, _ in pushed[0]["targets"]}
    assert tokens == {"tok-admin-hw-0001", "tok-chef-hw-00001"}, "admin and chef of this hotel, nobody else"

    assert await sweep() == [], "once per order"
    for o in (fresh, late):
        await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(hw["admin"]), json={"reason": "test"})


async def test_no_alert_when_turned_off_or_already_picked_up(client, hw):
    from app.kitchen.overdue import sweep

    await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"pickup_alert_minutes": 0})
    o = await _order(client, hw)
    await _age(o["id"], 30)
    assert await sweep() == [], "alert switched off"

    await client.put(f"{API}/settings", headers=h(hw["admin"]), json={"pickup_alert_minutes": 5})
    assert (await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hw["Ravi"]))).status_code == 200
    assert await sweep() == [], "a chef already took it"
    await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(hw["admin"]), json={"reason": "test"})
