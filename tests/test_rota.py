"""Shift rota and lateness (docs/03 "Shift rota", docs/14)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.staff.rota import shift_state
from tests.conftest import h, make_hotel

API = "/api/v1"
T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=8)


def test_states_against_the_persons_own_clock_ins():
    assert shift_state(T0, T1, [], T0 - timedelta(hours=1))["state"] == "UPCOMING"
    assert shift_state(T0, T1, [], T0 + timedelta(minutes=10))["state"] == "UPCOMING", "within the grace"
    s = shift_state(T0, T1, [], T0 + timedelta(minutes=25))
    assert s["state"] == "NOT_IN" and s["late_minutes"] == 25
    assert shift_state(T0, T1, [], T1)["state"] == "NO_SHOW"
    assert shift_state(T0, T1, [(T0 - timedelta(minutes=5), True)], T0)["state"] == "WORKING"
    assert shift_state(T0, T1, [(T0 + timedelta(minutes=8), False)], T1)["state"] == "ON_TIME"
    late = shift_state(T0, T1, [(T0 + timedelta(minutes=40), True)], T0 + timedelta(hours=1))
    assert late["state"] == "LATE" and late["late_minutes"] == 40 and late["working"]
    # A clock-in from yesterday's shift does not count for today's.
    assert shift_state(T0, T1, [(T0 - timedelta(hours=10), False)], T1)["state"] == "NO_SHOW"


@pytest.fixture
async def hr(client, root):
    return await make_hotel(client, root)


async def _uid(client, hp, name):
    users = (await client.get(f"{API}/users", headers=h(hp["admin"]))).json()
    return next(u["id"] for u in users if u["name"] == name)


async def test_plan_overnight_overlap_and_copy_week(client, hr):
    a = h(hr["admin"])
    kiran = await _uid(client, hr, "Kiran")
    r = await client.post(
        f"{API}/rota", headers=a, json={"user_id": kiran, "day": "2030-01-07", "start": "18:00", "end": "02:00"}
    )
    assert r.status_code == 201, r.text
    night = r.json()
    assert night["overnight"] and night["start"] == "18:00" and night["end"] == "02:00" and night["state"] == "UPCOMING"
    clash = await client.post(
        f"{API}/rota", headers=a, json={"user_id": kiran, "day": "2030-01-08", "start": "01:00", "end": "09:00"}
    )
    assert clash.status_code == 409 and clash.json()["code"] == "shift_overlap", "1 am is still the night shift"
    ok = await client.post(
        f"{API}/rota", headers=a, json={"user_id": kiran, "day": "2030-01-08", "start": "10:00", "end": "18:00"}
    )
    assert ok.status_code == 201
    # Copy the week of 7 Jan to the week of 14 Jan; one shift already there is skipped, not doubled.
    await client.post(
        f"{API}/rota", headers=a, json={"user_id": kiran, "day": "2030-01-15", "start": "12:00", "end": "14:00"}
    )
    c = (await client.post(f"{API}/rota/copy-week", headers=a, json={"week_start": "2030-01-07"})).json()
    assert c == {"copied": 1, "skipped": 1}
    week = (await client.get(f"{API}/rota", headers=a, params={"from": "2030-01-14", "to": "2030-01-20"})).json()
    assert sorted((s["day"], s["start"]) for s in week["shifts"]) == [("2030-01-14", "18:00"), ("2030-01-15", "12:00")]
    assert (await client.get(f"{API}/rota", headers=h(hr["Kiran"]))).status_code == 403
    assert (await client.delete(f"{API}/rota/{ok.json()['id']}", headers=a)).status_code == 204


async def test_late_clock_in_is_reported_and_staff_see_their_own(client, hr):
    a, k = h(hr["admin"]), h(hr["Kiran"])
    kiran = await _uid(client, hr, "Kiran")
    local = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = local - timedelta(minutes=30)
    if start.date() != local.date() or local.hour >= 23:
        pytest.skip("too close to midnight for this fixed shift")
    end = local + timedelta(hours=2)
    body = {"user_id": kiran, "day": str(start.date()), "start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    plan = (await client.post(f"{API}/rota", headers=a, json=body)).json()
    assert plan["state"] == "NOT_IN" and plan["late_minutes"] >= 29
    await client.post(f"{API}/attendance/clock-in", headers=k)
    mine = (await client.get(f"{API}/rota/me", headers=k)).json()["shifts"]
    [s] = [x for x in mine if x["id"] == plan["id"]]
    assert s["state"] == "LATE" and s["late_minutes"] >= 29 and s["working"]
