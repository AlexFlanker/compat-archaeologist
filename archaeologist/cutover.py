"""Cutover fidelity: simulate a full-load + CDC migration to the target, then verify what arrived.

The copier does what a managed migration tool does: open a change stream, bulk-copy every
collection with its indexes and validator, keep the source taking live writes, then apply the
change events and cut over. The verifier then reconciles source and target independently of the
copier: counts, content hashes, index and constraint parity, the source oplog, business invariants,
and day-one writes that the source's constraints would refuse.

Scenario "clean" injects nothing, so anything it finds comes from the engine or the copier.
Scenario "drill" injects two faults on purpose: a gap in the CDC stream and a dropped unique index.
The verifier isn't told about them. The report lists which ones it caught.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import time
from datetime import datetime, timedelta, timezone

from bson import json_util
from bson.codec_options import CodecOptions
from pymongo.errors import PyMongoError

from archaeologist import normalize
from archaeologist.config import OUT_DIR, source_client, target_client
from scripts import generate
from scripts.seed import PROFILES, seed_database
from shoplog import carts, checkout, crud, customers, orders, refunds
from shoplog.schema import EMAIL_COLLATION, ORDER_VALIDATOR

SCENARIOS = {
    "clean": {"cdc_gap": None, "drop_indexes": []},
    "drill": {"cdc_gap": {"after_event": 60, "events": 30}, "drop_indexes": ["coupon_once_per_customer"]},
}
LIVE_OPERATIONS = {"shoplog": 300, "lite": 200}
STATUS_ENUM = ORDER_VALIDATOR["$jsonSchema"]["properties"]["status"]["enum"]
INDEX_OPTIONS = ("unique", "partialFilterExpression", "expireAfterSeconds", "collation", "weights", "sparse")


def _db(client, name):
    return client[name].with_options(codec_options=CodecOptions(tz_aware=True, uuid_representation=4))


def _latest_oplog_ts(client):
    entry = client.local["oplog.rs"].find_one(sort=[("$natural", -1)])
    return entry["ts"]


def _oplog_operations(client, database: str, since) -> int:
    """Counts document writes the source recorded for this database since the cutover started."""
    count = 0
    prefix = f"{database}."
    for entry in client.local["oplog.rs"].find({"ts": {"$gt": since}}):
        operations = entry.get("o", {}).get("applyOps") if entry.get("op") == "c" else [entry]
        for operation in operations or []:
            if operation.get("op") in {"i", "u", "d"} and operation.get("ns", "").startswith(prefix):
                count += 1
    return count


def _full_load(source_db, target_db, drop_indexes: list[str]) -> dict:
    report = {"collections": {}, "indexes": [], "validators": []}
    for info in source_db.list_collections():
        name = info["name"]
        if name.startswith("system."):
            continue
        options = info.get("options", {})
        try:
            if options.get("validator"):
                target_db.create_collection(
                    name,
                    validator=options["validator"],
                    validationLevel=options.get("validationLevel", "strict"),
                    validationAction=options.get("validationAction", "error"),
                )
                report["validators"].append({"collection": name, "ok": True})
            else:
                target_db.create_collection(name)
        except PyMongoError as exc:
            report["validators"].append({"collection": name, "ok": False, "error": str(exc)[:200]})

        copied = 0
        batch = []
        for document in source_db[name].find().sort("_id", 1):
            batch.append(document)
            if len(batch) == 1000:
                copied += len(target_db[name].insert_many(batch, ordered=False).inserted_ids)
                batch = []
        if batch:
            copied += len(target_db[name].insert_many(batch, ordered=False).inserted_ids)
        report["collections"][name] = copied

        for index in source_db[name].list_indexes():
            if index["name"] == "_id_":
                continue
            if index["name"] in drop_indexes:
                report["indexes"].append({"collection": name, "name": index["name"], "ok": None, "skipped_by_copier": True})
                continue
            options = {key: index[key] for key in INDEX_OPTIONS if key in index}
            try:
                target_db[name].create_index(list(index["key"].items()), name=index["name"], **options)
                report["indexes"].append({"collection": name, "name": index["name"], "ok": True})
            except PyMongoError as exc:
                report["indexes"].append({"collection": name, "name": index["name"], "ok": False, "code": getattr(exc, "code", None), "error": str(exc)[:200]})
    return report


def _live_traffic(profile: str, client, db, rng: random.Random, count: int) -> int:
    """Writes the application keeps making while the copy runs."""
    now = datetime.now(timezone.utc)
    done = 0
    if profile == "lite":
        for step in range(count):
            choice = rng.random()
            try:
                if choice < 0.5:
                    crud.set_status(db, generate.order_id(rng.randrange(1, 5000)), rng.choice(["paid", "shipped", "delivered"]), now)
                elif choice < 0.8:
                    crud.create_order(db, {"_id": f"N{step:06d}", "customer_id": generate.customer_id(rng.randrange(1, 501)), "status": "placed", "total": rng.randrange(1000, 9000), "placed_at": now})
                else:
                    crud.delete_order(db, generate.order_id(rng.randrange(5000, 10000)))
                done += 1
            except PyMongoError:
                pass
        return done

    settings = PROFILES["shoplog"]
    all_customers = generate.generate_customers(settings["customers"])
    all_products = generate.generate_products(settings["products"])
    tiers = {customer["_id"]: customer["tier"] for customer in all_customers}
    refundable = list(db.orders.find({"status": "delivered", "delivered_at": {"$gte": generate.AS_OF - timedelta(days=25)}}, {"_id": 1, "customer_id": 1, "items.line_id": 1}).limit(80))
    in_flight = list(db.orders.find({"status": {"$in": ["placed", "paid", "shipped"]}}, {"_id": 1, "status": 1}).limit(80))
    logical_now = generate.AS_OF + timedelta(days=1)
    for step in range(count):
        choice = rng.random()
        try:
            if choice < 0.2 and refundable:
                order = refundable.pop()
                refunds.refund_lines(db, order["_id"], [line["line_id"] for line in order["items"]][:1], tiers[order["customer_id"]], logical_now)
            elif choice < 0.35 and in_flight:
                order = in_flight.pop()
                orders.transition(db, order["_id"], {"placed": "paid", "paid": "shipped", "shipped": "delivered"}[order["status"]], logical_now)
            elif choice < 0.55:
                buyer = rng.choice(all_customers)
                product = rng.choice(all_products)
                checkout.checkout(client, db, f"C{step:07d}", buyer, [{"sku": product["_id"], "category": product["category"], "qty": 1, "unit_price": product["price"]}], logical_now)
            elif choice < 0.75:
                cart_id = f"K{rng.randrange(1, 400):07d}"
                carts.touch_cart(db, cart_id, rng.choice(all_customers)["_id"], [{"sku": rng.choice(all_products)["_id"], "qty": 1}], now)
            elif choice < 0.85:
                carts.discard_cart(db, f"K{rng.randrange(1, 400):07d}")
            elif choice < 0.95:
                customers.add_loyalty_points(db, rng.choice(all_customers)["_id"], rng.randrange(1, 40))
            else:
                customers.register(db, f"CN{step:06d}", f"new{step}@example.com", f"New {step}", "bronze", now)
            done += 1
        except (PyMongoError, checkout.OutOfStock, refunds.RefundRejected, orders.IllegalTransition):
            pass
    return done


def _apply_change_stream(source_db, target_db, token, gap: dict | None) -> dict:
    applied, dropped, skipped = 0, [], 0
    position = 0
    with source_db.watch(resume_after=token, full_document="updateLookup", max_await_time_ms=300) as stream:
        idle = 0
        while idle < 3:
            event = stream.try_next()
            if event is None:
                idle += 1
                continue
            idle = 0
            position += 1
            collection = event.get("ns", {}).get("coll")
            key = event.get("documentKey", {}).get("_id")
            if gap and gap["after_event"] < position <= gap["after_event"] + gap["events"]:
                dropped.append({"collection": collection, "_id": str(key), "operation": event["operationType"]})
                continue
            operation = event["operationType"]
            if operation in {"insert", "update", "replace"}:
                document = event.get("fullDocument")
                if document is None:
                    skipped += 1
                    continue
                target_db[collection].replace_one({"_id": key}, document, upsert=True)
            elif operation == "delete":
                target_db[collection].delete_one({"_id": key})
            else:
                skipped += 1
                continue
            applied += 1
    return {"events_seen": position, "applied": applied, "skipped_no_document": skipped, "dropped_by_injection": dropped}


def _document_hashes(db, name: str) -> dict[str, str]:
    hashes = {}
    for document in db[name].find():
        canonical = json.dumps(normalize.canonical(document), sort_keys=True, default=str)
        hashes[str(document["_id"])] = hashlib.sha1(canonical.encode()).hexdigest()
    return hashes


def _indexes(db, name: str) -> dict[str, dict]:
    try:
        return {
            index["name"]: {"key": dict(index["key"]), **{key: index[key] for key in INDEX_OPTIONS if key in index}}
            for index in db[name].list_indexes()
            if index["name"] != "_id_"
        }
    except PyMongoError:
        return {}


def _validator(db, name: str):
    found = list(db.list_collections(filter={"name": name}))
    return found[0].get("options", {}).get("validator") if found else None


def _business_invariants(profile: str, db) -> dict[str, int]:
    """Counts violations. Each check mirrors a rule the ledger reconstructed."""
    if profile == "lite":
        return {"orders with a negative total": db.orders.count_documents({"total": {"$lt": 0}})}
    coupon_reuse = list(
        db.orders.aggregate([
            {"$match": {"coupon_code": {"$exists": True}}},
            {"$group": {"_id": {"customer": "$customer_id", "coupon": "$coupon_code"}, "uses": {"$sum": 1}}},
            {"$match": {"uses": {"$gt": 1}}},
            {"$count": "customers"},
        ])
    )
    email_collisions = list(
        db.customers.aggregate([
            {"$group": {"_id": {"$toLower": "$email"}, "accounts": {"$sum": 1}}},
            {"$match": {"accounts": {"$gt": 1}}},
            {"$count": "emails"},
        ])
    )
    refund_mismatch = db.orders.count_documents(
        {"$or": [
            {"status": {"$in": ["refunded", "partially_refunded"]}, "refund_total": {"$lte": 0}},
            {"status": {"$nin": ["refunded", "partially_refunded"]}, "refund_total": {"$gt": 0}},
        ]}
    )
    invalid = db.orders.count_documents({"$or": [{"status": {"$nin": STATUS_ENUM}}, {"total": {"$lt": 0}}, {"items": {"$size": 0}}]})
    return {
        "customers who used the welcome coupon twice": coupon_reuse[0]["customers"] if coupon_reuse else 0,
        "emails that collide when case is ignored": email_collisions[0]["emails"] if email_collisions else 0,
        "orders whose refund total contradicts their status": refund_mismatch,
        "orders that break the order schema": invalid,
    }


def _day_one_writes(profile: str, db) -> list[dict]:
    """Writes the source's constraints refuse. A target that accepts them has lost a guarantee."""
    attempts = []
    if profile == "lite":
        return attempts
    now = datetime.now(timezone.utc)
    reuser = db.orders.find_one({"coupon_code": "WELCOME10"}, {"customer_id": 1}, sort=[("_id", 1)])
    existing = db.customers.find_one({}, {"email": 1}, sort=[("_id", 1)])
    cases = [
        ("Second use of the welcome coupon by the same customer", "orders", {"_id": "DAY1-COUPON", "customer_id": reuser["customer_id"] if reuser else "C000001", "status": "placed", "items": [{"sku": "SKU-0001", "qty": 1}], "total": 100, "coupon_code": "WELCOME10", "placed_at": now}),
        ("Sign-up with an existing email in different case", "customers", {"_id": "DAY1-EMAIL", "email": existing["email"].swapcase(), "name": "Dup", "tier": "bronze", "signup_date": now, "loyalty_points": 0}),
        ("Order with an unknown status and a negative total", "orders", {"_id": "DAY1-INVALID", "customer_id": "C000001", "status": "lost_in_mail", "items": [], "total": -5, "placed_at": now}),
    ]
    for label, collection, document in cases:
        try:
            db[collection].insert_one(document)
            attempts.append({"write": label, "accepted": True})
        except PyMongoError as exc:
            attempts.append({"write": label, "accepted": False, "code": getattr(exc, "code", None)})
    return attempts


def run(profile: str, scenario: str, self_check: bool = False) -> dict:
    settings = PROFILES[profile]
    config = SCENARIOS[scenario]
    drop = config["drop_indexes"] if profile == "shoplog" else []
    source = source_client(timeout_ms=300_000)
    target = source if self_check else target_client(timeout_ms=300_000)
    source_name, target_name = f"{profile}_cut_src", f"{profile}_cut"
    started = time.perf_counter()

    seed_database(source, source_name, profile, settings["orders"])
    target.drop_database(target_name)
    source_db, target_db = _db(source, source_name), _db(target, target_name)

    with source_db.watch(full_document="updateLookup") as stream:
        stream.try_next()
        token = stream.resume_token
    oplog_start = _latest_oplog_ts(source)

    load = _full_load(source_db, target_db, drop)
    live_writes = _live_traffic(profile, source, source_db, random.Random(generate.SEED + 300), LIVE_OPERATIONS[profile])
    cdc = _apply_change_stream(source_db, target_db, token, config["cdc_gap"] if profile in PROFILES else None)
    source_operations = _oplog_operations(source, source_name, oplog_start)

    checks = []
    collections = sorted(name for name in source_db.list_collection_names() if not name.startswith("system."))
    out_of_sync_ids: set[str] = set()
    for name in collections:
        source_hashes, target_hashes = _document_hashes(source_db, name), _document_hashes(target_db, name)
        differing = sorted(key for key in set(source_hashes) | set(target_hashes) if source_hashes.get(key) != target_hashes.get(key))
        out_of_sync_ids.update(f"{name}:{key}" for key in differing)
        checks.append(
            {
                "check": f"{name}: documents and content",
                "area": "data",
                "ok": not differing,
                "evidence": {"source_count": len(source_hashes), "target_count": len(target_hashes), "documents_differing": len(differing), "examples": differing[:5]},
            }
        )

        source_indexes, target_indexes = _indexes(source_db, name), _indexes(target_db, name)
        for index_name, spec in source_indexes.items():
            present = target_indexes.get(index_name)
            same = present is not None and normalize.canonical(present) == normalize.canonical(spec)
            kind = ", ".join(key for key in INDEX_OPTIONS if key in spec) or "plain"
            checks.append(
                {
                    "check": f"{name}.{index_name} index ({kind})",
                    "area": "constraints",
                    "ok": same,
                    "evidence": {"source": json.loads(json_util.dumps(spec)), "target": json.loads(json_util.dumps(present)) if present else None},
                }
            )
        source_validator = _validator(source_db, name)
        if source_validator:
            target_validator = _validator(target_db, name)
            checks.append(
                {
                    "check": f"{name} $jsonSchema validator",
                    "area": "constraints",
                    "ok": target_validator is not None and normalize.canonical(target_validator) == normalize.canonical(source_validator),
                    "evidence": {"source_has_validator": True, "target_has_validator": target_validator is not None},
                }
            )

    checks.append(
        {
            "check": "Change events applied vs writes in the source oplog",
            "area": "cdc",
            "ok": cdc["applied"] + cdc["skipped_no_document"] >= source_operations,
            "evidence": {"source_oplog_writes": source_operations, "events_applied": cdc["applied"], "events_skipped_no_document": cdc["skipped_no_document"], "live_writes_issued": live_writes},
        }
    )

    source_invariants_before = _business_invariants(profile, source_db)
    target_invariants_before = _business_invariants(profile, target_db)
    day_one_source = _day_one_writes(profile, source_db)
    day_one_target = _day_one_writes(profile, target_db)
    target_invariants_after = _business_invariants(profile, target_db)
    for rule, violations in target_invariants_after.items():
        checks.append(
            {
                "check": f"Business rule: {rule}",
                "area": "invariants",
                "ok": violations <= source_invariants_before.get(rule, 0),
                "evidence": {"source": source_invariants_before.get(rule, 0), "target_at_cutover": target_invariants_before[rule], "target_after_day_one_writes": violations},
            }
        )
    for source_attempt, target_attempt in zip(day_one_source, day_one_target):
        checks.append(
            {
                "check": f"Day-one write refused: {target_attempt['write']}",
                "area": "constraints",
                "ok": source_attempt["accepted"] == target_attempt["accepted"],
                "evidence": {"source": "refused" if not source_attempt["accepted"] else "accepted", "target": "refused" if not target_attempt["accepted"] else "accepted"},
            }
        )

    injected = []
    if cdc["dropped_by_injection"]:
        dropped_ids = {f"{item['collection']}:{item['_id']}" for item in cdc["dropped_by_injection"]}
        caught = dropped_ids & out_of_sync_ids
        injected.append(
            {
                "fault": f"CDC gap: {len(cdc['dropped_by_injection'])} change events dropped mid-cutover",
                "caught": bool(caught) or not any(check["ok"] for check in checks if check["area"] == "cdc"),
                "evidence": f"{len(caught)} of {len(dropped_ids)} affected documents found out of sync; oplog count check {'failed' if not next(check for check in checks if check['area'] == 'cdc')['ok'] else 'passed'}",
                "healed_by_later_events": len(dropped_ids - out_of_sync_ids),
            }
        )
    for index_name in drop:
        flagged = [check for check in checks if check["area"] == "constraints" and f".{index_name} index" in check["check"] and not check["ok"]]
        injected.append({"fault": f"Copier skipped unique index {index_name}", "caught": bool(flagged), "evidence": flagged[0]["check"] if flagged else "not flagged"})

    for check in checks:
        check["severity"] = _severity(check)

    document = {
        "profile": profile,
        "scenario": scenario,
        "self_check": self_check,
        "target": "MongoDB (self-check)" if self_check else "FerretDB",
        "full_load": load,
        "cdc": {key: value for key, value in cdc.items() if key != "dropped_by_injection"},
        "checks": checks,
        "failed": [check for check in checks if not check["ok"]],
        "injected_faults": injected,
        "seconds": round(time.perf_counter() - started, 1),
    }
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_self" if self_check else ""
    (out_dir / f"cutover_{scenario}{suffix}.json").write_text(json.dumps(document, indent=2, default=str))
    source.drop_database(source_name)
    target.drop_database(target_name)
    return document


def _severity(check: dict) -> str:
    if check["ok"]:
        return "pass"
    if check["area"] in {"invariants", "cdc", "data"}:
        return "critical"
    return "high"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="clean")
    parser.add_argument("--self-check", action="store_true", help="Migrate MongoDB to MongoDB; every check must pass")
    arguments = parser.parse_args()
    result = run(arguments.profile, arguments.scenario, arguments.self_check)
    print(f"{result['scenario']} → {result['target']}: {len(result['failed'])} of {len(result['checks'])} checks failed in {result['seconds']}s")
    print(f"  cdc: {result['cdc']}")
    for check in result["failed"]:
        print(f"  FAIL [{check['area']}] {check['check']}: {json.dumps(check['evidence'], default=str)[:220]}")
    for fault in result["injected_faults"]:
        print(f"  injected: {fault['fault']} → caught={fault['caught']} ({fault['evidence']})")
