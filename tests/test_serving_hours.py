"""Serving hours on categories: breakfast / lunch / dinner (docs/03 "Serving hours", docs/14)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ordering.pricing import in_window
from tests.conftest import h, make_hotel

API = "/api/v1"


def test_no_times_means_all_day_on_those_days():
    fri = datetime(2026, 11, 20, 3, 0)
    assert in_window(127, None, None, fri) and not in_window(1, None, None, fri)  # 1 = Monday only
    assert in_window(16, time(7), time(11), fri.replace(hour=8)) and not in_window(16, time(7), time(11), fri)


@pytest.fixture
async def hs(client, root):
    hp = await make_hotel(client, root)
    cat = (await client.get(f"{API}/categories", headers=h(hp["admin"]))).json()[0]
    return {**hp, "cat": cat}


async def _hours(client, hs, start, end, who="admin"):
    return await client.put(
        f"{API}/categories/{hs['cat']['id']}",
        headers=h(hs[who]),
        json={"name": hs["cat"]["name"], "available_from": start, "available_to": end},
    )


def _later():
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    return (now + timedelta(hours=2)).strftime("%H:%M"), (now + timedelta(hours=3)).strftime("%H:%M")


async def test_diners_cannot_order_outside_the_hours(client, hs):
    r = await _hours(client, hs, *_later())
    assert r.status_code == 200 and r.json()["open_now"] is False, r.text
    qr = hs["table"]["qr_token"]
    menu = (await client.get(f"{API}/d/{qr}")).json()
    section = menu["categories"][0]
    assert section["open_now"] is False and section["hours"] == "-".join(_later())
    # Dishes stay "available" (not sold out); the section is closed, and the server refuses them below.
    q = await client.post(f"{API}/d/{qr}/quote", json={"items": [{"item_id": hs["items"]["Naan"], "qty": 1}]})
    assert q.status_code == 409 and q.json()["code"] == "outside_hours"


async def test_staff_are_warned_and_can_order_anyway(client, hs):
    await _hours(client, hs, *_later())
    body = {"table_id": hs["table"]["id"], "items": [{"item_id": hs["items"]["Naan"], "qty": 1}]}
    # The running total still works in the composer.
    q = await client.post(f"{API}/orders/quote", headers=h(hs["Kiran"]), json={"items": body["items"]})
    assert q.status_code == 200
    warn = await client.post(f"{API}/orders", headers=h(hs["Kiran"]), json=body)
    assert warn.status_code == 409 and warn.json()["code"] == "outside_hours"
    assert warn.json()["details"]["items"] == ["Naan"] and warn.json()["details"]["category"] == hs["cat"]["name"]
    ok = await client.post(f"{API}/orders", headers=h(hs["Kiran"]), json={**body, "allow_outside_hours": True})
    assert ok.status_code == 201, ok.text


async def test_inside_the_hours_and_all_day(client, hs):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start, end = (now - timedelta(hours=1)).strftime("%H:%M"), (now + timedelta(hours=1)).strftime("%H:%M")
    assert (await _hours(client, hs, start, end)).json()["open_now"] is True
    body = {"table_id": hs["table"]["id"], "items": [{"item_id": hs["items"]["Naan"], "qty": 1}]}
    assert (await client.post(f"{API}/orders", headers=h(hs["Kiran"]), json=body)).status_code == 201
    back = await _hours(client, hs, None, None)
    assert back.json()["available_from"] is None and back.json()["open_now"] is True


async def test_rules_for_hours(client, root, hs):
    assert (await _hours(client, hs, "07:00", None)).status_code == 422, "both times or neither"
    assert (await _hours(client, hs, "07:00", "07:00")).status_code == 422
    assert (await _hours(client, hs, "07:00", "11:00", who="Kiran")).status_code == 403
    other = await make_hotel(client, root)
    r = await client.put(f"{API}/categories/{hs['cat']['id']}", headers=h(other["admin"]), json={"name": "Mine"})
    assert r.status_code == 404
