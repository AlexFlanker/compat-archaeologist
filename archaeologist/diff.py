"""Runs probes and the workload replay on source and target, and rolls results up per feature."""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from archaeologist import heldout
from archaeologist.config import OUT_DIR, server_info, source_client, target_client
from archaeologist.extract_querylog import load_log, query_log_path
from archaeologist.features import FEATURES
from archaeologist.probe_runner import diff_probe, worst_verdict
from archaeologist.replay import STATE_COLLECTIONS, partition, replay_reads, replay_writes
from scripts.seed import PROFILES, database_names, seed_database

SCHEMA_STEP_FEATURE = {
    "orders.validator": "F8",
    "orders.coupon_once": "F7",
    "customers.email_ci": "F6",
    "carts.expiry": "F7",
    "products.search": "F9",
}
PROBE_WORKERS = 6


def _schema_findings(profile: str, label: str) -> list[dict]:
    path = OUT_DIR / f"seed_report_{profile}.json"
    if not path.exists():
        return []
    report = json.loads(path.read_text()).get(label, [])
    if not report:
        return []
    return [step for step in report[0]["schema"] if not step["ok"]]


def _feature_rollup(probe_results: list[dict], read_replay: dict, write_replay: dict, schema_rejections: list[dict]) -> dict:
    rollup = {}
    features = sorted(
        {result["feature_id"] for result in probe_results}
        | {feature for shape in read_replay["shapes"] for feature in shape["features"]}
        | set(write_replay.get("by_feature", {})),
        key=lambda key: (0, int(key[1:]), key) if key in FEATURES else (1, 0, key),
    )
    for feature in features:
        probes = [result for result in probe_results if result["feature_id"] == feature]
        shapes = [shape for shape in read_replay["shapes"] if feature in shape["features"]]
        rejections = [step for step in schema_rejections if SCHEMA_STEP_FEATURE.get(step["step"]) == feature]
        verdicts = [result["verdict"] for result in probes]
        writes = write_replay.get("by_feature", {}).get(feature, {})
        replay_errors = sum(shape["target_errors"] for shape in shapes) + writes.get("errors", 0)
        replay_differences = sum(shape["different"] for shape in shapes) + writes.get("different", 0)
        ranking_differences = sum(shape["different"] for shape in shapes if shape.get("ranking_only"))
        replay_equal = sum(shape["equal"] for shape in shapes) + writes.get("equal", 0)
        if not verdicts and not replay_errors and not replay_differences and replay_equal:
            verdicts.append("equal")
        if replay_errors:
            verdicts.append("error")
        if replay_differences > ranking_differences:
            verdicts.append("result_diff")
        if ranking_differences:
            verdicts.append("ranking_diff")
        if rejections:
            verdicts.append("unsupported")
        rollup[feature] = {
            "verdict": worst_verdict(verdicts),
            "probes": [{"probe_id": result["probe_id"], "verdict": result["verdict"]} for result in probes],
            "replay": {
                "replayed": replay_equal + replay_differences + replay_errors,
                "equal": replay_equal,
                "different": replay_differences,
                "errors": replay_errors,
            },
            "schema_rejections": rejections,
        }
    return rollup


def run(
    profile: str,
    probes: list[dict],
    self_check: bool = False,
    probe_origin: str = "muse",
    target_uri: str | None = None,
    output_name: str | None = None,
) -> dict:
    source = source_client(timeout_ms=30_000)
    if self_check:
        target = source
    elif target_uri:
        from archaeologist.config import _client

        target = _client(target_uri, 30_000)
    else:
        target = target_client(timeout_ms=30_000)
    started = time.perf_counter()
    names = database_names(profile)
    settings = PROFILES[profile]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ("-self" if self_check else "")

    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        probe_results = list(pool.map(lambda probe: diff_probe(source, target, probe), probes))

    entries = load_log(query_log_path(profile))
    parts = partition(entries)
    heldout_ids = set(heldout.load_or_create(profile)["heldout_shape_ids"])
    read_replay = replay_reads(source, target, names["base"], parts["reads"], heldout_ids)

    if self_check:
        # With one engine on both sides, writes would land twice, so a second fixture stands in for the target.
        twin = f"{names['fixture']}_twin"
        seed_database(source, names["fixture"], profile, settings["fixture_orders"])
        seed_database(source, twin, profile, settings["fixture_orders"])
        write_replay = _replay_writes_twin(source, names["fixture"], twin, parts["writes"], profile)
        source.drop_database(twin)
    else:
        seed_database(source, names["fixture"], profile, settings["fixture_orders"])
        seed_database(target, names["fixture"], profile, settings["fixture_orders"])
        collections = ["orders"] if profile == "lite" else STATE_COLLECTIONS
        write_replay = replay_writes(source, target, names["fixture"], parts["writes"], collections)

    schema_rejections = [] if self_check else _schema_findings(profile, output_name or "target")
    document = {
        "run_id": run_id,
        "profile": profile,
        "self_check": self_check,
        "probe_origin": probe_origin,
        "engines": {"source": server_info(source), "target": server_info(target)},
        "probes": probe_results,
        "replay": {
            "reads": read_replay,
            "writes": write_replay,
            "excluded": parts["excluded"],
            "in_log": len(entries),
        },
        "schema_rejections": schema_rejections,
        "features": _feature_rollup(probe_results, read_replay, write_replay, schema_rejections),
        "seconds": round(time.perf_counter() - started, 1),
    }
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    file_name = "self_check.json" if self_check else (f"runs_{output_name}.json" if output_name else "runs.json")
    (out_dir / file_name).write_text(json.dumps(document, indent=2, default=str))
    # Status history tracks the current target only: a named run (the upgrade check's older target) is not a flip.
    if not self_check and not output_name:
        entry = {
            "run_id": run_id,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "strategy_version": probes[0].get("strategy_version", "v000") if probes else "v000",
            "verdicts": {result["probe_id"]: result["verdict"] for result in probe_results},
        }
        with (out_dir / "history.jsonl").open("a") as history:
            history.write(json.dumps(entry) + "\n")
    return document


def _replay_writes_twin(client, fixture: str, twin: str, writes: list[dict], profile: str) -> dict:
    """Self-check variant: same engine, separate databases, so each side has its own state."""

    class _Renamed:
        def __init__(self, inner, name):
            self._inner, self._name = inner, name

        def __getitem__(self, _):
            return self._inner[self._name]

    collections = ["orders"] if profile == "lite" else STATE_COLLECTIONS
    return replay_writes(_Renamed(client, fixture), _Renamed(client, twin), "ignored", writes, collections)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--self-check", action="store_true", help="Run the source against itself; everything must be equal")
    parser.add_argument("--probes-file", help="Use probes from this file instead of the generated cache")
    arguments = parser.parse_args()
    from archaeologist.probes import load_cached

    probes = json.load(open(arguments.probes_file)) if arguments.probes_file else load_cached(arguments.profile)
    origin = "handwritten_fixture" if arguments.probes_file else "muse"
    result = run(arguments.profile, probes, arguments.self_check, origin)
    print(f"run {result['run_id']} in {result['seconds']}s")
    for feature, info in result["features"].items():
        print(f"  {feature:4} {info['verdict']:14} probes={[p['verdict'] for p in info['probes']]} replay={info['replay']} rejected={[s['step'] for s in info['schema_rejections']]}")
    print("  reads overall:", result["replay"]["reads"]["overall"])
    writes = result["replay"]["writes"]
    print("  writes:", {key: writes[key] for key in ("replayed", "equal", "different", "target_errors", "source_errors")}, "state diffs:", writes["final_state_differences"])
    print("  excluded:", result["replay"]["excluded"])
