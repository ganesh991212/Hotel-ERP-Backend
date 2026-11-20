"""Attendance (docs/03 "Attendance", docs/14): own clock in/out, chefs follow the clock, flagged missed clock-outs,
audited corrections."""

import os
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import h, make_hotel

API = "/api/v1"


@pytest.fixture
async def ha(client, root):
    return await make_hotel(client, root)


async def _chef(client, hp, name):
    board = (await client.get(f"{API}/kitchen/board", headers=h(hp["admin"]))).json()
    return next(c for c in board["chefs"] if c["name"] == name)


async def test_clock_in_and_out_on_your_own_login(client, ha):
    k = h(ha["Kiran"])
    assert (await client.get(f"{API}/attendance/me", headers=k)).json()["clocked_in"] is False
    r = await client.post(f"{API}/attendance/clock-in", headers=k)
    assert r.status_code == 201 and r.json()["open"]
    again = await client.post(f"{API}/attendance/clock-in", headers=k)
    assert again.status_code == 409 and again.json()["code"] == "already_clocked_in"
    out = await client.post(f"{API}/attendance/clock-out", headers=k)
    assert out.status_code == 200 and out.json()["open"] is False and out.json()["minutes"] == 0
    assert (await client.post(f"{API}/attendance/clock-out", headers=k)).json()["code"] == "not_clocked_in"

    sheet = (await client.get(f"{API}/attendance", headers=h(ha["admin"]))).json()
    [kiran] = sheet["people"]
    assert kiran["name"] == "Kiran" and kiran["shifts"] == 1 and kiran["days"] == 1
    assert (await client.get(f"{API}/attendance", headers=k)).status_code == 403, "staff see only their own"


async def test_a_chef_goes_off_shift_when_clocking_out(client, ha):
    r = h(ha["Ravi"])
    await client.post(f"{API}/attendance/clock-in", headers=r)
    assert (await _chef(client, ha, "Ravi"))["on_shift"] is True
    await client.post(f"{API}/attendance/clock-out", headers=r)
    assert (await _chef(client, ha, "Ravi"))["on_shift"] is False, "no orders routed to someone who went home"
    await client.post(f"{API}/attendance/clock-in", headers=r)
    assert (await _chef(client, ha, "Ravi"))["on_shift"] is True


async def test_a_missed_clock_out_is_flagged_and_fixed_with_a_reason(client, ha):
    a, k = h(ha["admin"]), h(ha["Kiran"])
    shift = (await client.post(f"{API}/attendance/clock-in", headers=k)).json()
    # Pretend it started 17 hours ago (yesterday evening); the app never moves punches by itself.
    eng = create_async_engine(os.environ["MIGRATION_DATABASE_URL"])
    async with eng.begin() as c:
        sql = "UPDATE attendance SET clock_in_at = clock_in_at - interval '17 hours' WHERE id = :i"
        await c.execute(text(sql), {"i": shift["id"]})
    await eng.dispose()
    started = datetime.fromisoformat(shift["clock_in_at"]) - timedelta(hours=17)
    sheet = (
        await client.get(f"{API}/attendance", headers=a, params={"from": str(started.date() - timedelta(days=1))})
    ).json()
    row = next(s for s in sheet["shifts"] if s["id"] == shift["id"])
    assert row["missing_clock_out"] and row["open"], "flagged, not closed by the system"

    out_at = (started + timedelta(hours=8)).isoformat()
    bad = await client.post(f"{API}/attendance/{shift['id']}/fix", headers=a, json={"clock_out_at": out_at})
    assert bad.status_code == 422, "a reason is required"
    k_fix = await client.post(
        f"{API}/attendance/{shift['id']}/fix", headers=k, json={"clock_out_at": out_at, "reason": "forgot"}
    )
    assert k_fix.status_code == 403, "nobody fixes their own hours"
    r = await client.post(
        f"{API}/attendance/{shift['id']}/fix", headers=a, json={"clock_out_at": out_at, "reason": "Forgot to clock out"}
    )
    assert r.status_code == 200, r.text
    fixed = r.json()
    assert fixed["minutes"] == 8 * 60 and fixed["corrected"] and fixed["correction_reason"] == "Forgot to clock out"
    later = (datetime.fromisoformat(out_at) + timedelta(days=3)).isoformat()
    future = await client.post(
        f"{API}/attendance/{shift['id']}/fix", headers=a, json={"clock_out_at": later, "reason": "x y z"}
    )
    assert future.json()["code"] == "future_time"
    # Kiran can clock in again now that the old shift is closed.
    assert (await client.post(f"{API}/attendance/clock-in", headers=k)).status_code == 201
