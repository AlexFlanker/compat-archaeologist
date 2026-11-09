"""One-off: regenerate probes/lite.json after the seal, under the prompt guard, with the strategy the pipeline would use.

The pipeline's own regeneration path (_load_or_generate_probes -> probes.generate(profile, ledger)) passes no
strategy, so probes.generate uses strategy.active(): the shared roster's active version. Keep that.
"""

import json

from archaeologist import heldout, improve, meta_client, probes, strategy
from archaeologist.config import OUT_DIR
from archaeologist.extract_querylog import load_log, query_log_path

profile = "lite"
held = heldout.load_or_create(profile)
heldout.install_guard(held, load_log(query_log_path(profile)))
ledger = json.loads((OUT_DIR / profile / "ledger.json").read_text())
improve._refuse_pre_seal_ledger(ledger)  # raises StaleLedger if the ledger still carries sealed operations
assert meta_client.guard_installed()
active = strategy.active()
print(f"seal {held['created']} held-out {held['heldout_shape_ids']}; strategy {active['version']}; items {[i['id'] for i in ledger['items']]}")
generated = probes.generate(profile, ledger, strategy=active, base_probes=[], out_path=probes.cache_path(profile))
for probe in generated:
    print(f"  {probe['status']:8} {probe['probe_id']} attempts={probe['generation']['attempts']} generated_at={probe['generated_at']} "
          f"guarded={probe['prompt_guarded']} {probe.get('invalid_reason', '')[:140]}")
print("provenance:", probes.provenance(generated, held))
