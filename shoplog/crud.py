from __future__ import annotations

from datetime import datetime

from pymongo.database import Database


def get_order(db: Database, order_id: str) -> dict | None:
    return db.orders.find_one({"_id": order_id})


def orders_for_customer(db: Database, customer_id: str, limit: int = 20) -> list[dict]:
    return list(db.orders.find({"customer_id": customer_id}).sort("_id", 1).limit(limit))


def orders_with_status(db: Database, status: str, limit: int = 20) -> list[dict]:
    return list(db.orders.find({"status": status}).sort("_id", 1).limit(limit))


def create_order(db: Database, order: dict) -> None:
    db.orders.insert_one(order)


def set_status(db: Database, order_id: str, status: str, now: datetime) -> None:
    db.orders.update_one({"_id": order_id}, {"$set": {"status": status, "updated_at": now}})


def delete_order(db: Database, order_id: str) -> None:
    db.orders.delete_one({"_id": order_id})


def count_with_status(db: Database, status: str) -> int:
    return db.orders.count_documents({"status": status})
