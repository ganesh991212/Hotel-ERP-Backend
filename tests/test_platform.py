"""Super admin: onboarding, plans, subscriptions, overrides, suspension."""

from __future__ import annotations

import httpx

from tests.conftest import Session, onboard, tax_profile, unique_phone, unique_slug


async def test_onboarding_creates_everything_atomically(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    slug = unique_slug()
    r = await client.post(
        "/v1/platform/hotels",
        headers=platform_admin.headers,
        json={
            "name": "Paradise Biryani",
            "slug": slug,
            "plan_code": "starter",
            "tax_profile": tax_profile("composition"),
            "admin": {"full_name": "Owner", "login": unique_phone()},
        },
    )
    assert r.status_code == 201, r.text
    hotel = r.json()["hotel"]
    assert hotel["subscription"]["plan_code"] == "starter"
    assert hotel["subscription"]["status"] == "trial"
    assert hotel["subscription"]["price_minor"] == 99_900
    assert hotel["tax_profile"]["invoice_document"] == "bill_of_supply"
    assert hotel["outlet_count"] == 1 and hotel["member_count"] == 1

    dup = await client.post(
        "/v1/platform/hotels",
        headers=platform_admin.headers,
        json={
            "name": "Copy",
            "slug": slug,
            "tax_profile": tax_profile(),
            "admin": {"full_name": "Owner", "login": unique_phone()},
        },
    )
    assert dup.status_code == 409 and dup.json()["code"] == "SLUG_TAKEN"


async def test_invalid_gstin_is_rejected(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    bad = {**tax_profile(), "gstin": "27AAPFU0939F1ZW"}
    r = await client.post(
        "/v1/platform/hotels",
        headers=platform_admin.headers,
        json={
            "name": "Bad GST",
            "tax_profile": bad,
            "admin": {"full_name": "Owner", "login": unique_phone()},
        },
    )
    assert r.status_code == 422 and r.json()["code"] == "VALIDATION_ERROR"


async def test_plan_price_change_is_grandfathered(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    plans = (await client.get("/v1/platform/plans", headers=platform_admin.headers)).json()
    seeded = {p["code"]: p for p in plans}
    assert {"starter", "growth", "pro"} <= seeded.keys()
    assert [seeded[c]["price_monthly_minor"] for c in ("starter", "growth", "pro")] == [
        99_900,
        249_900,
        499_900,
    ]
    code = unique_slug("p").replace("-", "_")
    created = await client.post(
        "/v1/platform/plans",
        headers=platform_admin.headers,
        json={
            "code": code,
            "name": "Test plan",
            "price_monthly_minor": 100_000,
            "price_annual_minor": 1_000_000,
        },
    )
    assert created.status_code == 201
    plan_id = created.json()["id"]
    feats = await client.put(
        f"/v1/platform/plans/{plan_id}/features",
        headers=platform_admin.headers,
        json={
            "set": {
                "WAITER_ORDERING": True,
                "ADMIN_PRINT_MODE": True,
                "MAX_OUTLETS": 2,
                "MAX_STAFF": None,
            }
        },
    )
    assert feats.json()["features"]["MAX_STAFF"] is None
    bad = await client.put(
        f"/v1/platform/plans/{plan_id}/features",
        headers=platform_admin.headers,
        json={"set": {"MAX_OUTLETS": True}},
    )
    assert bad.status_code == 422 and bad.json()["code"] == "FEATURE_VALUE_INVALID"

    hotel = await onboard(client, platform_admin, plan=code)
    await client.patch(
        f"/v1/platform/plans/{plan_id}",
        headers=platform_admin.headers,
        json={"price_monthly_minor": 150_000, "is_placeholder": False},
    )
    detail = (
        await client.get(f"/v1/platform/hotels/{hotel.id}", headers=platform_admin.headers)
    ).json()
    assert detail["subscription"]["price_minor"] == 100_000  # the price they signed up at


async def test_subscription_change_applies_on_next_request(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="starter")
    before = (await client.get("/v1/hotel/entitlements", headers=hotel.admin.headers)).json()
    assert before["flags"]["QR_ORDERING"] is False and before["limits"]["MAX_OUTLETS"] == 1

    r = await client.put(
        f"/v1/platform/hotels/{hotel.id}/subscription",
        headers=platform_admin.headers,
        json={"plan_code": "pro", "billing_interval": "annual"},
    )
    assert r.json()["subscription"]["price_minor"] == 4_999_000
    after = (await client.get("/v1/hotel/entitlements", headers=hotel.admin.headers)).json()
    assert after["plan_code"] == "pro" and after["version"] > before["version"]
    assert after["flags"]["QR_ORDERING"] is True and after["limits"]["MAX_OUTLETS"] is None


async def test_override_beats_plan_and_can_be_removed(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin, plan="starter")
    r = await client.put(
        f"/v1/platform/hotels/{hotel.id}/overrides/QR_ORDERING",
        headers=platform_admin.headers,
        json={"value": True, "reason": "pilot customer"},
    )
    assert r.json()["flags"]["QR_ORDERING"] is True
    assert r.json()["sources"]["QR_ORDERING"] == "override"
    ent = (await client.get("/v1/hotel/entitlements", headers=hotel.admin.headers)).json()
    assert ent["flags"]["QR_ORDERING"] is True
    r = await client.delete(
        f"/v1/platform/hotels/{hotel.id}/overrides/QR_ORDERING", headers=platform_admin.headers
    )
    assert r.json()["flags"]["QR_ORDERING"] is False
    ent = (await client.get("/v1/hotel/entitlements", headers=hotel.admin.headers)).json()
    assert ent["flags"]["QR_ORDERING"] is False


async def test_suspended_hotel_is_read_only(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    hotel = await onboard(client, platform_admin)
    r = await client.post(
        f"/v1/platform/hotels/{hotel.id}/suspend",
        headers=platform_admin.headers,
        json={"reason": "payment overdue"},
    )
    assert r.json()["status"] == "suspended"
    assert (await client.get("/v1/outlets", headers=hotel.admin.headers)).status_code == 200
    w = await client.post(
        "/v1/outlets", headers=hotel.admin.headers, json={"name": "Bar", "code": "BAR"}
    )
    assert w.status_code == 403 and w.json()["code"] == "HOTEL_SUSPENDED"
    await client.post(f"/v1/platform/hotels/{hotel.id}/resume", headers=platform_admin.headers)
    w = await client.post(
        "/v1/outlets", headers=hotel.admin.headers, json={"name": "Bar", "code": "BAR"}
    )
    assert w.status_code == 201


async def test_hotel_list_pages_with_cursor(
    client: httpx.AsyncClient, platform_admin: Session
) -> None:
    for _ in range(3):
        await onboard(client, platform_admin)
    first = (
        await client.get("/v1/platform/hotels", headers=platform_admin.headers, params={"limit": 2})
    ).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = (
        await client.get(
            "/v1/platform/hotels",
            headers=platform_admin.headers,
            params={"limit": 2, "cursor": first["next_cursor"]},
        )
    ).json()
    assert {h["id"] for h in first["items"]}.isdisjoint({h["id"] for h in second["items"]})
    audit = (await client.get("/v1/platform/audit", headers=platform_admin.headers)).json()
    assert audit["items"][0]["action"] == "hotel.onboarded"
