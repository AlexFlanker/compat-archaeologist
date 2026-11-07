"""Atlas as an evidence source: seed ShopLog, run its workload including Atlas Search, then
collect what Atlas observed through the MongoDB MCP Server.

Atlas is never used for the latency comparison; that stays local, Docker against Docker.

  python scripts/atlas_evidence.py run       # seed, build the search index, run the workload
  python scripts/atlas_evidence.py collect   # Performance Advisor, schema and search indexes via MCP
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo import MongoClient  # noqa: E402
from pymongo.errors import PyMongoError  # noqa: E402

from archaeologist import mcp_tools  # noqa: E402
from archaeologist.config import EVIDENCE_DIR, env  # noqa: E402
from scripts import generate  # noqa: E402
from scripts.seed import PROFILES, seed_database  # noqa: E402
from shoplog import analytics, catalog, customers, orders  # noqa: E402
from shoplog.schema import ensure_search_index  # noqa: E402

DATABASE = "shoplog"
SEARCHES = ["wireles headphones", "noise cancelling speaker", "rugged tnt", "smart lamp", "ergonomic chair", "quiet kettle"]
PREFIXES = ["wir", "port", "smar", "ergo", "rug", "qui"]


def _client() -> MongoClient:
    uri = env("MONGODB_URI_ATLAS")
    if not uri:
        raise SystemExit("MONGODB_URI_ATLAS is not set in .env")
    return MongoClient(uri, serverSelectionTimeoutMS=15000, tz_aware=True)


def _wait_for_search_index(db, timeout_s: int = 300) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        indexes = list(db.products.list_search_indexes("storefront"))
        if indexes and indexes[0].get("queryable"):
            return True
        time.sleep(5)
    return False


def run() -> int:
    client = _client()
    settings = PROFILES["shoplog"]
    print("seeding ShopLog on Atlas…", flush=True)
    report = seed_database(client, DATABASE, "shoplog", settings["orders"])
    print(f"  {report['inserted']} in {report['seconds']}s; rejected: {[step['step'] for step in report['schema'] if not step['ok']] or 'none'}")
    db = client[DATABASE]
    search_step = ensure_search_index(db)
    print(f"  search index: {search_step}")
    ready = search_step["ok"] and _wait_for_search_index(db)
    print(f"  search index queryable: {ready}")

    rng = random.Random(generate.SEED + 200)
    all_customers = generate.generate_customers(settings["customers"])
    all_products = generate.generate_products(settings["products"])
    counts = {"ok": 0, "failed": 0}

    def attempt(action):
        try:
            action()
            counts["ok"] += 1
        except PyMongoError:
            counts["failed"] += 1

    started = time.perf_counter()
    for _ in range(60):
        if ready:
            attempt(lambda: catalog.storefront_search(db, rng.choice(SEARCHES)))
            attempt(lambda: catalog.autocomplete(db, rng.choice(PREFIXES)))
        attempt(lambda: catalog.search_products(db, rng.choice(["wireless", "premium", "smart lamp"])))
        email = rng.choice(all_customers)["email"]
        attempt(lambda: customers.find_by_email(db, email.upper()))
        attempt(lambda: orders.orders_containing_sku(db, rng.choice(all_products)["_id"], generate.AS_OF - timedelta(days=3650)))
    for _ in range(20):
        year, month = rng.randrange(2016, 2026), rng.randrange(1, 13)
        since = generate.AS_OF.replace(year=year, month=month, day=1)
        attempt(lambda: analytics.revenue_report(db, since, since + timedelta(days=92)))
        attempt(lambda: analytics.customer_spend_timeline(db, rng.choice(all_customers)["_id"]))
    print(f"workload on Atlas: {counts} in {time.perf_counter() - started:.0f}s")
    print("Performance Advisor needs a few minutes to pick up slow queries. Then run: python scripts/atlas_evidence.py collect")
    return 0


def _advisor_arguments(schema: dict, project_id: str, cluster: str, operation: str | None) -> dict:
    properties = schema.get("properties", {})
    arguments = {}
    for key in properties:
        lowered = key.lower()
        if "project" in lowered:
            arguments[key] = project_id
        elif "cluster" in lowered:
            arguments[key] = cluster
    if operation:
        for key, spec in properties.items():
            if spec.get("type") == "array" and "operation" in key.lower():
                arguments[key] = [operation]
    return arguments


def collect() -> int:
    project_id, cluster = env("ATLAS_PROJECT_ID"), env("ATLAS_CLUSTER_NAME")
    if not (project_id and cluster):
        raise SystemExit("ATLAS_PROJECT_ID and ATLAS_CLUSTER_NAME must be set in .env")
    uri = env("MONGODB_URI_ATLAS")

    schemas = mcp_tools.list_tool_schemas(uri, with_atlas_api=True)
    advisor_schema = schemas.get("atlas-get-performance-advisor")
    calls: list[tuple[str, dict]] = []
    if advisor_schema:
        calls.append(("atlas-get-performance-advisor", _advisor_arguments(advisor_schema, project_id, cluster, None)))
    calls += [
        ("collection-schema", {"connectionId": "preconfigured", "database": DATABASE, "collection": "orders", "sampleSize": 200}),
        ("aggregate", {"connectionId": "preconfigured", "database": DATABASE, "collection": "products", "pipeline": [{"$listSearchIndexes": {}}]}),
        ("mongodb-logs", {"connectionId": "preconfigured", "type": "global", "limit": 200}),
    ]
    results = mcp_tools.call_tools(uri, calls, label="Atlas", with_atlas_api=True)

    document = {"cluster": cluster, "advisor_tool_available": bool(advisor_schema), "results": {}}
    for (name, _), result in zip(calls, results):
        document["results"][name] = {"ok": result["ok"], "text": result.get("text", "")[:200_000]}
    EVIDENCE_DIR.mkdir(exist_ok=True)
    path = EVIDENCE_DIR / "shoplog_atlas_raw.json"
    path.write_text(json.dumps(document, indent=2))
    print(f"wrote {path.relative_to(EVIDENCE_DIR.parent)}: " + ", ".join(f"{name}={'ok' if value['ok'] else 'error'}" for name, value in document["results"].items()))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "collect"])
    raise SystemExit(run() if parser.parse_args().action == "run" else collect())
