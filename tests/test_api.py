import asyncio
import hashlib
import hmac
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import text

from app.core.db import platform_session, tenant_session
from app.models import TENANT_TABLES
from app.realtime.hub import diner_channel, hub, outlet_channel
from tests.conftest import h, login, make_hotel

API = "/api/v1"


async def diner_order(client, qr: str, lines: list[dict], **extra) -> tuple[dict, str]:
    s = await client.post(f"{API}/d/{qr}/session")
    assert s.status_code == 201, s.text
    tok = s.json()["session_token"]
    r = await client.post(f"{API}/d/orders", headers={"X-Diner-Token": tok}, json={"items": lines, **extra})
    assert r.status_code == 201, r.text
    return r.json(), tok


@pytest.fixture(scope="module")
async def hx(client, root):
    return await make_hotel(client, root)


# ------------------------------------------------------------------------- auth --


async def test_refresh_rotates_and_reuse_revokes_family(client, hx):
    first = hx["Kiran"]["_refresh"]
    r1 = await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
    assert r1.status_code == 200
    second = r1.json()["refresh_token"]
    reuse = await client.post(f"{API}/auth/refresh", json={"refresh_token": first})
    assert reuse.status_code == 401 and reuse.json()["code"] == "refresh_reused"
    # The whole family is revoked, including the token issued after rotation.
    after = await client.post(f"{API}/auth/refresh", json={"refresh_token": second})
    assert after.status_code == 401


async def test_wrong_password_and_audience(client, hx, root):
    r = await client.post(f"{API}/auth/login", json={"email": "root@platform.example.com", "password": "nope"})
    assert r.status_code == 401
    assert (await client.get(f"{API}/platform/hotels", headers=h(hx["admin"]))).status_code == 403
    assert (await client.get(f"{API}/tables", headers=h(root))).status_code == 403


# -------------------------------------------------------------------------- RLS --


async def test_rls_every_tenant_table_isolated(client, root, hx):
    other = await make_hotel(client, root)
    # Put some rows in every interesting table for the other hotel.
    await diner_order(client, other["table"]["qr_token"], [{"item_id": other["items"]["Chai"], "qty": 1}])
    cats = (await client.get(f"{API}/expense-categories", headers=h(other["admin"]))).json()
    await client.post(
        f"{API}/expenses",
        headers=h(other["admin"]),
        json={"category_id": cats[0]["id"], "amount_minor": 5000, "spent_on": date.today().isoformat()},
    )
    a_id, b_id = hx["hotel"]["id"], other["hotel"]["id"]
    for table in TENANT_TABLES:
        async with platform_session() as db:
            b_rows = (await db.execute(text(f"select count(*) from {table} where hotel_id = :b"), {"b": b_id})).scalar()
        async with tenant_session(uuid.UUID(a_id)) as db:
            seen = (await db.execute(text(f"select count(*) from {table} where hotel_id = :b"), {"b": b_id})).scalar()
            touched = (
                await db.execute(text(f"update {table} set hotel_id = hotel_id where hotel_id = :b"), {"b": b_id})
            ).rowcount
        assert seen == 0, f"{table}: hotel A read hotel B rows"
        assert touched == 0, f"{table}: hotel A updated hotel B rows"
        if table in ("expenses", "dining_tables", "orders", "users", "menu_items"):
            assert b_rows > 0, f"{table}: test setup should have rows for B"
    # Writing a row for another hotel is rejected by WITH CHECK.
    # Global expense categories are readable by every hotel but writable by none.
    async with tenant_session(uuid.UUID(a_id)) as db:
        n_global = (await db.execute(text("select count(*) from expense_categories where hotel_id is null"))).scalar()
        changed = (await db.execute(text("update expense_categories set name = name where hotel_id is null"))).rowcount
        assert n_global > 0 and changed == 0
    async with tenant_session(uuid.UUID(a_id)) as db:
        with pytest.raises(Exception, match="row-level security"):
            await db.execute(text("insert into expense_categories (hotel_id, name) values (:b, 'x')"), {"b": b_id})


async def test_rls_without_context_sees_nothing():
    from app.core.db import TenantSession

    async with TenantSession() as db:
        assert (await db.execute(text("select count(*) from orders"))).scalar() == 0
        assert (await db.execute(text("select count(*) from users"))).scalar() == 0


# ------------------------------------------------------------ the main flow --


async def test_qr_order_as_the_phone_sends_it(client, hx):
    """Regression: the app sends a 32-char client_ref with every QR order; the server prefixed it with the
    session id and overflowed the 64-char column, so every phone order failed with a 500."""
    qr = hx["table"]["qr_token"]
    s = await client.post(f"{API}/d/{qr}/session")
    tok = s.json()["session_token"]
    body = {
        "items": [{"item_id": hx["items"]["Naan"], "qty": 1, "note": "less spicy"}],
        "note": "Please bring water too",
        "client_ref": "a" * 64,  # longest the API accepts
    }
    first = await client.post(f"{API}/d/orders", headers={"X-Diner-Token": tok}, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["note"] == "Please bring water too"
    # A retry after a dropped connection returns the same order instead of a duplicate.
    again = await client.post(f"{API}/d/orders", headers={"X-Diner-Token": tok}, json=body)
    assert again.status_code in (200, 201) and again.json()["id"] == first.json()["id"]
    # The same client_ref from another table session is a different order.
    other = (await client.post(f"{API}/d/{qr}/session")).json()["session_token"]
    third = await client.post(f"{API}/d/orders", headers={"X-Diner-Token": other}, json=body)
    assert third.status_code == 201 and third.json()["id"] != first.json()["id"]



async def test_qr_order_to_chef_to_served(client, hx):
    qr = hx["table"]["qr_token"]
    menu = (await client.get(f"{API}/d/{qr}")).json()
    assert menu["table"]["label"] == "T1" and menu["ordering_enabled"]
    # Client-sent prices are ignored: only ids and quantities matter.
    lines = [
        {"item_id": hx["items"]["Biryani"], "qty": 2, "price_minor": 1},
        {"item_id": hx["items"]["Naan"], "qty": 3},
    ]
    q = (await client.post(f"{API}/d/{qr}/quote", json={"items": lines})).json()
    assert q["subtotal_minor"] == 2 * 28000 + 3 * 6000
    order, tok = await diner_order(client, qr, lines)
    assert order["status"] == "CONFIRMED" and order["total_minor"] == q["total_minor"]
    assert order["channel"] == "QR"

    board = (await client.get(f"{API}/kitchen/board", headers=h(hx["Ravi"]))).json()
    assert order["id"] in [o["id"] for o in board["up_for_grabs"]]

    me = (await client.get(f"{API}/me", headers=h(hx["admin"]))).json()
    dch = await _session_channel(order["id"])
    diner_q = hub.subscribe(dch)
    outlet_q = hub.subscribe(outlet_channel(me["outlet"]["id"]))
    r = await client.post(f"{API}/orders/{order['id']}/accept", headers=h(hx["Ravi"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PREPARING" and r.json()["chef"]["name"] == "Ravi"
    # Diner was notified and the other chef's screen got the update (published after commit).
    ev = diner_q.get_nowait()
    assert ev["order"]["status"] == "PREPARING" and ev["seq"] >= 1
    assert outlet_q.get_nowait()["order"]["chef"]["name"] == "Ravi"

    taken = await client.post(f"{API}/orders/{order['id']}/accept", headers=h(hx["Sita"]))
    assert taken.status_code == 409 and taken.json()["code"] == "taken"

    board = (await client.get(f"{API}/kitchen/board", headers=h(hx["Sita"]))).json()
    ravi = next(c for c in board["chefs"] if c["name"] == "Ravi")
    assert ravi["queue"][0]["status"] == "PREPARING"

    assert (await client.post(f"{API}/orders/{order['id']}/ready", headers=h(hx["Sita"]))).status_code == 409
    assert (await client.post(f"{API}/orders/{order['id']}/ready", headers=h(hx["Ravi"]))).json()["status"] == "READY"
    served = await client.post(f"{API}/orders/{order['id']}/serve", headers=h(hx["Kiran"]))
    assert served.json()["status"] == "SERVED"

    mine = (await client.get(f"{API}/d/orders/mine", headers={"X-Diner-Token": tok})).json()
    assert mine[0]["status"] == "SERVED"

    dash = (await client.get(f"{API}/dashboard/summary", headers=h(hx["admin"]))).json()
    assert dash["orders_today"] >= 1 and dash["revenue_today_minor"] >= order["total_minor"]
    assert any(i["name"] == "Biryani" for i in dash["top_items"])
    # Postgres SUM/AVG return numeric; the API must still emit JSON numbers, not strings.
    assert isinstance(dash["avg_prep_minutes"], float)
    for key in ("revenue_today_minor", "revenue_month_minor", "expenses_month_minor", "net_month_minor"):
        assert isinstance(dash[key], int), key
    assert all(isinstance(c["avg_minutes"], (float, type(None))) for c in dash["chefs"])
    hub.unsubscribe(dch, diner_q)


async def _session_channel(order_id: str) -> str:
    async with platform_session() as db:
        sid = (await db.execute(text("select table_session_id from orders where id = :i"), {"i": order_id})).scalar()
    return diner_channel(sid)


# ------------------------------------------------------------------- strict queue --


async def test_strict_queue_and_cancel_closes_up(client, hx):
    qr = hx["table"]["qr_token"]
    ids = []
    for _ in range(3):
        o, _ = await diner_order(client, qr, [{"item_id": hx["items"]["Chai"], "qty": 1}])
        ids.append(o["id"])
    for oid in ids:
        r = await client.post(f"{API}/orders/{oid}/accept", headers=h(hx["Sita"]))
        assert r.status_code == 200, r.text
    a, b, c = ids
    # Example 1: B cannot start while A is the head.
    r = await client.post(f"{API}/orders/{b}/start", headers=h(hx["Sita"]))
    assert r.status_code == 409 and r.json()["code"] == "not_head"
    # Queue full at max_queue_orders=3.
    extra, _ = await diner_order(client, qr, [{"item_id": hx["items"]["Chai"], "qty": 1}])
    r = await client.post(f"{API}/orders/{extra['id']}/accept", headers=h(hx["Sita"]))
    assert r.status_code == 409 and r.json()["code"] == "queue_full"
    # Example 12: cancel B, C moves up to position 2.
    r = await client.post(f"{API}/orders/{b}/cancel", headers=h(hx["admin"]), json={"reason": "guest left"})
    assert r.json()["status"] == "CANCELLED"
    board = (await client.get(f"{API}/kitchen/board", headers=h(hx["Sita"]))).json()
    sita = next(x for x in board["chefs"] if x["name"] == "Sita")
    assert [o["id"] for o in sita["queue"]] == [a, c]
    assert [o["chef"]["queue_position"] for o in sita["queue"]] == [1, 2]
    # Finish A, then C becomes the head and can start.
    await client.post(f"{API}/orders/{a}/ready", headers=h(hx["Sita"]))
    assert (await client.post(f"{API}/orders/{c}/start", headers=h(hx["Sita"]))).json()["status"] == "PREPARING"
    await client.post(f"{API}/orders/{c}/ready", headers=h(hx["Sita"]))
    await client.post(f"{API}/orders/{extra['id']}/cancel", headers=h(hx["admin"]), json={"reason": "cleanup"})


async def test_accept_race_exactly_one_wins(client, hx):
    """Example 8: two chefs accept in the same instant; one claim succeeds."""
    o, _ = await diner_order(client, hx["table"]["qr_token"], [{"item_id": hx["items"]["Chai"], "qty": 1}])
    r1, r2 = await asyncio.gather(
        client.post(f"{API}/orders/{o['id']}/accept", headers=h(hx["Ravi"])),
        client.post(f"{API}/orders/{o['id']}/accept", headers=h(hx["Sita"])),
    )
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [200, 409], (r1.text, r2.text)
    winner = hx["Ravi"] if r1.status_code == 200 else hx["Sita"]
    await client.post(f"{API}/orders/{o['id']}/ready", headers=h(winner))


async def test_auto_assign_concurrent_orders_spread(client, root):
    """Example 13 through the API: AUTO_ASSIGN, two orders at once, two idle chefs -> one each."""
    hz = await make_hotel(client, root)
    r = await client.put(f"{API}/settings", headers=h(hz["admin"]), json={"kitchen_mode": "AUTO_ASSIGN"})
    assert r.status_code == 200, r.text
    lines = [{"item_id": hz["items"]["Biryani"], "qty": 1}]
    r1, r2 = await asyncio.gather(
        *[
            client.post(f"{API}/orders", headers=h(hz["Kiran"]), json={"table_id": hz["table"]["id"], "items": lines})
            for _ in range(2)
        ]
    )
    assert r1.status_code == r2.status_code == 201
    board = (await client.get(f"{API}/kitchen/board", headers=h(hz["admin"]))).json()
    lens = sorted(len(c["queue"]) for c in board["chefs"])
    assert lens == [1, 1] and not board["up_for_grabs"]
    # Off shift: queued orders return to routing and go to the remaining chef (example 11).
    ravi = next(c for c in board["chefs"] if c["name"] == "Ravi")
    r = await client.put(f"{API}/kitchen/chefs/{ravi['id']}/shift", headers=h(hz["Ravi"]), json={"on_shift": False})
    assert r.status_code == 200
    board = (await client.get(f"{API}/kitchen/board", headers=h(hz["admin"]))).json()
    sita = next(c for c in board["chefs"] if c["name"] == "Sita")
    assert len(sita["queue"]) == 2


async def test_waiter_order_idempotent_on_client_ref(client, hx):
    body = {"table_id": hx["table"]["id"], "items": [{"item_id": hx["items"]["Chai"], "qty": 2}], "client_ref": "off-1"}
    r1 = await client.post(f"{API}/orders", headers=h(hx["Kiran"]), json=body)
    r2 = await client.post(f"{API}/orders", headers=h(hx["Kiran"]), json=body)
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    assert r1.json()["channel"] == "WAITER"
    await client.post(f"{API}/orders/{r1.json()['id']}/cancel", headers=h(hx["admin"]), json={"reason": "test"})


async def test_qr_confirmation_required(client, hx):
    assert (
        await client.put(f"{API}/settings", headers=h(hx["admin"]), json={"qr_confirm_required": True})
    ).status_code == 200
    o, _ = await diner_order(client, hx["table"]["qr_token"], [{"item_id": hx["items"]["Chai"], "qty": 1}])
    assert o["status"] == "PLACED"
    assert (await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hx["Ravi"]))).status_code == 409
    assert (await client.post(f"{API}/orders/{o['id']}/confirm", headers=h(hx["Kiran"]))).json()[
        "status"
    ] == "CONFIRMED"
    await client.put(f"{API}/settings", headers=h(hx["admin"]), json={"qr_confirm_required": False})
    await client.post(f"{API}/orders/{o['id']}/cancel", headers=h(hx["admin"]), json={"reason": "test"})


async def test_sold_out_item_rejected(client, hx):
    item = hx["items"]["Naan"]
    r = await client.post(f"{API}/items/{item}/availability", headers=h(hx["Ravi"]), json={"is_available": False})
    assert r.status_code == 200
    s = (await client.post(f"{API}/d/{hx['table']['qr_token']}/session")).json()["session_token"]
    r = await client.post(
        f"{API}/d/orders", headers={"X-Diner-Token": s}, json={"items": [{"item_id": item, "qty": 1}]}
    )
    assert r.status_code == 409 and r.json()["code"] == "item_unavailable"
    await client.post(f"{API}/items/{item}/availability", headers=h(hx["admin"]), json={"is_available": True})


async def test_order_item_events_are_audited(client, hx):
    o, _ = await diner_order(client, hx["table"]["qr_token"], [{"item_id": hx["items"]["Chai"], "qty": 1}])
    await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hx["Ravi"]))
    async with platform_session() as db:
        rows = (
            await db.execute(
                text(
                    "select from_status, to_status, actor_kind from order_item_events "
                    "where order_id = :o order by at, to_status"
                ),
                {"o": o["id"]},
            )
        ).all()
    path = [(f, t) for f, t, _ in rows]
    assert (None, "PLACED") in path and ("PLACED", "CONFIRMED") in path and ("ASSIGNED", "PREPARING") in path
    await client.post(f"{API}/orders/{o['id']}/ready", headers=h(hx["Ravi"]))


# ------------------------------------------------- permissions and entitlements --


async def test_role_permissions_configurable_by_hotel_admin(client, hx):
    waiter_role = hx["roles"]["WAITER"]
    assert (await client.get(f"{API}/expenses?month=2026-11", headers=h(hx["Kiran"]))).status_code == 403
    r = await client.put(
        f"{API}/roles/{waiter_role}/permissions",
        headers=h(hx["admin"]),
        json={"permissions": ["order.view", "order.serve", "expense.view"]},
    )
    assert r.status_code == 200, r.text
    assert (await client.get(f"{API}/expenses?month=2026-11", headers=h(hx["Kiran"]))).status_code == 200
    body = {"table_id": hx["table"]["id"], "items": [{"item_id": hx["items"]["Chai"], "qty": 1}]}
    r = await client.post(f"{API}/orders", headers=h(hx["Kiran"]), json=body)
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    # Restore defaults; hotel admin role cannot be edited.
    await client.put(
        f"{API}/roles/{waiter_role}/permissions",
        headers=h(hx["admin"]),
        json={"permissions": ["order.view", "order.create", "order.confirm", "order.serve", "menu.availability"]},
    )
    r = await client.put(
        f"{API}/roles/{hx['roles']['HOTEL_ADMIN']}/permissions", headers=h(hx["admin"]), json={"permissions": []}
    )
    assert r.status_code == 403


async def test_plan_gates_and_limits(client, root):
    hs = await make_hotel(client, root, plan="STARTER")
    r = await client.post(f"{API}/roles", headers=h(hs["admin"]), json={"name": "Host", "permissions": []})
    assert r.status_code == 403 and r.json()["code"] == "feature_locked"
    r = await client.put(f"{API}/settings", headers=h(hs["admin"]), json={"kitchen_mode": "AUTO_ASSIGN"})
    assert r.status_code == 403
    # Super admin override unlocks one feature for this hotel only.
    r = await client.put(
        f"{API}/platform/hotels/{hs['hotel']['id']}/overrides",
        headers=h(root),
        json={"feature_key": "CUSTOM_ROLES", "bool_value": True, "reason": "pilot customer"},
    )
    assert r.status_code == 200
    assert (
        await client.post(f"{API}/roles", headers=h(hs["admin"]), json={"name": "Host", "permissions": []})
    ).status_code == 201
    # Limit: STARTER allows 15 tables; override to 1 and the second table is refused.
    await client.put(
        f"{API}/platform/hotels/{hs['hotel']['id']}/overrides",
        headers=h(root),
        json={"feature_key": "MAX_TABLES", "int_limit": 1, "reason": "test"},
    )
    r = await client.post(f"{API}/tables", headers=h(hs["admin"]), json={"label": "T2"})
    assert r.status_code == 403 and r.json()["code"] == "limit_reached"
    ent = (await client.get(f"{API}/me/entitlements", headers=h(hs["admin"]))).json()
    assert ent["limits"]["MAX_TABLES"] == 1 and ent["features"]["CUSTOM_ROLES"] is True


async def test_suspended_hotel_cannot_take_orders(client, root):
    hz = await make_hotel(client, root)
    r = await client.post(
        f"{API}/platform/hotels/{hz['hotel']['id']}/suspend", headers=h(root), json={"reason": "unpaid"}
    )
    assert r.json()["status"] == "SUSPENDED"
    body = {"table_id": hz["table"]["id"], "items": [{"item_id": hz["items"]["Chai"], "qty": 1}]}
    assert (await client.post(f"{API}/orders", headers=h(hz["Kiran"]), json=body)).status_code == 403
    assert (await client.post(f"{API}/d/{hz['table']['qr_token']}/session")).status_code == 403
    r = await client.post(f"{API}/platform/hotels/{hz['hotel']['id']}/resume", headers=h(root), json={"reason": "paid"})
    assert r.json()["status"] == "ACTIVE"
    assert (await client.post(f"{API}/orders", headers=h(hz["Kiran"]), json=body)).status_code == 201


async def test_registered_hotel_requires_valid_gstin(client, root):
    from app.platform.gstin import checksum_char

    base = {
        "name": "Taxed Inn",
        "plan_code": "GROWTH",
        "gst_registration": "REGULAR",
        "admin": {"name": "A", "email": f"tax-{uuid.uuid4().hex[:6]}@hotel.example.com", "password": "admin-pass-123"},
    }
    r = await client.post(f"{API}/platform/hotels", headers=h(root), json={**base, "gstin": "36ABCDE1234F1Z0"})
    assert r.status_code == 422
    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    r = await client.post(f"{API}/platform/hotels", headers=h(root), json={**base, "gstin": g})
    assert r.status_code == 201, r.text
    admin = await login(client, base["admin"]["email"], "admin-pass-123")
    cat = (await client.post(f"{API}/categories", headers=h(admin), json={"name": "Mains"})).json()
    item = (
        await client.post(
            f"{API}/items", headers=h(admin), json={"category_id": cat["id"], "name": "Thali", "price_minor": 20000}
        )
    ).json()
    t = (await client.post(f"{API}/tables", headers=h(admin), json={"label": "A1"})).json()
    q = (
        await client.post(f"{API}/d/{t['qr_token']}/quote", json={"items": [{"item_id": item["id"], "qty": 1}]})
    ).json()
    assert q["tax_minor"] == 1000  # 5% default FOOD rule, effective-dated config


# ------------------------------------------------------------- platform and billing --


async def test_platform_summary_and_plans(client, root, hx):
    s = (await client.get(f"{API}/platform/summary", headers=h(root))).json()
    assert s["hotels_total"] >= 1 and "TRIAL" in s["by_status"]
    plans = (await client.get(f"{API}/platform/plans", headers=h(root))).json()
    starter = next(p for p in plans if p["code"] == "STARTER")
    assert starter["is_placeholder"]
    r = await client.patch(
        f"{API}/platform/plans/{starter['id']}", headers=h(root), json={"limits": {"MAX_TABLES": 20}}
    )
    assert r.json()["limits"]["MAX_TABLES"] == 20


def _sign(body: bytes) -> str:
    return hmac.new(b"whsec_test_only", body, hashlib.sha256).hexdigest()


async def test_razorpay_webhook_signature_and_idempotency(client, hx):
    async with platform_session() as db:
        await db.execute(
            text("update subscriptions set razorpay_sub_id = 'sub_test_1' where hotel_id = :h"),
            {"h": hx["hotel"]["id"]},
        )
    payload = {
        "event": "subscription.charged",
        "payload": {
            "subscription": {"entity": {"id": "sub_test_1", "current_end": 1893456000}},
            "payment": {"entity": {"id": "pay_test_1", "amount": 249900}},
        },
    }
    raw = json.dumps(payload).encode()
    bad = await client.post(
        f"{API}/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": "00", "X-Razorpay-Event-Id": "evt_1"}
    )
    assert bad.status_code == 401
    hdrs = {"X-Razorpay-Signature": _sign(raw), "X-Razorpay-Event-Id": "evt_1", "Content-Type": "application/json"}
    assert (await client.post(f"{API}/webhooks/razorpay", content=raw, headers=hdrs)).json()["status"] == "ok"
    assert (await client.post(f"{API}/webhooks/razorpay", content=raw, headers=hdrs)).json()["status"] == "duplicate"
    sub = (await client.get(f"{API}/billing/subscription", headers=h(hx["admin"]))).json()
    assert sub["status"] == "ACTIVE" and len(sub["invoices"]) == 1 and sub["invoices"][0]["amount_minor"] == 249900


async def test_checkout_without_keys_is_explicit(client, hx):
    r = await client.post(f"{API}/billing/subscription/checkout", headers=h(hx["admin"]), json={"plan_code": "PRO"})
    assert r.status_code == 503 and r.json()["code"] == "payments_not_configured"


# --------------------------------------------------------------------- expenses --


async def test_expenses_month_summary(client, hx):
    cats = {c["name"]: c["id"] for c in (await client.get(f"{API}/expense-categories", headers=h(hx["admin"]))).json()}
    for cat, amt, day in [
        ("Gas", 120000, "2026-10-03"),
        ("Gas", 80000, "2026-10-17"),
        ("Rent", 2500000, "2026-10-01"),
        ("Gas", 99, "2026-09-30"),
    ]:
        r = await client.post(
            f"{API}/expenses",
            headers=h(hx["admin"]),
            json={"category_id": cats[cat], "amount_minor": amt, "spent_on": day, "payment_method": "UPI"},
        )
        assert r.status_code == 201, r.text
    s = (await client.get(f"{API}/expenses?month=2026-10", headers=h(hx["admin"]))).json()
    assert s["total_minor"] == 2_700_000
    assert s["by_category"][0] == {"name": "Rent", "total_minor": 2_500_000, "share_bp": 9259}
    assert len(s["items"]) == 3
    r = await client.post(
        f"{API}/expenses",
        headers=h(hx["admin"]),
        json={"category_id": cats["Gas"], "amount_minor": -5, "spent_on": "2026-10-01"},
    )
    assert r.status_code == 422


async def test_server_error_reaches_the_browser_with_cors(client):
    """A crash must still carry CORS headers, or the web app sees a network failure ("check your internet")."""
    from app.main import app

    async def boom() -> None:
        raise RuntimeError("simulated crash")

    app.add_api_route("/api/v1/__boom", boom)
    try:
        r = await client.get(f"{API}/__boom", headers={"Origin": "https://web.example.com"})
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/api/v1/__boom"]
    assert r.status_code == 500
    assert r.json()["code"] == "server_error"
    assert r.headers.get("access-control-allow-origin") in ("*", "https://web.example.com")
