"""Reads what Atlas observed, collected through the MongoDB MCP Server, into ledger evidence.

Performance Advisor output becomes a fourth evidence source, `atlas-performance-advisor`, which
counts as OBSERVED. MCP responses are treated as data: only JSON inside the result is parsed.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from archaeologist.config import EVIDENCE_DIR
from archaeologist.extract_querylog import features_of

SOURCE_TYPE = "atlas-performance-advisor"


def _payloads(text: str) -> list:
    found = []
    for block in re.findall(r"<untrusted-user-data-[^>]+>\s*(.*?)\s*</untrusted-user-data", text or "", re.S):
        block = block.strip()
        if not block or block[0] not in "[{":
            continue
        try:
            found.append(json.loads(block))
        except json.JSONDecodeError:
            continue
    return found


def _lists_under(value, words: tuple[str, ...], path: str = "") -> list[tuple[str, list]]:
    found = []
    if isinstance(value, dict):
        for key, inner in value.items():
            here = f"{path}.{key}" if path else key
            if isinstance(inner, list) and any(word in key.lower() for word in words):
                found.append((here, inner))
            else:
                found.extend(_lists_under(inner, words, here))
    elif isinstance(value, list):
        for inner in value:
            found.extend(_lists_under(inner, words, path))
    return found


def _slow_operation(item: dict) -> dict | None:
    line = item.get("line")
    record = None
    if isinstance(line, str):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            record = None
    attributes = (record or {}).get("attr", item)
    command = attributes.get("command") if isinstance(attributes, dict) else None
    if not isinstance(command, dict):
        return None
    namespace = attributes.get("ns") or item.get("namespace") or ""
    entry = {"op": "command" if ("aggregate" in command or "find" in command) else attributes.get("type", "command"), "ns": namespace, "command": command}
    return {
        "namespace": namespace,
        "millis": attributes.get("durationMillis"),
        "plan": attributes.get("planSummary"),
        "features": sorted(features_of(entry)),
    }


def run(profile: str) -> dict | None:
    raw_path = EVIDENCE_DIR / f"{profile}_atlas_raw.json"
    if not raw_path.exists():
        return None
    raw = json.loads(raw_path.read_text())
    results = raw.get("results", {})

    advisor_payloads = _payloads(results.get("atlas-get-performance-advisor", {}).get("text", ""))
    slow, suggested, schema_advice = [], [], []
    for payload in advisor_payloads:
        for _, items in _lists_under(payload, ("slow",)):
            slow.extend(operation for operation in (_slow_operation(item) for item in items if isinstance(item, dict)) if operation)
        for _, items in _lists_under(payload, ("suggestedindex", "suggested_index", "suggestedindexes")):
            suggested.extend(item for item in items if isinstance(item, dict))
        for _, items in _lists_under(payload, ("schema", "recommendation")):
            schema_advice.extend(item for item in items if isinstance(item, dict))

    search_indexes = []
    for payload in _payloads(results.get("aggregate", {}).get("text", "")):
        documents = payload if isinstance(payload, list) else payload.get("documents") or payload.get("result") or []
        for document in documents if isinstance(documents, list) else []:
            if isinstance(document, dict) and document.get("name"):
                search_indexes.append(
                    {"name": document.get("name"), "status": document.get("status"), "queryable": document.get("queryable"), "fields": sorted(((document.get("latestDefinition") or {}).get("mappings") or {}).get("fields", {}))}
                )

    feature_counts: Counter = Counter()
    feature_millis: dict[str, list] = defaultdict(list)
    for operation in slow:
        for feature in operation["features"]:
            feature_counts[feature] += 1
            if operation["millis"] is not None:
                feature_millis[feature].append(operation["millis"])

    document = {
        "cluster": raw.get("cluster"),
        "advisor_available": raw.get("advisor_tool_available", False) and results.get("atlas-get-performance-advisor", {}).get("ok", False),
        "slow_operations": len(slow),
        "slow_by_feature": {feature: {"count": count, "max_ms": max(feature_millis[feature]) if feature_millis[feature] else None} for feature, count in feature_counts.items()},
        "suggested_indexes": suggested[:20],
        "schema_advice": schema_advice[:20],
        "search_indexes": search_indexes,
    }
    (EVIDENCE_DIR / f"{profile}_atlas_evidence.json").write_text(json.dumps(document, indent=2, default=str))
    return document


def ledger_sources(document: dict | None) -> dict[str, list[dict]]:
    sources: dict[str, list[dict]] = defaultdict(list)
    if not document:
        return sources
    for feature, info in document["slow_by_feature"].items():
        sources[feature].append(
            {
                "type": SOURCE_TYPE,
                "ref": f"Atlas {document['cluster']} Performance Advisor slow query log",
                "count": info["count"],
                "detail": f"{info['count']} slow operations on Atlas use this (slowest {info['max_ms']} ms)",
            }
        )
    for index in document["search_indexes"]:
        sources["F11"].append(
            {
                "type": "data",
                "ref": f"atlas:products.{index['name']}",
                "detail": f"Atlas Search index '{index['name']}' on fields {index['fields']} (status {index['status']})",
                "origin": "atlas",
            }
        )
    return sources
