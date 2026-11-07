from __future__ import annotations

from pymongo.database import Database

STOREFRONT_INDEX = "storefront"


def storefront_search(db: Database, query: str, limit: int = 20) -> list[dict]:
    pipeline = [
        {"$search": {"index": STOREFRONT_INDEX, "compound": {
            "must": [{"text": {"query": query, "path": ["name", "description", "tags"], "fuzzy": {"maxEdits": 1}}}],
            "filter": [{"equals": {"path": "active", "value": True}}],
        }}},
        {"$limit": limit},
        {"$project": {"name": 1, "price": 1, "category": 1, "score": {"$meta": "searchScore"}}},
    ]
    return list(db.products.aggregate(pipeline))


def autocomplete(db: Database, prefix: str, limit: int = 8) -> list[dict]:
    pipeline = [
        {"$search": {"index": STOREFRONT_INDEX, "autocomplete": {"query": prefix, "path": "name"}}},
        {"$limit": limit},
        {"$project": {"name": 1}},
    ]
    return list(db.products.aggregate(pipeline))


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
