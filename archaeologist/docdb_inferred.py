"""AWS DocumentDB: what its public documentation says, compared with how this app uses each feature.

Everything here is INFERRED. Nothing ran on DocumentDB, because it needs a VPC cluster we did not
provision. Summaries are our own words, with links to the source. Pointing TARGET_URI at a real
8.0.2 cluster replaces these rows with observed results.

Docs read on 2026-11-07. Newest engine at that time: 8.0.2.
"""

from __future__ import annotations

import json
from typing import Callable

from archaeologist.config import EVIDENCE_DIR
from archaeologist.extract_querylog import load_log, operators_in, query_log_path

ENGINE = "8.0.2"
DOCS = "https://docs.aws.amazon.com/documentdb/latest"
COMPATIBILITY_INDEX = f"{DOCS}/developerguide/mongo-apis.html"


def _uses_operator(context: dict, operator: str) -> bool:
    return operator in context["operators"]


def _text_search_uses_exclusion(context: dict) -> bool:
    return any(" -" in search or search.startswith("-") for search in context["text_searches"])


def _text_index_covers_array(context: dict) -> bool:
    for collection in context["data"]["collections"].values():
        for index in collection.get("indexes", []):
            if not any(value == "text" for value in index["key"].values()):
                continue
            for field in (index.get("weights") or {}):
                if "array" in collection["fields"].get(field, {}).get("types", {}):
                    return True
    return False


TABLE: dict[str, dict] = {
    "F1": {
        "since": "Sub-pipeline correlated $lookup and $facet: 8.0.2",
        "requires": "Engine 8.0.2 with planner version 3",
        "limits": "A $lookup that uses let can't contain $facet, $setWindowFields, or a $match with $text. $facet can't be nested. Older docs and release notes disagree on when uncorrelated sub-pipelines arrived.",
        "url": f"{DOCS}/developerguide/lookup.html",
        "conflicts": [],
    },
    "F2": {
        "since": "8.0.2",
        "requires": "Engine 8.0.2 with planner version 3",
        "limits": "26 window operators are supported. $shift, $derivative and $integral are not.",
        "url": f"{DOCS}/developerguide/setWindowFields.html",
        "conflicts": [
            {
                "check": lambda context: _uses_operator(context, "$shift"),
                "detail": "The app uses $shift, which the docs list as unsupported",
            }
        ],
    },
    "F3": {
        "since": "All versions",
        "requires": None,
        "limits": "None stated.",
        "url": f"{DOCS}/developerguide/dollarIdentifier-update.html",
        "conflicts": [],
    },
    "F4": {
        "since": "4.0",
        "requires": None,
        "limits": "1 minute execution limit, 32 MB transaction log, no cursors inside a transaction, can't create collections inside one.",
        "url": f"{DOCS}/developerguide/transactions.html",
        "conflicts": [],
    },
    "F5": {
        "since": "All supported versions",
        "requires": "Must be enabled with modifyChangeStreams",
        "limits": "Off by default. Default retention is 3 hours, up to 7 days. There is no oplog, so a consumer that is down longer than retention can't resume.",
        "url": f"{DOCS}/developerguide/change_streams.html",
        "conflicts": [],
    },
    "F6": {
        "since": "8.0",
        "requires": "Engine 8.0 with planner version 3",
        "limits": "Collation can be set at collection or index level, only on 8.0.",
        "url": f"{DOCS}/devguide/collation.html",
        "conflicts": [],
    },
    "F7": {
        "since": "Partial index 5.0; TTL all versions",
        "requires": None,
        "limits": "Partial filters allow $eq, $exists and top-level $and. Inequalities need an exact filter match. $or and $not aren't allowed. TTL expiry starts after the index build. A TTL index that is also partial isn't addressed.",
        "url": f"{DOCS}/developerguide/partial-index.html",
        "conflicts": [],
    },
    "F8": {
        "since": "4.0",
        "requires": None,
        "limits": "Works as both filter and validator. validationAction can only be error.",
        "url": f"{DOCS}/developerguide/json-schema-validation.html",
        "conflicts": [],
    },
    "F9": {
        "since": "5.0",
        "requires": None,
        "limits": "English only, one text index per collection, no array fields, no term exclusion.",
        "url": f"{DOCS}/developerguide/text-search.html",
        "conflicts": [
            {
                "check": _text_search_uses_exclusion,
                "detail": "The app searches with exclusion terms (for example -refurbished), which the docs say aren't supported",
            },
            {
                "check": _text_index_covers_array,
                "detail": "The app's text index covers an array field, which the docs say isn't supported",
            },
        ],
    },
    "F10": {
        "since": "All versions",
        "requires": None,
        "limits": "None relevant.",
        "url": COMPATIBILITY_INDEX,
        "conflicts": [],
    },
}


def _context(profile: str) -> dict:
    data = json.loads((EVIDENCE_DIR / f"{profile}_data_profile.json").read_text())
    operators: set[str] = set()
    searches: list[str] = []
    for entry in load_log(query_log_path(profile)):
        command = entry.get("command", {})
        operators.update(operators_in(command))
        text = command.get("filter", {}).get("$text") if isinstance(command.get("filter"), dict) else None
        if isinstance(text, dict) and isinstance(text.get("$search"), str):
            searches.append(text["$search"])
    return {"data": data, "operators": operators, "text_searches": searches}


def assess(profile: str, feature_ids: list[str]) -> dict[str, dict]:
    """Compares each documented limit with what the evidence shows this app does."""
    context = _context(profile)
    assessed = {}
    for feature in feature_ids:
        entry = TABLE.get(feature)
        if entry is None:
            assessed[feature] = {
                "status": "INFERRED",
                "verdict": "not_documented_here",
                "summary": "Not covered by our documentation review",
                "url": COMPATIBILITY_INDEX,
                "conflicts": [],
            }
            continue
        checks: list[dict] = entry["conflicts"]
        conflicts = [check["detail"] for check in checks if _run_check(check["check"], context)]
        assessed[feature] = {
            "status": "INFERRED",
            "verdict": "documented_conflict" if conflicts else "documented_supported",
            "since": entry["since"],
            "requires": entry["requires"],
            "limits": entry["limits"],
            "url": entry["url"],
            "conflicts": conflicts,
        }
    return assessed


def _run_check(check: Callable[[dict], bool], context: dict) -> bool:
    return bool(check(context))
