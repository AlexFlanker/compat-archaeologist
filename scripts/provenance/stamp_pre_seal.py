"""One-off: stamp `pre_seal_check` on every ShopLog probe that has no `generated_at` stamp.

For each such probe, the ledger item its prompt was built from must hold no sealed shape: the held-out
guard (shape strings and literal commands of every sealed entry, built from heldout.load_or_create and
the query log) must not fire on the item's `sample_operations` or `sources`. One failure stops the
script before anything is written.

Which ledger a probe's prompt came from is decided from git, not assumed:
- carried: probe_id and steps identical to a probe at 6b69027 (written 2026-11-07, before the seal).
  Checked against the ledger at 6b69027, as the fix brief specifies; also reported at 76187a2 and
  84c6841, the ledger versions that existed when these probes were written.
- v005 (first present at 7448e65): written by improve.iterate at 09:04Z on 2026-11-09, before that
  commit's pipeline run rewrote ledger.json, so from the ledger committed at 65ba1b9 (same blob as 6b69027).
- F10 regenerated (first present at 8bf8e64): written by logs/regen_f10.py from the ledger committed at
  7448e65 (8bf8e64 has the same blob).
For the post-seal probes the script also rebuilds the exact prompt (system + user) from that ledger and
strategy v005 and looks for its sha256 prefix among the ok calls in logs/meta_calls.jsonl, which proves
the attribution. That is reported, not required.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from archaeologist import heldout, probes, strategy
from archaeologist.extract_querylog import load_log, query_log_path
from archaeologist.probe_runner import TTL_WAIT_LIMIT_S

PROFILE = "shoplog"
PROBES_PATH = Path("probes/shoplog.json")
CARRIED, V005, F10_REGEN = "6b69027", "7448e65", "8bf8e64"


def git_json(commit: str, path: str):
    return json.loads(subprocess.run(["git", "show", f"{commit}:{path}"], capture_output=True, text=True, check=True).stdout)


def blob(commit: str, path: str) -> str:
    return subprocess.run(["git", "rev-parse", f"{commit}:{path}"], capture_output=True, text=True, check=True).stdout.strip()


def fingerprint(probe: dict) -> tuple[str, str]:
    return probe["probe_id"], probes.probe_hash(probe)


def item_for(ledger: dict, feature: str) -> dict | None:
    return next((item for item in ledger["items"] if item["id"] == feature), None)


doc = heldout.load_or_create(PROFILE)
entries = load_log(query_log_path(PROFILE))
guard = heldout.guard_for(doc, entries)
per_shape = {sid: heldout.guard_for({"heldout_shape_ids": [sid]}, entries) for sid in doc["heldout_shape_ids"]}


def sealed_shapes(item: dict) -> list[str]:
    text = json.dumps(item.get("sample_operations", []), default=str) + json.dumps(item.get("sources", []), default=str)
    return sorted(sid for sid, check in per_shape.items() if check(text))


def guard_hits(item: dict) -> list[str]:
    hits = [guard(json.dumps(item.get(key, []), default=str)) for key in ("sample_operations", "sources")]
    return [hit for hit in hits if hit]


def prompt_sha(item: dict, active: dict) -> str:
    count = strategy.count_for(active, item)
    system = strategy.render_system(probes.SYSTEM_PROMPT.format(ttl_wait=TTL_WAIT_LIMIT_S - 8, count=count), active)
    user = probes._user_prompt(item)
    hint = strategy.hint_for(active, item["id"])
    if hint:
        user += f"\n\nAdditional guidance for this feature: {hint}"
    return hashlib.sha256((system + user).encode()).hexdigest()[:16]


logged = {}
for line in Path("logs/meta_calls.jsonl").read_text().splitlines():
    call = json.loads(line)
    if call.get("status") == "ok" and call["purpose"].startswith("probe_generation:"):
        logged.setdefault(call.get("prompt_sha256_16"), []).append(f"{call['at']} {call['purpose']}")

assert blob("65ba1b9", "out/shoplog/ledger.json") == blob(CARRIED, "out/shoplog/ledger.json"), "v005 ledger is not the 6b69027 ledger"
assert blob(F10_REGEN, "out/shoplog/ledger.json") == blob(V005, "out/shoplog/ledger.json"), "F10 ledger moved between 7448e65 and 8bf8e64"
ledgers = {commit: git_json(commit, "out/shoplog/ledger.json") for commit in (CARRIED, "76187a2", "84c6841", V005)}
present = {commit: {fingerprint(p) for p in git_json(commit, "probes/shoplog.json")} for commit in (CARRIED, V005)}
v005 = strategy.load("v005")

current = PROBES_PATH.read_text()
batch = json.loads(current)
print(f"held-out {doc['heldout_shape_ids']} sealed {doc['created']}")
print(f"{len(batch)} probes in {PROBES_PATH}; {sum(1 for p in batch if 'generated_at' in p)} carry generated_at")
plan, failures = [], []
for probe in batch:
    if "generated_at" in probe:
        continue
    key = fingerprint(probe)
    if key in present[CARRIED]:
        origin, ledger_commit, extra = "carried pre-seal", CARRIED, ["76187a2", "84c6841"]
    elif key in present[V005]:
        origin, ledger_commit, extra = "v005 post-seal", CARRIED, []
    else:
        origin, ledger_commit, extra = "F10 regen post-seal", V005, []
    item = item_for(ledgers[ledger_commit], probe["feature_id"])
    if item is None:
        failures.append(f"{probe['probe_id']}: no ledger item {probe['feature_id']} at {ledger_commit}")
        continue
    shapes, hits = sealed_shapes(item), guard_hits(item)
    also = {commit: sealed_shapes(item_for(ledgers[commit], probe["feature_id"]) or {}) for commit in extra}
    proof = ""
    if origin != "carried pre-seal":
        sha = prompt_sha(item, v005)
        proof = f" prompt sha {sha} logged: {logged.get(sha, ['NOT FOUND'])[0]}"
    print(f"  {probe['probe_id']:45} {origin:20} ledger {ledger_commit}: sealed shapes {shapes or 0} guard {hits or 'clean'}"
          + (f"; also {also}" if also else "") + proof)
    if shapes or hits or any(also.values()):
        failures.append(f"{probe['probe_id']}: ledger {ledger_commit} item {probe['feature_id']} carries {shapes or hits}")
    else:
        plan.append((probe, ledger_commit))

if failures:
    print("STOP: nothing stamped")
    for failure in failures:
        print("  " + failure)
    sys.exit(1)
for probe, ledger_commit in plan:
    probe["pre_seal_check"] = {"ledger_commit": ledger_commit, "heldout_shapes_in_prompt": 0}
PROBES_PATH.write_text(json.dumps(batch, indent=2) + ("\n" if current.endswith("\n") else ""))
print(f"stamped {len(plan)}: " + ", ".join(f"{c} x{sum(1 for _, x in plan if x == c)}" for c in sorted({c for _, c in plan})))
print("provenance:", probes.provenance(batch, doc))
