# Compat Archaeologist

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3) ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Before you migrate a database, get the archaeology done.**

A TB-scale production app moves from MongoDB Atlas to a "MongoDB-compatible" service to cut the bill and consolidate cloud spend. A managed full-load + CDC tool does the migration. After cutover, the team finds that the CDC missed events, and that unique, TTL and validator constraints never came across. Online and reporting aggregations slow down or time out. Features driven by change streams break. I/O billing and scale-ups follow, then extra services. A later major-version upgrade of the target doesn't materially help. The team is still on it, paying more than before.

The CTO who approved it never had three things: a 12-month TCO, performance evidence from the real workload, and an assessment of risk and reversibility. That story is a composite and is kept generic.

Compat Archaeologist is an agent pipeline that produces that evidence before the decision:

1. **Reconstructs the real MongoDB usage spec** of an application from three kinds of evidence: data samples (and the catalog), the profiler query log, and the application code. Atlas Performance Advisor adds a fourth source when an Atlas cluster is available. Each item is **OBSERVED** (seen at runtime or in the catalog) or **INFERRED** (code only), with a confidence score.
2. **Proves behavioral equivalence, or finds the gaps.** It generates probes for every ledger item and replays the recorded workload on MongoDB and on a compatible target, side by side: result diffs, errors and p50/p95 latency, plus query plans for the slowest paths.
3. **Verifies the cutover itself.** It simulates a full-load + CDC migration while the source keeps taking writes, then reconciles independently: counts and content hashes, index and validator parity, the source oplog against applied events, business rules, and day-one writes the source would refuse.
4. **Checks whether an upgrade helps.** The same probes and replay run against an older and a newer target version. Version-compatible isn't behavior- or performance-compatible.
5. **Writes a one-page brief for the CTO**, with an evidence appendix. It covers the recommendation and its confidence, the top five risks with evidence links, and a 12-month TCO (infrastructure, I/O, scale-ups, extra services, rewrite person-days, incident cost). It also gives replay p95 and timeout rates, a reversibility score, and a projection to production size, marked INFERRED.

The output is a Markdown memo ([ShopLog](out/shoplog/memo.md), [control app](out/lite/memo.md)) and, in ASI:One, interactive cards. There is no dashboard.

## What it found

The demo app, ShopLog, is a fictional 10-year-old e-commerce system built today. It deliberately depends on MongoDB-specific features, as real apps do. FerretDB 2.7 ran locally in Docker; AWS DocumentDB was compared against its documentation only.

Headline results, each with the committed file it comes from:

| Result | Number | Source |
|---|---|---|
| Probes for ShopLog | 30 valid, 0 invalid (strategy v005); 11 needed more than one attempt (15 extra in all) | `probes/shoplog.json` (`status`, `generation.attempts`) |
| Planted incompatibilities caught | 10 of 10 (train 6/6, held-out 4/4); 0 false flips with nothing planted; 3 repeats | `out/shoplog/mutation_test.json` |
| Replay on operations the model never saw | 97 recorded operations across 5 shapes: 97 equal, 0 different | `out/shoplog/runs.json` (`replay.reads.unseen_only`) |
| Probe strategy versions | 1 adopted (v005), 6 rolled back (v001-v004, v006, v007); the starting v000 caught 9 of 10 | `strategy/roster.json` |
| Cutover checks | Clean copy: 6 of 23 checks fail. Drill: 2 of 2 injected faults caught | `out/shoplog/cutover_clean.json`, `out/shoplog/cutover_drill.json` |
| 12-month net, ShopLog to FerretDB | +$20,124 on paper, -$113,043 with the evidence (flips at the transactions finding) | `out/shoplog/tco.json` |

The roster, in order (held-out catch counts the 4 planted gaps the strategy editor never saw; train counts the 6 it did see). Rows marked "not measured" carry `"measured": false` in `strategy/roster.json`: their numbers are the parent's, copied when no candidate was measured, and the evidence site's Strategy page shows them as "not measured":

| Version | Decision | Held-out | Train | Why |
|---|---|---|---|---|
| v000 | reference | 3/4 | 6/6 | Baseline measurement |
| v001-v003 | rolled back | not measured (parent's numbers: 3/4) | not measured (6/6) | Empty edits: with no escaped train gap, the prompt did not list ledger features, so Muse had nothing to target. Fixed in the prompt afterwards (commit 01f9c64); the entries are kept |
| v004 | rolled back | 3/4 | 6/6 | Regenerated F3, F7: no held-out or train gain |
| v005 | **adopted** | 4/4 | 6/6 | Regenerated F1, F3: held-out 3 to 4 |
| v006 | rolled back | not measured (parent's numbers: 4/4) | not measured (6/6) | Crashed on four invalid-JSON Muse responses for F3; recorded as rolled back after the fact |
| v007 | rolled back | not measured (parent's numbers: 4/4) | not measured (6/6) | The one retry of that round crashed the same way, but on `probe_generation:F2` (four invalid-JSON responses, 09:53-09:58Z per `logs/meta_calls.jsonl`), not F3. The reason text in the roster was written at recording time and is left as recorded |

One adoption on a 4-mutant held-out set is a single mutant, not a trend. Held-out catch counts chose v005, so the final 10 of 10 is no longer a blind number for those 4. The F10 probes were regenerated after review, and the mutation-test table is measured on the final set. v006 and v007 were recorded by hand-run calls to `strategy.record` after the crash, because the code did not yet record failures (it does now, commit 65ba1b9).

Observed on FerretDB 2.7.0, each also checked with plain pymongo outside the pipeline (the arrayFilters row was re-checked on 2026-11-09 against the same two engines, in a scratch database that was dropped afterwards):

| Feature | MongoDB 8.0 | FerretDB 2.7 |
|---|---|---|
| Write inside a transaction, read from outside before commit | Not visible | **Visible immediately** |
| `abort_transaction()` | Rolls back | **No error, and no rollback** (the driver ignores the abort error) |
| `commit_transaction()` | Commits | **Error 59, yet the write has already happened** |
| Insert that violates a `$jsonSchema` validator | Refused (121) | **Accepted; the validator isn't kept** |
| Case-insensitive collation index | Works | Refused (115), and case-variant logins find nobody |
| Change streams | Work | Not supported (115) |
| Revenue report (`$lookup` sub-pipeline + `$facet`) | ~7 ms | **~4,000 ms**: the Postgres plan does a sequential scan inside a nested loop |
| An update with two `arrayFilters` identifiers in one `$set` (`$[a].refund_status` and `$[b].reviewed_by`, both matching the same line) | Applies both fields | **Applies the first and silently drops the second's field**, while `modified_count` is still 1. A single-identifier update and an all-positional `$[]` update behave the same on both engines. Found by three v005 probes (`F3.arrayfilters-*`); the 56 recorded refund writes replayed equal |
| `$setWindowFields`, plain CRUD, `$text` matching | | Same results. `$text` ranks differently; writes are faster on FerretDB |

FerretDB's own [compatibility page](https://docs.ferretdb.io/migration/compatibility/) lists transactions as not implemented yet, so that part is documented. The risk is that at runtime nothing tells the application.

**Cutover fidelity.** The same copier, MongoDB to MongoDB, passes all 23 checks, so the verifier raises no false alarms.
- **Clean migration to FerretDB**, with nothing injected: data and change events match exactly, yet 6 of 23 checks fail. The case-insensitive unique index and the schema validator don't survive. On day one, a duplicate account (same email, different case) and an invalid order are both accepted, and two business rules break.
- **Drill**, with a CDC gap and a dropped unique index injected on purpose: the verifier caught both, finding 21 of the 27 affected documents out of sync. The other 6 were healed by later change events. The oplog count also exposed the gap.

**Upgrade check.** From FerretDB 2.2.0 to 2.7.0, none of the failing behaviors were fixed, and the revenue report stayed at about 4.1–4.4 s. Window functions did get about 2× faster (8.9 → 4.4 ms).

**Business questions.** The pipeline asks both systems eight questions the business runs on: revenue, refunds, orders by status, active customers, top products, coupon redemptions, stock on hand, and customers by tier.
- **On the same untouched data**, all 8 answers match. FerretDB computes these aggregates correctly.
- **After a clean cutover and one morning of identical traffic**, 5 of 8 differ. 12 units of stock stay "reserved" by checkouts that failed and never released them. An invalid order adds a status that shouldn't exist. A duplicate account shows up in the tier counts.
- **The same 12 checkouts**: the 6 that succeed on MongoDB all return error 59 on FerretDB, yet their orders were saved.

**Edge cases.** SubQ's generative model, `subq-preview`, designed 48 awkward documents for ShopLog's real fields: dates before 1970 and in year 9999, int64 beyond 2^53, -0.0, NaN, Decimal128 money, Unicode, and null versus missing. MongoDB refereed, and round-trips were compared with BSON types kept exactly. FerretDB stored, matched, sorted and typed all 48 the same as MongoDB, and MongoDB matched itself on all 48 too. So the gaps are in behavior (transactions, validators, collation, change streams), not in storing data.

**MongoDB Atlas.** A dedicated M10 cluster served as an evidence source and results store, never as part of the latency comparison. ShopLog was seeded there (every index and validator accepted, including the case-insensitive unique index FerretDB refuses), an Atlas Search index was built, and the workload ran storefront `$search` queries. The MongoDB MCP Server read the search index back, which upgrades F11 (Atlas Search) from INFERRED to OBSERVED. Every assessment is saved to `compat_archaeologist.assessments` in Atlas. Performance Advisor wasn't collected, because it needs an Atlas API service account.

**Hidden rules, scored blind.** ShopLog has 10 planted business rules ([docs/RULES.md](docs/RULES.md)) that the pipeline never reads. A separate scorer matches the recovered evidence against fixed patterns, not a model's judgment.
- **From data and the catalog alone:** 8 of 10 found, plus 1 partly.
- **With Muse reading the code:** 10 of 10 found and 10 of 10 labeled correctly. That includes the refund window, which exists only in code and is correctly labeled INFERRED.
- **Disclosure:** one scoring pattern was fixed after the first Muse run, because it depended on word order. Before the fix, the score was 9 found and 8 labeled. The change log is in `archaeologist/scoreboard.py`.

**Fairness check.** The same pipeline, thresholds and assumptions run on a control app, ShopLog Lite, which uses only plain CRUD. Its verdict is *the migration is fine*: FerretDB matched MongoDB on everything it does, with a 12-month net of +$8,604.

**Probes.** Meta Muse wrote 30 behavioral probes for ShopLog (strategy v005). 11 of them needed more than one generation attempt (15 extra attempts in all, from each probe's `generation.attempts` in `probes/shoplog.json`): MongoDB, acting as the oracle, rejected Muse's first prediction and Muse repaired it. No probe had to be excluded. MongoDB against itself matched on all 30.

**Second opinion.** SubQ Snap's probability that migrating is net-positive within 12 months, given the same facts: ShopLog to FerretDB **0.0002**, control app to FerretDB **0.94**.

**AWS DocumentDB.** Engine 8.0.2's documentation lists support for most of these features. The memo flags only where ShopLog's actual usage conflicts with documented limits (`$shift` in window functions, `$text` exclusion terms and array fields, Atlas Search) and marks the target as **not verified**. FerretDB results say nothing about DocumentDB: they are different engines with similar names.

## How we checked the checks

A probe suite that finds gaps is only trustworthy if it also finds gaps we know are there, and if it is not graded on what the model already saw.

- **Held out from the model.** A slice of the recorded workload was sealed on 2026-11-09, before the improvement rounds: the model saw the code; it never saw how these operations ran in production. The sealed slice (`out/shoplog/heldout.json`) is replayed on both engines as the "unseen-only" row above. The memo and the evidence site print that sentence only when every probe's prompt is accounted for (`probes.provenance`); otherwise they say how many probes predate the seal unchecked and make no claim. For the 30 ShopLog probes: the 22 carried from 2026-11-07 now carry a `pre_seal_check` stamp recorded by a programmatic check against the pre-seal ledger at commit 6b69027 (the ledger item each prompt was built from holds no sealed shape string or sealed command). The six F1 and F3 probes were written after the seal (v005) from that same ledger and carry the same stamp. The two F10 probes were regenerated after the seal, because their 2026-11-07 prompt carried sealed operations; they carry the stamp against the ledger they were written from (7448e65). For those eight, the prompt rebuilt from the named ledger matches the hash recorded for the call in `logs/meta_calls.jsonl`. Probes written from now on carry `generated_at` and `prompt_guarded` instead. The ShopLog Lite control probes were regenerated after the seal on 2026-11-09, under the guard. A prompt's query-log source line can still name a sealed shape by its hash id (S181542d4 for ShopLog F10); the id carries no part of the operation.
- **Planted gaps.** A proxy sits in front of MongoDB and breaks one behavior at a time (for example, abort that commits, validator dropped, `$lookup` that drops unmatched rows). 10 such mutants are in `mutants/catalog.json`, split into 6 train and 4 held-out. The probes run against each mutant; a mutant is caught if any probe differs from the unmodified MongoDB. With zero mutations the proxy must produce zero differences (the false-flip check). Everything repeats 3 times, so a flaky catch is reported as flaky, not as caught (`archaeologist/mutation_test.py`).
- **Adoption rule.** A new probe strategy is adopted only if it adds no false flips and no more than one more invalid probe than the active strategy, and catches more held-out mutants (or the same number and more train mutants); the decision is made in code (`improve.decide`), not by the model, and Muse only ever sees the train mutants that escaped.
- **Evidence site.** `python -m archaeologist.evidence_site.serve --profile shoplog --port 8791`, then open http://localhost:8791. The server's default port is 8765; the guide uses 8791 because 8765 was taken on the demo machine (a taken port prints `port <n> in use; pass --port`). It shows the coloured query log, one page per finding with its status history, and the planted-gap results. The Challenge button on a finding re-runs that probe live on both engines and refreshes the difference and verdict. For a static copy: `python -m archaeologist.evidence_site.build --profile shoplog --out site/` (the Challenge button needs the live server).

## How it works

```
 evidence sources (fictional app "ShopLog")
   data + catalog ─┐   profiler query log ─┐   app code ─┐   Atlas Performance Advisor (MCP) ─┐
                   ▼                       ▼             ▼                                    ▼
          deterministic miners        log normalizer   Meta Muse + citation check     MongoDB MCP Server
                   └──────────────┬────────┴─────────────┴────────────────────────────────────┘
                                  ▼
                      Usage-Spec Ledger  (OBSERVED / INFERRED, confidence; SubQ Snap for single-source items)
                                  ▼
                      Probe generator (Meta Muse) → declarative probes, validated on MongoDB first
                                  ▼
            ┌─────────────────────┴─────────────────────┐
     MongoDB 8.0 (Docker)                       FerretDB 2.7 (Docker)        AWS DocumentDB 8.0.2: docs only (INFERRED)
            └─────────────────────┬─────────────────────┘
                                  ▼
          Differential runner: probes + workload replay + explain() plans via MCP
                                  ▼
          Cost model + decision (SubQ Snap: P(net-positive)) → memo (Markdown) · Atlas · ASI:One cards
```

Design choices that keep the results honest:

- **MongoDB is the oracle.** A probe counts only if it runs cleanly on MongoDB and predicts its own result there. A probe that fails is sent back to the model with MongoDB's actual behavior, up to twice, and then excluded. It is never blamed on the target.
- **Probes are data, not code.** Muse writes declarative JSON steps (commands in Extended JSON, transactions, change streams, TTL waits). No model-written Python runs on the machine.
- **Behavior, not syntax.** An engine can accept a command and ignore it, so every probe reads back the effect.
- **Citations are checked.** Every file:line the model cites must contain the quoted token, or the finding is dropped.
- **Self-check.** MongoDB is diffed against itself before any result is trusted; every probe, read and write must match.
- **Same rule for every workload.** The decision thresholds in [assumptions.yaml](assumptions.yaml) are shared by ShopLog and the control app.
- **No invented numbers.** If SubQ or Muse is unavailable, the memo says so instead of filling a value.

## Run it

Requirements: Docker, Python 3.11 or newer (`run_demo.sh` creates a 3.11 venv; the committed results were produced with 3.12), [uv](https://docs.astral.sh/uv/), Node 22+ (for the MongoDB MCP Server).

```bash
cp .env.example .env     # add META_API_KEY (or MODEL_API_KEY), SUBQ_API_KEY, optional Atlas values
./run_demo.sh --profile shoplog --with-self-check
./run_demo.sh --profile lite
```

`run_demo.sh` starts both engines, seeds them identically (50,000 synthetic orders), and runs the pipeline end to end. The memo lands in `out/<profile>/memo.md`. Add `--refresh-probes` to regenerate probes with Muse; otherwise cached probes in `probes/` keep runs repeatable. `--with-mutation-test` runs the planted-gap test. If the engine containers were started from another checkout, set `COMPOSE_PROJECT_NAME` to that project's name.

Regenerate the evidence: `python scripts/seed.py`, then `python scripts/workload.py --profile shoplog` (profiler on, exports `evidence/query_log.jsonl`).

Cutover check on its own: `python -m archaeologist.cutover --scenario clean` (or `drill`, or add `--self-check` to migrate MongoDB to MongoDB). Upgrade mode: `docker compose --profile upgrade up -d`, then `./run_demo.sh --with-upgrade`.

Atlas evidence (optional, M10+): `python scripts/atlas_evidence.py run`, then, a few minutes later, `python scripts/atlas_evidence.py collect`. Atlas stores results and serves as an evidence source; it is never part of the latency comparison.

Tests: `.venv/bin/python -m pytest tests`.

## ASI:One agent (Fetch.ai)

- **Name:** `compat-archaeologist`
- **Address:** `agent1q2nccakc9zqesdcyyhk2r8xe4usukgvkpvn58r97gq6neyta0c6rjc6jus3`
- Implements the Agent Chat Protocol and runs over an Agentverse mailbox. Asking it to assess a migration runs the real pipeline and answers with ASI:One Interactive Cards: the decision, what breaks, the ledger, and latency with query plans.
- Run: `.venv/bin/python -m fetch_agent.setup_seed` once, then `.venv/bin/python -m fetch_agent.agent`. Open the Inspector link it prints and connect a mailbox.

## Built today (2026-11-07)

Everything in this repository was written today, after 10:45 AM PT, during the AIE Code Summit hackathon:

- ShopLog, the fictional legacy app, with its synthetic data generator, workload and business rules (`shoplog/`, `scripts/`, `docs/RULES.md`)
- Evidence extractors: invariant mining over data, profiler log normalization, code analysis with citation checks, Atlas evidence via MCP (`archaeologist/extract_*.py`)
- The Usage-Spec Ledger and its confidence rules (`archaeologist/ledger.py`)
- The declarative probe language, its runner, the MongoDB-as-oracle gate, and the probe generator (`archaeologist/probe_runner.py`, `archaeologist/probes.py`)
- Normalization and the differential runner, workload replay, and explain() comparison (`archaeologist/normalize.py`, `diff.py`, `replay.py`, `explain_compare.py`)
- The cutover fidelity check, with its full-load + CDC copier, fault injection and independent verifier (`archaeologist/cutover.py`)
- Upgrade mode, comparing two target versions (`archaeologist/upgrade.py`)
- The CTO brief: top risks, path latency and timeouts, reversibility, production-size projection (`archaeologist/brief.py`)
- Business-question replay and the blind scoreboard for hidden rules (`archaeologist/business_questions.py`, `archaeologist/scoreboard.py`)
- The edge-case fuzz, with model-designed cases, MongoDB as referee and exact BSON type comparison (`archaeologist/edge_cases.py`)
- A second fictional legacy app, **International Racking**, built by teammate Lev0gan: a deterministic data generator, an evidence pack, a checker and an answer key (`datasets/international_racking/`, `tools/international_racking/`, `docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md`). It's optional and not yet wired into the pipeline. The generator was first written with Meta Muse (muse-wonder-1.0).
- The DocumentDB documentation mapping, the cost model, the memo writer, Atlas storage and the pipeline (`archaeologist/`)
- The ASI:One agent and its cards (`fetch_agent/`), plus tests (`tests/`)

## Used, not built

- **Databases:** [MongoDB](https://www.mongodb.com/) 8.0 (Docker image `mongo:8.0`), [MongoDB Atlas](https://www.mongodb.com/atlas), [Atlas Search](https://www.mongodb.com/docs/atlas/atlas-search/), [FerretDB](https://www.ferretdb.com/) 2.7 (Apache-2.0, eval image), Docker
- **Tools:** the official [MongoDB MCP Server](https://github.com/mongodb-js/mongodb-mcp-server) (`mongodb-mcp-server@3.2.2`)
- **Models:** [Meta Model API](https://dev.meta.ai/docs/quickstart) (Muse Spark 1.3) for code evidence, probe generation and rewrite estimates. [SubQ Snap 1.1](https://docs.subq.ai/endpoints/decisions-api/) for confidence and decision probabilities. SubQ Preview (`subq-preview`) for edge-case design.
- **Fetch.ai:** [uAgents](https://uagents.fetch.ai/), Agentverse, ASI:One
- **Python libraries:** pymongo, openai, mcp, uagents, requests, PyYAML, rich, python-dotenv, pytest

## Sources

Vendor facts are summarized in our own words, with links:

- AWS DocumentDB: [supported APIs](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html), [functional differences](https://docs.aws.amazon.com/documentdb/latest/developerguide/functional-differences.html), and pages for [$lookup](https://docs.aws.amazon.com/documentdb/latest/developerguide/lookup.html), [$setWindowFields](https://docs.aws.amazon.com/documentdb/latest/developerguide/setWindowFields.html), [text search](https://docs.aws.amazon.com/documentdb/latest/developerguide/text-search.html), [transactions](https://docs.aws.amazon.com/documentdb/latest/developerguide/transactions.html) and [change streams](https://docs.aws.amazon.com/documentdb/latest/developerguide/change_streams.html)
- FerretDB: [compatibility](https://docs.ferretdb.io/migration/compatibility/), [evaluation image](https://docs.ferretdb.io/installation/evaluation/)
- MongoDB: [database profiler](https://www.mongodb.com/docs/manual/reference/database-profiler/), [Atlas pricing](https://www.mongodb.com/pricing), [Atlas cluster costs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/), [Performance Advisor](https://www.mongodb.com/docs/atlas/performance-advisor/)
- Prices: [AWS Price List for DocumentDB](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDocDB/current/us-east-1/index.json) and [OpenSearch](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonES/current/us-east-1/index.json), [EC2 r6g.large](https://instances.vantage.sh/aws/ec2/r6g.large). All prices are on-demand list prices, read on 2026-11-07.

## Limits, stated plainly

- ShopLog and its data are fictional and synthetic. Latency is measured locally on a mid-size dataset (50,000 orders, about 38 MB), with both engines in Docker on one machine and the same limits, so it's for comparison, not a production forecast. Projections to TB scale come from each query plan's growth shape and are labeled INFERRED.
- The cutover drill's faults are injected on purpose to test the verifier. The clean run injects nothing.
- Rewrite hours are model estimates, and incident risk inputs are assumptions. Both are labeled in the memo, with best and worst cases.
- MongoDB's public sources disagree on whether Atlas list prices are per cluster or per node. The memo shows both readings.
- The decision thresholds were committed before any model-generated probe ran, but after the author had seen results from handwritten test probes.
