from __future__ import annotations

from datetime import datetime, timedelta

from pymongo.database import Database

WINDOW_DAYS = {"legacy_vip": 90}
DEFAULT_WINDOW_DAYS = 30
FINAL_SALE_CATEGORIES = {"clearance"}
REFUNDABLE_STATUSES = ["delivered", "partially_refunded"]


class RefundRejected(Exception):
    pass


def refund_lines(db: Database, order_id: str, line_ids: list[int], tier: str, now: datetime) -> int:
    order = db.orders.find_one({"_id": order_id})
    if not order or order["status"] not in REFUNDABLE_STATUSES:
        raise RefundRejected("not refundable")
    window = timedelta(days=WINDOW_DAYS.get(tier, DEFAULT_WINDOW_DAYS))
    if now - order["delivered_at"] > window:
        raise RefundRejected("window closed")

    eligible = [
        line
        for line in order["items"]
        if line["line_id"] in line_ids
        and line["refund_status"] == "none"
        and line["category"] not in FINAL_SALE_CATEGORIES
    ]
    if not eligible:
        raise RefundRejected("no eligible lines")

    amount = sum(line["line_total"] for line in eligible)
    db.orders.update_one(
        {"_id": order_id, "status": {"$in": REFUNDABLE_STATUSES}},
        {
            "$set": {
                "items.$[line].refund_status": "refunded",
                "items.$[line].refunded_at": now,
                "updated_at": now,
            },
            "$inc": {"refund_total": amount},
        },
        array_filters=[{"line.line_id": {"$in": [line["line_id"] for line in eligible]}, "line.refund_status": "none"}],
    )

    remaining = db.orders.count_documents(
        {
            "_id": order_id,
            "items": {
                "$elemMatch": {
                    "refund_status": "none",
                    "category": {"$nin": sorted(FINAL_SALE_CATEGORIES)},
                }
            },
        }
    )
    new_status = "partially_refunded" if remaining else "refunded"
    db.orders.update_one(
        {"_id": order_id},
        {"$set": {"status": new_status}, "$push": {"history": {"status": new_status, "at": now}}},
    )
    return amount


def mark_all_lines_reviewed(db: Database, order_id: str, reviewer: str) -> None:
    db.orders.update_one({"_id": order_id}, {"$set": {"items.$[].reviewed_by": reviewer}})
