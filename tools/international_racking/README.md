# International Racking: generator and checker (scoring side)

⚠️ **Never give this folder to the agent.** The generator and the checker encode the hidden rules.
The answer key is [`docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md`](../../docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md).

| File | What it does |
|---|---|
| `generate.py` | Deterministic, stdlib-only generator (seed 42). Writes only the agent-visible files into `datasets/international_racking/`. Originally written by Meta Muse Code, with fixes by Claude Code. |
| `check_ground_truth.py` | Verifies that every rule R1–R10 is planted with zero exceptions, that the agent's inputs don't leak answers or secrets, and prints the real numbers behind each pitch line. |
| `GENERATION_SUMMARY.md` | Actual generated counts, and the list of fixes applied after generation. |

```bash
python3 tools/international_racking/generate.py            # regenerate (identical output every time)
python3 tools/international_racking/check_ground_truth.py  # expect: 83 pass · 0 warnings · 0 failures
```
