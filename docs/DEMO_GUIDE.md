# Demo guide: how it works, the 3-minute loop, and the official callbacks

Snapshot: 2026-11-07, 2:55 PM PT, `main` @ 165182f. Freeze is at 4:15 and submission at 4:45. Every number here comes from today's real runs.

> **Before you migrate a database, get the archaeology done.**

## The story in 30 seconds

A TB-scale app moved from MongoDB Atlas to a "MongoDB-compatible" service to cut the bill. A managed full-load + CDC tool did the copy. After cutover, the team found that the CDC had missed events and that unique, TTL and validator constraints never came across. Reports then slowed down or timed out, features driven by change streams broke, and I/O billing and scale-ups followed. A major-version upgrade of the target didn't fix it.

The CTO never had three things: a 12-month TCO, performance evidence from the real workload, or a risk and reversibility assessment. Our agent produces all three. The story is a generic composite.

Two apps were built today for the demo:

- **ShopLog**: a fictional 10-year-old shop, with 50,000 synthetic orders and 10 hidden rules.
- **ShopLog Lite**: a control app that only does plain CRUD.

## How it works: dig, label, probe, prove, rehearse, decide

**Evidence layers**

- **Data and catalog** (observed): field types, indexes and validators, plus 29 invariants mined from 58,000 documents.
- **Query log** (observed): 1,554 profiler operations in 29 shapes, each tagged online, reporting or event-driven.
- **Code**: Meta Muse reads every file and cites file:line. A program checks each citation; 0 were wrong today. Code-only items are INFERRED.
- **Atlas**: an M10 cluster runs the workload, including Atlas Search. Its search index is read through the MongoDB MCP Server, which makes F11 OBSERVED. Performance Advisor wasn't collected, because there's no API service account.

**Pipeline**

| Stage | In plain words | Proof |
|---|---|---|
| 1. Dig | Read the data, the query log and the code, independently | 29 invariants · 29 shapes · 0 bad citations |
| 2. Label | Usage-Spec Ledger: every feature marked OBSERVED or INFERRED, with a confidence (from source agreement, or SubQ Snap for single-source items) | 11 features · 50 behaviors · dead code scored 0.57 |
| 3. Probe | Muse writes declarative tests. MongoDB is the referee: a test counts only if MongoDB agrees with Muse's prediction | 30 probes · 10 needed a repair · 0 excluded |
| 4. Prove | Same tests and same recorded workload on MongoDB 8.0 and FerretDB 2.7, with plans from the MongoDB MCP Server. DocumentDB is checked from its docs only | 384 reads · 353 writes · self-check 30/30 |
| 5. Rehearse | Simulate full-load + CDC while the app writes; audit what arrived; ask the business questions again. Also run the upgrade check, FerretDB 2.2 → 2.7 | clean: 6/23 checks fail · drill: 2/2 injected faults caught |
| 6. Decide | One-page CTO memo: top risks, 12-month TCO, latency, reversibility, SubQ second opinion | `out/shoplog/memo.md` · ASI:One cards · 134 s end to end |

**Safety nets**

- MongoDB must match itself exactly.
- MongoDB is the oracle for every probe.
- Citations are machine-checked.
- No model-written code runs.
- The same thresholds apply to every app.
- When a value can't be computed, it says "unavailable" instead of inventing a number.
- A secret check runs before every push.

## Numbers to remember

- **10/10** hidden rules recovered blind, all labeled correctly. One scoring pattern was fixed after the first run; before the fix, the score was 9 found and 8 labeled.
- **5 of 8** business answers changed after a clean cutover and one morning of traffic.
- **9 ms → 4.2 s**: revenue report p95, about 470× slower.
- **2/2** injected cutover faults caught.
- **ShopLog → FerretDB**: −$113,043 over 12 months; SubQ gives 0.0002 that it pays off; reversibility 15/100.
- **48/48** edge-case documents designed by SubQ Preview behaved the same on FerretDB. The gaps are behavior, not data.
- **Control → FerretDB**: +$8,604; SubQ 0.94; "the migration is fine".

| # | ShopLog depends on | FerretDB 2.7 (measured) | DocumentDB 8.0.2 (docs only) |
|---|---|---|---|
| F1 | $lookup sub-pipeline + $facet | Same results, ~470× slower | Documented |
| F2 | $setWindowFields | Same | $shift not supported |
| F3 | arrayFilters + all-positional `$[]` | **Silent**: the all-positional field is left empty | Documented |
| F4 | Transactions | **Silent**: abort doesn't roll back, and commit errors after the write | Documented, with limits |
| F5 | Change streams | Not supported | Off by default |
| F6 | Case-insensitive email index | Refused; 17/40 logins miss | 8.0 only |
| F7 | Partial + TTL indexes | Same | Documented |
| F8 | $jsonSchema validator | **Silent**: bad orders accepted | Documented |
| F9 | $text | Same matches, different ranking | No exclusion terms |
| F10 | Plain CRUD | 504/504 same | Documented |
| F11 | Atlas Search | Inferred: needs a separate search service | Inferred: needs a separate search service |

## The reversal (the spine of the demo)

The opening story is about a decision that looked right on paper. The run shows that decision being reversed, one finding at a time, in the same 12-month net:

| # | What the agent found | Running net | Decision |
|---|---|---|---|
| 0 | On paper: wire-compatible, cheaper infrastructure, standard migration | +$20,124 | Migrate |
| 1 | arrayFilters / positional updates | +$5,904 | Migrate |
| 2 | Transactions don't roll back | −$20,796 | **Stay ← flips here** |
| 3–6 | Collation, validator, change streams, slow reports | −$71,285 | Stay |
| 7 | No Atlas Search | −$101,523 | Stay |
| 8 | Running it yourself | −$113,043 | Stay |

SubQ Snap answers the same question twice: **>0.999** with only the paper facts, **<0.001** with the evidence. The control app doesn't flip: +$20,124 on paper, +$8,604 with evidence.

## The 3-minute loop

Start `./run_demo.sh` in the first 10 seconds. It takes about 134 s, mostly a real 72-second TTL wait. Narrate from the previous memo while it runs, show the live decision table when it finishes, then run the 9-second control flip.

**Loop:** Dig → Label → Probe → Prove → Rehearse → Decide → Flip → Score

| Time | Say | Show | Callback |
|---|---|---|---|
| 0:00 | "Everything here was built today after 10:45." Then the story in one breath. Start `./run_demo.sh` and the evidence site (`python -m archaeologist.evidence_site.serve --profile shoplog --port 8791`). For the first 15 seconds, stay on the query log page: "This is how the app really ran. A slice of it was sealed: the model saw the code; it never saw how these operations ran in production." Then one Challenge on an F4 finding: the probe re-runs on both engines live. | Evidence site: query log, then an F4 finding page and its Challenge button | Hard rule: built today · Impact |
| 0:20 | "The agent writes the spec nobody wrote: 11 features, each observed or inferred, with a confidence. It even finds dead code." | Ledger table, then "hidden rules recovered blind: 10/10" | Software Archaeology · Meta |
| 0:45 | (Results table and roster in the README: 10 of 10 planted gaps caught, one strategy adopted, six rolled back, and why.) "Muse writes 30 tests. MongoDB is the referee and overruled Muse's first try on 10 probes. Then both engines run them." | Memo: Top risks | Behavioral equivalence · Trust but verify · Meta |
| 1:20 | "The revenue report goes from 9 ms to 4 s. The MCP explain plan shows a sequential scan inside a nested loop. Projected, it hits a 30 s timeout near 100 MB, versus near 70 GB on MongoDB. That projection is inferred." | Memo: latency, "At production size" | Technical demo · MongoDB MCP |
| 1:50 | "Even a clean copy loses the case-insensitive unique index and the validator. In the drill we inject a CDC gap and a dropped index, and both are caught. After one morning, 5 of 8 business numbers changed, and checkout said 'failed' while saving the order." | Memo: Cutover fidelity, Business questions | Impact · Creativity · Difficulty |
| 2:20 | "Here is the decision the team made on paper: +$20k, migrate. SubQ agreed, at over 0.999. Watch it reverse: the first finding, transactions, flips it, and by the end it's −$113,043 and SubQ is under 0.001. Stay. Reversibility 15. DocumentDB isn't verified, so test it first." Then run `./run_demo.sh --profile lite`. "Migration is fine, +$8,604, SubQ 0.94. Same pipeline, same thresholds." | Decision table, then the Lite run | Fairness · TCO |
| 2:45 | "It also runs inside ASI:One. Before you migrate a database, get the archaeology done." | ASI:One card screenshot | Fetch.ai · close |

**1-minute video:**

1. 0:00 Built today, plus the story.
2. 0:10 Ledger and the 10/10 scoreboard.
3. 0:25 Transactions that don't roll back; 5 of 8 business numbers changed.
4. 0:40 Stay versus the control app.
5. 0:55 Closing line.

**Fetch.ai video (3–5 min, inside ASI:One):**

1. The Agentverse page: Active, ASI Available, Mailbox.
2. Send: `@agent1q2nccakc9zqesdcyyhk2r8xe4usukgvkpvn58r97gq6neyta0c6rjc6jus3 Assess the migration of ShopLog from MongoDB to a compatible engine`
3. The decision card, then "What breaks" and "Usage-Spec Ledger".
4. "Run the control app": the verdict flips.

## Official callbacks

| Official point | Status | Where we hit it |
|---|---|---|
| Problem: Software Archaeology (implicit spec, observed vs inferred, behavioral equivalence) | Done | Ledger from 3 sources, labels, probes plus replay |
| Trust but Verify | Done | MongoDB as oracle, self-check 30/30, citation checks, cutover verifier |
| Judging: Impact 20% | Done | CTO one-pager, TCO, changed business numbers |
| Judging: Technical Demo 35% | Done | Live 134 s run, live flip, ASI:One card |
| Judging: Creativity 15% | Done | Database as referee for an LLM, fault-injection drill, plan-shape projection, blind scoreboard |
| Judging: Implementation Difficulty 30% | Done | Probe language, differential runner, replay, CDC copier and verifier, 24 tests |
| Best Use of Meta Model API | Done | muse-spark-1.3 for code evidence, reconciliation, 30 probes with repair, rewrite estimates; 51 calls in `logs/meta_calls.jsonl` |
| Fetch.ai: Agentverse registration | Done | Active · ASI Available · Mailbox |
| Fetch.ai: Chat Protocol | Done | Manifest published |
| Fetch.ai: discoverable, workflow inside ASI:One, tool execution | Done | Tested today: message received, pipeline ran, card rendered |
| Fetch.ai: Interactive Cards (recommended) | Partly | Decision card verified; the other cards pass format tests but haven't been seen in ASI:One |
| Fetch.ai judging: payment protocol | **Not built** | Don't claim it |
| Fetch.ai: README name, address, badges | Done | README |
| Fetch.ai: ASI:One Submission Agent | **To do** | Alex |
| MongoDB as hero | Done (minus Performance Advisor) | Oracle, MongoDB MCP Server, Atlas M10 as evidence source and results store, Atlas Search index observed (F11 OBSERVED). Performance Advisor not collected |
| Hard rules | Done | README sections, pre-push secret check, synthetic data, memo plus cards instead of a dashboard |

## Guardrails for Q&A

- **Don't say "DocumentDB lacks these features."** Its docs claim support, and nothing was run there, so it needs testing.
- **FerretDB results don't apply to DocumentDB.** They're different engines.
- **FerretDB documents** that commit and abort aren't implemented. The risk is that the driver ignores the abort error at runtime.
- **The drill faults were injected on purpose.**
- **10/10 has a footnote:** one pattern was fixed after the first run.
- **TB-scale numbers are projections** (INFERRED).
- **International Racking isn't wired into the pipeline yet.**
- **We didn't build** the payment protocol, and **didn't collect** Atlas Performance Advisor output (no API service account).
- **"Isn't this MongoDB marketing?"** No. The same pipeline tells the control app migration is fine, and it reports where FerretDB matches or is faster.

## Left to do

| Task | Owner | When |
|---|---|---|
| Register with the AIE ASI:One Submission Agent | Alex | Now |
| Click through the other cards once in ASI:One | Alex | Before recording |
| Rehearse this loop and adapt the wording | Lev0gan | By 3:45 |
| `docs/QA.md` and `docs/SUBMISSION.md`, via PR | Lev0gan | By 4:00 |
| Record the 1-minute and 3–5 minute videos | Alex | 4:15–4:35 |
| Submit the Cerebral Valley form | Alex | By 4:45 |
| Keep the agent and Docker running through judging | Builder | Until judging ends |
