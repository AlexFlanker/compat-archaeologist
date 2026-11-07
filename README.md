# Compat Archaeologist

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3) ![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Before you migrate a database, get the archaeology done.**

A team moves from MongoDB Atlas to a "MongoDB-compatible" service to save money. On paper it's compatible and cheaper. In production, queries get rewritten, aggregations slow down, features go missing, and the total cost ends up higher. Nobody had a precise, evidence-backed spec of how the app *actually* used MongoDB.

Compat Archaeologist is an agent pipeline that writes that spec and then tests it:

1. **Reconstructs the real MongoDB usage spec** of an application from three kinds of evidence: data samples (and the catalog), the profiler query log, and the application code. Atlas Performance Advisor adds a fourth source when an Atlas cluster is available. Each item is **OBSERVED** (seen at runtime or in the catalog) or **INFERRED** (code only), with a confidence score.
2. **Proves behavioral equivalence, or finds the gaps.** It generates probes for every ledger item and replays the recorded workload on MongoDB and on a compatible target, side by side: result diffs, errors and p50/p95 latency, plus query plans for the slowest paths.
3. **Produces a decision memo** that weighs infrastructure cost, rewrite effort and incident risk over 12 months, with a recommendation. Every claim links to its evidence.

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

**Fairness check.** The same pipeline, thresholds and assumptions run on a control app, ShopLog Lite, which uses only plain CRUD. Its verdict: *the migration is fine*, with FerretDB matching MongoDB on everything it does.

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
- The DocumentDB documentation mapping, the cost model, the memo writer, Atlas storage and the pipeline (`archaeologist/`)
- The ASI:One agent and its cards (`fetch_agent/`), plus tests (`tests/`)

## Used, not built

- **Databases:** [MongoDB](https://www.mongodb.com/) 8.0 (Docker image `mongo:8.0`), [MongoDB Atlas](https://www.mongodb.com/atlas), [Atlas Search](https://www.mongodb.com/docs/atlas/atlas-search/), [FerretDB](https://www.ferretdb.com/) 2.7 (Apache-2.0, eval image), Docker
- **Tools:** the official [MongoDB MCP Server](https://github.com/mongodb-js/mongodb-mcp-server) (`mongodb-mcp-server@3.2.2`)
- **Models:** [Meta Model API](https://dev.meta.ai/docs/quickstart) (Muse Spark 1.3) for code evidence, probe generation and rewrite estimates. [SubQ Snap 1.1](https://docs.subq.ai/endpoints/decisions-api/) for confidence and decision probabilities.
- **Fetch.ai:** [uAgents](https://uagents.fetch.ai/), Agentverse, ASI:One
- **Python libraries:** pymongo, openai, mcp, uagents, requests, PyYAML, rich, python-dotenv, pytest

## Sources

Vendor facts are summarized in our own words, with links:

- AWS DocumentDB: [supported APIs](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html), [functional differences](https://docs.aws.amazon.com/documentdb/latest/developerguide/functional-differences.html), and pages for [$lookup](https://docs.aws.amazon.com/documentdb/latest/developerguide/lookup.html), [$setWindowFields](https://docs.aws.amazon.com/documentdb/latest/developerguide/setWindowFields.html), [text search](https://docs.aws.amazon.com/documentdb/latest/developerguide/text-search.html), [transactions](https://docs.aws.amazon.com/documentdb/latest/developerguide/transactions.html) and [change streams](https://docs.aws.amazon.com/documentdb/latest/developerguide/change_streams.html)
- FerretDB: [compatibility](https://docs.ferretdb.io/migration/compatibility/), [evaluation image](https://docs.ferretdb.io/installation/evaluation/)
- MongoDB: [database profiler](https://www.mongodb.com/docs/manual/reference/database-profiler/), [Atlas pricing](https://www.mongodb.com/pricing), [Atlas cluster costs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/), [Performance Advisor](https://www.mongodb.com/docs/atlas/performance-advisor/)
- Prices: [AWS Price List for DocumentDB](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonDocDB/current/us-east-1/index.json) and [OpenSearch](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonES/current/us-east-1/index.json), [EC2 r6g.large](https://instances.vantage.sh/aws/ec2/r6g.large). All prices are on-demand list prices, read on 2026-11-07.

## Limits, stated plainly

- ShopLog and its data are fictional and synthetic. Latency is measured locally, both engines in Docker on one machine with the same limits, so it's for comparison, not a production forecast.
- Rewrite hours are model estimates, and incident risk inputs are assumptions. Both are labeled in the memo, with best and worst cases.
- MongoDB's public sources disagree on whether Atlas list prices are per cluster or per node. The memo shows both readings.
- The decision thresholds were committed before any model-generated probe ran, but after the author had seen results from handwritten test probes.
