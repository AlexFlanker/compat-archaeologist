"""Upgrade mode: does a newer version of the target behave or perform any better?

Runs the same probes and replay against an older FerretDB and compares them with the current
target run. For AWS DocumentDB, version differences come from its docs and are INFERRED.
Version-compatible is not the same as behavior- or performance-compatible.
"""

from __future__ import annotations

import json

from archaeologist.config import OUT_DIR, _client, env
from archaeologist.diff import run as run_diff
from scripts.seed import PROFILES, database_names, seed_database

OLDER_LABEL = "ferretdb-2.2.0"
DEFAULT_OLDER_URI = "mongodb://{user}:{password}@127.0.0.1:27019/"

DOCUMENTDB_VERSION_NOTES = [
    ("F1", "5.0 → 8.0.2", "Correlated $lookup sub-pipelines and $facet are documented from 8.0.2 (planner v3)"),
    ("F2", "5.0 → 8.0.2", "$setWindowFields is documented from 8.0.2; $shift is still unsupported"),
    ("F6", "5.0 → 8.0", "Collation is documented only on 8.0"),
]


def _older_uri() -> str:
    return env("TARGET_URI_OLDER") or DEFAULT_OLDER_URI.format(
        user=env("FERRETDB_USER", "ca_local"), password=env("FERRETDB_PASSWORD", "local-only-not-a-secret")
    )


def _slowest(runs: dict, count: int = 3) -> dict[str, dict]:
    shapes = [shape for shape in runs["replay"]["reads"]["shapes"] if shape.get("target_p95_ms")]
    return {shape["shape_id"]: shape for shape in sorted(shapes, key=lambda item: -item["target_p95_ms"])[:count]}


def run(profile: str, probes: list[dict], current: dict, origin: str) -> dict:
    client = _client(_older_uri(), 300_000)
    names = database_names(profile)
    settings = PROFILES[profile]
    if client[names["base"]].orders.estimated_document_count() < settings["orders"]:
        report = seed_database(client, names["base"], profile, settings["orders"])
        seeds = json.loads((OUT_DIR / f"seed_report_{profile}.json").read_text())
        seeds[OLDER_LABEL] = [report]
        (OUT_DIR / f"seed_report_{profile}.json").write_text(json.dumps(seeds, indent=2, default=str))
    older = run_diff(profile, probes, probe_origin=origin, target_uri=_older_uri(), output_name=OLDER_LABEL)
    document = compare(profile, older, current)
    (OUT_DIR / profile / "upgrade.json").write_text(json.dumps(document, indent=2, default=str))
    return document


def load(profile: str) -> dict | None:
    path = OUT_DIR / profile / "upgrade.json"
    return json.loads(path.read_text()) if path.exists() else None


def compare(profile: str, older: dict, current: dict) -> dict:
    older_version = older["engines"]["target"].get("ferretdb_version")
    current_version = current["engines"]["target"].get("ferretdb_version")
    rows, fixed, regressed = [], [], []
    for feature in sorted(set(older["features"]) | set(current["features"]), key=lambda key: (len(key), key)):
        before = older["features"].get(feature, {}).get("verdict", "no_probe")
        after = current["features"].get(feature, {}).get("verdict", "no_probe")
        rows.append((feature, before, after))
        if before != "equal" and after == "equal":
            fixed.append(feature)
        if before == "equal" and after != "equal":
            regressed.append(feature)

    older_shapes = {shape["shape_id"]: shape for shape in older["replay"]["reads"]["shapes"]}
    latency = []
    for shape_id, shape in _slowest(current).items():
        previous = older_shapes.get(shape_id, {})
        latency.append((", ".join(shape["features"]) or "other", shape["collection"], previous.get("target_p95_ms"), shape["target_p95_ms"], shape["source_p95_ms"]))

    unchanged = [feature for feature, before, after in rows if before == after and before not in {"equal", "no_probe"}]
    faster = [
        f"{feature} on {collection} {before:,.1f} → {after:,.1f} ms"
        for feature, collection, before, after, _ in latency
        if before and after and before >= after * 2
    ]
    summary = (
        f"FerretDB {older_version} → {current_version}: {len(fixed)} feature(s) fixed, {len(regressed)} regressed, "
        f"{len(unchanged)} still failing ({', '.join(unchanged) or 'none'}). "
        + (f"The slowest path went from {latency[0][2]} ms to {latency[0][3]} ms p95 (MongoDB {latency[0][4]} ms)." if latency and latency[0][2] else "")
        + " A newer version number didn't change the decision."
        if not fixed
        else f"FerretDB {older_version} → {current_version}: {len(fixed)} feature(s) fixed ({', '.join(fixed)}), {len(unchanged)} still failing."
    )
    if faster:
        summary += f" It did get faster elsewhere: {'; '.join(faster)}."
    table = ["| Feature | " + f"FerretDB {older_version} | FerretDB {current_version} |", "|---|---|---|"]
    table += [f"| {feature} | {before} | {after} |" for feature, before, after in rows]
    table += ["", "| Slow path | Collection | " + f"{older_version} p95 | {current_version} p95 | MongoDB p95 |", "|---|---|---|---|---|"]
    table += [f"| {feature} | {collection} | {before} ms | {after} ms | {source} ms |" for feature, collection, before, after, source in latency]
    table += ["", "AWS DocumentDB version differences (INFERRED from docs, not measured):", ""]
    table += [f"- {feature}, {versions}: {note}." for feature, versions, note in DOCUMENTDB_VERSION_NOTES]
    return {
        "profile": profile,
        "older": older_version,
        "current": current_version,
        "fixed": fixed,
        "regressed": regressed,
        "still_failing": unchanged,
        "summary": summary,
        "table": table,
        "run_id": older["run_id"],
    }
