from __future__ import annotations

from pymongo.database import Database


def referral_chain(db: Database, customer_id: str, max_depth: int = 3) -> list[dict]:
    pipeline = [
        {"$match": {"_id": customer_id}},
        {
            "$graphLookup": {
                "from": "customers",
                "startWith": "$referred_by",
                "connectFromField": "referred_by",
                "connectToField": "_id",
                "as": "referrers",
                "maxDepth": max_depth,
                "depthField": "depth",
            }
        },
    ]
    return list(db.customers.aggregate(pipeline))
