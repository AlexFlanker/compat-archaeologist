"""Runs a scripted workload on the source with the profiler on, then exports the query log.

Writes only touch the prefix of orders that exists in the small fixture database, so the
write path can be replayed later against a freshly reset copy.
"""

from __future__ import annotations

import argparse
import random
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bson import json_util  # noqa: E402
from bson.binary import UuidRepresentation  # noqa: E402
from bson.json_util import CANONICAL_JSON_OPTIONS  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from archaeologist.config import EVIDENCE_DIR, source_client  # noqa: E402
from scripts import generate  # noqa: E402
from scripts.seed import PROFILES, database_names, seed_database  # noqa: E402
from shoplog import analytics, carts, catalog, checkout, crud, customers, inventory_sync, orders, refunds  # noqa: E402

PROFILE_BYTES = 128 * 1024 * 1024
# Canonical form keeps int and double distinct, which replay depends on.
EXPORT_OPTIONS = CANONICAL_JSON_OPTIONS.with_options(uuid_representation=UuidRepresentation.STANDARD)
SEARCHES = [
    "wireless headphones",
    '"noise cancelling"',
    "portable speaker -refurbished",
    "rugged tent",
    "premium",
    "quiet kettle -gift",
    "smart lamp",
    "ergonomic chair -refurbished",
]
CATEGORIES = ["audio", "home", "kitchen", "outdoor", "office", "clearance"]


def start_profiler(db) -> None:
    db.command("profile", 0)
    db.drop_collection("system.profile")
    db.create_collection("system.profile", capped=True, size=PROFILE_BYTES)
    db.command("profile", 2)


def export_profile(db, path: Path) -> int:
    db.command("profile", 0)
    count = 0
    path.parent.mkdir(exist_ok=True)
    with path.open("w") as handle:
        for entry in db["system.profile"].find({"ns": {"$ne": f"{db.name}.system.profile"}}).sort("ts", 1):
            handle.write(json_util.dumps(entry, json_options=EXPORT_OPTIONS) + "\n")
            count += 1
    return count


def run_shoplog(client, db, rng: random.Random) -> dict:
    settings = PROFILES["shoplog"]
    all_customers = generate.generate_customers(settings["customers"])
    all_products = generate.generate_products(settings["products"])
    fixture_max = generate.order_id(settings["fixture_orders"])
    logical_now = generate.AS_OF + timedelta(days=1)
    wall_clock_now = datetime.now(timezone.utc)

    refundable = list(
        db.orders.find(
            {"_id": {"$lte": fixture_max}, "status": "delivered", "delivered_at": {"$gte": generate.AS_OF - timedelta(days=25)}},
            {"_id": 1, "customer_id": 1, "items.line_id": 1},
        ).sort("_id", 1)
    )
    in_flight = list(
        db.orders.find(
            {"_id": {"$lte": fixture_max}, "status": {"$in": ["placed", "paid", "shipped"]}},
            {"_id": 1, "status": 1},
        ).sort("_id", 1)
    )
    tier_of = {customer["_id"]: customer["tier"] for customer in all_customers}
    next_order = iter(range(90_000_000, 90_100_000))
    outcomes = {"ok": 0, "rejected": 0}

    def customer() -> dict:
        return all_customers[rng.randrange(len(all_customers))]

    def get_order():
        crud.get_order(db, generate.order_id(rng.randrange(1, settings["orders"] + 1)))

    def history():
        orders.order_history(db, customer()["_id"])

    def status_list():
        orders.open_orders_by_status(db, rng.choice(["placed", "paid", "shipped"]))

    def login():
        email = customer()["email"]
        customers.find_by_email(db, rng.choice([email, email.lower(), email.upper()]))

    def search():
        catalog.search_products(db, rng.choice(SEARCHES))

    def browse():
        catalog.products_in_category(db, rng.choice(CATEGORIES))

    def buy():
        buyer = customer()
        lines = [
            {"sku": product["_id"], "category": product["category"], "qty": rng.randrange(1, 3), "unit_price": product["price"]}
            for product in rng.sample(all_products, rng.randrange(1, 4))
        ]
        checkout.checkout(
            client,
            db,
            generate.order_id(next(next_order)),
            buyer,
            lines,
            logical_now,
            coupon="WELCOME10" if rng.random() < 0.3 else None,
        )

    def refund():
        if not refundable:
            return
        order = refundable.pop()
        line_ids = [line["line_id"] for line in order["items"]]
        chosen = line_ids if rng.random() < 0.5 else line_ids[:1]
        refunds.refund_lines(db, order["_id"], chosen, tier_of[order["customer_id"]], logical_now)

    def advance():
        if not in_flight:
            return
        order = in_flight.pop()
        following = {"placed": "paid", "paid": "shipped", "shipped": "delivered"}[order["status"]]
        orders.transition(db, order["_id"], following, logical_now)

    def illegal_cancel():
        orders.transition(db, generate.order_id(rng.randrange(1, settings["fixture_orders"] + 1)), "cancelled", logical_now)

    def cart():
        buyer = customer()
        cart_id = f"K{rng.randrange(1, 200):07d}"
        carts.touch_cart(db, cart_id, buyer["_id"], [{"sku": rng.choice(all_products)["_id"], "qty": 1}], wall_clock_now)
        carts.active_cart(db, buyer["_id"])
        if rng.random() < 0.3:
            carts.convert_cart(db, cart_id, "pending", wall_clock_now)

    def bad_order():
        crud.create_order(
            db,
            {
                "_id": f"BAD{rng.randrange(1, 10_000)}",
                "customer_id": customer()["_id"],
                "status": "lost_in_mail",
                "items": [],
                "total": -1,
                "placed_at": logical_now,
            },
        )

    def revenue():
        year = rng.randrange(2016, 2026)
        month = rng.randrange(1, 13)
        since = datetime(year, month, 1, tzinfo=timezone.utc)
        analytics.revenue_report(db, since, since + timedelta(days=31))

    def low_stock():
        analytics.low_stock_categories(db, rng.choice([50, 100, 200]))

    def timeline():
        analytics.customer_spend_timeline(db, customer()["_id"])

    def loyalty():
        customers.add_loyalty_points(db, customer()["_id"], rng.randrange(1, 50))

    def review():
        refunds.mark_all_lines_reviewed(db, generate.order_id(rng.randrange(1, settings["fixture_orders"] + 1)), "qa-bot")

    mix = (
        [get_order] * 220 + [history] * 100 + [status_list] * 40 + [login] * 110 + [search] * 100 + [browse] * 30
        + [buy] * 60 + [refund] * 50 + [advance] * 30 + [illegal_cancel] * 10 + [cart] * 70 + [bad_order] * 5
        + [revenue] * 20 + [low_stock] * 10 + [timeline] * 60 + [loyalty] * 20 + [review] * 10
    )
    rng.shuffle(mix)

    stop, ready = threading.Event(), threading.Event()
    worker_result: list[int] = []
    worker = threading.Thread(
        target=lambda: worker_result.append(inventory_sync.run_sync(db, stop, ready)), daemon=True
    )
    worker.start()
    ready.wait(timeout=10)

    for action in mix:
        try:
            action()
            outcomes["ok"] += 1
        except (PyMongoError, checkout.OutOfStock, refunds.RefundRejected, orders.IllegalTransition):
            outcomes["rejected"] += 1

    time.sleep(1)
    stop.set()
    worker.join(timeout=10)
    outcomes["change_events_processed"] = worker_result[0] if worker_result else None
    return outcomes


def run_lite(db, rng: random.Random) -> dict:
    settings = PROFILES["lite"]
    now = generate.AS_OF
    fixture = settings["fixture_orders"]
    next_new = iter(range(90_000_000, 90_100_000))

    def get_order():
        crud.get_order(db, generate.order_id(rng.randrange(1, settings["orders"] + 1)))

    def for_customer():
        crud.orders_for_customer(db, generate.customer_id(rng.randrange(1, 501)))

    def with_status():
        crud.orders_with_status(db, rng.choice(["placed", "paid", "shipped", "delivered"]))

    def count():
        crud.count_with_status(db, rng.choice(["placed", "paid", "shipped", "delivered"]))

    def create():
        crud.create_order(
            db,
            {
                "_id": generate.order_id(next(next_new)),
                "customer_id": generate.customer_id(rng.randrange(1, 501)),
                "status": "placed",
                "total": rng.randrange(1000, 50000),
                "placed_at": now,
            },
        )

    def update():
        crud.set_status(db, generate.order_id(rng.randrange(1, fixture + 1)), rng.choice(["paid", "shipped"]), now)

    def delete():
        crud.delete_order(db, generate.order_id(rng.randrange(fixture // 2, fixture + 1)))

    mix = [get_order] * 300 + [for_customer] * 150 + [with_status] * 50 + [count] * 30 + [create] * 40 + [update] * 60 + [delete] * 10
    rng.shuffle(mix)
    for action in mix:
        action()
    return {"ok": len(mix), "rejected": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="shoplog")
    args = parser.parse_args()

    client = source_client(timeout_ms=120_000)
    names = database_names(args.profile)
    settings = PROFILES[args.profile]
    seed_database(client, names["workload"], args.profile, settings["orders"])
    db = client[names["workload"]]

    rng = random.Random(generate.SEED + 100)
    start_profiler(db)
    started = time.perf_counter()
    outcomes = run_lite(db, rng) if args.profile == "lite" else run_shoplog(client, db, rng)
    elapsed = time.perf_counter() - started

    log_name = "query_log.jsonl" if args.profile == "shoplog" else f"{args.profile}_query_log.jsonl"
    exported = export_profile(db, EVIDENCE_DIR / log_name)
    print(f"{args.profile}: {outcomes} in {elapsed:.1f}s; exported {exported} profile entries to evidence/{log_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
