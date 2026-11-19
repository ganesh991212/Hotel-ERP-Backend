"""Change own password; admin reset for staff who forgot theirs (no escalation)."""

import pytest

from tests.conftest import h, login, make_hotel

API = "/api/v1"


@pytest.fixture(scope="module")
async def hp(client, root):
    return await make_hotel(client, root)


async def _email(client, hp, name: str) -> str:
    users = (await client.get(f"{API}/users", headers=h(hp["admin"]))).json()
    return next(u["email"] for u in users if u["name"] == name)


async def test_change_own_password_signs_out_other_devices(client, hp):
    email = await _email(client, hp, "Kiran")
    phone = await login(client, email, "staff-pass-123")  # a second device
    laptop = await login(client, email, "staff-pass-123")

    wrong = await client.post(
        f"{API}/me/password", headers=h(laptop), json={"current_password": "nope", "new_password": "new-pass-456"}
    )
    assert wrong.status_code == 400 and wrong.json()["code"] == "wrong_password"
    same = await client.post(
        f"{API}/me/password",
        headers=h(laptop),
        json={"current_password": "staff-pass-123", "new_password": "staff-pass-123"},
    )
    assert same.status_code == 400 and same.json()["code"] == "same_password"
    short = await client.post(
        f"{API}/me/password", headers=h(laptop), json={"current_password": "staff-pass-123", "new_password": "short"}
    )
    assert short.status_code == 422

    ok = await client.post(
        f"{API}/me/password",
        headers=h(laptop),
        json={"current_password": "staff-pass-123", "new_password": "new-pass-456"},
    )
    assert ok.status_code == 200 and ok.json()["access_token"] and ok.json()["refresh_token"]

    old = await client.post(f"{API}/auth/login", json={"email": email, "password": "staff-pass-123"})
    assert old.status_code == 401
    await login(client, email, "new-pass-456")
    # The other device cannot renew its session any more.
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": phone["_refresh"]})
    assert r.status_code == 401
    # This device keeps working with the fresh tokens it was given.
    r = await client.post(f"{API}/auth/refresh", json={"refresh_token": ok.json()["refresh_token"]})
    assert r.status_code == 200


async def test_platform_admin_can_change_password(client, root):
    r = await client.post(
        f"{API}/me/password",
        headers=h(root),
        json={"current_password": "root-pass-123", "new_password": "root-pass-456"},
    )
    assert r.status_code == 200
    back = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post(
        f"{API}/me/password",
        headers=back,
        json={"current_password": "root-pass-456", "new_password": "root-pass-123"},
    )
    assert r.status_code == 200


async def test_admin_resets_a_staff_password(client, hp):
    email = await _email(client, hp, "Sita")
    sita = await login(client, email, "staff-pass-123")
    r = await client.post(
        f"{API}/users/{hp['Sita']['_id']}/password", headers=h(hp["admin"]), json={"new_password": "fresh-pass-789"}
    )
    assert r.status_code == 204
    assert (
        await client.post(f"{API}/auth/login", json={"email": email, "password": "staff-pass-123"})
    ).status_code == 401
    await login(client, email, "fresh-pass-789")
    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": sita["_refresh"]})).status_code == 401

    # Staff without staff.manage cannot reset anyone.
    r = await client.post(
        f"{API}/users/{hp['Ravi']['_id']}/password", headers=h(hp["Kiran"]), json={"new_password": "x-pass-0000"}
    )
    assert r.status_code == 403
    # Your own password goes through "change password" (it needs the current one).
    me = (await client.get(f"{API}/me", headers=h(hp["admin"]))).json()
    r = await client.post(
        f"{API}/users/{me['id']}/password", headers=h(hp["admin"]), json={"new_password": "x-pass-0000"}
    )
    assert r.status_code == 400 and r.json()["code"] == "use_change_password"


async def test_manager_cannot_take_over_the_admin_account(client, hp):
    """A manager may reset staff, but not the hotel admin: that would hand them the admin's account."""
    roles = {r["code"]: r["id"] for r in (await client.get(f"{API}/roles", headers=h(hp["admin"]))).json()}
    created = await client.post(
        f"{API}/users",
        headers=h(hp["admin"]),
        json={
            "name": "Mona",
            "email": "mona-pw@hotel.example.com",
            "password": "manager-pass-1",
            "role_id": roles["MANAGER"],
        },
    )
    assert created.status_code == 201, created.text
    perms = (await client.get(f"{API}/roles", headers=h(hp["admin"]))).json()
    if "staff.manage" not in next(r for r in perms if r["code"] == "MANAGER")["permissions"]:
        await client.put(
            f"{API}/roles/{roles['MANAGER']}/permissions",
            headers=h(hp["admin"]),
            json={"permissions": [*next(r for r in perms if r["code"] == "MANAGER")["permissions"], "staff.manage"]},
        )
    mona = await login(client, "mona-pw@hotel.example.com", "manager-pass-1")
    me = (await client.get(f"{API}/me", headers=h(hp["admin"]))).json()
    r = await client.post(f"{API}/users/{me['id']}/password", headers=h(mona), json={"new_password": "stolen-pass-1"})
    assert r.status_code == 403
    # ...but the manager can still help a waiter who forgot theirs.
    r = await client.post(
        f"{API}/users/{hp['Kiran']['_id']}/password", headers=h(mona), json={"new_password": "waiter-pass-2"}
    )
    assert r.status_code == 204
