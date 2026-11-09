"""One round of the self-improving probe writer: edit the instructions, test them on planted gaps, keep or discard.

Muse sees only the train mutants that escaped. The adoption decision uses held-out mutants it never saw,
and is made by `decide` in code, not by the model. Every evaluation lands in strategy/roster.json.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from archaeologist import diff, heldout, meta_client, mutants, mutation_test, probes, strategy
from archaeologist.config import OUT_DIR
from archaeologist.extract_querylog import load_log, query_log_path

STRATEGY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["system_prompt_additions", "per_feature_hints", "probe_count_overrides", "required_checks"],
    "properties": {
        "system_prompt_additions": {"type": "string"},
        "per_feature_hints": {"type": "object", "additionalProperties": {"type": "string"}},
        "probe_count_overrides": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1, "maximum": 4}},
        "required_checks": {"type": "array", "items": {"type": "string"}},
    },
}

STRATEGY_SYSTEM = """You are improving the instructions given to a probe writer.

The probe writer produces behavioral probes that run on real MongoDB and on a MongoDB-compatible engine. To measure
how good its probes are, known incompatibilities were planted in front of MongoDB; the probes then should differ
from the unmodified MongoDB. Below are planted incompatibilities the current probes did not catch, the probes that
exist for those features, and any failures from the oracle gate (real MongoDB refusing a probe).

Propose the smallest change to the instructions that would make a rule-following probe catch them: system prompt
additions, per-feature hints (keyed by feature id), probe count overrides, and required checks (short sentences
every probe must satisfy). Return the complete new values for all four fields; an empty value keeps nothing.
You must always return at least one per_feature_hints entry, keyed by one of the listed feature ids exactly, that
proposes a concrete behaviour the current probes do not exercise (boundary conditions, empty or unmatched cases,
options not yet used), even when no planted incompatibility was missed. Rules: never ask probes to project or compare textScore values; keep the existing guidance valid; do not name
specific engines. Be concrete about what a probe must set up and read back."""

CONTENT_KEYS = ("system_prompt_additions", "per_feature_hints", "probe_count_overrides", "required_checks")


def decide(candidate: dict, active: dict) -> tuple[str, str]:
    """Pure adoption rule. Both arguments carry heldout_catch, train_catch, false_flips_max, invalid_probes."""
    if candidate["false_flips_max"] > active["false_flips_max"]:
        return "rolled_back", f"false_flips_max rose {active['false_flips_max']} -> {candidate['false_flips_max']}"
    if candidate["invalid_probes"] > active["invalid_probes"] + 1:
        return "rolled_back", f"invalid_probes {active['invalid_probes']} -> {candidate['invalid_probes']} exceeds +1"
    if candidate["heldout_catch"] > active["heldout_catch"]:
        return "adopted", f"heldout_catch {active['heldout_catch']} -> {candidate['heldout_catch']}"
    if candidate["heldout_catch"] == active["heldout_catch"] and candidate["train_catch"] > active["train_catch"]:
        return "adopted", f"train_catch {active['train_catch']} -> {candidate['train_catch']} at equal heldout_catch"
    if candidate["heldout_catch"] < active["heldout_catch"]:
        return "rolled_back", f"heldout_catch {active['heldout_catch']} -> {candidate['heldout_catch']}"
    return "rolled_back", "no held-out or train gain"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _measures(doc: dict, all_probes: list[dict]) -> dict:
    summary = doc["summary"]
    invalid = sum(1 for probe in all_probes if probe.get("status") == "invalid")
    return {
        "train_catch": summary["train"]["caught"],
        "heldout_catch": summary["heldout"]["caught"],
        "false_flips_max": summary["false_flips_max"],
        "invalid_probes": invalid + summary["invalid_probes_max"],
    }


def _all_probes(path) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []


def _valid(all_probes: list[dict]) -> list[dict]:
    return [probe for probe in all_probes if probe.get("status") != "invalid"]


def _entry_for(version: str) -> dict | None:
    found = [entry for entry in strategy.roster()["history"] if entry["version"] == version and entry["decision"] in ("adopted", "reference")]
    return found[-1] if found else None


def reference(profile: str, repeats: int) -> dict:
    """Measures the active strategy's probes and records a reference entry unless the roster already has one."""
    active = strategy.active()
    existing = _entry_for(active["version"])
    if existing:
        return existing
    all_probes = _all_probes(probes.cache_path(profile))
    valid = _valid(all_probes)
    doc = mutation_test.run(profile, valid, repeats)
    entry = {
        "version": active["version"],
        "parent": active["parent"],
        "decision": "reference",
        **_measures(doc, all_probes),
        "probes": len(valid),
        "generated_features": [],
        "ferretdb_run_id": None,
        "at": _now(),
        "reason": "baseline measurement of the starting strategy",
    }
    strategy.record(entry)
    return entry


def _latest_doc(profile: str, active: dict, repeats: int) -> dict:
    path = OUT_DIR / profile / "mutation_test.json"
    if path.exists():
        doc = json.loads(path.read_text())
        if doc.get("strategy_version", "v000") == active["version"]:
            return doc
    return mutation_test.run(profile, _valid(_all_probes(probes.cache_path(profile))), repeats)


def _coverage_section(ledger: dict, current: list[dict]) -> str:
    lines = ["Feature ids you may write per_feature_hints for (use these exact ids), with the current probes for each:"]
    for item in ledger["items"]:
        if item["id"] in probes.ORACLE_UNAVAILABLE:
            continue
        lines.append(f"{item['id']}: {item.get('feature', '')} (risk: {item.get('why_risky', '')})")
        for probe in current:
            if probe["feature_id"] == item["id"] and probe.get("status") != "invalid":
                lines.append(f"  - {probe['title']}: {probe['behavior']}")
    lines.append(
        "Required: return at least one per_feature_hints entry keyed by one of these exact ids, proposing a concrete "
        "behaviour the current probes above do not exercise."
    )
    return "\n".join(lines)


def _edit_prompt(active: dict, escaped: list[dict], current: list[dict], ledger: dict | None = None) -> str:
    sections = [f"Current strategy:\n{json.dumps({key: active[key] for key in CONTENT_KEYS}, indent=1)}"]
    if not escaped:
        sections.append("Every train mutant was caught; no train gap is outstanding.")
    if ledger is not None:
        sections.append(_coverage_section(ledger, current))
    for mutant in escaped:
        features = set(mutant["feature_ids"])
        lines = [f"Missed planted incompatibility {mutant['id']} (features {', '.join(mutant['feature_ids'])}): {mutant['description']}"]
        for probe in current:
            if probe["feature_id"] in features:
                tag = "oracle-gate failure" if probe.get("status") == "invalid" else "existing probe"
                detail = probe.get("invalid_reason", "")[:300]
                lines.append(f"- {tag} {probe['probe_id']}: {probe['title']} -- {probe['behavior']}" + (f" [{detail}]" if detail else ""))
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _propose(system: str, user: str) -> dict:
    for attempt in range(2):
        response = meta_client.structured(
            purpose="strategy_edit", system=system, user=user, schema=STRATEGY_SCHEMA, schema_name="strategy",
        )
        if all(key in response for key in CONTENT_KEYS):
            return response
    raise ValueError("strategy edit did not return the required fields")


def iterate(profile: str, repeats: int = 2) -> dict:
    ref = reference(profile, repeats)
    active = strategy.active()
    catalog = mutants.load_catalog()
    held = [m for m in catalog if m["split"] == "heldout"]
    extra = [text for m in held for text in (m["id"], m["description"])]
    heldout.install_guard(heldout.load_or_create(profile), load_log(query_log_path(profile)), extra_forbidden=extra)

    cache = probes.cache_path(profile)
    mutation_path = OUT_DIR / profile / "mutation_test.json"
    current = _all_probes(cache)
    doc = _latest_doc(profile, active, repeats)
    escaped = [m for m in doc["mutants"] if m["split"] == "train" and not m["caught"]]

    ledger = json.loads((OUT_DIR / profile / "ledger.json").read_text())
    response = _propose(STRATEGY_SYSTEM, _edit_prompt(active, escaped, current, ledger))
    candidate = {
        "version": strategy.next_version(),
        "parent": active["version"],
        "created": _now(),
        **{key: response[key] for key in CONTENT_KEYS},
    }
    strategy.save(candidate)

    targets = set(candidate["per_feature_hints"]) | {feature for m in escaped for feature in m["feature_ids"]}
    generatable = sorted(targets & {item["id"] for item in ledger["items"] if item["id"] not in probes.ORACLE_UNAVAILABLE})
    if not generatable:  # Nothing would change, so a measurement could only adopt noise.
        entry = {
            "version": candidate["version"], "parent": candidate["parent"], "decision": "rolled_back",
            **{key: ref[key] for key in ("train_catch", "heldout_catch", "false_flips_max", "invalid_probes", "probes")},
            "generated_features": [], "ferretdb_run_id": None, "at": _now(),
            "reason": "edit targets no generatable feature",
        }
        strategy.record(entry)
        return entry
    candidate_path = cache.with_name(f"{profile}.candidate.json")
    generated = probes.generate(
        profile, ledger, strategy=candidate, only_features=targets, base_probes=current, out_path=candidate_path,
        extra_forbidden=extra,
    )
    for probe in generated:  # The file is evaluated, and later replayed, as one version; probe_hash keeps replacements visible.
        probe["strategy_version"] = candidate["version"]
    candidate_path.write_text(json.dumps(generated, indent=2))
    valid = _valid(generated)

    previous = mutation_path.read_text() if mutation_path.exists() else None
    candidate_doc = mutation_test.run(profile, valid, repeats)
    measured = _measures(candidate_doc, generated)
    decision, reason = decide(measured, ref)

    entry = {
        "version": candidate["version"],
        "parent": candidate["parent"],
        "decision": decision,
        **measured,
        "probes": len(valid),
        "generated_features": generatable,
        "ferretdb_run_id": None,
        "at": _now(),
        "reason": reason,
    }
    if decision == "adopted":
        cache.write_text(candidate_path.read_text())
        try:
            entry["ferretdb_run_id"] = diff.run(profile, valid, probe_origin="muse")["run_id"]
        except Exception as error:  # The probes are already adopted on disk; the roster must say so.
            entry["reason"] += f"; FerretDB run failed: {type(error).__name__}: {str(error)[:120]}"
    elif previous is not None:
        mutation_path.write_text(previous)  # mutation_test.json keeps describing the active probes
    strategy.record(entry)
    return entry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["reference", "iterate"])
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--repeats", type=int, default=2)
    arguments = parser.parse_args()
    run = reference if arguments.command == "reference" else iterate
    entry = run(arguments.profile, arguments.repeats)
    print(json.dumps(entry, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
