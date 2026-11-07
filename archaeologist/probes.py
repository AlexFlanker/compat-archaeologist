"""Generates one or more probes per ledger item with Meta Muse, and keeps only those MongoDB confirms.

Each probe is validated against the source. If MongoDB disagrees with what the model predicted,
the disagreement goes back to the model. A probe that still fails is marked invalid and never
counted against the target.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from archaeologist import meta_client
from archaeologist.config import PROBES_DIR, source_client
from archaeologist.features import FEATURES
from archaeologist.probe_runner import STEP_OPS, TTL_WAIT_LIMIT_S, run_probe, source_gate

MAX_RETRIES = 2

STEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "op", "label", "command_ejson", "capture", "expect_ejson", "expect_error", "expect_error_code",
        "in_txn", "unordered", "collection", "pipeline_ejson", "full_document", "max_events", "timeout_ms", "seconds",
    ],
    "properties": {
        "op": {"type": "string", "enum": sorted(STEP_OPS)},
        "label": {"type": "string"},
        "command_ejson": {"type": "string"},
        "capture": {"type": "boolean"},
        "expect_ejson": {"type": "string"},
        "expect_error": {"type": "boolean"},
        "expect_error_code": {"type": "integer"},
        "in_txn": {"type": "boolean"},
        "unordered": {"type": "boolean"},
        "collection": {"type": "string"},
        "pipeline_ejson": {"type": "string"},
        "full_document": {"type": "string"},
        "max_events": {"type": "integer"},
        "timeout_ms": {"type": "integer"},
        "seconds": {"type": "integer"},
    },
}

PROBES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["probes"],
    "properties": {
        "probes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["slug", "title", "behavior", "steps"],
                "properties": {
                    "slug": {"type": "string"},
                    "title": {"type": "string"},
                    "behavior": {"type": "string"},
                    "steps": {"type": "array", "items": STEP_SCHEMA},
                },
            },
        }
    },
}

SYSTEM_PROMPT = """You write behavioral compatibility probes for MongoDB-compatible database engines.

A probe is a short script that runs on real MongoDB 8.0 (a replica set) and on a target engine. Results are compared.
Your probes must test what the application relies on, not whether a command is accepted.
An engine can accept a command and then ignore it, so always read back and check the effect:
- transaction: write, abort, then read and show the write is gone. Also test commit in a separate probe. A write inside a transaction must not be visible outside before commit.
- index option: exercise the behavior (a unique index must reject a duplicate; a partial index must only constrain matching documents; a collation must change matching).
- validator: a violating insert must be refused, and a valid one accepted.
- TTL: insert an expired document, use a wait_ttl step of {ttl_wait} seconds, then read and show it is gone.
- aggregation: seed a small dataset and compare exact output, using the same stages and operators the application uses.

Output format: each step has every field. Use "" / false / 0 for fields that don't apply.
ops:
- "command": runs command_ejson with db.command(). command_ejson is a JSON string in MongoDB Extended JSON: use {{"$date": "2026-01-01T00:00:00Z"}} for dates. The first key must be the command name. aggregate needs "cursor": {{}}.
  Set in_txn true to run inside the open transaction.
- "txn_start", "txn_commit", "txn_abort": manage one transaction on one session.
- "watch_open": opens a change stream on `collection`, with pipeline_ejson (a JSON array string, or "") and full_document ("updateLookup" or "").
- "watch_next": reads up to max_events events, waiting up to timeout_ms. Always capture this. Events contain operationType, documentKey, fullDocument, updateDescription, and ns with only {{"coll": ...}}.
- "watch_close": closes the stream.
- "wait_ttl": sleeps for `seconds`.

Capturing and expectations:
- capture true marks a step whose result is compared across engines. Every probe needs at least one captured step with a meaningful result.
- A captured "command" must state expect_ejson: the exact result you predict on MongoDB. For a read: {{"docs": [...]}}. For a write: {{"n": 1, "nModified": 1}} and "upserted": ["id"] if applicable. Other command replies appear as returned, minus ok and server metadata.
- Numbers compare by value, so 5 and 5.0 match. Field order doesn't matter.
- Set unordered true if the read has no sort, so documents compare as a set. Prefer a deterministic sort.
- A step that must fail on MongoDB sets expect_error true and expect_error_code to the code (11000 duplicate key, 121 document validation). Don't set capture or expect_ejson on it: its error code is captured automatically.

Rules:
- Use explicit _id values, so results are predictable. Don't project or compare textScore values, only which documents match and in what order.
- Use short collection names. Each probe runs in its own empty database, so create what you need in the first steps.
- Keep each probe to at most 10 steps, and focus each on a single behavior.
- Write {count} probes covering the distinct behaviors listed, using the exact operators, options and shapes the evidence shows.
- slug is a short lowercase identifier."""


def _user_prompt(item: dict) -> str:
    evidence = json.dumps(item.get("sources", []), indent=1, default=str)
    return (
        f"Ledger item {item['id']}: {item['feature']}\n"
        f"Behavior the application depends on: {item.get('behavior', '')}\n"
        f"Why it is risky on compatible engines: {item.get('why_risky', '')}\n\n"
        f"Evidence from this application:\n{evidence[:6000]}\n\n"
        f"Real operations from its query log:\n{json.dumps(item.get('sample_operations', []), indent=1, default=str)[:6000]}"
    )


def probe_count_for(item: dict) -> int:
    return 1 if item["id"].startswith("X:") else (2 if item["id"] == "F10" else 3)


def _with_defaults(step: dict) -> dict:
    return {
        key: step.get(key)
        for key in step
        if step.get(key) not in ("", None) and not (key in {"expect_error_code", "max_events", "timeout_ms", "seconds"} and step.get(key) == 0)
    }


def _to_probe(item_id: str, raw: dict) -> dict:
    return {
        "probe_id": f"{item_id}.{raw['slug']}",
        "feature_id": item_id,
        "title": raw["title"],
        "behavior": raw["behavior"],
        "steps": [_with_defaults(step) for step in raw["steps"]],
    }


def generate_for_item(item: dict, source) -> list[dict]:
    """Returns validated probes for one item, with invalid ones marked so the report can count them."""
    system = SYSTEM_PROMPT.format(ttl_wait=TTL_WAIT_LIMIT_S - 8, count=probe_count_for(item))
    user = _user_prompt(item)
    response = meta_client.structured(
        purpose=f"probe_generation:{item['id']}",
        system=system,
        user=user,
        schema=PROBES_SCHEMA,
        schema_name="probes",
    )
    validated = []
    for raw in response.get("probes", []):
        attempt, candidate = 0, raw
        while True:
            probe = _to_probe(item["id"], candidate)
            failure = _validate_on_source(probe, source)
            probe["generation"] = {"attempts": attempt + 1, "model": meta_client.model_name()}
            if not failure:
                probe["status"] = "valid"
                validated.append(probe)
                break
            attempt += 1
            if attempt > MAX_RETRIES:
                probe["status"] = "invalid"
                probe["invalid_reason"] = failure
                validated.append(probe)
                break
            retry = meta_client.structured(
                purpose=f"probe_repair:{item['id']}",
                system=system.replace(f"Write {probe_count_for(item)} probes", "Write 1 probe"),
                user=(
                    f"{user}\n\nThis probe failed on real MongoDB 8.0:\n{json.dumps(candidate)[:6000]}\n\n"
                    f"MongoDB's actual behavior: {failure}\n\n"
                    "Fix the probe so that it runs cleanly on MongoDB and predicts the result correctly, "
                    "still testing the same behavior. Return it as the only item in probes."
                ),
                schema=PROBES_SCHEMA,
                schema_name="probes",
            )
            repaired = retry.get("probes", [])
            if not repaired:
                probe["status"] = "invalid"
                probe["invalid_reason"] = failure
                validated.append(probe)
                break
            candidate = repaired[0]
    return validated


def _validate_on_source(probe: dict, source) -> str | None:
    for step in probe["steps"]:
        if step["op"] not in STEP_OPS:
            return f"unknown op {step['op']}"
    run = run_probe(source, probe, "gen")
    return source_gate(probe, run)


def cache_path(profile: str) -> Path:
    return PROBES_DIR / f"{profile}.json"


def load_cached(profile: str) -> list[dict]:
    path = cache_path(profile)
    if not path.exists():
        raise FileNotFoundError(f"No cached probes at {path}. Run: python -m archaeologist.probes --profile {profile}")
    return [probe for probe in json.loads(path.read_text()) if probe.get("status") != "invalid"]


def load_invalid(profile: str) -> list[dict]:
    path = cache_path(profile)
    return [probe for probe in json.loads(path.read_text()) if probe.get("status") == "invalid"] if path.exists() else []


def generate(profile: str, ledger: dict) -> list[dict]:
    source = source_client(timeout_ms=120_000)
    items = [item for item in ledger["items"] if item["status"] == "OBSERVED" or item["id"] in FEATURES or item["id"].startswith("X:")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda item: generate_for_item(item, source), items))
    probes = [probe for group in results for probe in group]
    PROBES_DIR.mkdir(exist_ok=True)
    cache_path(profile).write_text(json.dumps(probes, indent=2))
    return probes


if __name__ == "__main__":
    from archaeologist.config import OUT_DIR

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    arguments = parser.parse_args()
    generated = generate(arguments.profile, json.loads((OUT_DIR / arguments.profile / "ledger.json").read_text()))
    valid = [probe for probe in generated if probe["status"] == "valid"]
    print(f"{len(valid)} valid, {len(generated) - len(valid)} invalid")
    for probe in generated:
        print(f"  {probe['status']:8} {probe['probe_id']} attempts={probe['generation']['attempts']} {probe.get('invalid_reason', '')[:140]}")
