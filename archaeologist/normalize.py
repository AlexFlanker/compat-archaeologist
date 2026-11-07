"""Makes results from two engines comparable without hiding real differences.

Removed: server bookkeeping and values that change on every run.
Kept: anything an application could read, including document contents and order when requested.
Numeric types are compared by value, with a separate flag when the types themselves differ.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime, timezone

from bson import Decimal128, Int64, ObjectId, Timestamp
from bson.binary import Binary

SERVER_FIELDS = {
    "ok",
    "$clusterTime",
    "operationTime",
    "electionId",
    "opTime",
    "lastCommittedOpTime",
    "$configTime",
    "$topologyTime",
    "commitQuorum",
    "createdCollectionAutomatically",
    "numIndexesBefore",
    "numIndexesAfter",
    "note",
}
CHANGE_EVENT_VOLATILE = {"_id", "clusterTime", "wallTime", "txnNumber", "lsid", "collectionUUID"}
FLOAT_DIGITS = 6


def canonical(value):
    """Reduces a BSON value to plain JSON-safe data with a stable form."""
    if isinstance(value, Mapping):
        return {key: canonical(inner) for key, inner in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(inner) for inner in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Int64):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        rounded = round(value, FLOAT_DIGITS)
        return int(rounded) if rounded == int(rounded) else rounded
    if isinstance(value, Decimal128):
        return canonical(float(value.to_decimal()))
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    if isinstance(value, ObjectId):
        return f"ObjectId({value})"
    if isinstance(value, Timestamp):
        return f"Timestamp({value.time},{value.inc})"
    if isinstance(value, (bytes, Binary)):
        return f"bytes:{bytes(value).hex()}"
    return repr(value)


def type_signature(value):
    """Mirrors the structure of a value, but records numeric types, to detect int/double drift."""
    if isinstance(value, Mapping):
        return {key: type_signature(inner) for key, inner in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [type_signature(inner) for inner in value]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, float):
        return "double"
    if isinstance(value, (int, Int64)):
        return "int"
    return None


def reduce_result(result: Mapping) -> dict:
    """Keeps what the application sees from a command reply."""
    if "cursor" in result and isinstance(result["cursor"], Mapping):
        cursor = result["cursor"]
        reduced: dict = {"docs": list(cursor.get("firstBatch", cursor.get("nextBatch", [])))}
        if cursor.get("id"):
            reduced["more_available"] = True
        return reduced
    reduced = {key: value for key, value in result.items() if key not in SERVER_FIELDS}
    if "writeErrors" in reduced:
        reduced["writeErrors"] = [{"code": error.get("code")} for error in reduced["writeErrors"]]
    if "upserted" in reduced:
        reduced["upserted"] = [item.get("_id") for item in reduced["upserted"]]
    return reduced


def strip_change_event(event: Mapping) -> dict:
    stripped = {key: value for key, value in event.items() if key not in CHANGE_EVENT_VOLATILE}
    if isinstance(stripped.get("ns"), Mapping):
        # Each engine runs a probe in its own scratch database, so only the collection is comparable.
        stripped["ns"] = {"coll": stripped["ns"].get("coll")}
    return stripped


def comparable(value, unordered: bool = False):
    """The canonical form of a captured value. With unordered, `docs` compare as a multiset."""
    result = canonical(value)
    if unordered and isinstance(result, dict) and isinstance(result.get("docs"), list):
        result = {**result, "docs": sorted(result["docs"], key=lambda doc: json.dumps(doc, sort_keys=True))}
    elif unordered and isinstance(result, list):
        result = sorted(result, key=lambda doc: json.dumps(doc, sort_keys=True))
    return result


def first_difference(left, right, path: str = "$") -> dict | None:
    """Locates the first place two canonical values disagree, so a diff can be read by a person."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left:
                return {"path": f"{path}.{key}", "source": "<absent>", "target": _short(right[key])}
            if key not in right:
                return {"path": f"{path}.{key}", "source": _short(left[key]), "target": "<absent>"}
            found = first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return {"path": f"{path}.length", "source": len(left), "target": len(right)}
        for position, (a, b) in enumerate(zip(left, right)):
            found = first_difference(a, b, f"{path}[{position}]")
            if found:
                return found
        return None
    if left != right:
        return {"path": path, "source": _short(left), "target": _short(right)}
    return None


def _short(value, limit: int = 160):
    text = json.dumps(value, sort_keys=True, default=str)
    return value if len(text) <= limit else text[:limit] + "…"
