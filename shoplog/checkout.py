from __future__ import annotations

from datetime import datetime

from pymongo import MongoClient
from pymongo.database import Database

from shoplog.pricing import price_order


class OutOfStock(Exception):
    pass


def checkout(
    client: MongoClient,
    db: Database,
    order_id: str,
    customer: dict,
    lines: list[dict],
    now: datetime,
    coupon: str | None = None,
    cart_id: str | None = None,
) -> dict:
    priced = price_order(lines, customer["tier"], coupon)
    order = {
        "_id": order_id,
        "customer_id": customer["_id"],
        "status": "placed",
        "items": [
            {
                "line_id": index + 1,
                "sku": line["sku"],
                "category": line["category"],
                "qty": line["qty"],
                "unit_price": line["unit_price"],
                "line_total": line["qty"] * line["unit_price"],
                "refund_status": "none",
                "refunded": 0,
            }
            for index, line in enumerate(lines)
        ],
        "subtotal": priced["subtotal"],
        "discount": priced["discount"],
        "shipping": priced["shipping"],
        "tax": priced["tax"],
        "total": priced["total"],
        "refund_total": 0,
        "placed_at": now,
        "updated_at": now,
        "history": [{"status": "placed", "at": now}],
    }
    if priced["coupon_code"]:
        order["coupon_code"] = priced["coupon_code"]

    with client.start_session() as session:
        with session.start_transaction():
            for line in lines:
                reserved = db.inventory.update_one(
                    {"_id": line["sku"], "on_hand": {"$gte": line["qty"]}},
                    {"$inc": {"on_hand": -line["qty"], "reserved": line["qty"]}, "$set": {"updated_at": now}},
                    session=session,
                )
                if reserved.modified_count != 1:
                    raise OutOfStock(line["sku"])
            db.orders.insert_one(order, session=session)
            if cart_id:
                db.carts.update_one(
                    {"_id": cart_id},
                    {"$set": {"status": "converted", "order_id": order_id, "updated_at": now}},
                    session=session,
                )
    return order
