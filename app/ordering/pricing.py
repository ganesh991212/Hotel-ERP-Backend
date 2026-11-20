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
class AddonPick:
    """One chosen add-on at its price of the moment; `qty` picks of it on each unit of the dish."""

    id: str
    name: str
    price_minor: int
    qty: int = 1


@dataclass(frozen=True)
class PriceLine:
    item_id: str
    name: str
    qty: int
    unit_price_minor: int  # dish (or variant) + add-ons, per unit
    tax_rate_bp: int
    variant_id: str | None = None
    variant_name: str | None = None
    addons: tuple[AddonPick, ...] = ()


@dataclass(frozen=True)
class PricedLine:
    item_id: str
    name: str
    qty: int
    unit_price_minor: int
    tax_rate_bp: int
    line_total_minor: int
    tax_minor: int
    variant_id: str | None = None
    variant_name: str | None = None
    addons: tuple[AddonPick, ...] = ()


def unit_price(base_minor: int, addons: tuple[AddonPick, ...]) -> int:
    """A unit of the dish with its add-ons. Add-ons served with a dish take the dish's GST rate (composite
    supply, docs/14), so they are simply part of the unit price."""
    return base_minor + sum(a.price_minor * a.qty for a in addons)


def addons_label(addons) -> str:
    """'Raita, 2 x Extra cheese' for bills and lists (dicts from a snapshot or AddonPick)."""

    def part(a) -> str:
        name, qty = (a["name"], a.get("qty", 1)) if isinstance(a, dict) else (a.name, a.qty)
        return f"{qty} x {name}" if qty > 1 else name

    return ", ".join(part(a) for a in addons or [])


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
                variant_id=ln.variant_id,
                variant_name=ln.variant_name,
                addons=ln.addons,
            )
        )
    subtotal = sum(p.line_total_minor for p in priced)
    tax = sum(p.tax_minor for p in priced)
    service = bp_of(subtotal, service_charge_bp)
    return Totals(priced, subtotal, tax, service, subtotal + tax + service)


@dataclass(frozen=True)
class Discounted:
    """A bill after a discount: per-line share of the discount, and the recomputed money."""

    line_discounts: list[int]
    discount_minor: int
    tax_minor: int
    service_charge_minor: int
    total_minor: int


def discount_minor_for(subtotal_minor: int, *, percent_bp: int | None, amount_minor: int | None) -> int:
    """Rupee value of a discount given as basis points (1000 = 10%) or a fixed amount, capped at the subtotal."""
    d = bp_of(subtotal_minor, percent_bp) if percent_bp is not None else (amount_minor or 0)
    return max(0, min(d, subtotal_minor))


def discount_bp_of(discount_minor: int, subtotal_minor: int) -> int:
    """The discount as basis points of the subtotal, rounded UP so a role limit can never be beaten by rounding."""
    if subtotal_minor <= 0:
        return 0
    return -(-discount_minor * 10_000 // subtotal_minor)


def apply_discount(lines: list[tuple[int, int]], discount_minor: int, service_charge_bp: int) -> Discounted:
    """`lines` = (line_total_minor, tax_rate_bp). The discount is shared across lines in proportion to their value
    (largest-remainder, so the shares add up to the paisa). GST is charged on each line's discounted value: a discount
    shown on the invoice reduces the taxable value. Service charge applies to the discounted subtotal."""
    subtotal = sum(t for t, _ in lines)
    discount_minor = max(0, min(discount_minor, subtotal))
    if subtotal == 0 or discount_minor == 0:
        shares = [0] * len(lines)
    else:
        exact = [t * discount_minor for t, _ in lines]
        shares = [e // subtotal for e in exact]
        rest = discount_minor - sum(shares)
        # Hand the leftover paise to the lines with the largest remainders (ties: earlier lines first).
        order = sorted(range(len(lines)), key=lambda i: (-(exact[i] % subtotal), i))
        for i in order[:rest]:
            shares[i] += 1
    tax = sum(bp_of(t - s, rate) for (t, rate), s in zip(lines, shares, strict=True))
    taxable = subtotal - discount_minor
    service = bp_of(taxable, service_charge_bp)
    return Discounted(shares, discount_minor, tax, service, taxable + tax + service)
