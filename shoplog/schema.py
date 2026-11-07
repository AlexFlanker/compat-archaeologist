from __future__ import annotations

from typing import Callable

from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.collation import Collation
from pymongo.database import Database

EMAIL_COLLATION = Collation(locale="en", strength=2)

ORDER_VALIDATOR = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["customer_id", "status", "items", "total", "placed_at"],
        "properties": {
            "customer_id": {"bsonType": "string"},
            "status": {
                "enum": [
                    "placed",
                    "paid",
                    "shipped",
                    "delivered",
                    "cancelled",
                    "partially_refunded",
                    "refunded",
                ]
            },
            "items": {"bsonType": "array", "minItems": 1},
            "total": {"bsonType": ["int", "long", "double"], "minimum": 0},
            "placed_at": {"bsonType": "date"},
        },
    }
}


def ensure_schema(db: Database) -> list[dict]:
    """Creates collections and indexes, reporting each step instead of stopping at a failure."""
    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "orders.validator",
            lambda: db.create_collection(
                "orders", validator=ORDER_VALIDATOR, validationLevel="moderate", validationAction="error"
            ),
        ),
        ("orders.customer_placed", lambda: db.orders.create_index([("customer_id", ASCENDING), ("placed_at", DESCENDING)])),
        ("orders.status_placed", lambda: db.orders.create_index([("status", ASCENDING), ("placed_at", DESCENDING)])),
        ("orders.placed_at", lambda: db.orders.create_index([("placed_at", ASCENDING)])),
        (
            "orders.coupon_once",
            lambda: db.orders.create_index(
                [("customer_id", ASCENDING), ("coupon_code", ASCENDING)],
                unique=True,
                partialFilterExpression={"coupon_code": {"$exists": True}},
                name="coupon_once_per_customer",
            ),
        ),
        (
            "customers.email_ci",
            lambda: db.customers.create_index(
                [("email", ASCENDING)], unique=True, collation=EMAIL_COLLATION, name="email_ci_unique"
            ),
        ),
        (
            "carts.expiry",
            lambda: db.carts.create_index(
                [("expires_at", ASCENDING)],
                expireAfterSeconds=0,
                partialFilterExpression={"status": "active"},
                name="active_cart_expiry",
            ),
        ),
        ("carts.customer", lambda: db.carts.create_index([("customer_id", ASCENDING)])),
        (
            "products.search",
            lambda: db.products.create_index(
                [("name", TEXT), ("description", TEXT), ("tags", TEXT)],
                weights={"name": 10, "tags": 5, "description": 1},
                name="product_search",
            ),
        ),
        ("products.category", lambda: db.products.create_index([("category", ASCENDING)])),
    ]
    return _run_steps(steps)


def ensure_lite_schema(db: Database) -> list[dict]:
    steps: list[tuple[str, Callable[[], object]]] = [
        ("orders.customer", lambda: db.orders.create_index([("customer_id", ASCENDING)])),
        ("orders.status", lambda: db.orders.create_index([("status", ASCENDING)])),
    ]
    return _run_steps(steps)


def _run_steps(steps: list[tuple[str, Callable[[], object]]]) -> list[dict]:
    report = []
    for name, action in steps:
        try:
            action()
            report.append({"step": name, "ok": True})
        except Exception as exc:  # A rejected index or validator is a finding to record, not a crash.
            report.append(
                {
                    "step": name,
                    "ok": False,
                    "code": getattr(exc, "code", None),
                    "error": str(exc)[:300],
                }
            )
    return report
