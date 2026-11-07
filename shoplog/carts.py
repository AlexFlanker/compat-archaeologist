from __future__ import annotations

from datetime import datetime, timedelta

from pymongo.database import Database

CART_LIFETIME = timedelta(hours=24)


def touch_cart(db: Database, cart_id: str, customer_id: str, items: list[dict], now: datetime) -> None:
    db.carts.update_one(
        {"_id": cart_id},
        {
            "$set": {
                "customer_id": customer_id,
                "items": items,
                "status": "active",
                "updated_at": now,
                "expires_at": now + CART_LIFETIME,
            }
        },
        upsert=True,
    )


def active_cart(db: Database, customer_id: str) -> dict | None:
    return db.carts.find_one({"customer_id": customer_id, "status": "active"})


def convert_cart(db: Database, cart_id: str, order_id: str, now: datetime) -> None:
    db.carts.update_one(
        {"_id": cart_id},
        {"$set": {"status": "converted", "order_id": order_id, "updated_at": now}},
    )
