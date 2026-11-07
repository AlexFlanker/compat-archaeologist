"""Reads the data and the catalog: field types, indexes, validators, and conditional invariants.

Invariants are mined from values with no knowledge of the application's rules. A candidate
is kept only if it holds with zero counterexamples and tells groups apart.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime

from archaeologist.config import EVIDENCE_DIR, source_client

MAX_SAMPLE = 20_000
MIN_SUPPORT = 20
MAX_CATEGORY_VALUES = 12
CHANCE_LIMIT = 0.001
MISSING = "<missing>"


def sample_collection(collection, limit: int = MAX_SAMPLE) -> list[dict]:
    total = collection.estimated_document_count()
    stride = max(1, total // limit)
    documents = []
    for position, document in enumerate(collection.find().sort("_id", 1)):
        if position % stride == 0:
            documents.append(document)
    return documents


def type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "double"
    if isinstance(value, str):
        return "string"
    if isinstance(value, datetime):
        return "date"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def field_profile(rows: list[dict]) -> dict:
    types: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for key, value in row.items():
            types[key][type_name(value)] += 1
    return {
        key: {"types": dict(counter), "missing_rate": round(1 - sum(counter.values()) / max(len(rows), 1), 4)}
        for key, counter in sorted(types.items())
    }


def _scalar_columns(rows: list[dict]) -> dict[str, list]:
    columns: dict[str, list] = defaultdict(lambda: [MISSING] * len(rows))
    for position, row in enumerate(rows):
        for key, value in row.items():
            if not isinstance(value, (dict, list)):
                columns[key][position] = value
    return dict(columns)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def mine_invariants(rows: list[dict], scope: str) -> list[dict]:
    if len(rows) < MIN_SUPPORT:
        return []
    columns = _scalar_columns(rows)
    found = []
    categorical = {}
    for name, values in columns.items():
        if name == "_id":
            continue
        distinct = {value for value in values if value != MISSING}
        if 2 <= len(distinct) <= MAX_CATEGORY_VALUES and all(isinstance(value, (str, bool)) for value in distinct):
            categorical[name] = sorted(distinct, key=str)

    dates = {
        name: values
        for name, values in columns.items()
        if any(isinstance(value, datetime) for value in values)
    }

    for category, category_values in categorical.items():
        for value in category_values:
            group = [position for position, item in enumerate(columns[category]) if item == value]
            if len(group) < MIN_SUPPORT:
                continue
            others = [position for position, item in enumerate(columns[category]) if item != value]
            for name, column in columns.items():
                if name == category or name == "_id":
                    continue
                found.extend(_implications(scope, category, value, name, column, group, others, categorical))

    for name, column in columns.items():
        if name == "_id" or name in dates:
            continue
        for date_name, date_column in dates.items():
            found.extend(_date_splits(scope, name, column, date_name, date_column))

    found.extend(_threshold_rules(scope, rows, columns))
    return _dedupe(found)


def _not_by_chance(group_size: int, violation_rate_elsewhere: float) -> bool:
    """True if a clean group this size is unlikely to be luck, given how often others violate the rule."""
    return (1 - violation_rate_elsewhere) ** group_size < CHANCE_LIMIT


def _implications(scope, category, value, name, column, group, others, categorical) -> list[dict]:
    rules = []
    group_values = [column[position] for position in group]
    other_values = [column[position] for position in others]
    size = len(group_values)

    def rate(predicate, values) -> float:
        return sum(1 for item in values if predicate(item)) / max(len(values), 1)

    if all(item == MISSING for item in group_values) and _not_by_chance(size, rate(lambda item: item != MISSING, other_values)):
        rules.append(f"{category}={value} ⇒ {name} is absent")
    if all(item != MISSING for item in group_values) and _not_by_chance(size, rate(lambda item: item == MISSING, other_values)):
        rules.append(f"{category}={value} ⇒ {name} is present")

    numbers = [item for item in group_values if _is_number(item)]
    other_numbers = [item for item in other_values if _is_number(item)]
    if numbers and len(numbers) == size and other_numbers:
        if all(item > 0 for item in numbers) and _not_by_chance(size, rate(lambda item: item <= 0, other_numbers)):
            rules.append(f"{category}={value} ⇒ {name} > 0")
        if all(item == 0 for item in numbers) and _not_by_chance(size, rate(lambda item: item != 0, other_numbers)):
            rules.append(f"{category}={value} ⇒ {name} = 0")

    if name in categorical:
        seen = {item for item in group_values if item != MISSING}
        if len(seen) == 1 and all(item != MISSING for item in group_values):
            only = next(iter(seen))
            if _not_by_chance(size, rate(lambda item: item != MISSING and item != only, other_values)):
                rules.append(f"{category}={value} ⇒ {name}={only}")

    return [{"scope": scope, "rule": rule, "support": size, "counterexamples": 0} for rule in rules]


def _date_splits(scope, name, column, date_name, date_column) -> list[dict]:
    """Finds fields whose type or presence changes cleanly at a single point in time."""
    labels: dict[str, list[datetime]] = defaultdict(list)
    for item, moment in zip(column, date_column):
        if isinstance(moment, datetime):
            labels["absent" if item == MISSING else type_name(item)].append(moment)
    if len(labels) != 2 or any(len(moments) < MIN_SUPPORT for moments in labels.values()):
        return []
    first, second = sorted(labels, key=lambda label: min(labels[label]))
    if max(labels[first]) >= min(labels[second]):
        return []
    boundary = min(labels[second])
    return [
        {
            "scope": scope,
            "rule": f"{name} is {first} before ~{boundary.date()} and {second} from then on ({date_name})",
            "support": len(labels[first]) + len(labels[second]),
            "counterexamples": 0,
        }
    ]


def _threshold_rules(scope, rows, columns) -> list[dict]:
    """Finds a field with few values that is decided by a threshold on another number or difference."""
    numeric = [
        name
        for name, values in columns.items()
        if name != "_id" and sum(isinstance(value, int) and not isinstance(value, bool) for value in values) >= MIN_SUPPORT
    ]
    rules = []
    for target in numeric:
        values = {value for value in columns[target] if isinstance(value, int)}
        if not 2 <= len(values) <= 3:
            continue
        candidates = [(other, None) for other in numeric if other != target]
        candidates += [(left, right) for left in numeric for right in numeric if left != right and target not in (left, right)]
        for left, right in candidates:
            buckets: dict[int, list[int]] = defaultdict(list)
            for position in range(len(rows)):
                value = columns[target][position]
                a = columns[left][position]
                b = 0 if right is None else columns[right][position]
                if isinstance(value, int) and isinstance(a, int) and isinstance(b, int):
                    buckets[value].append(a - b)
            if len(buckets) != 2 or any(len(items) < MIN_SUPPORT for items in buckets.values()):
                continue
            # A negative difference is the mirror image of the same rule with its terms swapped.
            if right is not None and sum(item < 0 for items in buckets.values() for item in items) > 0:
                continue
            low, high = sorted(buckets, key=lambda key: min(buckets[key]))
            if max(buckets[low]) < min(buckets[high]):
                expression = left if right is None else f"{left} - {right}"
                rules.append(
                    {
                        "scope": scope,
                        "rule": f"{target}={high} when {expression} ≥ {min(buckets[high])}, otherwise {target}={low}",
                        "support": sum(len(items) for items in buckets.values()),
                        "counterexamples": 0,
                    }
                )
    return rules


def _dedupe(rules: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for rule in rules:
        if rule["rule"] not in seen:
            seen.add(rule["rule"])
            unique.append(rule)
    return unique


def _with_foreign_columns(rows: list[dict], all_samples: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """Copies low-cardinality columns across a detected reference, so rules can span collections."""
    links = []
    enriched = rows
    for field in {key for row in rows[:200] for key, value in row.items() if isinstance(value, str)} - {"_id", "status"}:
        values = {row[field] for row in rows if isinstance(row.get(field), str)}
        for other_name, other_rows in all_samples.items():
            if other_rows is rows or not values:
                continue
            ids = {row["_id"]: row for row in other_rows}
            if sum(value in ids for value in values) >= 0.99 * len(values):
                links.append({"field": field, "references": other_name, "coverage": round(sum(value in ids for value in values) / len(values), 4)})
                copied = []
                for row in enriched:
                    target = ids.get(row.get(field), {})
                    extras = {f"{other_name}.{key}": value for key, value in target.items() if isinstance(value, str) and key not in ("_id", "email", "name")}
                    copied.append({**row, **extras})
                enriched = copied
    return enriched, links


def _unrolled(rows: list[dict], field: str) -> list[dict]:
    return [element for row in rows for element in row.get(field, []) if isinstance(element, dict)]


def catalog_of(db) -> dict:
    catalog = {}
    for info in db.list_collections():
        name = info["name"]
        if name.startswith("system."):
            continue
        indexes = []
        for index in db[name].list_indexes():
            described = {"name": index["name"], "key": dict(index["key"])}
            for option in ("unique", "partialFilterExpression", "expireAfterSeconds", "collation", "weights"):
                if option in index:
                    described[option] = index[option]
            indexes.append(described)
        catalog[name] = {"options": {key: info.get("options", {}).get(key) for key in ("validator", "validationLevel", "validationAction") if key in info.get("options", {})}, "indexes": indexes}
    return catalog


def feature_signals(catalog: dict, samples: dict[str, list[dict]]) -> dict[str, list[dict]]:
    signals: dict[str, list[dict]] = defaultdict(list)
    for name, entry in catalog.items():
        ref = f"catalog:{name}"
        if "validator" in entry["options"]:
            signals["F8"].append({"ref": ref, "detail": f"{name} has a $jsonSchema validator (level={entry['options'].get('validationLevel')})"})
        for index in entry["indexes"]:
            if "collation" in index:
                collation = index["collation"]
                signals["F6"].append({"ref": f"{ref}.{index['name']}", "detail": f"{name}.{index['name']} uses collation locale={collation.get('locale')} strength={collation.get('strength')}, unique={index.get('unique', False)}"})
            if "partialFilterExpression" in index:
                signals["F7"].append({"ref": f"{ref}.{index['name']}", "detail": f"{name}.{index['name']} is partial on {index['partialFilterExpression']}"})
            if "expireAfterSeconds" in index:
                signals["F7"].append({"ref": f"{ref}.{index['name']}", "detail": f"{name}.{index['name']} is TTL, expireAfterSeconds={index['expireAfterSeconds']}"})
            if any(value == "text" for value in index["key"].values()):
                signals["F9"].append({"ref": f"{ref}.{index['name']}", "detail": f"{name}.{index['name']} is a text index with weights {index.get('weights')}"})
            special = {"collation", "partialFilterExpression", "expireAfterSeconds", "weights"} & set(index)
            if index["name"] != "_id_" and not special and not any(value == "text" for value in index["key"].values()):
                signals["F10"].append({"ref": f"{ref}.{index['name']}", "detail": f"{name}.{index['name']} is a plain index on {list(index['key'])}"})

    for name, rows in samples.items():
        for field in {key for row in rows[:500] for key, value in row.items() if isinstance(value, list)}:
            mixed = sum(
                1
                for row in rows
                if isinstance(row.get(field), list)
                and len({element.get("refund_status") for element in row[field] if isinstance(element, dict) and "refund_status" in element}) > 1
            )
            if mixed:
                signals["F3"].append({"ref": f"data:{name}.{field}", "detail": f"{mixed} sampled {name} have elements of {field} in different states, so updates must target individual elements"})
        for field in ("email",):
            values = [row[field] for row in rows if isinstance(row.get(field), str)]
            if values and len({value.lower() for value in values}) == len(values) and any(value != value.lower() for value in values):
                signals["F6"].append({"ref": f"data:{name}.{field}", "detail": f"{name}.{field} stores mixed case, yet no two values collide when case is ignored ({len(values)} sampled)"})
    return dict(signals)


def run(profile: str) -> dict:
    db = source_client(timeout_ms=120_000)[profile]
    catalog = catalog_of(db)
    samples = {name: sample_collection(db[name]) for name in catalog}

    collections, invariants = {}, []
    for name, rows in samples.items():
        enriched, links = _with_foreign_columns(rows, samples)
        collections[name] = {
            "documents": db[name].estimated_document_count(),
            "sampled": len(rows),
            "fields": field_profile(rows),
            "references": links,
            **catalog[name],
        }
        invariants.extend(mine_invariants(enriched, name))
        for field in {key for row in rows[:500] for key, value in row.items() if isinstance(value, list)}:
            elements = _unrolled(rows, field)
            if elements and len(elements) >= MIN_SUPPORT:
                collections[name]["fields"][f"{field}[]"] = {"element_fields": field_profile(elements)}
                invariants.extend(mine_invariants(elements, f"{name}.{field}[]"))

    for position, invariant in enumerate(invariants, start=1):
        invariant["id"] = f"INV-{position:03d}"

    profile_doc = {
        "profile": profile,
        "collections": collections,
        "invariants": invariants,
        "feature_signals": feature_signals(catalog, samples),
    }
    (EVIDENCE_DIR / f"{profile}_data_profile.json").write_text(json.dumps(profile_doc, indent=2, default=str))
    return profile_doc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    result = run(parser.parse_args().profile)
    print("collections:", {name: info["documents"] for name, info in result["collections"].items()})
    print("signals:", {key: len(value) for key, value in result["feature_signals"].items()})
    print(f"invariants: {len(result['invariants'])}")
    for invariant in result["invariants"]:
        print(f"  {invariant['id']} [{invariant['scope']}] {invariant['rule']} (n={invariant['support']})")
