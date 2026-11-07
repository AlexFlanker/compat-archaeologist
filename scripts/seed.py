"""Seeds identical databases on each engine and records what each engine accepted."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archaeologist.config import OUT_DIR, source_client, target_client  # noqa: E402
from scripts import generate  # noqa: E402
from shoplog.schema import ensure_lite_schema, ensure_schema  # noqa: E402

PROFILES = {
    "shoplog": {"orders": 50_000, "fixture_orders": 2_000, "customers": 5_000, "products": 500, "carts": 2_000},
    "lite": {"orders": 10_000, "fixture_orders": 1_000},
}
BATCH = 1_000


def database_names(profile: str) -> dict[str, str]:
    return {"base": profile, "workload": f"{profile}_wl", "fixture": f"{profile}_fx"}


def build_collections(profile: str, order_count: int) -> dict[str, list[dict]]:
    if profile == "lite":
        return {"orders": generate.generate_lite_orders(order_count)}
    settings = PROFILES["shoplog"]
    customers = generate.generate_customers(settings["customers"])
    products = generate.generate_products(settings["products"])
    return {
        "customers": customers,
        "products": products,
        "inventory": generate.generate_inventory(products),
        "orders": generate.generate_orders(order_count, customers, products),
        "carts": generate.generate_carts(settings["carts"], customers, products),
    }


def seed_database(client, name: str, profile: str, order_count: int) -> dict:
    client.drop_database(name)
    db = client[name]
    schema_report = ensure_lite_schema(db) if profile == "lite" else ensure_schema(db)
    inserted = {}
    insert_errors = []
    started = time.perf_counter()
    for collection, documents in build_collections(profile, order_count).items():
        count = 0
        for start in range(0, len(documents), BATCH):
            try:
                count += len(db[collection].insert_many(documents[start : start + BATCH], ordered=False).inserted_ids)
            except Exception as exc:  # Rejected inserts are findings; keep seeding and record them.
                insert_errors.append({"collection": collection, "error": str(exc)[:300]})
        inserted[collection] = count
    return {
        "database": name,
        "inserted": inserted,
        "seconds": round(time.perf_counter() - started, 1),
        "schema": schema_report,
        "insert_errors": insert_errors[:5],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="shoplog")
    parser.add_argument("--only", choices=["source", "target"], help="Seed one engine only")
    parser.add_argument("--fixture-only", action="store_true", help="Reseed only the small write-replay databases")
    args = parser.parse_args()

    settings = PROFILES[args.profile]
    names = database_names(args.profile)
    report: dict = {}
    engines = {"source": source_client, "target": target_client}
    for engine, make_client in engines.items():
        if args.only and args.only != engine:
            continue
        client = make_client(timeout_ms=300_000)
        report[engine] = []
        plan = [(names["fixture"], settings["fixture_orders"])]
        if not args.fixture_only:
            plan.insert(0, (names["base"], settings["orders"]))
            if engine == "source":
                plan.insert(1, (names["workload"], settings["orders"]))
        for name, count in plan:
            result = seed_database(client, name, args.profile, count)
            report[engine].append(result)
            rejected = [step["step"] for step in result["schema"] if not step["ok"]]
            print(f"{engine}: {name} {result['inserted']} in {result['seconds']}s; rejected: {rejected or 'none'}")

    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"seed_report_{args.profile}.json"
    existing = json.loads(path.read_text()) if path.exists() else {}
    existing.update(report)
    path.write_text(json.dumps(existing, indent=2, default=str))
    print(f"wrote {path.relative_to(OUT_DIR.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
