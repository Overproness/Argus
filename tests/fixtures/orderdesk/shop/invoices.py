from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def invoice_total(lines: list[dict], tax_rate: str = "0.08") -> Decimal:
    subtotal = sum(
        (Decimal(str(l["unit_price"])) * l["qty"] for l in lines),
        Decimal("0"),
    )
    tax = (subtotal * Decimal(tax_rate)).quantize(CENT, rounding=ROUND_HALF_UP)
    return (subtotal + tax).quantize(CENT, rounding=ROUND_HALF_UP)
