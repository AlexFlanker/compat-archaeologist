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

Observed on FerretDB 2.7.0, each also checked with plain pymongo outside the pipeline:

| Feature | MongoDB 8.0 | FerretDB 2.7 |
|---|---|---|
| Write inside a transaction, read from outside before commit | Not visible | **Visible immediately** |
| `abort_transaction()` | Rolls back | **No error, and no rollback** (the driver ignores the abort error) |
| `commit_transaction()` | Commits | **Error 59, yet the write has already happened** |
| Insert that violates a `$jsonSchema` validator | Refused (121) | **Accepted; the validator isn't kept** |
| Case-insensitive collation index | Works | Refused (115), and case-variant logins find nobody |
| Change streams | Work | Not supported (115) |
| Revenue report (`$lookup` sub-pipeline + `$facet`) | ~7 ms | **~4,000 ms**: the Postgres plan does a sequential scan inside a nested loop |
| `$setWindowFields`, `arrayFilters`, plain CRUD, `$text` matching | | Same results. `$text` ranks differently; writes are faster on FerretDB |

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

**Probes.** Meta Muse wrote 30 behavioral probes for ShopLog. Muse predicted MongoDB's behavior wrongly 9 times; each time MongoDB, acting as the oracle, rejected the probe and Muse repaired it. No probe had to be excluded. MongoDB against itself matched on all 30.

**Second opinion.** SubQ Snap's probability that migrating is net-positive within 12 months, given the same facts: ShopLog to FerretDB **0.0001**, control app to FerretDB **0.94**.

**AWS DocumentDB.** Engine 8.0.2's documentation lists support for most of these features. The memo flags only where ShopLog's actual usage conflicts with documented limits (`$shift` in window functions, `$text` exclusion terms and array fields, Atlas Search) and marks the target as **not verified**. FerretDB results say nothing about DocumentDB: they are different engines with similar names.

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

Requirements: Docker, Python 3.11, [uv](https://docs.astral.sh/uv/), Node 22+ (for the MongoDB MCP Server).

```bash
cp .env.example .env     # add META_API_KEY (or MODEL_API_KEY), SUBQ_API_KEY, optional Atlas values
./run_demo.sh --profile shoplog --with-self-check
./run_demo.sh --profile lite
```

`run_demo.sh` starts both engines, seeds them identically (50,000 synthetic orders), and runs the pipeline end to end. The memo lands in `out/<profile>/memo.md`. Add `--refresh-probes` to regenerate probes with Muse; otherwise cached probes in `probes/` keep runs repeatable.

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
