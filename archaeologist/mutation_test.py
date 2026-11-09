"""Which probes catch which planted gap? Known incompatibilities are injected in front of MongoDB.

The proxy is a copy of the oracle with exactly one named defect, so a probe that stays "equal"
against it is a probe that cannot see that kind of gap. Run with no mutation, the same proxy
measures false flips. Each mutant runs several times because a check that catches a gap only
sometimes is not a check we can rely on.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from archaeologist.config import OUT_DIR, source_client
from archaeologist.diff import PROBE_WORKERS
from archaeologist.mutants import MutantClient, load_catalog
from archaeologist.probe_runner import diff_probe

NAMESPACES = ("mts", "mtt")
NOT_A_DIFFERENCE = {"equal", "probe_invalid"}


def _slim(result: dict) -> dict:
    return {key: result[key] for key in ("probe_id", "feature_id", "verdict")}


def _caught_by(mutant: dict, results: list[dict]) -> list[str]:
    return sorted(
        result["probe_id"] for result in results
        if result["feature_id"] in mutant["feature_ids"] and result["verdict"] not in NOT_A_DIFFERENCE
    )


def summarize(catalog: list[dict], per_repeat: list[dict]) -> dict:
    """Pure: turns the raw verdicts of every repeat into the output document (without run metadata)."""
    mutants = []
    for mutant in catalog:
        each = [_caught_by(mutant, repeat.get(mutant["id"], [])) for repeat in per_repeat]
        caught_each = [bool(names) for names in each]
        all_results = [result for repeat in per_repeat for result in repeat.get(mutant["id"], [])]
        mutants.append({
            "id": mutant["id"],
            "split": mutant["split"],
            "feature_ids": mutant["feature_ids"],
            "description": mutant["description"],
            "caught": bool(caught_each) and all(caught_each),
            "flaky": any(caught_each) and not all(caught_each),
            "no_probe_for_features": not any(result["feature_id"] in mutant["feature_ids"] for result in all_results),
            "caught_each_repeat": caught_each,
            "caught_by": sorted({name for names in each for name in names}),
        })
    zero = [repeat.get("zero", []) for repeat in per_repeat]
    flips = [[r["probe_id"] for r in results if r["verdict"] not in NOT_A_DIFFERENCE] for results in zero]
    invalid = [sum(1 for r in results if r["verdict"] == "probe_invalid") for results in zero]

    def split_counts(name: str) -> dict:
        chosen = [m for m in mutants if m["split"] == name]
        return {"planted": len(chosen), "caught": sum(1 for m in chosen if m["caught"])}

    return {
        "mutants": mutants,
        "zero_mutation": {
            "false_flips_each_repeat": [len(names) for names in flips],
            "false_flip_probes": sorted({name for names in flips for name in names}),
            "invalid_each_repeat": invalid,
        },
        "summary": {
            "planted": len(mutants),
            "caught": sum(1 for m in mutants if m["caught"]),
            "flaky": sum(1 for m in mutants if m["flaky"]),
            "train": split_counts("train"),
            "heldout": split_counts("heldout"),
            "false_flips_max": max((len(names) for names in flips), default=0),
            "invalid_probes_max": max(invalid, default=0),
        },
    }


def _run_against(mutation: str | None, probes: list[dict]) -> list[dict]:
    source = source_client(timeout_ms=15000)
    target = MutantClient(source_client(timeout_ms=15000), mutation)
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        return [_slim(result) for result in pool.map(lambda probe: diff_probe(source, target, probe, namespaces=NAMESPACES), probes)]


def run(profile: str, probes: list[dict], repeats: int = 3, mutation_ids: list[str] | None = None) -> dict:
    catalog = load_catalog()
    if mutation_ids is not None:
        known = {entry["id"] for entry in catalog}
        unknown = [name for name in mutation_ids if name not in known]
        if unknown:
            raise ValueError(f"unknown mutation id(s): {', '.join(unknown)}; known: {', '.join(sorted(known))}")
        catalog = [entry for entry in catalog if entry["id"] in set(mutation_ids)]

    per_repeat = []
    for _ in range(repeats):
        outcome = {"zero": _run_against(None, probes)}
        for mutant in catalog:  # Sequential: each mutant gets the proxy to itself.
            outcome[mutant["id"]] = _run_against(mutant["id"], probes)
        per_repeat.append(outcome)

    now = datetime.now(timezone.utc)
    doc = {
        "profile": profile,
        "run_id": now.strftime("%Y%m%dT%H%M%SZ") + "-mutation",
        "at": now.isoformat(timespec="seconds"),
        "repeats": repeats,
        "probe_count": len(probes),
        "strategy_version": probes[0].get("strategy_version", "v000") if probes else "v000",
        **summarize(catalog, per_repeat),
    }
    path = OUT_DIR / profile / "mutation_test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return doc


def summary_line(doc: dict) -> str:
    s = doc["summary"]
    return f"planted {s['planted']} · caught {s['caught']} (flaky {s['flaky']}) · false flips (max over repeats) {s['false_flips_max']}"


def main() -> int:
    from archaeologist import probes as probe_store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--mutations", help="Comma-separated mutation ids; default is the whole catalog")
    arguments = parser.parse_args()
    ids = [name for name in arguments.mutations.split(",") if name] if arguments.mutations else None
    doc = run(arguments.profile, probe_store.load_cached(arguments.profile), arguments.repeats, ids)
    print(summary_line(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
