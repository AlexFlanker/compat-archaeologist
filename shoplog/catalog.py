from __future__ import annotations

from pymongo.database import Database


def search_products(db: Database, query: str, limit: int = 20) -> list[dict]:
    return list(
        db.products.find(
            {"$text": {"$search": query}, "active": True},
            {"name": 1, "price": 1, "category": 1, "score": {"$meta": "textScore"}},
        )
        .sort([("score", {"$meta": "textScore"}), ("_id", 1)])
        .limit(limit)
    )


def products_in_category(db: Database, category: str, limit: int = 40) -> list[dict]:
    return list(db.products.find({"category": category, "active": True}).sort("_id", 1).limit(limit))
