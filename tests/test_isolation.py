"""Hotel A can never see or touch hotel B, through any endpoint. Required before merge."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import Session, onboard, system_role_id

pytestmark = pytest.mark.isolation


async def test_outlets_staff_roles_and_audit_are_isolated(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    a = await onboard(client, platform_admin)
    b = await onboard(client, platform_admin)
    assert a.outlet_id and b.outlet_id

    # Reading B's outlet by id from A: 404, exactly like a row that does not exist.
    r = await client.get(f"/v1/outlets/{b.outlet_id}", headers=a.admin.headers)
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    r = await client.patch(
        f"/v1/outlets/{b.outlet_id}", headers=a.admin.headers, json={"name": "Hijacked"}
    )
    assert r.status_code == 404
    assert [o["id"] for o in (await client.get("/v1/outlets", headers=a.admin.headers)).json()] == [
        str(a.outlet_id)
    ]

    b_staff = (await client.get("/v1/staff", headers=b.admin.headers)).json()["items"]
    a_staff = (await client.get("/v1/staff", headers=a.admin.headers)).json()["items"]
    assert {s["id"] for s in a_staff}.isdisjoint({s["id"] for s in b_staff})
    r = await client.get(f"/v1/staff/{b_staff[0]['id']}", headers=a.admin.headers)
    assert r.status_code == 404

    a_audit = (await client.get("/v1/audit-log", headers=a.admin.headers)).json()["items"]
    b_audit = (await client.get("/v1/audit-log", headers=b.admin.headers)).json()["items"]
    assert a_audit and b_audit
    assert {e["id"] for e in a_audit}.isdisjoint({e["id"] for e in b_audit})


async def test_custom_role_of_another_hotel_cannot_be_used(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    a = await onboard(client, platform_admin)
    b = await onboard(client, platform_admin)
    created = await client.post(
        "/v1/roles",
        headers=b.admin.headers,
        json={"code": "captain", "name": "Captain", "permissions": ["order.create", "order.view"]},
    )
    b_role = created.json()["id"]
    assert b_role not in {
        r["id"] for r in (await client.get("/v1/roles", headers=a.admin.headers)).json()
    }
    r = await client.post(
        "/v1/staff",
        headers=a.admin.headers,
        json={"full_name": "Sneaky", "login": "9123456780", "role_id": b_role},
    )
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_ROLE"
    r = await client.patch(f"/v1/roles/{b_role}", headers=a.admin.headers, json={"name": "Mine"})
    assert r.status_code == 404
    # System roles are shared and readable by both.
    assert await system_role_id(client, a.admin, "chef") == await system_role_id(
        client, b.admin, "chef"
    )


async def test_outlet_ids_from_another_hotel_are_rejected(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    a = await onboard(client, platform_admin)
    b = await onboard(client, platform_admin)
    r = await client.post(
        "/v1/staff",
        headers=a.admin.headers,
        json={
            "full_name": "Waiter",
            "login": "9123456781",
            "role_id": await system_role_id(client, a.admin, "waiter"),
            "outlet_ids": [str(b.outlet_id)],
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "UNKNOWN_OUTLET"
