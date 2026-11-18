"""Pure-module tests: routing worked examples (docs/04), pricing, entitlements, lifecycle, GSTIN."""

from datetime import UTC, date, datetime, timedelta

from app.kitchen.routing import ChefState, RoutingConfig, Snapshot, assign, assign_many
from app.ordering.pricing import PriceLine, TaxRuleRow, bp_of, price, tax_rate_for
from app.platform.entitlements import FeatureValue, resolve
from app.platform.gstin import checksum_char, is_valid_gstin
from app.platform.lifecycle import effective_status

CFG = RoutingConfig(max_queue_orders=3, rating_band_minutes=5)


def at(h: int, m: int) -> datetime:
    return datetime(2026, 11, 18, h, m, tzinfo=UTC)


def chef(cid: str, *minutes: int, last=None, rating=None, n=0, on_shift=True) -> ChefState:
    return ChefState(
        cid, on_shift=on_shift, queue_minutes=tuple(minutes), last_completed_at=last, rating=rating, rating_n=n
    )


# --- docs/04 worked examples ------------------------------------------------------


def test_ex2_idle_chef_wins():
    snap = Snapshot([chef("ganesh"), chef("ravi", 15), chef("sita", 20)])
    assert assign(snap, CFG) == "ganesh"


def test_ex3_full_queue_skipped():
    snap = Snapshot([chef("ganesh", 1, 1, 1), chef("ravi", 15), chef("sita", 20)])
    assert assign(snap, CFG) == "ravi"


def test_ex4_tie_longest_idle():
    snap = Snapshot([chef("ravi", 20, last=at(12, 1)), chef("sita", 20, last=at(12, 4))])
    assert assign(snap, CFG) == "ravi"


def test_ex5_rating_breaks_near_tie_only():
    weighted = RoutingConfig(3, 5, rating_weighted=True)
    near = Snapshot([chef("ravi", 20, rating=3.9, n=30), chef("sita", 24, rating=4.8, n=30)])
    assert assign(near, weighted) == "sita"
    far = Snapshot([chef("ravi", 20, rating=3.9, n=30), chef("sita", 30, rating=4.8, n=30)])
    assert assign(far, weighted) == "ravi"


def test_ex6_rating_ignored_below_sample_size():
    weighted = RoutingConfig(3, 5, rating_weighted=True)
    snap = Snapshot(
        [chef("ravi", 20, last=at(12, 1), rating=3.0, n=1), chef("sita", 20, last=at(12, 4), rating=5.0, n=1)]
    )
    assert assign(snap, weighted) == "ravi"  # falls back to idle time


def test_ex7_all_full_waits():
    snap = Snapshot([chef(c, 5, 5, 5) for c in ("ganesh", "ravi", "sita")])
    assert assign(snap, CFG) is None


def test_ex9_timeout_excludes_chef_once():
    snap = Snapshot([chef("ravi"), chef("sita", 10)])
    assert assign(snap, CFG, exclude=frozenset({"ravi"})) == "sita"


def test_ex11_off_shift_redistributes_in_order():
    snap = Snapshot([chef("ganesh", 10), chef("ravi", 12), chef("sita", on_shift=False)])
    out = assign_many(snap, [("A", 8), ("B", 8)], CFG)
    assert out == [("A", "ganesh"), ("B", "ravi")]


def test_ex13_concurrent_orders_see_each_other():
    snap = Snapshot([chef("ganesh"), chef("ravi", 15)])
    out = assign_many(snap, [("A", 20), ("B", 10)], CFG)
    assert out == [("A", "ganesh"), ("B", "ravi")]


def test_idle_chef_beats_slightly_busier_chef_without_ratings():
    snap = Snapshot([chef("ravi", 3, last=at(9, 0)), chef("sita", last=at(12, 0))])
    assert assign(snap, CFG) == "sita"


def test_off_shift_and_inactive_never_chosen():
    snap = Snapshot([chef("ravi", on_shift=False), ChefState("sita", active=False), chef("gita", 30)])
    assert assign(snap, CFG) == "gita"


# --- pricing ------------------------------------------------------------------------

RULES = [TaxRuleRow("FOOD", 1800, date(2010, 1, 1), date(2017, 6, 30)), TaxRuleRow("FOOD", 500, date(2017, 7, 1), None)]


def test_bp_rounds_half_up_integer():
    assert bp_of(28000, 500) == 1400
    assert bp_of(333, 500) == 17  # 16.65 -> 17
    assert bp_of(0, 500) == 0


def test_effective_dated_tax():
    assert tax_rate_for("FOOD", date(2026, 1, 1), RULES, "REGULAR") == 500
    assert tax_rate_for("FOOD", date(2016, 1, 1), RULES, "REGULAR") == 1800
    assert tax_rate_for("FOOD", date(2026, 1, 1), RULES, "COMPOSITION") == 0
    assert tax_rate_for("BAR", date(2026, 1, 1), RULES, "REGULAR") == 0


def test_price_totals():
    t = price([PriceLine("a", "Biryani", 2, 28000, 500), PriceLine("b", "Naan", 3, 6000, 500)], service_charge_bp=1000)
    assert t.subtotal_minor == 74000
    assert t.tax_minor == 2800 + 900
    assert t.service_charge_minor == 7400
    assert t.total_minor == 74000 + 3700 + 7400


# --- entitlements and lifecycle ------------------------------------------------------


def test_override_beats_plan_and_suspension_blocks_everything():
    plan = {"QR_ORDERING": FeatureValue(False), "MAX_TABLES": FeatureValue(int_limit=10)}
    ovr = {"QR_ORDERING": FeatureValue(True)}
    r = resolve(plan, ovr, "ACTIVE")
    assert r["features"]["QR_ORDERING"] is True and r["limits"]["MAX_TABLES"] == 10
    assert r["limits"]["MAX_STAFF"] == 0  # absent from plan => not allowed
    s = resolve(plan, ovr, "SUSPENDED")
    assert not any(s["features"].values()) and s["read_only"]


def test_lifecycle():
    now = at(12, 0)
    assert effective_status("TRIAL", now, now + timedelta(days=1), None, 7) == "TRIAL"
    assert effective_status("TRIAL", now, now - timedelta(days=1), None, 7) == "PAST_DUE"
    assert effective_status("TRIAL", now, now - timedelta(days=8), None, 7) == "SUSPENDED"
    assert effective_status("ACTIVE", now, None, now + timedelta(days=3), 7) == "ACTIVE"
    assert effective_status("ACTIVE", now, None, now - timedelta(days=3), 7) == "PAST_DUE"
    assert effective_status("PAST_DUE", now, None, now - timedelta(days=30), 7) == "SUSPENDED"


def test_gstin_checksum():
    base = "36ABCDE1234F1Z"
    good = base + checksum_char(base)
    assert is_valid_gstin(good)
    bad_char = "0" if good[-1] != "0" else "1"
    assert not is_valid_gstin(good[:-1] + bad_char)
    assert not is_valid_gstin("NOT-A-GSTIN")
