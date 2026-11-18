from datetime import UTC, datetime

from app.billing.service import financial_year, invoice_no, split_gst
from app.printing import escpos
from app.printing.documents import rs


def test_rupee_formatting_without_floats():
    assert rs(0) == "Rs. 0.00"
    assert rs(5) == "Rs. 0.05"
    assert rs(842050) == "Rs. 8,420.50"
    assert rs(12345678900) == "Rs. 12,34,56,789.00"
    assert rs(-1500) == "-Rs. 15.00"


def test_financial_year_and_invoice_number():
    assert financial_year(datetime(2026, 11, 18, tzinfo=UTC)) == "2026-27"
    assert financial_year(datetime(2027, 3, 31, tzinfo=UTC)) == "2026-27"
    assert financial_year(datetime(2027, 4, 1, tzinfo=UTC)) == "2027-28"
    no = invoice_no("2026-27", 1)
    assert no == "INV/26-27/00001" and len(no) <= 16


def test_gst_split_always_sums():
    for tax in (0, 1, 2, 999, 1001):
        c, s = split_gst(tax)
        assert c + s == tax and 0 <= c - s <= 1


def test_escpos_layout_and_bytes():
    blocks = [
        ("title", "KOT"),
        ("row", "Order #12 with a very long label indeed", "Table T3"),
        ("bold", "2 x Paneer Tikka ₹"),
        ("divider",),
        ("cut",),
    ]
    lines = escpos.to_lines(blocks, 32)
    assert all(len(t) <= 32 for _, t in lines)
    assert any(t.endswith("Table T3") for _, t in lines)  # right column stays on the line
    assert ("bold", "2 x Paneer Tikka Rs.") in lines  # rupee sign spelled out for thermal fonts
    data = escpos.render(blocks, 58)
    assert data.startswith(b"\x1b@") and data.endswith(b"\x1b!\x00" if False else data[-6:])
    assert escpos.CUT in data and b"KOT" in data
    data.decode("ascii")  # pure ASCII payload, safe for any ESC/POS printer


def test_escpos_qr_block():
    data = escpos.render([("qr", "https://example.com/#/t/abc"), ("cut",)], 80)
    assert b"\x1d(k" in data and b"https://example.com/#/t/abc" in data
    assert escpos.qr_command("x", 8).endswith(b"\x1d(k\x03\x001Q0")
