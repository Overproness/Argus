from decimal import Decimal


def invoice_total(lines):
    return sum((Decimal(price) * quantity for price, quantity in lines), start=Decimal("0"))
