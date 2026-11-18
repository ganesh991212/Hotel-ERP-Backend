"""Sign-in, refresh rotation and reuse detection, revocation, invites."""

from __future__ import annotations

import asyncio

import httpx
from sqlalchemy import text

from app.core.security import hash_token
from app.db.session import system_session
from tests.conftest import (
    PASSWORD,
    Session,
    accept,
    add_staff,
    auth,
    login,
    onboard,
    system_role_id,
    unique_phone,
)


async def test_login_wrong_password_and_unknown_user_look_identical(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    wrong = await client.post(
        "/v1/auth/login", json={"login": hotel.admin.login, "password": "nope"}
    )
    unknown = await client.post(
        "/v1/auth/login", json={"login": unique_phone(), "password": "nope"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "INVALID_CREDENTIALS"


async def test_lockout_after_repeated_failures(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    for _ in range(5):
        await client.post("/v1/auth/login", json={"login": hotel.admin.login, "password": "bad"})
    # Correct password while locked: told it is locked (only someone who knows the password
    # learns this, so it does not help an attacker enumerate accounts).
    r = await client.post("/v1/auth/login", json={"login": hotel.admin.login, "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["code"] == "ACCOUNT_LOCKED"
    r = await client.post("/v1/auth/login", json={"login": hotel.admin.login, "password": "bad"})
    assert r.status_code == 401


async def test_refresh_rotates_and_old_token_is_single_use(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    first = await client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
    assert first.status_code == 200
    new = first.json()
    assert new["refresh_token"] != hotel.admin.refresh
    assert (await client.get("/v1/me", headers=auth(new["access_token"]))).status_code == 200

    # The old token again, moments later: a client race, not theft. Rejected, session kept.
    again = await client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
    assert again.status_code == 401
    assert again.json()["code"] == "REFRESH_TOKEN_SUPERSEDED"
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": new["refresh_token"]})
    ).status_code == 200


async def test_reuse_after_grace_revokes_the_session(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    rotated = (
        await client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
    ).json()
    # Pretend the old token was used an hour ago: a copy is being replayed.
    async with system_session() as db:
        await db.execute(
            text(
                "UPDATE refresh_token SET used_at = now() - interval '1 hour' WHERE token_hash = :h"
            ),
            {"h": hash_token(hotel.admin.refresh)},
        )
    replay = await client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
    assert replay.status_code == 401
    assert replay.json()["code"] == "REFRESH_TOKEN_REUSED"
    # The whole session is gone: the legitimate holder's newer tokens stop working too.
    assert (await client.get("/v1/me", headers=auth(rotated["access_token"]))).status_code == 401
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    ).status_code == 401
    log = (
        await client.get(
            "/v1/audit-log",
            headers=(await login(client, hotel.admin.login)).headers,
            params={"entity_type": "auth_session"},
        )
    ).json()
    assert any(e["action"] == "auth.refresh_reuse_detected" for e in log["items"])


async def test_concurrent_refreshes_exactly_one_wins(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    results = await asyncio.gather(
        *[
            client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
            for _ in range(5)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 401, 401, 401, 401]
    losers = [r.json()["code"] for r in results if r.status_code == 401]
    assert set(losers) == {"REFRESH_TOKEN_SUPERSEDED"}  # a race never revokes the session
    winner = next(r for r in results if r.status_code == 200).json()
    assert (await client.get("/v1/me", headers=auth(winner["access_token"]))).status_code == 200


async def test_logout_takes_effect_immediately(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    assert (await client.post("/v1/auth/logout", headers=hotel.admin.headers)).status_code == 204
    # The access token has not expired, but the session behind it has.
    assert (await client.get("/v1/me", headers=hotel.admin.headers)).status_code == 401
    assert (
        await client.post("/v1/auth/refresh", json={"refresh_token": hotel.admin.refresh})
    ).status_code == 401


async def test_disabled_member_loses_access_on_next_request(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    member_id, waiter = await add_staff(
        client, hotel.admin, await system_role_id(client, hotel.admin, "waiter")
    )
    assert (await client.get("/v1/me", headers=waiter.headers)).status_code == 200
    r = await client.post(f"/v1/staff/{member_id}/disable", headers=hotel.admin.headers)
    assert r.status_code == 200 and r.json()["status"] == "disabled"
    assert (await client.get("/v1/me", headers=waiter.headers)).status_code == 401
    bad = await client.post("/v1/auth/login", json={"login": waiter.login, "password": PASSWORD})
    assert bad.status_code == 401


async def test_person_at_two_hotels_chooses_one(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    a = await onboard(client, platform_admin)
    b = await onboard(client, platform_admin)
    phone = unique_phone()
    await add_staff(client, a.admin, await system_role_id(client, a.admin, "chef"), phone=phone)
    # Joining hotel B: the invite must be accepted with the EXISTING password.
    r = await client.post(
        "/v1/staff",
        headers=b.admin.headers,
        json={
            "full_name": "Someone Else",
            "login": phone,
            "role_id": await system_role_id(client, b.admin, "waiter"),
        },
    )
    assert r.status_code == 201
    assert r.json()["member"]["full_name"] == "Staff Member"  # hotel B cannot rename them
    assert (await accept(client, r.json()["invite"]["token"])).status_code == 200

    pick = await client.post("/v1/auth/login", json={"login": phone, "password": PASSWORD})
    assert pick.status_code == 409
    assert pick.json()["code"] == "HOTEL_SELECTION_REQUIRED"
    assert {h["hotel_id"] for h in pick.json()["details"]} == {str(a.id), str(b.id)}
    in_b = await login(client, phone, hotel_id=b.id)
    me = (await client.get("/v1/me", headers=in_b.headers)).json()
    assert me["hotel"]["id"] == str(b.id) and me["role"]["code"] == "waiter"


async def test_invite_cannot_take_over_an_existing_account(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    """A hotel admin holds the invite token. If accepting it could set a new password, any
    hotel could hijack a chef who works at another hotel."""
    a = await onboard(client, platform_admin)
    b = await onboard(client, platform_admin)
    phone = unique_phone()
    await add_staff(client, a.admin, await system_role_id(client, a.admin, "chef"), phone=phone)
    r = await client.post(
        "/v1/staff",
        headers=b.admin.headers,
        json={
            "full_name": "Xavier",
            "login": phone,
            "role_id": await system_role_id(client, b.admin, "waiter"),
        },
    )
    hijack = await accept(client, r.json()["invite"]["token"], password="attacker-chosen-pw")
    assert hijack.status_code == 401
    # The real password still works, and the invite is still unused (rolled back).
    await login(client, phone, hotel_id=a.id)
    assert (await client.get(f"/v1/auth/invites/{r.json()['invite']['token']}")).status_code == 200


async def test_invite_is_single_use(client: httpx.AsyncClient, platform_admin: Session) -> None:
    hotel = await onboard(client, platform_admin)
    r = await client.post(
        "/v1/staff",
        headers=hotel.admin.headers,
        json={
            "full_name": "New Waiter",
            "login": unique_phone(),
            "role_id": await system_role_id(client, hotel.admin, "waiter"),
        },
    )
    token = r.json()["invite"]["token"]
    info = (await client.get(f"/v1/auth/invites/{token}")).json()
    assert info == {
        "hotel_name": "Test Hotel",
        "full_name": "New Waiter",
        "needs_new_password": True,
    }
    assert (await accept(client, token)).status_code == 200
    second = await accept(client, token)
    assert second.status_code == 410 and second.json()["code"] == "INVITE_INVALID"


async def test_platform_and_hotel_tokens_stay_in_their_lane(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    me = (await client.get("/v1/me", headers=platform_admin.headers)).json()
    assert me["is_platform_admin"] is True and me["hotel"] is None
    r = await client.get("/v1/outlets", headers=platform_admin.headers)
    assert r.status_code == 403 and r.json()["code"] == "HOTEL_CONTEXT_REQUIRED"
    r = await client.get("/v1/platform/hotels", headers=hotel.admin.headers)
    assert r.status_code == 403 and r.json()["code"] == "PLATFORM_ADMIN_REQUIRED"


async def test_tampered_token_is_rejected(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    head, body, sig = hotel.admin.access.split(".")
    forged = f"{head}.{body}.{sig[:-2]}AA"
    assert (await client.get("/v1/me", headers=auth(forged))).status_code == 401
    assert (await client.get("/v1/me")).status_code == 401
