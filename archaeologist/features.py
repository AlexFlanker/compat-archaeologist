"""The feature vocabulary that evidence gets mapped onto.

This names what to look for. It says nothing about which engine supports what: that is
discovered by probes at runtime.
"""

from __future__ import annotations

FEATURES = {
    "F1": {"name": "$lookup sub-pipeline + $facet", "why_risky": "Partial or different support across compatible engines"},
    "F2": {"name": "$setWindowFields", "why_risky": "Newer stage, often missing or limited to some window operators"},
    "F3": {"name": "arrayFilters / positional $[<id>] updates", "why_risky": "Semantic gaps in array element matching"},
    "F4": {"name": "Multi-document transactions", "why_risky": "Limits and rollback behavior differ"},
    "F5": {"name": "Change streams", "why_risky": "Support, retention and event shape differ"},
    "F6": {"name": "Case-insensitive collation index", "why_risky": "Often unsupported, which breaks uniqueness and lookups"},
    "F7": {"name": "Partial index + TTL index", "why_risky": "Filter expressions and expiry behavior differ"},
    "F8": {"name": "$jsonSchema validator", "why_risky": "Validation may be limited or silently not enforced"},
    "F9": {"name": "$text search", "why_risky": "Scoring, exclusion terms and language handling differ"},
    "F10": {"name": "Plain CRUD by _id and simple filters", "why_risky": "Control group: expected to behave the same"},
    "F11": {"name": "Atlas Search ($search with a search index)", "why_risky": "Atlas-only: other engines need a separate search service"},
}

# Strings a static scan can match in source code, used to check model findings and as a
# labeled fallback when the model is unavailable.
CODE_TOKENS = {
    "F1": ["$lookup", "$facet"],
    "F2": ["$setWindowFields", "$shift", "$documentNumber"],
    "F3": ["array_filters", "$[line]", "$[]"],
    "F4": ["start_transaction"],
    "F5": [".watch("],
    "F6": ["Collation(", "collation="],
    "F7": ["partialFilterExpression", "expireAfterSeconds"],
    "F8": ["$jsonSchema", "validator="],
    "F9": ["$text", "$meta"],
    "F10": ["find_one(", "insert_one(", "update_one(", "delete_one(", "count_documents("],
    "F11": ['{"$search": {"index"', "create_search_index("],
}

# Stages that are unusual enough to surface as their own item when found outside F1-F10.
EXTRA_STAGE_TOKENS = ["$graphLookup", "$merge", "$out", "$unionWith", "$densify", "$fill", "$bucketAuto", "$geoNear"]


def extra_item_id(token: str) -> str:
    return "X:" + token.lstrip("$")
