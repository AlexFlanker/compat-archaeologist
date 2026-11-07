"""Merges the three evidence sources into the Usage-Spec Ledger.

OBSERVED means something at runtime or in the catalog shows it. INFERRED means only the code
suggests it, which may be a dead path. Independent sources that agree raise confidence; an item
with a single source gets its confidence from SubQ Snap, and stays unknown if SubQ can't answer.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from archaeologist import extract_code, meta_client, subq_client
from archaeologist.config import EVIDENCE_DIR, OUT_DIR
from archaeologist.extract_querylog import clean_command, features_of, load_log, query_log_path, shape_of
from archaeologist.features import EXTRA_STAGE_TOKENS, FEATURES, extra_item_id

AGREEMENT_CONFIDENCE = {2: 0.90, 3: 0.97}
DEPENDENCY_QUESTION = "Does the application depend on this behavior in production?"
SAMPLE_OPERATIONS_PER_ITEM = 6

RECONCILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["behaviors"],
    "properties": {
        "behaviors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["statement", "code_rule_numbers", "invariant_ids"],
                "properties": {
                    "statement": {"type": "string"},
                    "code_rule_numbers": {"type": "array", "items": {"type": "integer"}},
                    "invariant_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

RECONCILE_PROMPT = """You are reconstructing the implicit business rules of a legacy application from two independent kinds of evidence:
numbered rules read from its code, and invariants mined from its data. Merge them into a de-duplicated list of business behaviors.

Rules:
- Link a code rule and a data invariant only if they truly describe the same behavior.
- Keep behaviors that have just one kind of evidence. Don't invent any behavior without evidence.
- Some invariants are side effects of correlation, not rules: for example "status=paid ⇒ tax is present" only reflects that paid orders are recent.
  Leave those out unless a code rule explains them.
- Combine invariants that say the same thing across many fields into one behavior.
- Write each statement as a rule a product owner would recognize, with its specific numbers."""


def _item_id_for_finding(finding: dict) -> str | None:
    feature = finding.get("feature_id")
    if feature in FEATURES:
        return feature
    if feature and feature.startswith("X:"):
        return feature
    for token in EXTRA_STAGE_TOKENS:
        if token in finding.get("token", "") or token in finding.get("behavior", ""):
            return extra_item_id(token)
    return None


def _code_sources(code_doc: dict) -> dict[str, list[dict]]:
    sources: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple] = set()
    for finding in code_doc["findings"]:
        item = _item_id_for_finding(finding)
        if not item:
            continue
        key = (item, finding["file"], finding["line"])
        if key not in seen:
            seen.add(key)
            sources[item].append(
                {
                    "type": "code",
                    "ref": f"{finding['file']}:{finding['line']}",
                    "token": finding.get("token"),
                    "detail": finding.get("behavior"),
                    "origin": code_doc["source"],
                }
            )
    for hit in code_doc.get("static_scan", []):
        key = (hit["feature_id"], hit["file"], hit["line"])
        if key not in seen:
            seen.add(key)
            sources[hit["feature_id"]].append(
                {
                    "type": "code",
                    "ref": f"{hit['file']}:{hit['line']}",
                    "token": hit["token"],
                    "detail": f"Static token match on {hit['token']}",
                    "origin": "static_scan",
                }
            )
    return sources


def _querylog_sources(summary: dict) -> dict[str, list[dict]]:
    sources: dict[str, list[dict]] = defaultdict(list)
    for feature, info in summary["features"].items():
        sources[feature].append(
            {
                "type": "querylog",
                "ref": f"evidence query log, shapes {', '.join(info['shapes'][:4])}",
                "count": info["ops"],
                "detail": f"{info['ops']} recorded operations across {len(info['shapes'])} shape(s)",
            }
        )
    rejected = summary.get("rejected_inserts", {})
    for collection, count in rejected.items():
        sources["F8"].append(
            {
                "type": "querylog",
                "ref": f"query log, ninserted=0 on {collection}",
                "count": count,
                "detail": f"{count} inserts into {collection} were refused by the server at runtime",
            }
        )
    for field, count in summary.get("fields_written", {}).items():
        if field.endswith("expires_at"):
            sources["F7"].append(
                {
                    "type": "querylog",
                    "ref": f"query log, writes to {field}",
                    "count": count,
                    "detail": f"{field} was rewritten {count} times, which keeps a TTL-managed document alive",
                }
            )
    return sources


def _data_sources(data_profile: dict) -> dict[str, list[dict]]:
    sources: dict[str, list[dict]] = defaultdict(list)
    for feature, signals in data_profile["feature_signals"].items():
        for signal in signals:
            sources[feature].append({"type": "data", "ref": signal["ref"], "detail": signal["detail"]})
    return sources


def _sample_operations(entries: list[dict]) -> dict[str, list[dict]]:
    samples: dict[str, list[dict]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        shape = shape_of(entry)
        for feature in features_of(entry):
            if (feature, shape) in seen or len(samples[feature]) >= SAMPLE_OPERATIONS_PER_ITEM:
                continue
            seen.add((feature, shape))
            samples[feature].append({"op": entry.get("op"), "ns": entry.get("ns"), "command": clean_command(entry)})
    return samples


def _status_and_confidence(sources: list[dict]) -> tuple[str, float | None, str, int]:
    kinds = {source["type"] for source in sources}
    status = "OBSERVED" if kinds & {"querylog", "data"} else "INFERRED"
    count = len(kinds)
    if count >= 2:
        return status, AGREEMENT_CONFIDENCE[count], f"{count} independent sources agree", count
    return status, None, "pending", count


def build_items(data_profile: dict, summary: dict, code_doc: dict, entries: list[dict]) -> list[dict]:
    code, querylog, data = _code_sources(code_doc), _querylog_sources(summary), _data_sources(data_profile)
    samples = _sample_operations(entries)
    all_ids = set(code) | set(querylog) | set(data)
    order = list(FEATURES) + sorted(all_ids - set(FEATURES))

    items = []
    for item_id in order:
        sources = querylog.get(item_id, []) + data.get(item_id, []) + code.get(item_id, [])
        if not sources:
            continue
        status, confidence, basis, count = _status_and_confidence(sources)
        descriptions = [source["detail"] for source in code.get(item_id, []) if source.get("origin") != "static_scan" and source.get("detail")]
        feature = FEATURES.get(item_id)
        items.append(
            {
                "id": item_id,
                "feature": feature["name"] if feature else item_id.split(":", 1)[-1],
                "behavior": " | ".join(dict.fromkeys(descriptions))[:1500] or (feature["name"] if feature else item_id),
                "why_risky": feature["why_risky"] if feature else "Unusual stage outside the common subset",
                "sources": sources,
                "independent_sources": count,
                "status": status,
                "confidence": confidence,
                "confidence_basis": basis,
                "sample_operations": samples.get(item_id, []),
            }
        )
    return items


def build_behaviors(data_profile: dict, code_doc: dict) -> list[dict]:
    invariants = {invariant["id"]: invariant for invariant in data_profile["invariants"]}
    rules = code_doc.get("business_rules", [])

    if meta_client.is_configured() and (rules or invariants):
        listing = "\n".join(f"{number}. {rule['rule']} ({rule['file']}:{rule['line']})" for number, rule in enumerate(rules, start=1))
        mined = "\n".join(f"{key} [{value['scope']}] {value['rule']} (n={value['support']})" for key, value in invariants.items())
        try:
            response = meta_client.structured(
                purpose="reconcile_behaviors",
                system=RECONCILE_PROMPT,
                user=f"Code rules:\n{listing or '(none)'}\n\nData invariants:\n{mined or '(none)'}",
                schema=RECONCILE_SCHEMA,
                schema_name="behaviors",
            )
        except meta_client.MetaUnavailable as exc:
            print(f"  [ledger] Muse unavailable for reconciliation ({exc}); keeping raw evidence")
        else:
            return _behaviors_from_model(response.get("behaviors", []), rules, invariants)

    behaviors = []
    for number, rule in enumerate(rules, start=1):
        behaviors.append(_behavior(f"B{number:02d}", rule["rule"], [_rule_source(rule)], []))
    for invariant in invariants.values():
        behaviors.append(
            _behavior(f"B{len(behaviors) + 1:02d}", f"{invariant['rule']} [{invariant['scope']}]", [], [_invariant_source(invariant)])
        )
    return behaviors


def _rule_source(rule: dict) -> dict:
    return {"type": "code", "ref": f"{rule['file']}:{rule['line']}", "token": rule.get("token")}


def _invariant_source(invariant: dict) -> dict:
    return {
        "type": "data",
        "ref": invariant["id"],
        "count": invariant["support"],
        "detail": f"{invariant['rule']} [{invariant['scope']}], {invariant['counterexamples']} counterexamples",
    }


def _behavior(behavior_id: str, statement: str, code_sources: list[dict], data_sources: list[dict]) -> dict:
    sources = data_sources + code_sources
    status = "OBSERVED" if data_sources else "INFERRED"
    both = bool(code_sources and data_sources)
    return {
        "id": behavior_id,
        "statement": statement,
        "sources": sources,
        "status": status,
        "confidence": AGREEMENT_CONFIDENCE[2] if both else None,
        "confidence_basis": "2 independent sources agree" if both else "pending",
        "independent_sources": 2 if both else 1,
    }


def _behaviors_from_model(raw: list[dict], rules: list[dict], invariants: dict) -> list[dict]:
    behaviors = []
    for position, entry in enumerate(raw, start=1):
        code_sources = [_rule_source(rules[number - 1]) for number in entry["code_rule_numbers"] if 1 <= number <= len(rules)]
        data_sources = [_invariant_source(invariants[key]) for key in entry["invariant_ids"] if key in invariants]
        if code_sources or data_sources:  # A model statement with no real evidence behind it is dropped.
            behaviors.append(_behavior(f"B{position:02d}", entry["statement"], code_sources, data_sources))
    return behaviors


def _resolve_single_source_confidence(profile: str, records: list[dict], describe) -> dict:
    pending = [record for record in records if record["confidence"] is None]
    if not pending:
        return {"available": True, "asked": 0}
    questions = {
        record["id"].replace(":", "_"): {
            "instructions": f"{DEPENDENCY_QUESTION} Behavior: {describe(record)}",
            "criteria": {
                "true": "Evidence shows a production path relies on it, and removing it would change behavior users see",
                "false": "It looks like dead code, a coincidence in the data, or an optional detail",
            },
        }
        for record in pending[:128]
    }
    state = {
        "application": "A 10-year-old e-commerce order system" if profile == "shoplog" else "A small order lookup service",
        "items": [
            {
                "id": record["id"],
                "status": record["status"],
                "evidence": [{key: source.get(key) for key in ("type", "ref", "count", "detail")} for source in record["sources"][:4]],
            }
            for record in pending[:128]
        ],
    }
    answer = subq_client.ask_probabilities(purpose="single_source_confidence", state=state, questions=questions)
    for record in pending:
        name = record["id"].replace(":", "_")
        if answer["available"] and name in answer["answers"]:
            record["confidence"] = round(answer["answers"][name], 3)
            record["confidence_basis"] = f"{answer['model']}, single source"
        else:
            record["confidence_basis"] = f"unknown: {answer.get('reason', 'not asked')}"
    return {"available": answer["available"], "asked": len(pending), "reason": answer.get("reason")}


def run(profile: str, refresh_code: bool = True) -> dict:
    data_profile = json.loads((EVIDENCE_DIR / f"{profile}_data_profile.json").read_text())
    summary = json.loads((EVIDENCE_DIR / f"{profile}_querylog_summary.json").read_text())
    entries = load_log(query_log_path(profile))
    code_doc = extract_code.run(profile, refresh=refresh_code)

    items = build_items(data_profile, summary, code_doc, entries)
    behaviors = build_behaviors(data_profile, code_doc)
    item_answer = _resolve_single_source_confidence(profile, items, lambda record: f"{record['feature']}. {record['behavior']}")
    behavior_answer = _resolve_single_source_confidence(profile, behaviors, lambda record: record["statement"])

    ledger = {
        "profile": profile,
        "code_evidence_source": code_doc["source"],
        "code_model": code_doc.get("model"),
        "citations_dropped": len(code_doc.get("dropped_citations", [])),
        "operators": summary["operators"],
        "stages": summary["stages"],
        "items": items,
        "behaviors": behaviors,
        "subq": {"items": item_answer, "behaviors": behavior_answer},
    }
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ledger.json").write_text(json.dumps(ledger, indent=2, default=str))
    return ledger


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--no-refresh-code", action="store_true")
    arguments = parser.parse_args()
    result = run(arguments.profile, refresh_code=not arguments.no_refresh_code)
    print(f"code evidence: {result['code_evidence_source']}; citations dropped: {result['citations_dropped']}")
    for item in result["items"]:
        kinds = sorted({source["type"] for source in item["sources"]})
        print(f"  {item['id']:14} {item['status']:9} conf={item['confidence']!s:6} {kinds} — {item['confidence_basis']}")
    print(f"behaviors: {len(result['behaviors'])}")
