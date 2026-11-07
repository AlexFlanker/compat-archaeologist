from __future__ import annotations

from pymongo.database import Database

from shoplog.schema import EMAIL_COLLATION


def find_by_email(db: Database, email: str) -> dict | None:
    return db.customers.find_one({"email": email}, collation=EMAIL_COLLATION)


def register(db: Database, customer_id: str, email: str, name: str, tier: str, now) -> None:
    db.customers.insert_one(
        {"_id": customer_id, "email": email, "name": name, "tier": tier, "signup_date": now, "loyalty_points": 0}
    )


def add_loyalty_points(db: Database, customer_id: str, points: int) -> None:
    db.customers.update_one({"_id": customer_id}, {"$inc": {"loyalty_points": points}})
