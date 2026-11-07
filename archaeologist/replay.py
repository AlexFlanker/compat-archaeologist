"""Replays the recorded workload on two engines, comparing results and client-side latency.

Reads run against the pristine base database. Writes run in order against a small fixture
that is reset first, then the final state of both engines is compared.
"""

from __future__ import annotations

import hashlib
import time
from collections import Counter, defaultdict

from bson.codec_options import CodecOptions
from pymongo.errors import PyMongoError

from archaeologist import normalize
from archaeologist.extract_querylog import (
    clean_command,
    collection_of,
    features_of,
    in_transaction,
    path_of,
    shape_of,
    stages_of,
)

EXCLUDED_COLLECTIONS = {"inventory_events", "sync_state"}
MAX_PER_SHAPE = 40
SHAPE_BUDGET_S = 25.0
OP_TIMEOUT_MS = 20_000
MAX_DOCS = 500
STATE_COLLECTIONS = ["orders", "customers", "inventory", "carts"]
RANKING_ONLY = "same matches, ranked differently"


def partition(entries: list[dict]) -> dict:
    """Splits the log into what can be replayed, and accounts for everything else by reason."""
    reads, writes = [], []
    excluded: Counter = Counter()
    for entry in entries:
        op = entry.get("op")
        command = entry.get("command", {})
        if "$truncated" in command:
            excluded["command truncated by the profiler"] += 1
        elif "$changeStream" in stages_of(entry):
            excluded["change stream (covered by probes)"] += 1
        elif in_transaction(entry):
            excluded["inside a transaction (covered by probes)"] += 1
        elif collection_of(entry) in EXCLUDED_COLLECTIONS:
            excluded["sync worker bookkeeping"] += 1
        elif op == "insert":
            excluded["insert: the profiler doesn't keep the documents"] += 1
        elif op in {"getmore"} or "killCursors" in command:
            excluded["cursor maintenance"] += 1
        elif "find" in command or "aggregate" in command:
            reads.append(entry)
        elif op in {"update", "remove"}:
            writes.append(entry)
        else:
            excluded[f"other: {op}"] += 1
    return {"reads": reads, "writes": writes, "excluded": dict(excluded)}


def _has_sort(command: dict) -> bool:
    return "sort" in command or any("$sort" in stage for stage in command.get("pipeline", []))


def _execute_read(db, command: dict) -> list:
    reply = db.command({**command, "maxTimeMS": OP_TIMEOUT_MS})
    cursor = reply["cursor"]
    documents = list(cursor.get("firstBatch", []))
    collection = cursor["ns"].split(".", 1)[-1]
    cursor_id = cursor.get("id")
    while cursor_id and len(documents) < MAX_DOCS:
        more = db.command({"getMore": cursor_id, "collection": collection})["cursor"]
        documents.extend(more.get("nextBatch", []))
        cursor_id = more.get("id")
    if cursor_id:
        try:
            db.command({"killCursors": collection, "cursors": [cursor_id]})
        except PyMongoError:
            pass
    return documents


def _timed(db, command: dict) -> dict:
    started = time.perf_counter()
    try:
        documents = _execute_read(db, command)
    except PyMongoError as exc:
        return {"ok": False, "ms": (time.perf_counter() - started) * 1000, "code": getattr(exc, "code", None), "message": str(exc)[:300]}
    return {"ok": True, "ms": (time.perf_counter() - started) * 1000, "docs": documents}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 2)


def _with_tz(client, name: str):
    return client[name].with_options(codec_options=CodecOptions(tz_aware=True, uuid_representation=4))


def replay_reads(source, target, database: str, reads: list[dict]) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry in reads:
        groups[shape_of(entry)].append(entry)

    source_db, target_db = _with_tz(source, database), _with_tz(target, database)
    source_all, target_all, shapes = [], [], []
    swap = False

    for shape, entries in sorted(groups.items(), key=lambda item: -len(item[1])):
        command_sample = clean_command(entries[0])
        ordered = _has_sort(command_sample)
        # Warm both engines for this shape so the first timed run isn't charged for a cold cache.
        _timed(source_db, command_sample)
        _timed(target_db, command_sample)

        source_ms, target_ms = [], []
        equal = different = target_errors = source_errors = timed_out = 0
        first_difference = first_error = None
        kinds: Counter = Counter()
        started = time.perf_counter()
        for entry in entries[:MAX_PER_SHAPE]:
            if time.perf_counter() - started > SHAPE_BUDGET_S:
                timed_out += 1
                break
            command = clean_command(entry)
            first, second = (target_db, source_db) if swap else (source_db, target_db)
            swap = not swap
            result_first, result_second = _timed(first, command), _timed(second, command)
            source_result, target_result = (result_second, result_first) if first is target_db else (result_first, result_second)

            if not source_result["ok"]:
                source_errors += 1
                continue
            source_ms.append(source_result["ms"])
            if not target_result["ok"]:
                target_errors += 1
                if first_error is None:
                    first_error = {"code": target_result.get("code"), "message": target_result.get("message")}
                continue
            target_ms.append(target_result["ms"])
            left = normalize.comparable({"docs": source_result["docs"]}, not ordered)
            right = normalize.comparable({"docs": target_result["docs"]}, not ordered)
            difference = normalize.first_difference(left, right)
            if difference:
                different += 1
                kind = _difference_kind(source_result["docs"], target_result["docs"])
                if kind == "different documents" and _same_matches_without_limit(source_db, target_db, command):
                    kind = RANKING_ONLY
                kinds[kind] += 1
                if first_difference is None:
                    first_difference = difference
            else:
                equal += 1

        source_all.extend(source_ms)
        target_all.extend(target_ms)
        shapes.append(
            {
                "shape_id": _shape_id(shape),
                "collection": collection_of(entries[0]),
                "path": path_of(entries[0]),
                "features": sorted(features_of(entries[0])),
                "source_ms_samples": [round(value, 2) for value in source_ms],
                "target_ms_samples": [round(value, 2) for value in target_ms],
                "target_timeouts": target_errors if first_error and "time" in str(first_error.get("message", "")).lower() else 0,
                "stages": stages_of(entries[0]),
                "replayed": equal + different + target_errors,
                "in_log": len(entries),
                "equal": equal,
                "different": different,
                "target_errors": target_errors,
                "source_errors": source_errors,
                "budget_exhausted": bool(timed_out),
                "source_p50_ms": _percentile(source_ms, 0.5),
                "source_p95_ms": _percentile(source_ms, 0.95),
                "target_p50_ms": _percentile(target_ms, 0.5),
                "target_p95_ms": _percentile(target_ms, 0.95),
                "first_difference": first_difference,
                "difference_kinds": dict(kinds),
                "ranking_only": different > 0 and kinds[RANKING_ONLY] == different,
                "first_error": first_error,
            }
        )

    return {
        "shapes": shapes,
        "overall": {
            "source_p50_ms": _percentile(source_all, 0.5),
            "source_p95_ms": _percentile(source_all, 0.95),
            "target_p50_ms": _percentile(target_all, 0.5),
            "target_p95_ms": _percentile(target_all, 0.95),
            "compared_on_both": len(target_all),
        },
    }


def _shape_id(shape: str) -> str:
    return "S" + hashlib.sha1(shape.encode()).hexdigest()[:8]


def _same_matches_without_limit(source_db, target_db, command: dict) -> bool:
    """A top-N query can differ only because the engines score ties differently. Checks the full match set."""
    if "limit" not in command:
        return False
    unlimited = {key: value for key, value in command.items() if key not in {"limit", "singleBatch"}}
    source_result, target_result = _timed(source_db, unlimited), _timed(target_db, unlimited)
    if not (source_result["ok"] and target_result["ok"]):
        return False
    source_ids = sorted(str(doc.get("_id")) for doc in source_result["docs"])
    target_ids = sorted(str(doc.get("_id")) for doc in target_result["docs"])
    return source_ids == target_ids


def _difference_kind(source_docs: list, target_docs: list) -> str:
    """Tells a person whether the engines returned different documents or only ranked them differently."""
    source_ids = sorted(str(doc.get("_id")) for doc in source_docs if isinstance(doc, dict))
    target_ids = sorted(str(doc.get("_id")) for doc in target_docs if isinstance(doc, dict))
    if len(source_docs) != len(target_docs):
        return "different number of documents"
    if source_ids != target_ids:
        return "different documents"
    return "same documents, different order or field values"


def _write_command(entry: dict) -> dict:
    collection = collection_of(entry)
    command = clean_command(entry)
    if entry["op"] == "update":
        return {"update": collection, "updates": [command]}
    return {"delete": collection, "deletes": [{"q": command.get("q", {}), "limit": command.get("limit", 0)}]}


def _state(db, collections: list[str]) -> dict:
    state = {}
    for name in collections:
        query = {"status": {"$ne": "active"}} if name == "carts" else {}
        state[name] = {str(doc["_id"]): normalize.canonical(doc) for doc in db[name].find(query)}
    return state


def replay_writes(source, target, database: str, writes: list[dict], collections: list[str] | None = None) -> dict:
    """Assumes `database` was just reset on both engines."""
    source_db, target_db = _with_tz(source, database), _with_tz(target, database)
    source_ms, target_ms = [], []
    equal = different = target_errors = source_errors = 0
    first_difference = first_error = None
    by_feature: dict[str, Counter] = defaultdict(Counter)

    for entry in writes:
        command = _write_command(entry)
        features = sorted(features_of(entry))
        started = time.perf_counter()
        source_reply = None
        try:
            source_reply = normalize.reduce_result(source_db.command(command))
            source_ms.append((time.perf_counter() - started) * 1000)
        except PyMongoError:
            source_errors += 1
        started = time.perf_counter()
        try:
            target_reply = normalize.reduce_result(target_db.command(command))
            target_ms.append((time.perf_counter() - started) * 1000)
        except PyMongoError as exc:
            target_errors += 1
            for feature in features:
                by_feature[feature]["errors"] += 1
            if first_error is None:
                first_error = {"code": getattr(exc, "code", None), "message": str(exc)[:300], "features": features}
            continue
        if source_reply is None:
            continue
        difference = normalize.first_difference(normalize.comparable(source_reply), normalize.comparable(target_reply))
        if difference:
            different += 1
            for feature in features:
                by_feature[feature]["different"] += 1
            if first_difference is None:
                first_difference = {**difference, "features": features}
        else:
            equal += 1
            for feature in features:
                by_feature[feature]["equal"] += 1

    names = collections or STATE_COLLECTIONS
    source_state, target_state = _state(source_db, names), _state(target_db, names)
    final_state_differences = {}
    for name in names:
        difference = normalize.first_difference(source_state[name], target_state[name])
        if difference:
            changed = sum(
                1
                for key in set(source_state[name]) | set(target_state[name])
                if source_state[name].get(key) != target_state[name].get(key)
            )
            final_state_differences[name] = {"documents_differing": changed, "first": difference}

    return {
        "replayed": len(writes),
        "equal": equal,
        "different": different,
        "target_errors": target_errors,
        "source_errors": source_errors,
        "first_difference": first_difference,
        "first_error": first_error,
        "source_p50_ms": _percentile(source_ms, 0.5),
        "source_p95_ms": _percentile(source_ms, 0.95),
        "target_p50_ms": _percentile(target_ms, 0.5),
        "target_p95_ms": _percentile(target_ms, 0.95),
        "by_feature": {feature: dict(counts) for feature, counts in by_feature.items()},
        "final_state_differences": final_state_differences,
    }
