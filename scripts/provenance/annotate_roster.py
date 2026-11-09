"""One-off: mark the five roster entries whose numbers were copied from the reference as measured: false.

An annotation, not a rewrite: json.load keeps key order, only the five entries gain one key at the end,
and the file is written back the way strategy.record writes it.
"""

import json
from pathlib import Path

path = Path("strategy/roster.json")
before = path.read_text()
doc = json.loads(before)
unmeasured = {"v001", "v002", "v003", "v006", "v007"}
for entry in doc["history"]:
    if entry["version"] in unmeasured:
        assert "measured" not in entry, entry["version"]
        copied = entry["reason"] == "edit targets no generatable feature" or entry["reason"].startswith("generation failed")
        assert copied, (entry["version"], entry["reason"])
        entry["measured"] = False
after = json.dumps(doc, indent=2) + "\n"
assert json.dumps(json.loads(before), indent=2) + "\n" == before, "roster.json is not in strategy.record's format"
path.write_text(after)
changed = [e["version"] for e in doc["history"] if "measured" in e]
print(f"annotated {changed}; {len(doc['history'])} entries; active {doc['active']}")
