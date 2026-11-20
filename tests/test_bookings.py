"""Reservations and the walk-in waitlist (docs/03 "Bookings", docs/14)."""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def hb(client, root):
    return await make_hotel(client, root)


def _in(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _book(client, hp, **body):
    base = {"kind": "BOOKING", "name": "Sharma", "phone": "98480 00001", "guests": 4, "booked_for": _in(60)}
    return client.post(f"{API}/bookings", headers=h(hp["Kiran"]), json={**base, **body})


async def test_book_see_on_the_floor_and_seat(client, hb):
    t = hb["table"]
    r = await _book(client, hb, table_id=t["id"], note="Window seat")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "BOOKED" and b["table"]["label"] == t["label"] and b["no_shows_before"] == 0
    # The floor shows the booking on the table; the day's book lists it.
    tables = (await client.get(f"{API}/tables", headers=h(hb["Kiran"]))).json()
    assert next(x for x in tables if x["id"] == t["id"])["next_booking"]["name"] == "Sharma"
    day = (await client.get(f"{API}/bookings", headers=h(hb["Kiran"]))).json()
    assert [x["name"] for x in day["bookings"]] == ["Sharma"]
    # Another booking on the same table at an overlapping time warns, then may be allowed.
    clash = await _book(client, hb, name="Rao", table_id=t["id"], booked_for=_in(90))
    assert clash.status_code == 409 and clash.json()["code"] == "table_booked"
    assert clash.json()["details"]["name"] == "Sharma"
    ok = await _book(client, hb, name="Rao", table_id=t["id"], booked_for=_in(90), allow_overlap=True)
    assert ok.status_code == 201
    # Guests arrive: seat them; the table visit starts with their guest count.
    s = await client.post(f"{API}/bookings/{b['id']}/seat", headers=h(hb["Kiran"]), json={})
    assert s.status_code == 200 and s.json()["status"] == "SEATED"
    table = next(x for x in (await client.get(f"{API}/tables", headers=h(hb["Kiran"]))).json() if x["id"] == t["id"])
    assert table["state"] == "SEATED" and table["guests"] == 4
    # The other booking can't be seated on the now busy table.
    busy = await client.post(f"{API}/bookings/{ok.json()['id']}/seat", headers=h(hb["Kiran"]), json={})
    assert busy.status_code == 409 and busy.json()["code"] == "table_in_use"


async def test_no_shows_are_remembered_by_phone(client, hb):
    first = (await _book(client, hb)).json()
    r = await client.post(f"{API}/bookings/{first['id']}/status", headers=h(hb["Kiran"]), json={"status": "NO_SHOW"})
    assert r.json()["status"] == "NO_SHOW"
    again = (await _book(client, hb, booked_for=_in(1440))).json()
    assert again["no_shows_before"] == 1, "the host sees this guest didn't come last time"
    done = await client.post(
        f"{API}/bookings/{first['id']}/status", headers=h(hb["Kiran"]), json={"status": "CANCELLED"}
    )
    assert done.json()["code"] == "bad_booking_state"


async def test_waitlist_quotes_and_actual_wait(client, hb):
    r = await client.post(
        f"{API}/bookings",
        headers=h(hb["Kiran"]),
        json={"kind": "WAITLIST", "name": "Walk-in Rao", "guests": 2, "quoted_minutes": 15},
    )
    assert r.status_code == 201 and r.json()["status"] == "WAITING" and r.json()["waited_minutes"] == 0
    w = r.json()
    day = (await client.get(f"{API}/bookings", headers=h(hb["Kiran"]))).json()
    assert [x["name"] for x in day["waitlist"]] == ["Walk-in Rao"] and day["avg_wait_minutes_today"] is None
    need = await client.post(f"{API}/bookings/{w['id']}/seat", headers=h(hb["Kiran"]), json={})
    assert need.json()["code"] == "table_required"
    s = await client.post(
        f"{API}/bookings/{w['id']}/seat", headers=h(hb["Kiran"]), json={"table_id": hb["table"]["id"]}
    )
    assert s.json()["status"] == "SEATED"
    day = (await client.get(f"{API}/bookings", headers=h(hb["Kiran"]))).json()
    assert day["waitlist"] == [] and day["avg_wait_minutes_today"] == 0
    bad = await client.post(f"{API}/bookings/{w['id']}/status", headers=h(hb["Kiran"]), json={"status": "NO_SHOW"})
    assert bad.status_code == 409


async def test_rules_permissions_and_isolation(client, root, hb):
    assert (await _book(client, hb, booked_for=None)).status_code == 422, "a booking needs a time"
    past = await _book(client, hb, booked_for=_in(-180))
    assert past.status_code == 400 and past.json()["code"] == "past_time"
    assert (await _book(client, hb, booked_for="2026-12-01T20:00:00")).status_code == 422, "time zone required"
    chef = await client.post(
        f"{API}/bookings", headers=h(hb["Ravi"]), json={"kind": "WAITLIST", "name": "X", "guests": 1}
    )
    assert chef.status_code == 403
    b = (await _book(client, hb)).json()
    other = await make_hotel(client, root)
    assert (await client.get(f"{API}/bookings", headers=h(other["admin"]))).json()["bookings"] == []
    r = await client.post(f"{API}/bookings/{b['id']}/status", headers=h(other["admin"]), json={"status": "CANCELLED"})
    assert r.status_code == 404
