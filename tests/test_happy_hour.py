"""Happy hour price rules (docs/03 "Happy hour", docs/14)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ordering.pricing import PriceRuleRow, after_rule, best_rule, bp_of, rule_applies
from tests.conftest import h, make_hotel

API = "/api/v1"
MON, TUE, FRI, SAT, SUN = 1, 2, 16, 32, 64


def _rule(start, end, days=127, cats=None, pct=2000, name="Happy hour"):
    return PriceRuleRow(name, pct, days, time(*start), time(*end), cats)


def test_window_and_days():
    r = _rule((17, 0), (19, 0), days=FRI | SAT)
    fri = datetime(2026, 11, 20, 18, 30)  # a Friday
    assert rule_applies(r, fri, "c") and not rule_applies(r, fri.replace(hour=19), "c"), "end is exclusive"
    assert not rule_applies(r, datetime(2026, 11, 19, 18, 0), "c"), "Thursday is not in the rule"


def test_window_crossing_midnight_belongs_to_the_day_it_started():
    late = _rule((22, 0), (1, 0), days=SAT)
    assert rule_applies(late, datetime(2026, 11, 21, 23, 0), "c")  # Saturday night
    assert rule_applies(late, datetime(2026, 11, 22, 0, 30), "c"), "Sunday 00:30 still belongs to Saturday"
    assert not rule_applies(late, datetime(2026, 11, 22, 23, 0), "c"), "Sunday night is not in the rule"


def test_categories_and_best_rule_never_stack():
    bar = _rule((0, 0), (23, 59), cats=frozenset({"bar"}), pct=3000, name="Bar")
    all_ = _rule((0, 0), (23, 59), pct=1000, name="All")
    at = datetime(2026, 11, 20, 12, 0)
    assert best_rule([bar, all_], at, "bar").name == "Bar"
    assert best_rule([bar, all_], at, "mains").name == "All"
    assert after_rule(18000, bar) == 12600 and after_rule(18000, None) == 18000


@pytest.fixture
async def hh(client, root):
    hp = await make_hotel(client, root, plan="PRO")
    return hp


def _window_now(minutes=60):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    start = (now - timedelta(minutes=minutes)).strftime("%H:%M")
    end = (now + timedelta(minutes=minutes)).strftime("%H:%M")
    return start, end


async def _make_rule(client, hp, *, pct=2000, start=None, end=None, cats=None, active=True, who="admin"):
    s, e = _window_now()
    body = {
        "name": "Happy hour",
        "percent_bp": pct,
        "start_time": start or s,
        "end_time": end or e,
        "category_ids": cats,
        "is_active": active,
    }
    return await client.post(f"{API}/price-rules", headers=h(hp[who]), json=body)


async def test_every_channel_gets_the_price_and_the_bill_shows_it(client, hh):
    r = await _make_rule(client, hh)
    assert r.status_code == 201, r.text
    naan = hh["items"]["Naan"]
    items = (await client.get(f"{API}/items", headers=h(hh["Kiran"]))).json()
    assert next(i for i in items if i["id"] == naan)["offer"]["percent_bp"] == 2000, "a badge, not a price"

    o = (
        await client.post(
            f"{API}/orders",
            headers=h(hh["Kiran"]),
            json={"table_id": hh["table"]["id"], "items": [{"item_id": naan, "qty": 2}]},
        )
    ).json()
    line = o["items"][0]
    assert line["unit_price_minor"] == 6000 - bp_of(6000, 2000) == 4800
    assert line["list_unit_price_minor"] == 6000 and line["price_rule"] == "Happy hour"
    # The QR diner gets the same price for the same dish (one rule for every channel).
    qr = hh["table"]["qr_token"]
    q = (await client.post(f"{API}/d/{qr}/quote", json={"items": [{"item_id": naan, "qty": 2}]})).json()
    assert q["subtotal_minor"] == o["subtotal_minor"] == 9600
    menu = (await client.get(f"{API}/d/{qr}")).json()
    assert next(i for c in menu["categories"] for i in c["items"] if i["id"] == naan)["offer"]["name"] == "Happy hour"

    # The bill line says it was happy hour; the item report keeps it as its own row with the real amount.
    for step in ("accept", "ready"):
        await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hh["Ravi"]))
    await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hh["Kiran"]))
    bill = (await client.post(f"{API}/bills", headers=h(hh["admin"]), json={"table_id": hh["table"]["id"]})).json()
    assert [ln["name"] for ln in bill["lines"]] == ["Naan (Happy hour)"]
    rep = (await client.get(f"{API}/reports/items", headers=h(hh["admin"]))).json()
    assert {i["name"]: i["gross_minor"] for i in rep["items"]} == {"Naan (Happy hour)": 9600}


async def test_size_price_is_discounted_but_add_ons_are_not(client, hh):
    a = h(hh["admin"])
    bid = hh["items"]["Biryani"]
    sizes = (
        await client.put(
            f"{API}/items/{bid}/variants",
            headers=a,
            json={"variants": [{"name": "Half", "price_minor": 18000}, {"name": "Full", "price_minor": 28000}]},
        )
    ).json()
    g = (
        await client.post(
            f"{API}/addon-groups",
            headers=a,
            json={"name": "Extras", "max_select": 2, "addons": [{"name": "Raita", "price_minor": 3000}]},
        )
    ).json()
    await client.put(f"{API}/items/{bid}/addon-groups", headers=a, json={"group_ids": [g["id"]]})
    await _make_rule(client, hh, pct=5000)
    line = {"item_id": bid, "qty": 1, "variant_id": sizes[0]["id"], "addons": [{"addon_id": g["addons"][0]["id"]}]}
    q = (await client.post(f"{API}/orders/quote", headers=a, json={"items": [line]})).json()
    assert q["lines"][0]["unit_price_minor"] == 9000 + 3000, "half price on the size, raita at full price"


async def test_rules_outside_their_window_or_switched_off_do_nothing(client, hh):
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    later = (now + timedelta(hours=3)).strftime("%H:%M"), (now + timedelta(hours=4)).strftime("%H:%M")
    await _make_rule(client, hh, start=later[0], end=later[1])
    await _make_rule(client, hh, active=False)
    other_cat = (await client.post(f"{API}/categories", headers=h(hh["admin"]), json={"name": "Bar"})).json()
    await _make_rule(client, hh, cats=[other_cat["id"]])
    q = (
        await client.post(
            f"{API}/orders/quote", headers=h(hh["admin"]), json={"items": [{"item_id": hh["items"]["Naan"], "qty": 1}]}
        )
    ).json()
    assert q["lines"][0]["unit_price_minor"] == 6000 and q["lines"][0]["price_rule"] is None


async def test_only_menu_managers_change_rules_and_hotels_are_apart(client, root, hh):
    assert (await _make_rule(client, hh, who="Kiran")).status_code == 403
    bad = await client.post(
        f"{API}/price-rules",
        headers=h(hh["admin"]),
        json={"name": "X", "percent_bp": 1000, "start_time": "17:00", "end_time": "17:00"},
    )
    assert bad.status_code == 422
    r = (await _make_rule(client, hh)).json()
    other = await make_hotel(client, root)
    assert (await client.get(f"{API}/price-rules", headers=h(other["admin"]))).json() == []
    body = {"name": "Mine", "percent_bp": 9000, "start_time": "00:00", "end_time": "23:59"}
    assert (await client.put(f"{API}/price-rules/{r['id']}", headers=h(other["admin"]), json=body)).status_code == 404
    # Deleting a rule never changes orders already placed.
    assert (await client.delete(f"{API}/price-rules/{r['id']}", headers=h(hh["admin"]))).status_code == 204
