# ASI:One agent: free questions and gap drill-down

Status: approved 2026-11-08. Scope of this round: the agent becomes the main way in. What-if recalculation, assessing a user's own app and removing ShopLog-specific code are out of scope.

## 1. Routing: Muse first, keywords as the fallback

`fetch_agent/router.py` classifies each typed message with one Muse structured call into
`{action, view, gap, target, profile}`, where `action` is one of `decision`, `reversal`, `gaps`, `gap_detail`, `ledger`, `latency`, `assess`, `ask`.

- Card clicks that arrive as JSON skip the model.
- If Muse is unavailable, times out, or returns a value outside the enum, the keyword parser (`_intent`) decides, and the log records `router=fallback`.
- ASI:One sometimes delivers a button click as prose (the button label plus the selected radio option). Button labels therefore carry the feature id ("Dig into F4"), so both paths can resolve them.
- Per-sender memory keeps the last profile and gap, so "why is that one silent?" resolves to the gap last shown.

## 2. Gap detail card

Every blocking gap in "What breaks" gets a "Dig into Fn" button. The detail card is built only from files in `out/<profile>/` and `probes/<profile>.json`; no model call:

- the probe step that diverged, with MongoDB's and FerretDB's result side by side, or the error code and message;
- the command the probe sent at that step;
- the verified code locations from the ledger;
- related cutover checks (pass or fail);
- the fix approach and hour range (Muse, inferred), and this gap's step and cost in the reversal walk;
- previous, next, back to gaps, back to decision.

## 3. Grounded answers (`action = ask`)

`archaeologist/evidence_pack.py` deterministically compresses `out/<profile>/` (about 700 KB) into a small pack in which every fact has a stable id: `gap:F4`, `probe:F4.txn-abort-rollback`, `step:1`, `rule:R2`, `check:…`, `assumption:…`.

Muse answers only from the pack and returns `{answer, citations[], answerable}`. The same rule as `extract_code` applies: a citation that isn't in the pack is dropped. An answer with no valid citation that doesn't declare itself unanswerable is never sent; the agent says the evidence doesn't cover it and lists what it can answer. Citations become buttons into the gap detail card or the reversal walk.

## 4. Tests and evaluation

- Unit tests: router fallback, a detail card for every blocking gap under 64 KB, citation validation drops invented ids, the pack stays under its size bound.
- `scripts/eval_agent.py` sends about 25 phrasings (English and Chinese, follow-ups, out-of-scope questions) to live Muse and writes routing accuracy and citation validity to `out/agent_eval.json`.
