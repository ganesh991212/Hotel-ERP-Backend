"""Pure server-side pricing. Integer paise only; the client never computes money (rule 1, 4)."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class TaxRuleRow:
    code: str
    rate_bp: int
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class PriceLine:
    item_id: str
    name: str
    qty: int
    unit_price_minor: int
    tax_rate_bp: int


@dataclass(frozen=True)
class PricedLine:
    item_id: str
    name: str
    qty: int
    unit_price_minor: int
    tax_rate_bp: int
    line_total_minor: int
    tax_minor: int


@dataclass(frozen=True)
class Totals:
    lines: list[PricedLine]
    subtotal_minor: int
    tax_minor: int
    service_charge_minor: int
    total_minor: int


def bp_of(amount_minor: int, bp: int) -> int:
    """amount * bp / 10000 rounded half up, integer only."""
    return (amount_minor * bp + 5_000) // 10_000


def tax_rate_for(code: str, on: date, rules: list[TaxRuleRow], gst_registration: str) -> int:
    """Effective-dated lookup. Composition and unregistered hotels collect no GST (docs/03)."""
    if gst_registration != "REGULAR":
        return 0
    matching = [r for r in rules if r.code == code and r.valid_from <= on and (r.valid_to is None or on <= r.valid_to)]
    if not matching:
        return 0
    return max(matching, key=lambda r: r.valid_from).rate_bp


def price(lines: list[PriceLine], service_charge_bp: int = 0) -> Totals:
    priced = []
    for ln in lines:
        total = ln.unit_price_minor * ln.qty
        priced.append(
            PricedLine(
                item_id=ln.item_id,
                name=ln.name,
                qty=ln.qty,
                unit_price_minor=ln.unit_price_minor,
                tax_rate_bp=ln.tax_rate_bp,
                line_total_minor=total,
                tax_minor=bp_of(total, ln.tax_rate_bp),
            )
        )
    subtotal = sum(p.line_total_minor for p in priced)
    tax = sum(p.tax_minor for p in priced)
    service = bp_of(subtotal, service_charge_bp)
    return Totals(priced, subtotal, tax, service, subtotal + tax + service)
