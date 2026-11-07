"""Edge-case fuzz: a model designs awkward documents, MongoDB referees, both engines are compared.

SubQ Preview proposes edge-case documents for ShopLog's real fields, such as dates before 1970,
Decimal128 money, -0, NaN, int64 limits, Unicode, nulls versus missing fields, and type drift.
Cases are cached, so runs repeat exactly. A case MongoDB itself can't store is excluded.
Round-trips are compared with BSON types kept, because an int that comes back as a double is a
real difference that a value-only comparison would hide.

  python -m archaeologist.edge_cases --generate   # ask SubQ Preview for cases (needs SUBQ_API_KEY)
  python -m archaeologist.edge_cases              # run cached cases on both engines
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone

from bson import json_util
from bson.codec_options import CodecOptions
from bson.json_util import CANONICAL_JSON_OPTIONS, JSONOptions
from openai import OpenAI
from pymongo.errors import PyMongoError

from archaeologist.config import EVIDENCE_DIR, LOGS_DIR, OUT_DIR, env, source_client, target_client

MODEL = "subq-preview"
DATABASE = "ca_edge"
COLLECTION = "edge"
STRICT = JSONOptions(json_mode=CANONICAL_JSON_OPTIONS.json_mode, uuid_representation=4)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cases"],
    "properties": {
        "cases": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "description", "document_ejson", "filter_ejson", "why_risky"],
                "properties": {
                    "category": {"type": "string"},
                    "description": {"type": "string"},
                    "document_ejson": {"type": "string"},
                    "filter_ejson": {"type": "string"},
                    "why_risky": {"type": "string"},
                },
            },
        }
    },
}

PROMPT = """You design edge-case test documents for a database migration from MongoDB to a MongoDB-compatible engine.
The application is a 10-year-old e-commerce order system. Its real fields are listed below with the types seen in its data.

Write {count} documents that a long-lived production system really accumulates and that compatible engines often store,
compare, sort or match differently. Cover these categories, several cases each:
- dates: before 1970, far future, millisecond precision, year 0001 or 9999
- numbers: int32 and int64 limits, doubles with -0.0, NaN, Infinity, Decimal128 money values, the same field as int in one document and double in another
- strings: Unicode and emoji, combining accents, case variants of an email, empty string, very long values
- null versus a missing field versus an empty array or object
- arrays: empty, nested, mixed types, a single element compared with a scalar
- objects: deep nesting, keys in an unusual order
- other BSON types: ObjectId, Binary, UUID, boolean versus 0 and 1

Rules:
- document_ejson is ONE document in MongoDB Extended JSON v2 canonical form, so types are explicit:
  {{"$numberInt": "5"}}, {{"$numberLong": "9223372036854775807"}}, {{"$numberDouble": "-0.0"}}, {{"$numberDouble": "NaN"}},
  {{"$numberDecimal": "19.99"}}, {{"$date": {{"$numberLong": "-86400000"}}}}, {{"$oid": "..."}}.
  Do not include _id; it is assigned for you. Use field names from the application where they fit.
- filter_ejson is a find filter, in the same Extended JSON, that the application could plausibly run and that should match this document.
- Keep each document under 2 KB. Don't use field names that start with $ or contain dots.

Application fields:
{fields}"""


def _fields_summary() -> str:
    profile = json.loads((EVIDENCE_DIR / "shoplog_data_profile.json").read_text())
    lines = []
    for name in ("orders", "customers", "products", "carts"):
        fields = profile["collections"][name]["fields"]
        lines.append(f"{name}: " + ", ".join(f"{key} ({'/'.join(info.get('types', {}) or ['array'])})" for key, info in list(fields.items())[:16]))
    return "\n".join(lines)


def _record(entry: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / "subq_calls.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


BATCHES = [
    "dates (before 1970, far future, millisecond precision, year 0001 or 9999) and timestamps stored as strings",
    "numbers: int32 and int64 limits, -0.0, NaN, Infinity, Decimal128 money, and the same field as int in one document and double in another",
    "strings (Unicode, emoji, combining accents, email case variants, empty, very long) and null versus missing versus empty",
    "arrays (empty, nested, mixed types, single element versus scalar), deep objects, unusual key order, ObjectId, Binary, UUID, boolean versus 0 and 1",
]


def _generate_batch(client, focus: str, count: int) -> list[dict]:
    prompt = PROMPT.format(count=count, fields=_fields_summary()) + f"\n\nFor this batch, focus only on: {focus}."
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_schema", "json_schema": {"name": "edge_cases", "schema": SCHEMA}},
        max_completion_tokens=16000,
    )
    latency = round(time.perf_counter() - started, 2)
    try:
        cases = json.loads(response.choices[0].message.content or "{}").get("cases", [])
        status = "ok"
    except json.JSONDecodeError:
        cases, status = [], "invalid_json"
    _record(
        {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": "edge_case_generation",
            "model": MODEL,
            "status": status,
            "latency_s": latency,
            "input_tokens": getattr(response.usage, "prompt_tokens", None),
            "output_tokens": getattr(response.usage, "completion_tokens", None),
            "cases": len(cases),
        }
    )
    print(f"  [subq] {MODEL} · edge_case_generation · {latency}s · {len(cases)} cases · {status}")
    return cases


def generate(count: int = 48) -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor

    client = OpenAI(base_url="https://api.subq.ai/v1", api_key=env("SUBQ_API_KEY"), timeout=180, max_retries=1)
    per_batch = max(1, count // len(BATCHES))
    with ThreadPoolExecutor(max_workers=len(BATCHES)) as pool:
        batches = list(pool.map(lambda focus: _generate_batch(client, focus, per_batch), BATCHES))
    cases = [case for batch in batches for case in batch]
    prompt = PROMPT.format(count=per_batch, fields=_fields_summary())
    for position, case in enumerate(cases, start=1):
        case["id"] = f"E{position:02d}"
    document = {"model": MODEL, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "prompt_sha256_16": hashlib.sha256(prompt.encode()).hexdigest()[:16], "cases": cases}
    (EVIDENCE_DIR / "shoplog_edge_cases.json").write_text(json.dumps(document, indent=2))
    return cases


def _strict(value) -> str:
    """Extended JSON with every BSON type spelled out, keys sorted so field order doesn't count here."""
    return json.dumps(json.loads(json_util.dumps(value, json_options=STRICT)), sort_keys=True)


def _field_order(document: dict) -> list:
    return [key for key in document.keys()]


def _run_engine(client, cases: list[dict]) -> dict[str, dict]:
    client.drop_database(DATABASE)
    collection = client[DATABASE].get_collection(COLLECTION, codec_options=CodecOptions(tz_aware=False, uuid_representation=4))
    results = {}
    for case in cases:
        result: dict = {}
        try:
            document = {"_id": case["id"], **json_util.loads(case["document_ejson"])}
        except (ValueError, TypeError) as exc:
            results[case["id"]] = {"parse_error": str(exc)[:160]}
            continue
        try:
            collection.insert_one(document)
            stored = collection.find_one({"_id": case["id"]})
            result["stored"] = _strict(stored)
            result["field_order"] = _field_order(stored or {})
        except PyMongoError as exc:
            result["insert_error"] = f"{getattr(exc, 'code', None)}: {str(exc)[:160]}"
        try:
            query = json_util.loads(case["filter_ejson"]) if case.get("filter_ejson") else {}
            matched = sorted(str(doc["_id"]) for doc in collection.find(query, {"_id": 1}))
            result["filter_matches"] = matched
        except (PyMongoError, ValueError, TypeError) as exc:
            result["filter_error"] = f"{getattr(exc, 'code', None)}: {str(exc)[:160]}"
        results[case["id"]] = result

    # Whole-collection behaviors: how mixed types sort, and how $type sees them.
    summary = {}
    try:
        fields = sorted({key for case in cases for key in _safe_keys(case)})[:12]
        for field in fields:
            order = [str(doc["_id"]) for doc in collection.find({field: {"$exists": True}}, {"_id": 1}).sort([(field, 1), ("_id", 1)])]
            types = {str(doc["_id"]): doc.get("t") for doc in collection.aggregate([{"$match": {field: {"$exists": True}}}, {"$project": {"t": {"$type": f"${field}"}}}])}
            summary[field] = {"sort_order": order, "types": types}
    except PyMongoError as exc:
        summary["error"] = f"{getattr(exc, 'code', None)}: {str(exc)[:160]}"
    client.drop_database(DATABASE)
    return {"cases": results, "collection": summary}


def _safe_keys(case: dict) -> list[str]:
    try:
        return [key for key in json_util.loads(case["document_ejson"]).keys() if not key.startswith("$")]
    except (ValueError, TypeError, AttributeError):
        return []


def run(target_label: str = "FerretDB", self_check: bool = False) -> dict:
    path = EVIDENCE_DIR / "shoplog_edge_cases.json"
    if not path.exists():
        return {"available": False, "reason": "no cached edge cases; run with --generate"}
    cached = json.loads(path.read_text())
    cases = cached["cases"]
    source = source_client(timeout_ms=60_000)
    target = source if self_check else target_client(timeout_ms=60_000)
    source_run = _run_engine(source, cases)
    target_run = _run_engine(target, cases)

    rows = []
    for case in cases:
        left, right = source_run["cases"].get(case["id"], {}), target_run["cases"].get(case["id"], {})
        if "parse_error" in left:
            verdict, detail = "invalid_case", left["parse_error"]
        elif "insert_error" in left:
            verdict, detail = "invalid_case", "MongoDB refused it: " + left["insert_error"]
        elif "insert_error" in right:
            verdict, detail = "refused", right["insert_error"]
        elif left.get("stored") != right.get("stored"):
            verdict, detail = "stored_differently", _first_change(left.get("stored"), right.get("stored"))
        elif left.get("filter_matches") != right.get("filter_matches") or ("filter_error" in left) != ("filter_error" in right):
            verdict, detail = "query_differs", f"MongoDB {left.get('filter_matches', left.get('filter_error'))} vs target {right.get('filter_matches', right.get('filter_error'))}"
        elif left.get("field_order") != right.get("field_order"):
            verdict, detail = "field_order_differs", f"{left.get('field_order')} vs {right.get('field_order')}"
        else:
            verdict, detail = "same", ""
        rows.append({"id": case["id"], "category": case.get("category"), "description": case.get("description"), "verdict": verdict, "detail": str(detail)[:300]})

    sort_differences = [
        field
        for field, info in source_run["collection"].items()
        if isinstance(info, dict) and info != target_run["collection"].get(field)
    ]
    valid = [row for row in rows if row["verdict"] != "invalid_case"]
    counts: dict[str, int] = {}
    for row in valid:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    document = {
        "available": True,
        "generator": cached["model"],
        "target": "MongoDB (self-check)" if self_check else target_label,
        "cases": len(cases),
        "valid": len(valid),
        "excluded": len(rows) - len(valid),
        "counts": counts,
        "different": sum(count for verdict, count in counts.items() if verdict != "same"),
        "sort_or_type_differs_on_fields": sort_differences,
        "rows": rows,
    }
    (OUT_DIR / "shoplog").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "shoplog" / ("edge_cases_self.json" if self_check else "edge_cases.json")).write_text(json.dumps(document, indent=2, default=str))
    return document


def _first_change(left: str | None, right: str | None) -> str:
    if left is None or right is None:
        return f"MongoDB {left} vs target {right}"
    a, b = json.loads(left), json.loads(right)
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            return f"{key}: MongoDB {json.dumps(a.get(key))[:110]} vs target {json.dumps(b.get(key))[:110]}"
    return "differs"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--count", type=int, default=48)
    arguments = parser.parse_args()
    if arguments.generate:
        generate(arguments.count)
    result = run(self_check=arguments.self_check)
    if not result["available"]:
        print(result["reason"])
    else:
        print(f"{result['target']}: {result['valid']} valid cases ({result['excluded']} excluded), {result['different']} differ: {result['counts']}")
        print("  sort or $type differs on fields:", result["sort_or_type_differs_on_fields"])
        for row in result["rows"]:
            if row["verdict"] not in {"same"}:
                print(f"  {row['id']} [{row['category']}] {row['verdict']}: {row['description'][:70]} | {row['detail'][:150]}")
