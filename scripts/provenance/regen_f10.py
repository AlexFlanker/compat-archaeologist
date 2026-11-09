import json

from archaeologist import heldout, mutants, probes, strategy
from archaeologist.config import OUT_DIR
from archaeologist.extract_querylog import load_log, query_log_path

profile = "shoplog"
active = strategy.active()
assert active["version"] == "v005", active["version"]
held = [m for m in mutants.load_catalog() if m["split"] == "heldout"]
extra = [text for m in held for text in (m["id"], m["description"])]
heldout.install_guard(heldout.load_or_create(profile), load_log(query_log_path(profile)), extra_forbidden=extra)
cache = probes.cache_path(profile)
current = json.loads(cache.read_text())
ledger = json.loads((OUT_DIR / profile / "ledger.json").read_text())
generated = probes.generate(
    profile, ledger, strategy=active, only_features={"F10"}, base_probes=current, out_path=cache, extra_forbidden=extra,
)
for probe in generated:
    probe["strategy_version"] = "v005"
cache.write_text(json.dumps(generated, indent=2))
print(len(generated), [(p["probe_id"], p["status"]) for p in generated if p["feature_id"] == "F10"])
