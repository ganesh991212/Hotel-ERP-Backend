"""Printing (KOT paper path), bills, push targets, timing metrics, global expense categories."""

import asyncio
import base64

import pytest

from app.notify import push
from tests.conftest import h, make_hotel
from tests.test_api import diner_order

API = "/api/v1"


async def settle_push(sent: list, at_least: int = 1) -> None:
    """Push is fire-and-forget after commit; wait (bounded) for the background sends."""
    for _ in range(100):
        if len(sent) >= at_least and not push._tasks:
            return
        await asyncio.sleep(0.02)


@pytest.fixture(scope="module")
async def hz(client, root):
    return await make_hotel(client, root)


async def test_kot_paper_path(client, hz):
    o, _ = await diner_order(client, hz["table"]["qr_token"], [{"item_id": hz["items"]["Naan"], "qty": 3}])
    assert o["status"] == "CONFIRMED"
    r = await client.post(
        f"{API}/orders/{o['id']}/kot", headers=h(hz["Kiran"]), json={"send_to_kitchen": True, "paper_mm": 58}
    )
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["order"]["status"] == "PREPARING" and k["order"]["on_paper"] and k["copy_no"] == 1
    assert ["title", "KOT"] in k["blocks"] and ["bold", "3 x Naan"] in k["blocks"]
    assert base64.b64decode(k["escpos_b64"]).startswith(b"\x1b@")
    board = (await client.get(f"{API}/kitchen/board", headers=h(hz["Ravi"]))).json()
    assert o["id"] not in [x["id"] for x in board["up_for_grabs"]]
    assert o["id"] in [x["id"] for x in board["on_paper"]]
    assert (await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hz["Ravi"]))).status_code == 409
    again = (
        await client.post(f"{API}/orders/{o['id']}/kot", headers=h(hz["Kiran"]), json={"send_to_kitchen": True})
    ).json()
    assert again["copy_no"] == 2 and ["title", "KOT REPRINT 2"] in again["blocks"]
    r = await client.post(f"{API}/orders/{o['id']}/mark-ready", headers=h(hz["Kiran"]))
    assert r.json()["status"] == "READY"
    assert (await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hz["Kiran"]))).json()["status"] == "SERVED"


async def test_bills_numbering_gst_print_settle_void(client, root):
    from app.platform.gstin import checksum_char

    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    hb = await make_hotel(client, root, gst_registration="REGULAR", gstin=g)
    t, items = hb["table"], hb["items"]
    o1, tok = await diner_order(client, t["qr_token"], [{"item_id": items["Biryani"], "qty": 2}])
    o2, _ = await diner_order(client, t["qr_token"], [{"item_id": items["Chai"], "qty": 3}])
    r = await client.post(f"{API}/bills", headers=h(hb["Kiran"]), json={"table_id": t["id"]})
    assert r.status_code == 409 and r.json()["code"] == "orders_not_served"
    b = (
        await client.post(f"{API}/bills", headers=h(hb["Kiran"]), json={"table_id": t["id"], "include_unserved": True})
    ).json()
    assert b["doc_type"] == "TAX_INVOICE" and b["invoice_no"].endswith("/00001")
    assert b["total_minor"] == o1["total_minor"] + o2["total_minor"]
    assert b["cgst_minor"] + b["sgst_minor"] == b["tax_minor"] > 0
    assert sum(gp["tax_minor"] for gp in b["tax_groups"]) == b["tax_minor"]
    assert ["center", f"GSTIN: {g}"] in b["blocks"]
    # Nothing left to bill on that table now.
    r = await client.post(f"{API}/bills", headers=h(hb["Kiran"]), json={"table_id": t["id"], "include_unserved": True})
    assert r.json()["code"] == "nothing_to_bill"
    p1 = (await client.post(f"{API}/bills/{b['id']}/print", headers=h(hb["Kiran"]), json={"paper_mm": 80})).json()
    p2 = (await client.post(f"{API}/bills/{b['id']}/print", headers=h(hb["Kiran"]), json={})).json()
    assert ["center", "TAX INVOICE"] in p1["blocks"] and ["center", "TAX INVOICE - DUPLICATE"] in p2["blocks"]
    assert b"TOTAL" in base64.b64decode(p1["escpos_b64"])
    # Servers cannot void; managers/admins can.
    assert (
        await client.post(f"{API}/bills/{b['id']}/void", headers=h(hb["Kiran"]), json={"reason": "wrong table"})
    ).status_code == 403
    s = (await client.post(f"{API}/bills/{b['id']}/settle", headers=h(hb["Kiran"]), json={"method": "UPI"})).json()
    assert s["status"] == "PAID" and s["payment_method"] == "UPI"
    # Paying ends the table's QR session.
    assert (await client.get(f"{API}/d/orders/mine", headers={"X-Diner-Token": tok})).status_code == 401
    # Next bill continues the sequence; voiding frees its orders for re-billing.
    o3, _ = await diner_order(client, t["qr_token"], [{"item_id": items["Naan"], "qty": 1}])
    b2 = (
        await client.post(f"{API}/bills", headers=h(hb["admin"]), json={"table_id": t["id"], "include_unserved": True})
    ).json()
    assert b2["invoice_no"].endswith("/00002")
    v = (await client.post(f"{API}/bills/{b2['id']}/void", headers=h(hb["admin"]), json={"reason": "re-issue"})).json()
    assert v["status"] == "VOID" and v["orders"] == []
    b3 = (
        await client.post(f"{API}/bills", headers=h(hb["admin"]), json={"table_id": t["id"], "include_unserved": True})
    ).json()
    assert b3["invoice_no"].endswith("/00003") and b3["orders"] == [o3["number"]]


async def test_push_goes_to_the_right_people_with_loud_sound(client, hz, monkeypatch):
    sent: list[tuple[str, dict]] = []

    async def fake(token, message):
        sent.append((token, message))
        return 200

    monkeypatch.setattr(push, "transport", fake)
    for who, token, plat in [
        ("Ravi", "tok-ravi-0000", "android"),
        ("Sita", "tok-sita-0000", "ios"),
        ("Kiran", "tok-kiran-000", "web"),
    ]:
        r = await client.post(f"{API}/me/devices", headers=h(hz[who]), json={"token": token, "platform": plat})
        assert r.status_code == 200
    o, _ = await diner_order(client, hz["table"]["qr_token"], [{"item_id": hz["items"]["Chai"], "qty": 1}])
    await settle_push(sent, 2)
    to = {t for t, _ in sent}
    assert to == {"tok-ravi-0000", "tok-sita-0000"}  # chefs on shift; not the server
    android = next(m for t, m in sent if t == "tok-ravi-0000")
    assert android["android"]["notification"]["channel_id"] == "orders_loud"
    assert android["android"]["notification"]["sound"] == "kitchen_alert"
    ios = next(m for t, m in sent if t == "tok-sita-0000")
    assert ios["apns"]["payload"]["aps"]["sound"] == "kitchen_alert.wav"
    sent.clear()
    await client.post(f"{API}/orders/{o['id']}/accept", headers=h(hz["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/ready", headers=h(hz["Ravi"]))
    await settle_push(sent, 1)
    web = [m for t, m in sent if t == "tok-kiran-000"]
    assert web and "notification" not in web[0] and web[0]["data"]["sound"] == "ready_alert"  # data-only on web
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hz["Kiran"]))
    # Re-registering a token under another user moves it (shared device).
    await client.post(f"{API}/me/devices", headers=h(hz["admin"]), json={"token": "tok-kiran-000", "platform": "web"})
    sent.clear()
    o2, _ = await diner_order(client, hz["table"]["qr_token"], [{"item_id": hz["items"]["Chai"], "qty": 1}])
    await client.post(f"{API}/orders/{o2['id']}/accept", headers=h(hz["Sita"]))
    await client.post(f"{API}/orders/{o2['id']}/ready", headers=h(hz["Sita"]))
    await settle_push(sent, 1)
    assert any(t == "tok-kiran-000" for t, _ in sent)  # admin also has order.serve; token now belongs to admin
    await client.post(f"{API}/orders/{o2['id']}/serve", headers=h(hz["Kiran"]))


async def test_dashboard_stage_timings(client, hz):
    d = (await client.get(f"{API}/dashboard/summary", headers=h(hz["admin"]))).json()
    t = d["timings"]
    assert set(t) == {"wait_for_chef", "preparation", "serving", "total"}
    assert t["total"]["count"] >= 1 and isinstance(t["total"]["avg_minutes"], float)
    assert isinstance(t["total"]["p90_minutes"], float)
    assert any(s["name"] == "Kiran" and s["served"] >= 1 for s in d["servers"])


async def test_expense_categories_global_and_hotel_scoped(client, root, hz):
    other = await make_hotel(client, root)
    cats = (await client.get(f"{API}/expense-categories", headers=h(hz["admin"]))).json()
    globals_ = {c["name"] for c in cats if c["scope"] == "GLOBAL"}
    assert {"Gas", "Rent", "Groceries"} <= globals_
    # Hotel-only category: visible to its hotel, not to another.
    r = await client.post(f"{API}/expense-categories", headers=h(hz["admin"]), json={"name": "Laundry"})
    assert r.status_code == 201 and r.json()["scope"] == "HOTEL"
    assert "Laundry" not in {
        c["name"] for c in (await client.get(f"{API}/expense-categories", headers=h(other["admin"]))).json()
    }
    # Clash with a global name is refused; hotels cannot rename or delete globals.
    assert (
        await client.post(f"{API}/expense-categories", headers=h(hz["admin"]), json={"name": "gas"})
    ).status_code == 409
    gas = next(c for c in cats if c["name"] == "Gas")
    assert (
        await client.patch(f"{API}/expense-categories/{gas['id']}", headers=h(hz["admin"]), json={"name": "Fuel"})
    ).status_code == 403
    # Super admin adds a global category: every hotel sees it at once.
    r = await client.post(f"{API}/platform/expense-categories", headers=h(root), json={"name": "Licences & fees"})
    assert r.status_code == 201
    for hotel in (hz, other):
        names = {c["name"] for c in (await client.get(f"{API}/expense-categories", headers=h(hotel["admin"]))).json()}
        assert "Licences & fees" in names
    # Deactivated globals disappear from pickers but old expenses keep them.
    await client.patch(
        f"{API}/platform/expense-categories/{r.json()['id']}", headers=h(root), json={"is_active": False}
    )
    names = {c["name"] for c in (await client.get(f"{API}/expense-categories", headers=h(other["admin"]))).json()}
    assert "Licences & fees" not in names
    listed = (await client.get(f"{API}/platform/expense-categories", headers=h(root))).json()
    assert any(c["name"] == "Licences & fees" and not c["is_active"] for c in listed)
