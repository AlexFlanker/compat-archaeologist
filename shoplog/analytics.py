from __future__ import annotations

from datetime import datetime

from pymongo.database import Database

TOTAL_IN_CENTS = {
    "$cond": [
        {"$eq": [{"$type": "$total"}, "double"]},
        {"$round": [{"$multiply": ["$total", 100]}, 0]},
        "$total",
    ]
}


def revenue_report(db: Database, since: datetime, until: datetime) -> dict:
    pipeline = [
        {"$match": {"placed_at": {"$gte": since, "$lt": until}, "status": {"$ne": "cancelled"}}},
        {"$addFields": {"total_cents": TOTAL_IN_CENTS}},
        {
            "$lookup": {
                "from": "customers",
                "let": {"cid": "$customer_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$_id", "$$cid"]}}},
                    {"$project": {"tier": 1, "_id": 0}},
                ],
                "as": "customer",
            }
        },
        {"$unwind": "$customer"},
        {
            "$facet": {
                "by_tier": [
                    {"$group": {"_id": "$customer.tier", "revenue": {"$sum": "$total_cents"}, "orders": {"$sum": 1}}},
                    {"$sort": {"_id": 1}},
                ],
                "by_month": [
                    {
                        "$group": {
                            "_id": {"$dateToString": {"format": "%Y-%m", "date": "$placed_at"}},
                            "revenue": {"$sum": "$total_cents"},
                        }
                    },
                    {"$sort": {"_id": 1}},
                ],
                "largest": [
                    {"$sort": {"total_cents": -1, "_id": 1}},
                    {"$limit": 5},
                    {"$project": {"total_cents": 1, "tier": "$customer.tier"}},
                ],
            }
        },
    ]
    return next(db.orders.aggregate(pipeline), {})


def low_stock_categories(db: Database, threshold: int) -> list[dict]:
    pipeline = [
        {
            "$lookup": {
                "from": "inventory",
                "localField": "_id",
                "foreignField": "_id",
                "pipeline": [{"$match": {"$expr": {"$lt": ["$on_hand", threshold]}}}],
                "as": "stock",
            }
        },
        {"$match": {"stock.0": {"$exists": True}}},
        {"$group": {"_id": "$category", "skus": {"$sum": 1}}},
        {"$sort": {"skus": -1, "_id": 1}},
    ]
    return list(db.products.aggregate(pipeline))


def customer_spend_timeline(db: Database, customer_id: str) -> list[dict]:
    pipeline = [
        {"$match": {"customer_id": customer_id, "status": {"$ne": "cancelled"}}},
        {"$addFields": {"total_cents": TOTAL_IN_CENTS}},
        {
            "$setWindowFields": {
                "partitionBy": "$customer_id",
                "sortBy": {"placed_at": 1},
                "output": {
                    "running_total": {"$sum": "$total_cents", "window": {"documents": ["unbounded", "current"]}},
                    "rolling_90d": {"$sum": "$total_cents", "window": {"range": [-90, "current"], "unit": "day"}},
                    "order_seq": {"$documentNumber": {}},
                    "previous_order_at": {"$shift": {"output": "$placed_at", "by": -1}},
                },
            }
        },
        {"$project": {"placed_at": 1, "total_cents": 1, "running_total": 1, "rolling_90d": 1, "order_seq": 1, "previous_order_at": 1}},
    ]
    return list(db.orders.aggregate(pipeline))
