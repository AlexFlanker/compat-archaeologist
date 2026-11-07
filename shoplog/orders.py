from __future__ import annotations

from datetime import datetime

from pymongo.database import Database

ALLOWED_FROM = {
    "paid": ["placed"],
    "shipped": ["paid"],
    "delivered": ["shipped"],
    "cancelled": ["placed", "paid"],
}


class IllegalTransition(Exception):
    pass


def transition(db: Database, order_id: str, new_status: str, now: datetime) -> None:
    update: dict = {
        "$set": {"status": new_status, "updated_at": now},
        "$push": {"history": {"status": new_status, "at": now}},
    }
    if new_status == "delivered":
        update["$set"]["delivered_at"] = now
    result = db.orders.update_one({"_id": order_id, "status": {"$in": ALLOWED_FROM[new_status]}}, update)
    if result.modified_count != 1:
        raise IllegalTransition(f"{order_id} -> {new_status}")


def order_history(db: Database, customer_id: str, limit: int = 20) -> list[dict]:
    return list(
        db.orders.find(
            {"customer_id": customer_id},
            {"status": 1, "total": 1, "placed_at": 1},
        )
        .sort([("placed_at", -1), ("_id", 1)])
        .limit(limit)
    )


def orders_containing_sku(db: Database, sku: str, since: datetime, limit: int = 100) -> list[dict]:
    return list(
        db.orders.find({"items.sku": sku, "placed_at": {"$gte": since}}, {"customer_id": 1, "status": 1, "placed_at": 1})
        .sort([("placed_at", -1), ("_id", 1)])
        .limit(limit)
    )


def open_orders_by_status(db: Database, status: str, limit: int = 50) -> list[dict]:
    return list(db.orders.find({"status": status}).sort([("placed_at", -1), ("_id", 1)]).limit(limit))
