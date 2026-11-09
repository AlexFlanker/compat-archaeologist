"""Generates one or more probes per ledger item with Meta Muse, and keeps only those MongoDB confirms.

Each probe is validated against the source. If MongoDB disagrees with what the model predicted,
the disagreement goes back to the model. A probe that still fails is marked invalid and never
counted against the target.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from archaeologist import meta_client, strategy as strategy_store
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


def probe_hash(probe: dict) -> str:
    return hashlib.sha1(json.dumps(probe["steps"], sort_keys=True, default=str).encode()).hexdigest()[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_for_item(item: dict, source, strategy: dict | None = None) -> list[dict]:
    """Returns validated probes for one item, with invalid ones marked so the report can count them."""
    strategy = strategy or strategy_store.active()
    # Provenance: taken before the first prompt is sent, so a probe never looks newer than its prompt.
    generated_at, prompt_guarded = _now(), meta_client.guard_installed()
    count = strategy_store.count_for(strategy, item)
    system = strategy_store.render_system(SYSTEM_PROMPT.format(ttl_wait=TTL_WAIT_LIMIT_S - 8, count=count), strategy)
    user = _user_prompt(item)
    hint = strategy_store.hint_for(strategy, item["id"])
    if hint:
        user += f"\n\nAdditional guidance for this feature: {hint}"
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
                system=system.replace(f"Write {count} probes", "Write 1 probe"),
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
    for probe in validated:
        probe["probe_hash"] = probe_hash(probe)
        probe["strategy_version"] = strategy["version"]
        probe["generated_at"] = generated_at
        probe["prompt_guarded"] = prompt_guarded
    return validated


def provenance(probes: list[dict], held_out: dict) -> dict:
    """Counts probes by whether their prompts could have carried the held-out slice.

    post_seal: stamped at generation as written at or after the seal, with the prompt guard installed.
    checked_pre_seal: written before these stamps existed, and a recorded check (`pre_seal_check`, naming the
    ledger commit its prompt was built from) found no sealed shape in that ledger item.
    unchecked: everything else; any unchecked probe voids the claim that the model never saw the sealed operations.
    """
    sealed = datetime.fromisoformat(held_out["created"]) if held_out.get("created") else None
    post_seal = checked = 0
    for probe in probes:
        written = probe.get("generated_at")
        check = probe.get("pre_seal_check") or {}
        if sealed and written and probe.get("prompt_guarded") is True and datetime.fromisoformat(written) >= sealed:
            post_seal += 1
        elif check.get("ledger_commit") and check.get("heldout_shapes_in_prompt") == 0:
            checked += 1
    return {"total": len(probes), "post_seal": post_seal, "checked_pre_seal": checked, "unchecked": len(probes) - post_seal - checked}


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


# Features the local oracle can't run, so a probe could never pass the gate there.
ORACLE_UNAVAILABLE = {"F11": "Atlas Search needs Atlas (or mongot); the local MongoDB oracle doesn't have it"}


def generate(
    profile: str,
    ledger: dict,
    strategy: dict | None = None,
    only_features: set[str] | None = None,
    base_probes: list[dict] | None = None,
    out_path: Path | None = None,
    extra_forbidden: list[str] = (),
) -> list[dict]:
    """Writes probes for every item, or only for `only_features` (others carry over from `base_probes`)."""
    from archaeologist import heldout
    from archaeologist.extract_querylog import load_log, query_log_path

    heldout.install_guard(heldout.load_or_create(profile), load_log(query_log_path(profile)), extra_forbidden=extra_forbidden)
    strategy = strategy or strategy_store.active()
    source = source_client(timeout_ms=120_000)
    items = [item for item in ledger["items"] if item["id"] not in ORACLE_UNAVAILABLE]
    chosen = [item for item in items if only_features is None or item["id"] in only_features]
    with ThreadPoolExecutor(max_workers=4) as pool:
        fresh = dict(zip([item["id"] for item in chosen], pool.map(lambda item: generate_for_item(item, source, strategy), chosen)))
    probes = []
    for item in items:
        if item["id"] in fresh:
            probes.extend(fresh[item["id"]])
        else:
            for probe in (base_probes or []):
                if probe["feature_id"] == item["id"]:
                    probe.setdefault("probe_hash", probe_hash(probe))
                    probes.append(probe)
    target = out_path or cache_path(profile)
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(probes, indent=2))
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
