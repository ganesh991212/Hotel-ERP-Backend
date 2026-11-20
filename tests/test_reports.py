"""Sales reports (docs/14 "Reports", docs/13 "Reports"): one source of truth, a ladder that always reconciles."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app.core.db import tenant_session
from app.platform.gstin import checksum_char
from app.printing.documents import rs
from app.reports.router import rupees
from tests.conftest import h, make_hotel

API = "/api/v1"


async def _order(client, hp, items: dict[str, int], *, takeaway=False, serve=True) -> dict:
    body: dict = {"items": [{"item_id": hp["items"][n], "qty": q} for n, q in items.items()]}
    body |= {"takeaway": True} if takeaway else {"table_id": hp["table"]["id"]}
    o = (await client.post(f"{API}/orders", headers=h(hp["Kiran"]), json=body)).json()
    if serve:
        for step in ("accept", "ready"):
            await client.post(f"{API}/orders/{o['id']}/{step}", headers=h(hp["Ravi"]))
        await client.post(f"{API}/orders/{o['id']}/serve", headers=h(hp["Kiran"]))
    return o


@pytest.fixture(scope="module")
async def day(client, root):
    """A GST hotel's day: a discounted table bill paid in cash + UPI and replaced once when more food came,
    a takeaway bill cancelled by hand, and a takeaway order not billed yet."""
    g = "36ABCDE1234F1Z" + checksum_char("36ABCDE1234F1Z")
    hp = await make_hotel(client, root, plan="PRO", gst_registration="REGULAR", gstin=g)
    await _order(client, hp, {"Biryani": 2, "Naan": 1})
    disc = {"discount": {"percent_bp": 1000, "reason": "Regular guest"}}
    b1 = (
        await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"], **disc})
    ).json()
    await client.post(
        f"{API}/bills/{b1['id']}/payments", headers=h(hp["admin"]), json={"method": "CASH", "amount_minor": 10_000}
    )
    await _order(client, hp, {"Chai": 1})
    r = await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"table_id": hp["table"]["id"], **disc})
    assert r.status_code == 201, r.text
    b2 = r.json()
    assert b2["replaced_invoice_nos"] == [b1["invoice_no"]] and b2["discount_minor"] > 0
    paid = await client.post(
        f"{API}/bills/{b2['id']}/payments",
        headers=h(hp["admin"]),
        json={"method": "UPI", "amount_minor": b2["due_minor"]},
    )
    assert paid.json()["status"] == "PAID"

    t = await _order(client, hp, {"Naan": 2}, takeaway=True)
    b3 = (await client.post(f"{API}/bills", headers=h(hp["admin"]), json={"order_id": t["id"]})).json()
    v = await client.post(f"{API}/bills/{b3['id']}/void", headers=h(hp["admin"]), json={"reason": "Guest left"})
    assert v.status_code == 200, v.text
    unbilled = await _order(client, hp, {"Chai": 1}, takeaway=True, serve=False)
    bill = (await client.get(f"{API}/bills/{b2['id']}", headers=h(hp["admin"]))).json()
    return {"hp": hp, "bill": bill, "cancelled": b3, "unbilled": unbilled}


def _get(client, hp, path, who="admin", **params):
    return client.get(f"{API}/reports/{path}", headers=h(hp[who]), params=params)


async def test_sales_ladder_reconciles_and_keeps_states_apart(client, day):
    hp, b = day["hp"], day["bill"]
    r = await _get(client, hp, "sales")
    assert r.status_code == 200, r.text
    d = r.json()
    assert len(d["days"]) == 1 and d["timezone"] == "Asia/Kolkata" and "cancelled" in d["basis"]
    t = d["totals"]
    # Only the live bill is sales; the replaced one and the hand-cancelled one are kept apart.
    assert t["bills"] == 1 and t["replaced_bills"] == 1
    assert t["cancelled_bills"] == 1 and t["cancelled_minor"] == day["cancelled"]["total_minor"]
    assert t["gross_minor"] == b["subtotal_minor"] and t["discount_minor"] == b["discount_minor"]
    assert t["taxable_minor"] == b["taxable_minor"] and t["cgst_minor"] == b["cgst_minor"] > 0
    assert t["total_minor"] == b["total_minor"] and t["round_off_minor"] == 0
    ladder = t["taxable_minor"] + t["cgst_minor"] + t["sgst_minor"] + t["other_tax_minor"]
    assert ladder + t["service_charge_minor"] + t["round_off_minor"] == t["total_minor"]
    assert t["paid_minor"] == b["total_minor"] and t["unpaid_minor"] == 0
    # Money received, by method; the cash taken on the replaced bill is not lost.
    assert t["received"] == {"cash_minor": 10_000, "upi_minor": b["total_minor"] - 10_000, "card_minor": 0}
    assert t["received_minor"] == b["total_minor"]
    # Unbilled warning: the order not billed yet, and the takeaway whose bill was cancelled (food went out, no bill).
    expected = day["unbilled"]["total_minor"] + day["cancelled"]["total_minor"]
    assert d["unbilled"] == {"orders": 2, "value_minor": expected}


async def test_a_range_has_one_row_per_day_and_the_same_totals(client, day):
    hp = day["hp"]
    today = date.fromisoformat((await _get(client, hp, "sales")).json()["from"])
    d = (await _get(client, hp, "sales", **{"from": str(today - timedelta(days=2)), "to": str(today)})).json()
    assert [x["date"] for x in d["days"]] == [str(today - timedelta(days=i)) for i in (2, 1, 0)]
    assert d["days"][0]["bills"] == 0 and d["totals"]["total_minor"] == day["bill"]["total_minor"]
    too_long = await _get(client, hp, "sales", **{"from": str(today - timedelta(days=100)), "to": str(today)})
    assert too_long.status_code == 400 and too_long.json()["code"] == "range_too_long"
    backwards = await _get(client, hp, "sales", **{"from": str(today), "to": str(today - timedelta(days=1))})
    assert backwards.json()["code"] == "bad_range"


async def test_items_add_up_to_the_bill(client, day):
    hp, b = day["hp"], day["bill"]
    d = (await _get(client, hp, "items")).json()
    qty = {i["name"]: i["qty"] for i in d["items"]}
    assert qty == {"Biryani": 2, "Naan": 1, "Chai": 1}, "the cancelled takeaway Naan is not sales"
    assert d["items"][0]["name"] == "Biryani", "biggest seller first"
    assert d["totals"]["gross_minor"] == b["subtotal_minor"]
    assert d["totals"]["discount_minor"] == b["discount_minor"]


async def test_gst_summary_matches_the_printed_bills(client, day):
    hp, b = day["hp"], day["bill"]
    d = (await _get(client, hp, "gst")).json()
    assert d["sac"] == "996331"
    assert d["totals"]["taxable_minor"] == b["taxable_minor"]
    assert d["totals"]["cgst_minor"] == b["cgst_minor"] and d["totals"]["sgst_minor"] == b["sgst_minor"]
    printed = {g["rate_bp"]: g["tax_minor"] for g in b["tax_groups"]}
    assert {r["rate_bp"]: r["tax_minor"] for r in d["by_rate"]} == printed
    [docs] = d["documents"]
    assert docs["doc_type"] == "TAX_INVOICE" and docs["count"] == 3 and docs["cancelled"] == 2 and docs["net"] == 1


async def test_staff_report_needs_the_plan_and_the_permission(client, root, day):
    hp, b = day["hp"], day["bill"]
    d = (await _get(client, hp, "staff")).json()
    me = next(s for s in d["staff"] if s["name"] == "Admin")
    assert me["bills"] == 1 and me["billed_minor"] == b["total_minor"]
    assert me["discounts"] == 1 and me["discount_minor"] == b["discount_minor"]
    assert me["received_minor"] == b["total_minor"] and me["cancellations"] == 1
    # A server cannot see sales reports.
    r = await _get(client, hp, "sales", who="Kiran")
    assert r.status_code == 403 and r.json()["code"] == "permission_denied"
    # Growth plan: day sales yes, staff report no (entitlement checked on the server).
    growth = await make_hotel(client, root)
    assert (await _get(client, growth, "sales")).status_code == 200
    locked = await _get(client, growth, "staff")
    assert locked.status_code == 403 and locked.json()["code"] == "feature_locked"
    assert (await _get(client, growth, "staff.csv")).status_code == 403


async def test_categories_add_up_and_keep_what_was_billed(client, day):
    hp = day["hp"]
    d = (await _get(client, hp, "items")).json()
    assert {i["category"] for i in d["items"]} == {"Mains"}
    [mains] = d["categories"]
    assert mains["category"] == "Mains" and mains["items"] == 3 and mains["share_pct"] == 100
    for k in ("qty", "gross_minor", "discount_minor", "taxable_minor"):
        assert mains[k] == d["totals"][k], "categories add up to the item totals"
    # Moving Chai to a new category later does not rewrite past sales (snapshot, docs/14).
    a = h(hp["admin"])
    drinks = (await client.post(f"{API}/categories", headers=a, json={"name": "Drinks"})).json()
    r = await client.patch(f"{API}/items/{hp['items']['Chai']}", headers=a, json={"category_id": drinks["id"]})
    assert r.status_code == 200, r.text
    assert (await _get(client, hp, "items")).json()["categories"] == d["categories"]
    csv = (await _get(client, hp, "categories.csv")).text
    assert "Category,Items,Qty" in csv and any(line.startswith("Mains,3,") for line in csv.splitlines())


async def test_csv_exports_match_the_screen(client, day):
    hp, b = day["hp"], day["bill"]
    r = await _get(client, hp, "sales.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text
    total_row = next(line for line in body.splitlines() if line.startswith("Total,"))
    assert rupees(b["total_minor"]) in total_row
    reg = (await _get(client, hp, "register.csv")).text.splitlines()
    assert len([line for line in reg if ",VOID," in line]) == 2 and any(",PAID," in line for line in reg)
    assert "Guest left" in "\n".join(reg)
    assert sum(1 for line in reg if line.startswith(b["invoice_no"][:3])) == 3, "every bill, cancelled ones too"
    for kind in ("items", "gst", "staff"):
        assert (await _get(client, hp, f"{kind}.csv")).status_code == 200
    assert (await _get(client, hp, "nope.csv")).json()["code"] == "unknown_report"


def test_rupees_text_never_uses_floats():
    assert rupees(123_456) == "1234.56" and rupees(5) == "0.05" and rupees(-250) == "-2.50" and rupees(0) == "0.00"


async def test_reports_are_isolated_per_hotel(client, root, day):
    other = await make_hotel(client, root, plan="PRO")
    d = (await _get(client, other, "sales")).json()
    assert d["totals"]["bills"] == 0 and d["totals"]["received_minor"] == 0 and d["unbilled"]["orders"] == 0
    assert (await _get(client, other, "staff")).json()["staff"] == []
    async with tenant_session(uuid.UUID(other["hotel"]["id"])) as db:
        assert (await db.execute(text("select count(*) from bill_lines"))).scalar() == 0


async def test_print_blocks_carry_the_same_figures_as_the_screen(client, day):
    hp, b = day["hp"], day["bill"]
    for kind in ("sales", "items", "categories", "gst", "pnl", "staff"):
        r = await _get(client, hp, f"{kind}.print")
        assert r.status_code == 200, (kind, r.text)
        blocks = r.json()["blocks"]
        kinds = {x[0] for x in blocks}
        assert kinds <= {"title", "center", "small", "text", "bold", "row", "total", "divider"}, kind
        text = " ".join(" ".join(str(v) for v in x[1:]) for x in blocks)
        assert "Generated" in text and day["hp"]["hotel"]["name"] in text, "every page says what and when"
        if kind == "sales":
            assert rs(b["total_minor"]) in text and "Cancelled" in text
        if kind == "gst":
            assert "Documents issued" in text
        if kind == "pnl":
            assert "not income" in text and "(so far)" in text
    assert (await _get(client, hp, "nope.print")).json()["code"] == "unknown_report"
    assert (await _get(client, hp, "sales.print", who="Kiran")).status_code == 403
