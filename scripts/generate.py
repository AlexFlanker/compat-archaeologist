"""Deterministic synthetic data. Same seed and parameters give byte-identical documents."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from shoplog.pricing import price_order

SEED = 20261107
EPOCH_START = datetime(2016, 1, 1, tzinfo=timezone.utc)
AS_OF = datetime(2026, 11, 1, tzinfo=timezone.utc)
CENTS_FROM = datetime(2018, 1, 1, tzinfo=timezone.utc)
TAX_FIELD_FROM = datetime(2021, 1, 1, tzinfo=timezone.utc)

CATEGORIES = ["audio", "home", "kitchen", "outdoor", "office", "clearance"]
TIERS = (["bronze"] * 60) + (["silver"] * 25) + (["gold"] * 12) + (["legacy_vip"] * 3)
ADJECTIVES = ["wireless", "compact", "ergonomic", "portable", "premium", "quiet", "smart", "classic", "rugged", "slim"]
NOUNS = ["headphones", "speaker", "lamp", "blender", "backpack", "keyboard", "kettle", "tent", "monitor", "chair"]
EXTRAS = ["noise cancelling", "refurbished", "energy saving", "travel ready", "gift edition", "water resistant"]


def customer_id(index: int) -> str:
    return f"C{index:06d}"


def order_id(index: int) -> str:
    return f"O{index:08d}"


def sku(index: int) -> str:
    return f"SKU-{index:04d}"


def generate_customers(count: int) -> list[dict]:
    rng = random.Random(SEED + 1)
    customers = []
    for index in range(1, count + 1):
        name = f"user{index}"
        email = f"{name}@example.com"
        if rng.random() < 0.2:
            email = email.capitalize()
        if rng.random() < 0.1:
            email = email.upper()
        customers.append(
            {
                "_id": customer_id(index),
                "email": email,
                "name": name.title(),
                "tier": rng.choice(TIERS),
                "signup_date": EPOCH_START + timedelta(days=rng.randrange(0, 3900)),
                "loyalty_points": rng.randrange(0, 5000),
            }
        )
    return customers


def generate_products(count: int) -> list[dict]:
    rng = random.Random(SEED + 2)
    products = []
    for index in range(1, count + 1):
        adjective, noun = rng.choice(ADJECTIVES), rng.choice(NOUNS)
        extras = rng.sample(EXTRAS, rng.randrange(0, 3))
        products.append(
            {
                "_id": sku(index),
                "name": f"{adjective.title()} {noun.title()} {index}",
                "description": f"A {adjective} {noun}. {' '.join(extras)}".strip(),
                "tags": [adjective, noun, *[extra.split()[0] for extra in extras]],
                "category": rng.choice(CATEGORIES),
                "price": rng.randrange(900, 25000, 100),
                "active": rng.random() > 0.05,
            }
        )
    return products


def generate_inventory(products: list[dict]) -> list[dict]:
    rng = random.Random(SEED + 3)
    return [
        {"_id": product["_id"], "on_hand": rng.randrange(0, 2000), "reserved": 0, "updated_at": AS_OF}
        for product in products
    ]


def generate_orders(count: int, customers: list[dict], products: list[dict]) -> list[dict]:
    rng = random.Random(SEED + 4)
    span_seconds = int((AS_OF - EPOCH_START).total_seconds())
    recent_seconds = int(timedelta(days=60).total_seconds())
    welcome_used: set[str] = set()
    orders = []
    for index in range(1, count + 1):
        customer = customers[rng.randrange(len(customers))]
        if rng.random() < 0.12:
            placed_at = AS_OF - timedelta(seconds=rng.randrange(0, recent_seconds))
        else:
            placed_at = EPOCH_START + timedelta(seconds=rng.randrange(0, span_seconds))
        lines = [_line(rng, products) for _ in range(rng.randrange(1, 5))]
        wants_coupon = customer["_id"] not in welcome_used and rng.random() < 0.05
        priced = price_order(lines, customer["tier"], "WELCOME10" if wants_coupon else None)
        if priced["coupon_code"]:
            welcome_used.add(customer["_id"])
        orders.append(_order_document(rng, order_id(index), customer, lines, priced, placed_at))
    return orders


def _line(rng: random.Random, products: list[dict]) -> dict:
    product = products[rng.randrange(len(products))]
    return {
        "sku": product["_id"],
        "category": product["category"],
        "qty": rng.randrange(1, 4),
        "unit_price": product["price"],
    }


def _order_document(rng, oid, customer, lines, priced, placed_at) -> dict:
    status, history, delivered_at = _lifecycle(rng, placed_at)
    items = [
        {
            "line_id": position + 1,
            "sku": line["sku"],
            "category": line["category"],
            "qty": line["qty"],
            "unit_price": line["unit_price"],
            "line_total": line["qty"] * line["unit_price"],
            "refund_status": "none",
            "refunded": 0,
        }
        for position, line in enumerate(lines)
    ]
    refund_total = 0
    if delivered_at and rng.random() < 0.08:
        refundable = [item for item in items if item["category"] != "clearance"]
        if refundable:
            chosen = refundable if rng.random() < 0.5 else [refundable[0]]
            for item in chosen:
                item["refund_status"] = "refunded"
            refund_total = sum(item["line_total"] for item in chosen)
            status = "refunded" if len(chosen) == len(refundable) else "partially_refunded"
            history.append({"status": status, "at": delivered_at + timedelta(days=rng.randrange(1, 25))})

    document = {
        "_id": oid,
        "customer_id": customer["_id"],
        "status": status,
        "items": items,
        "subtotal": priced["subtotal"],
        "discount": priced["discount"],
        "shipping": priced["shipping"],
        "total": priced["total"],
        "refund_total": refund_total,
        "placed_at": placed_at,
        "updated_at": history[-1]["at"],
        "history": history,
    }
    if priced["coupon_code"]:
        document["coupon_code"] = priced["coupon_code"]
    if delivered_at:
        document["delivered_at"] = delivered_at
    if placed_at >= TAX_FIELD_FROM:
        document["tax"] = priced["tax"]
    if placed_at < CENTS_FROM:
        _convert_to_float_dollars(document)
    return document


def _convert_to_float_dollars(document: dict) -> None:
    for field in ("subtotal", "discount", "shipping", "total", "refund_total"):
        document[field] = round(document[field] / 100 + 0.0, 2)
    for item in document["items"]:
        item["unit_price"] = round(item["unit_price"] / 100 + 0.0, 2)
        item["line_total"] = round(item["line_total"] / 100 + 0.0, 2)


def _lifecycle(rng: random.Random, placed_at: datetime) -> tuple[str, list[dict], datetime | None]:
    history = [{"status": "placed", "at": placed_at}]
    age_days = (AS_OF - placed_at).days
    if rng.random() < 0.05:
        cancelled_at = placed_at + timedelta(hours=rng.randrange(1, 48))
        return "cancelled", [*history, {"status": "cancelled", "at": cancelled_at}], None
    if age_days < 1:
        return "placed", history, None
    paid_at = placed_at + timedelta(minutes=rng.randrange(1, 300))
    history.append({"status": "paid", "at": paid_at})
    if age_days < 2:
        return "paid", history, None
    shipped_at = paid_at + timedelta(hours=rng.randrange(6, 48))
    history.append({"status": "shipped", "at": shipped_at})
    if age_days < 6:
        return "shipped", history, None
    delivered_at = shipped_at + timedelta(days=rng.randrange(1, 5))
    history.append({"status": "delivered", "at": delivered_at})
    return "delivered", history, delivered_at


def generate_carts(count: int, customers: list[dict], products: list[dict]) -> list[dict]:
    rng = random.Random(SEED + 5)
    # Fixed far-future expiry keeps seeded carts stable between runs; the workload creates short-lived ones.
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    carts = []
    for index in range(1, count + 1):
        customer = customers[rng.randrange(len(customers))]
        converted = rng.random() < 0.4
        carts.append(
            {
                "_id": f"K{index:07d}",
                "customer_id": customer["_id"],
                "items": [{"sku": _line(rng, products)["sku"], "qty": 1}],
                "status": "converted" if converted else "active",
                "updated_at": AS_OF,
                "expires_at": far_future,
            }
        )
    return carts


def generate_lite_orders(count: int) -> list[dict]:
    rng = random.Random(SEED + 6)
    statuses = ["placed", "paid", "shipped", "delivered"]
    return [
        {
            "_id": order_id(index),
            "customer_id": customer_id(rng.randrange(1, 501)),
            "status": rng.choice(statuses),
            "total": rng.randrange(1000, 50000),
            "placed_at": AS_OF - timedelta(days=rng.randrange(0, 700)),
        }
        for index in range(1, count + 1)
    ]
