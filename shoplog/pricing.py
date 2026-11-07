from __future__ import annotations

TIER_RATE_PCT = {"bronze": 0, "silver": 5, "gold": 10, "legacy_vip": 15}
COUPON_VALUES = {"WELCOME10": 1000}
FREE_SHIPPING_FROM = 7500
SHIPPING_FLAT = 795
TAX_BPS = 850


def price_order(lines: list[dict], tier: str, coupon: str | None = None) -> dict:
    subtotal = sum(line["qty"] * line["unit_price"] for line in lines)
    discount = subtotal * TIER_RATE_PCT[tier] // 100
    applied_coupon = coupon if coupon in COUPON_VALUES and tier != "legacy_vip" else None
    if applied_coupon:
        discount += COUPON_VALUES[applied_coupon]
    discount = min(discount, subtotal)
    merchandise = subtotal - discount
    shipping = 0 if merchandise >= FREE_SHIPPING_FROM else SHIPPING_FLAT
    tax = merchandise * TAX_BPS // 10000
    return {
        "subtotal": subtotal,
        "discount": discount,
        "shipping": shipping,
        "tax": tax,
        "total": merchandise + shipping + tax,
        "coupon_code": applied_coupon,
    }
