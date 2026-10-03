import re

from . import settings

COUPON_FORMAT = re.compile(r"^([A-Z0-9]+-?)+$")


def line_total(unit_price: float, qty: int) -> float:
    return unit_price * qty


def order_total(lines: list[dict], discount_pct: float = 0.0) -> float:
    subtotal = sum(line_total(l["unit_price"], l["qty"]) for l in lines)
    return round(subtotal * (1 - discount_pct / 100), 2)


def coupon_discount(code: str | None) -> float:
    if not code:
        return 0.0
    if not COUPON_FORMAT.match(code):
        raise ValueError("malformed coupon code")
    return settings.COUPONS.get(code, 0.0)
