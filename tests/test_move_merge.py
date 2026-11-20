"""Move / merge tables (docs/03 "Table visits", docs/14)."""

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hm(client, root):
    hp = await make_hotel(client, root)
    t2 = (await client.post(f"{API}/tables", headers=h(hp["admin"]), json={"label": "T2"})).json()
    t3 = (await client.post(f"{API}/tables", headers=h(hp["admin"]), json={"label": "T3"})).json()
    return {**hp, "t2": t2, "t3": t3}


async def _tables(client, hp):
    return {t["label"]: t for t in (await client.get(f"{API}/tables", headers=h(hp["admin"]))).json()}


def _order(client, hp, table_id, qty=1):
    return client.post(
        f"{API}/orders",
        headers=h(hp["Kiran"]),
        json={"table_id": table_id, "items": [{"item_id": hp["items"]["Naan"], "qty": qty}]},
    )


async def test_move_takes_the_whole_visit_and_the_qr_diner(client, hm):
    t1 = hm["table"]
    await client.post(f"{API}/tables/{t1['id']}/seat", headers=h(hm["Kiran"]), json={"guests": 3})
    # A QR diner at T1 orders, then the party moves to T2.
    s = (await client.post(f"{API}/d/{t1['qr_token']}/session")).json()
    tok = s.get("token") or s.get("session_token")
    await client.post(
        f"{API}/d/orders",
        headers={"X-Diner-Token": tok},
        json={"items": [{"item_id": hm["items"]["Chai"], "qty": 1}], "client_ref": "abc12345"},
    )
    o = (await _order(client, hm, t1["id"], 2)).json()
    r = await client.post(f"{API}/tables/{t1['id']}/move", headers=h(hm["Kiran"]), json={"to_table_id": hm["t2"]["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "T2" and r.json()["guests"] == 3 and r.json()["state"] == "OCCUPIED"
    tables = await _tables(client, hm)
    assert tables["T1"]["state"] == "CLEANING"
    moved = (await client.get(f"{API}/orders/{o['id']}", headers=h(hm["admin"]))).json()
    assert moved["table"]["label"] == "T2", "kitchen screens and tickets show the new table"
    # The diner keeps ordering on the new table.
    again = await client.post(
        f"{API}/d/orders",
        headers={"X-Diner-Token": tok},
        json={"items": [{"item_id": hm["items"]["Chai"], "qty": 1}], "client_ref": "def67890"},
    )
    assert again.status_code in (200, 201), again.text
    assert again.json()["table"]["label"] == "T2"


async def test_merge_makes_one_party_and_one_bill(client, hm):
    t1, t2 = hm["table"], hm["t2"]
    await client.post(f"{API}/tables/{t1['id']}/seat", headers=h(hm["Kiran"]), json={"guests": 2})
    await client.post(f"{API}/tables/{t2['id']}/seat", headers=h(hm["Kiran"]), json={"guests": 4})
    await _order(client, hm, t1["id"])
    await _order(client, hm, t2["id"])
    r = await client.post(f"{API}/tables/{t1['id']}/merge", headers=h(hm["Kiran"]), json={"from_table_ids": [t2["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["guests"] == 6
    tables = await _tables(client, hm)
    assert tables["T2"]["state"] == "CLEANING"
    orders = (await client.get(f"{API}/orders?scope=open&table_id={t1['id']}", headers=h(hm["admin"]))).json()
    assert len(orders) == 2


async def test_refused_after_billing_or_onto_a_busy_table(client, hm):
    t1, t2, t3 = hm["table"], hm["t2"], hm["t3"]
    await _order(client, hm, t2["id"])
    busy = await client.post(f"{API}/tables/{t3['id']}/move", headers=h(hm["Kiran"]), json={"to_table_id": t2["id"]})
    assert busy.json()["code"] in ("nothing_to_move", "table_in_use")
    o = (await _order(client, hm, t1["id"])).json()
    await client.post(f"{API}/tables/{t1['id']}/move", headers=h(hm["Kiran"]), json={"to_table_id": t2["id"]})
    assert (await _tables(client, hm))["T2"]["state"] == "OCCUPIED", "T2 untouched"
    r = await client.post(f"{API}/tables/{t1['id']}/move", headers=h(hm["Kiran"]), json={"to_table_id": t2["id"]})
    assert r.status_code == 409 and r.json()["code"] == "table_in_use"
    # Bill T1, then it can't move or merge (an invoice never changes table).
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hm["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hm["Kiran"]))
    await client.post(f"{API}/bills", headers=h(hm["admin"]), json={"table_id": t1["id"]})
    r = await client.post(f"{API}/tables/{t1['id']}/move", headers=h(hm["Kiran"]), json={"to_table_id": t3["id"]})
    assert r.status_code == 409 and r.json()["code"] == "table_has_bill"
    r = await client.post(f"{API}/tables/{t2['id']}/merge", headers=h(hm["Kiran"]), json={"from_table_ids": [t1["id"]]})
    assert r.json()["code"] == "table_has_bill"


async def test_permissions_and_isolation(client, root, hm):
    t1 = hm["table"]
    r = await client.post(f"{API}/tables/{t1['id']}/move", headers=h(hm["Ravi"]), json={"to_table_id": hm["t2"]["id"]})
    assert r.status_code == 403
    other = await make_hotel(client, root)
    r = await client.post(
        f"{API}/tables/{other['table']['id']}/merge", headers=h(hm["admin"]), json={"from_table_ids": [t1["id"]]}
    )
    assert r.status_code == 404
