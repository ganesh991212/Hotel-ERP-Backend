"""Hotel admin: plan limits, plan-gated settings, RBAC guards, tax profile, audit."""

from __future__ import annotations

import asyncio

import httpx

from tests.conftest import Session, add_staff, onboard, system_role_id, tax_profile


async def test_outlet_limit_and_plan_gated_kitchen_modes(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="starter")  # 1 outlet, admin_print only
    r = await client.post(
        "/v1/outlets", headers=hotel.admin.headers, json={"name": "Rooftop", "code": "ROOF"}
    )
    assert r.status_code == 403 and r.json()["code"] == "PLAN_LIMIT_REACHED"
    assert r.json()["details"] == {"feature": "MAX_OUTLETS", "limit": 1, "current": 1}
    r = await client.patch(
        f"/v1/outlets/{hotel.outlet_id}",
        headers=hotel.admin.headers,
        json={"kitchen_mode": "auto_assign"},
    )
    assert r.status_code == 403 and r.json()["details"] == {"feature": "AUTO_ROUTING"}
    r = await client.patch(
        f"/v1/outlets/{hotel.outlet_id}",
        headers=hotel.admin.headers,
        json={"kitchen_mode": "admin_print", "max_queue_orders": 4},
    )
    assert r.status_code == 200 and r.json()["max_queue_orders"] == 4
    r = await client.patch(
        f"/v1/outlets/{hotel.outlet_id}",
        headers=hotel.admin.headers,
        json={"kitchen_mod": "admin_print"},
    )
    assert r.status_code == 422  # typos are errors, not silent no-ops


async def test_concurrent_creates_cannot_exceed_the_limit(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="growth")  # 3 outlets, 1 already exists
    results = await asyncio.gather(
        *[
            client.post(
                "/v1/outlets",
                headers=hotel.admin.headers,
                json={"name": f"Outlet {i}", "code": f"O{i}"},
            )
            for i in range(6)
        ]
    )
    assert sorted(r.status_code for r in results) == [201, 201, 403, 403, 403, 403]
    active = (await client.get("/v1/outlets", headers=hotel.admin.headers)).json()
    assert len(active) == 3


async def test_archive_frees_a_slot_and_restore_rechecks(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="starter")
    await client.post(f"/v1/outlets/{hotel.outlet_id}/archive", headers=hotel.admin.headers)
    new = await client.post(
        "/v1/outlets", headers=hotel.admin.headers, json={"name": "New Place", "code": "NEW"}
    )
    assert new.status_code == 201
    r = await client.post(f"/v1/outlets/{hotel.outlet_id}/restore", headers=hotel.admin.headers)
    assert r.status_code == 403 and r.json()["code"] == "PLAN_LIMIT_REACHED"


async def test_permissions_are_enforced_and_changes_apply_immediately(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    role = (
        await client.post(
            "/v1/roles",
            headers=hotel.admin.headers,
            json={"code": "supervisor", "name": "Supervisor", "permissions": ["order.view"]},
        )
    ).json()
    _, sup = await add_staff(client, hotel.admin, role["id"])
    r = await client.post("/v1/outlets", headers=sup.headers, json={"name": "Cafe", "code": "CAFE"})
    assert r.status_code == 403 and r.json()["code"] == "PERMISSION_DENIED"
    await client.patch(
        f"/v1/roles/{role['id']}",
        headers=hotel.admin.headers,
        json={"permissions": ["order.view", "outlet.manage"]},
    )
    # Same access token: the permission is read per request, not baked into the token.
    r = await client.post("/v1/outlets", headers=sup.headers, json={"name": "Cafe", "code": "CAFE"})
    assert r.status_code == 201


async def test_no_privilege_escalation(client: httpx.AsyncClient, platform_admin: Session) -> None:
    hotel = await onboard(client, platform_admin)
    hr = (
        await client.post(
            "/v1/roles",
            headers=hotel.admin.headers,
            json={
                "code": "hr",
                "name": "HR",
                "permissions": ["staff.manage", "role.manage", "order.create", "order.view"],
            },
        )
    ).json()
    _, hr_user = await add_staff(client, hotel.admin, hr["id"])
    admin_role = await system_role_id(client, hotel.admin, "hotel_admin")

    r = await client.post(
        "/v1/staff",
        headers=hr_user.headers,
        json={"full_name": "Friend", "login": "9000000001", "role_id": admin_role},
    )
    assert r.status_code == 403 and r.json()["code"] == "PRIVILEGE_ESCALATION"
    r = await client.post(
        "/v1/roles",
        headers=hr_user.headers,
        json={"code": "boss", "name": "Boss", "permissions": ["hotel.manage"]},
    )
    assert r.status_code == 403 and r.json()["details"] == {"missing": ["hotel.manage"]}
    admins = (
        await client.get("/v1/staff", headers=hr_user.headers, params={"role_id": admin_role})
    ).json()["items"]
    r = await client.post(f"/v1/staff/{admins[0]['id']}/disable", headers=hr_user.headers)
    assert r.status_code == 403 and r.json()["code"] == "PRIVILEGE_ESCALATION"
    # HR can still manage people below them.
    waiter = await system_role_id(client, hotel.admin, "waiter")
    r = await client.post(
        "/v1/staff",
        headers=hr_user.headers,
        json={"full_name": "Waiter", "login": "9000000002", "role_id": waiter},
    )
    assert r.status_code == 201


async def test_last_admin_is_protected_even_under_concurrency(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    admin_role = await system_role_id(client, hotel.admin, "hotel_admin")
    second_id, second = await add_staff(client, hotel.admin, admin_role)
    first_id = next(
        s["id"]
        for s in (await client.get("/v1/staff", headers=hotel.admin.headers)).json()["items"]
        if s["id"] != second_id
    )

    r = await client.post(f"/v1/staff/{first_id}/disable", headers=hotel.admin.headers)
    assert r.status_code == 403 and r.json()["code"] == "CANNOT_MODIFY_SELF"
    # Two admins disable each other at the same instant: exactly one may succeed.
    results = await asyncio.gather(
        client.post(f"/v1/staff/{second_id}/disable", headers=hotel.admin.headers),
        client.post(f"/v1/staff/{first_id}/disable", headers=second.headers),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()["code"] == "LAST_ADMIN"


async def test_chef_limit(client: httpx.AsyncClient, platform_admin: Session) -> None:
    hotel = await onboard(client, platform_admin, plan="starter")  # MAX_CHEFS = 3
    chef = await system_role_id(client, hotel.admin, "chef")
    for _ in range(3):
        await add_staff(client, hotel.admin, chef)
    r = await client.post(
        "/v1/staff",
        headers=hotel.admin.headers,
        json={"full_name": "Fourth Chef", "login": "9000000003", "role_id": chef},
    )
    assert r.status_code == 403 and r.json()["details"]["feature"] == "MAX_CHEFS"


async def test_outlet_restricted_staff_see_only_their_outlets(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="growth")
    bar = (
        await client.post(
            "/v1/outlets",
            headers=hotel.admin.headers,
            json={"name": "Bar", "code": "BAR", "kind": "bar"},
        )
    ).json()
    _, bartender = await add_staff(
        client,
        hotel.admin,
        await system_role_id(client, hotel.admin, "waiter"),
        outlet_ids=[bar["id"]],
    )
    seen = (await client.get("/v1/outlets", headers=bartender.headers)).json()
    assert [o["id"] for o in seen] == [bar["id"]]
    assert (
        await client.get(f"/v1/outlets/{hotel.outlet_id}", headers=bartender.headers)
    ).status_code == 404


async def test_tax_profile_and_audit_trail(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    r = await client.put(
        "/v1/hotel/tax-profile", headers=hotel.admin.headers, json={**tax_profile("unregistered")}
    )
    assert r.status_code == 200 and r.json()["invoice_document"] == "bill"
    assert r.json()["gstin"] is None
    r = await client.put(
        "/v1/hotel/tax-profile",
        headers=hotel.admin.headers,
        json={**tax_profile("regular"), "service_charge_applies": True, "service_charge_bp": 500},
    )
    assert r.json()["invoice_document"] == "tax_invoice" and r.json()["service_charge_bp"] == 500
    log = (await client.get("/v1/audit-log", headers=hotel.admin.headers)).json()["items"]
    actions = [e["action"] for e in log]
    assert actions[:2] == ["hotel.tax_profile_saved", "hotel.tax_profile_saved"]
    assert "hotel.onboarded" in actions and "staff.invite_accepted" in actions
    # Personal data never lands in audit payloads.
    assert all("full_name" not in (e["after_json"] or {}) for e in log)
