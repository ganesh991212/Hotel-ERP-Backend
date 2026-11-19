"""Discount maths (pure): shares add up exactly, GST on the discounted value, limits round against the giver."""

from app.ordering.pricing import apply_discount, bp_of, discount_bp_of, discount_minor_for


def test_shares_add_up_to_the_paisa_and_follow_line_value():
    lines = [(33_333, 500), (33_333, 500), (33_334, 1800)]
    d = apply_discount(lines, 10_001, 0)
    assert sum(d.line_discounts) == 10_001
    assert max(d.line_discounts) - min(d.line_discounts) <= 1, "equal lines get equal shares (to a paisa)"


def test_gst_is_charged_on_the_discounted_value():
    # Rs 1,000 food at 5% and Rs 500 drink at 18%; 10% off the bill.
    lines = [(100_000, 500), (50_000, 1800)]
    d = apply_discount(lines, discount_minor_for(150_000, percent_bp=1000, amount_minor=None), 0)
    assert d.discount_minor == 15_000 and d.line_discounts == [10_000, 5_000]
    assert d.tax_minor == bp_of(90_000, 500) + bp_of(45_000, 1800)  # 4,500 + 8,100
    assert d.total_minor == 150_000 - 15_000 + d.tax_minor


def test_no_discount_changes_nothing_and_service_charge_follows_the_discount():
    lines = [(100_000, 500)]
    plain = apply_discount(lines, 0, 1000)
    assert plain.tax_minor == 5_000 and plain.service_charge_minor == 10_000
    off = apply_discount(lines, 20_000, 1000)
    assert off.service_charge_minor == 8_000, "10% service on the discounted Rs 800"


def test_amount_is_capped_and_role_limits_round_up():
    assert discount_minor_for(10_000, percent_bp=None, amount_minor=99_999) == 10_000
    assert discount_minor_for(10_000, percent_bp=10_000, amount_minor=None) == 10_000
    # Rs 10.01 off Rs 100 is 10.01%: a 10% limit must refuse it, so the share rounds up.
    assert discount_bp_of(1_001, 10_000) == 1001
    assert discount_bp_of(1_000, 10_000) == 1000
    assert discount_bp_of(3_334, 33_333) == 1001, "rounds against the person giving the discount"
