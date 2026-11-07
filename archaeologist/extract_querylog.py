"""Turns an exported profiler log into an operator inventory and per-feature evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from bson import json_util

from archaeologist.config import EVIDENCE_DIR
from archaeologist.features import EXTRA_STAGE_TOKENS, extra_item_id

# Driver and session bookkeeping that says nothing about what the application asked for.
SESSION_FIELDS = {
    "lsid",
    "$clusterTime",
    "$db",
    "$readPreference",
    "txnNumber",
    "autocommit",
    "startTransaction",
    "writeConcern",
    "readConcern",
    "apiVersion",
    "comment",
    "stmtId",
    "stmtIds",
}
SIMPLE_FILTER_OPERATORS = {"$in", "$nin", "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$exists"}


def load_log(path: Path) -> list[dict]:
    with path.open() as handle:
        return [json_util.loads(line) for line in handle if line.strip()]


def clean_command(entry: dict) -> dict:
    return {key: value for key, value in entry.get("command", {}).items() if key not in SESSION_FIELDS}


def collection_of(entry: dict) -> str:
    return entry.get("ns", "").split(".", 1)[-1]


def in_transaction(entry: dict) -> bool:
    return entry.get("command", {}).get("autocommit") is False


def pipeline_of(entry: dict) -> list[dict]:
    command = entry.get("command", {})
    return command.get("pipeline") or entry.get("originatingCommand", {}).get("pipeline") or []


def stages_of(entry: dict) -> list[str]:
    return [next(iter(stage)) for stage in pipeline_of(entry) if stage]


def operators_in(value) -> Counter:
    found: Counter = Counter()
    if isinstance(value, dict):
        for key, inner in value.items():
            if key.startswith("$") and not key.startswith("$$"):
                found[key] += 1
            found.update(operators_in(inner))
    elif isinstance(value, list):
        for inner in value:
            found.update(operators_in(inner))
    return found


def _abstract(value):
    if isinstance(value, dict):
        return {key: _abstract(inner) for key, inner in value.items()}
    if isinstance(value, list):
        unique, seen = [], set()
        for inner in value:
            shape = _abstract(inner)
            marker = json.dumps(shape, sort_keys=True, default=str)
            if marker not in seen:
                seen.add(marker)
                unique.append(shape)
        return unique
    return "?"


def shape_of(entry: dict) -> str:
    """A stable description of the operation with every literal value removed."""
    source = clean_command(entry) if entry.get("op") != "getmore" else {"getMore": "?", "of": entry.get("originatingCommand", {}).get("pipeline", "cursor")}
    abstracted = _abstract(source)
    return json.dumps({"op": entry.get("op"), "coll": collection_of(entry), "cmd": abstracted}, sort_keys=True, default=str)


def _has_only_simple_filter(filter_doc: dict) -> bool:
    for key, condition in filter_doc.items():
        if key.startswith("$"):
            return False
        if isinstance(condition, dict) and any(
            op.startswith("$") and op not in SIMPLE_FILTER_OPERATORS for op in condition
        ):
            return False
    return True


def features_of(entry: dict) -> set[str]:
    command = entry.get("command", {})
    op = entry.get("op")
    features: set[str] = set()
    stages = stages_of(entry)

    if "$changeStream" in stages:
        features.add("F5")
    if "$search" in stages or "$searchMeta" in stages:
        features.add("F11")
    if "$facet" in stages or any(
        isinstance(stage.get("$lookup"), dict) and "pipeline" in stage["$lookup"] for stage in pipeline_of(entry)
    ):
        features.add("F1")
    if "$setWindowFields" in stages:
        features.add("F2")
    update_doc = command.get("u", {})
    if "arrayFilters" in command or (isinstance(update_doc, dict) and "$[" in json.dumps(update_doc, default=str)):
        features.add("F3")
    if in_transaction(entry):
        features.add("F4")
    if "collation" in command:
        features.add("F6")
    if "$text" in operators_in(command.get("filter", {})):
        features.add("F9")
    for token in EXTRA_STAGE_TOKENS:
        if token in stages:
            features.add(extra_item_id(token))

    if not features and op in {"query", "update", "remove", "insert"}:
        filter_doc = command.get("filter", command.get("q", {})) or {}
        update_is_pipeline = isinstance(update_doc, list)
        if _has_only_simple_filter(filter_doc) and not update_is_pipeline:
            features.add("F10")
    return features


KEY_FIELDS = {"_id", "customer_id", "email", "items.sku", "sku"}
ANALYTIC_STAGES = {"$group", "$facet", "$setWindowFields", "$bucket", "$bucketAuto"}


def path_of(entry: dict) -> str:
    """Online requests are keyed to one customer or order. Reporting scans ranges. Event-driven reads streams."""
    stages = stages_of(entry)
    if "$changeStream" in stages:
        return "event-driven"
    command = entry.get("command", {})
    pipeline = pipeline_of(entry)
    first_filter = command.get("filter") or command.get("q") or (pipeline[0].get("$match", {}) if pipeline else {})
    keyed = isinstance(first_filter, dict) and bool(KEY_FIELDS & set(first_filter))
    if set(stages) & ANALYTIC_STAGES and not keyed:
        return "reporting"
    if not keyed and pipeline and "$lookup" in stages:
        return "reporting"
    return "online"


def _percentile(sorted_values: list[int], fraction: float) -> int:
    if not sorted_values:
        return 0
    return sorted_values[min(len(sorted_values) - 1, int(fraction * len(sorted_values)))]


def summarize(entries: list[dict]) -> dict:
    operators: Counter = Counter()
    stages: Counter = Counter()
    by_shape: dict[str, list[dict]] = defaultdict(list)
    feature_ops: Counter = Counter()
    feature_shapes: dict[str, set[str]] = defaultdict(set)
    index_usage: Counter = Counter()
    fields_written: Counter = Counter()
    rejected_inserts: Counter = Counter()

    for entry in entries:
        command = clean_command(entry)
        operators.update(operators_in(command))
        stages.update(stages_of(entry))
        shape = shape_of(entry)
        by_shape[shape].append(entry)
        shape_id = "S" + hashlib.sha1(shape.encode()).hexdigest()[:8]
        for feature in features_of(entry):
            feature_ops[feature] += 1
            feature_shapes[feature].add(shape_id)
        plan = entry.get("planSummary")
        if plan and "IXSCAN" in plan:
            index_usage[f"{collection_of(entry)}: {plan}"] += 1
        update_doc = command.get("u")
        if isinstance(update_doc, dict):
            for operator_fields in update_doc.values():
                if isinstance(operator_fields, dict):
                    for field in operator_fields:
                        fields_written[f"{collection_of(entry)}.{field}"] += 1
        if entry.get("op") == "insert" and entry.get("ninserted") == 0 and not in_transaction(entry):
            rejected_inserts[collection_of(entry)] += 1

    shapes = []
    for shape, group in sorted(by_shape.items(), key=lambda item: -len(item[1])):
        millis = sorted(entry.get("millis", 0) for entry in group)
        shapes.append(
            {
                "id": "S" + hashlib.sha1(shape.encode()).hexdigest()[:8],
                "shape": shape,
                "count": len(group),
                "collection": collection_of(group[0]),
                "op": group[0].get("op"),
                "path": path_of(group[0]),
                "features": sorted(features_of(group[0])),
                "operators": sorted(operators_in(clean_command(group[0]))),
                "p50_ms": _percentile(millis, 0.5),
                "p95_ms": _percentile(millis, 0.95),
                "plan": group[0].get("planSummary"),
            }
        )

    return {
        "total_entries": len(entries),
        "by_op": dict(Counter(entry.get("op") for entry in entries).most_common()),
        "operators": dict(operators.most_common()),
        "stages": dict(stages.most_common()),
        "features": {
            feature: {"ops": feature_ops[feature], "shapes": sorted(feature_shapes[feature])}
            for feature in sorted(feature_ops)
        },
        "index_usage": dict(index_usage.most_common()),
        "fields_written": dict(fields_written.most_common()),
        "rejected_inserts": dict(rejected_inserts),
        "shapes": shapes,
    }


def query_log_path(profile: str) -> Path:
    return EVIDENCE_DIR / ("query_log.jsonl" if profile == "shoplog" else f"{profile}_query_log.jsonl")


def run(profile: str) -> dict:
    summary = summarize(load_log(query_log_path(profile)))
    (EVIDENCE_DIR / f"{profile}_querylog_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    result = run(parser.parse_args().profile)
    print(f"{result['total_entries']} entries, {len(result['shapes'])} shapes")
    print("features:", {key: value["ops"] for key, value in result["features"].items()})
    print("stages:", result["stages"])
    print("rejected inserts:", result["rejected_inserts"])
