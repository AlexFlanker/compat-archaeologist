"""Business-question replay: the numbers a CEO or CFO looks at, asked of both systems.

A query can return the same documents and still produce a different business answer, or the reverse.
So beyond replaying queries, this asks the questions the business runs on, and compares the answers.
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta

from bson.codec_options import CodecOptions
from pymongo.errors import PyMongoError

from archaeologist import normalize
from archaeologist.config import OUT_DIR, source_client, target_client
from scripts.generate import AS_OF
from shoplog.analytics import TOTAL_IN_CENTS

REFUND_IN_CENTS = {
    "$cond": [
        {"$eq": [{"$type": "$refund_total"}, "double"]},
        {"$round": [{"$multiply": ["$refund_total", 100]}, 0]},
        "$refund_total",
    ]
}

QUESTIONS = {
    "shoplog": [
        {
            "id": "Q1",
            "question": "What was revenue in each year?",
            "collection": "orders",
            "pipeline": [
                {"$match": {"status": {"$ne": "cancelled"}}},
                {"$group": {"_id": {"$year": "$placed_at"}, "revenue_cents": {"$sum": TOTAL_IN_CENTS}, "orders": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ],
        },
        {
            "id": "Q2",
            "question": "How much was refunded each year?",
            "collection": "orders",
            "pipeline": [
                {"$match": {"refund_total": {"$gt": 0}}},
                {"$group": {"_id": {"$year": "$placed_at"}, "refunded_cents": {"$sum": REFUND_IN_CENTS}, "orders": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ],
        },
        {
            "id": "Q3",
            "question": "How many orders are in each status?",
            "collection": "orders",
            "pipeline": [{"$group": {"_id": "$status", "orders": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
        },
        {
            "id": "Q4",
            "question": "How many customers ordered in the last 90 days?",
            "collection": "orders",
            "pipeline": [
                {"$match": {"placed_at": {"$gte": AS_OF - timedelta(days=90)}, "status": {"$ne": "cancelled"}}},
                {"$group": {"_id": "$customer_id"}},
                {"$count": "customers"},
            ],
        },
        {
            "id": "Q5",
            "question": "Which 5 products sold the most units in 2025?",
            "collection": "orders",
            "pipeline": [
                {"$match": {"placed_at": {"$gte": AS_OF.replace(year=2025, month=1, day=1), "$lt": AS_OF.replace(year=2026, month=1, day=1)}, "status": {"$ne": "cancelled"}}},
                {"$unwind": "$items"},
                {"$group": {"_id": "$items.sku", "units": {"$sum": "$items.qty"}}},
                {"$sort": {"units": -1, "_id": 1}},
                {"$limit": 5},
            ],
        },
        {
            "id": "Q6",
            "question": "How many welcome coupons were redeemed?",
            "collection": "orders",
            "pipeline": [{"$match": {"coupon_code": "WELCOME10"}}, {"$count": "redemptions"}],
        },
        {
            "id": "Q7",
            "question": "How many units are on hand across all products?",
            "collection": "inventory",
            "pipeline": [{"$group": {"_id": None, "on_hand": {"$sum": "$on_hand"}, "reserved": {"$sum": "$reserved"}}}],
        },
        {
            "id": "Q8",
            "question": "How many customers are in each loyalty tier?",
            "collection": "customers",
            "pipeline": [{"$group": {"_id": "$tier", "customers": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
        },
    ],
    "lite": [
        {
            "id": "Q1",
            "question": "How many orders are in each status?",
            "collection": "orders",
            "pipeline": [{"$group": {"_id": "$status", "orders": {"$sum": 1}}}, {"$sort": {"_id": 1}}],
        },
        {
            "id": "Q2",
            "question": "What is the total order value by status?",
            "collection": "orders",
            "pipeline": [{"$group": {"_id": "$status", "total": {"$sum": "$total"}}}, {"$sort": {"_id": 1}}],
        },
        {
            "id": "Q3",
            "question": "Which 5 customers ordered the most?",
            "collection": "orders",
            "pipeline": [{"$group": {"_id": "$customer_id", "orders": {"$sum": 1}}}, {"$sort": {"orders": -1, "_id": 1}}, {"$limit": 5}],
        },
    ],
}


def _with_tz(db):
    return db.client.get_database(db.name, codec_options=CodecOptions(tz_aware=True, uuid_representation=4))


def answer(profile: str, db) -> dict[str, dict]:
    db = _with_tz(db)
    answers = {}
    for question in QUESTIONS[profile]:
        try:
            answers[question["id"]] = {"ok": True, "value": list(db[question["collection"]].aggregate(question["pipeline"]))}
        except PyMongoError as exc:
            answers[question["id"]] = {"ok": False, "code": getattr(exc, "code", None), "error": str(exc)[:200]}
    return answers


def compare(profile: str, source_answers: dict, target_answers: dict) -> list[dict]:
    rows = []
    for question in QUESTIONS[profile]:
        source, target = source_answers[question["id"]], target_answers[question["id"]]
        row = {"id": question["id"], "question": question["question"]}
        if not target["ok"] or not source["ok"]:
            row.update(verdict="error", source=source.get("error") or "ok", target=target.get("error") or "ok")
        else:
            left, right = normalize.comparable(source["value"]), normalize.comparable(target["value"])
            difference = normalize.first_difference(left, right)
            row.update(verdict="different" if difference else "same", difference=difference, source=_summary(left), target=_summary(right))
            if difference:
                row["explanation"] = describe(left, right)
        rows.append(row)
    return rows


def describe(left: list, right: list) -> str:
    """Says in plain words where two grouped answers part ways: which group, which number."""
    def keyed(rows):
        return {json.dumps(row.get("_id"), default=str): row for row in rows if isinstance(row, dict)}

    source, target = keyed(left), keyed(right)
    parts = []
    extra = sorted(set(target) - set(source))
    missing = sorted(set(source) - set(target))
    if extra:
        parts.append(f"FerretDB has extra group(s) {', '.join(key.strip(chr(34)) for key in extra)}")
    if missing:
        parts.append(f"FerretDB is missing group(s) {', '.join(key.strip(chr(34)) for key in missing)}")
    for key in sorted(set(source) & set(target)):
        changes = [
            f"{field} {_number(source[key][field])} → {_number(target[key].get(field))}"
            for field in source[key]
            if field != "_id" and source[key][field] != target[key].get(field)
        ]
        if changes:
            label = "" if key == "null" else f"{key.strip(chr(34))}: "
            parts.append(label + ", ".join(changes))
    return "; ".join(parts[:3]) or "values differ"


def _number(value) -> str:
    return f"{value:,}" if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)


def _summary(value, limit: int = 240) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def run(profile: str) -> dict:
    """Engine comparison: the same untouched data on both engines, so any difference is the engine's."""
    source = source_client(timeout_ms=120_000)[profile]
    target = target_client(timeout_ms=120_000)[profile]
    rows = compare(profile, answer(profile, source), answer(profile, target))
    document = {"profile": profile, "scope": "engine", "rows": rows, "same": sum(1 for row in rows if row["verdict"] == "same")}
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "business_questions.json").write_text(json.dumps(document, indent=2, default=str))
    return document


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    result = run(parser.parse_args().profile)
    print(f"{result['same']} of {len(result['rows'])} business questions give the same answer")
    for row in result["rows"]:
        print(f"  {row['id']} {row['verdict']:9} {row['question']}  {json.dumps(row.get('difference'), default=str)[:160] if row['verdict'] != 'same' else ''}")
