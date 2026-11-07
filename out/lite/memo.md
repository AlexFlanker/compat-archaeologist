# Migration decision: ShopLog Lite

**The migration is fine for ShopLog Lite.** FerretDB matched MongoDB on everything this app does, and moving saves $8,604 over 12 months in the base case.

| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |
|---|---|---|---|---|---|
| FerretDB | OBSERVED | 0 | $8,604 | Safe to migrate, and it pays off | unavailable (SUBQ_API_KEY is not set) |
| AWS DocumentDB | INFERRED | 0 | $61 | Not verified: test on a real cluster before deciding | unavailable (SUBQ_API_KEY is not set) |

The decision comes from measured gaps, list prices and stated assumptions. The SubQ probability is a separate second opinion on the same facts.

Tested: MongoDB 8.0.34 and FerretDB v2.7.0 (which reports itself as MongoDB 7.0.77). This app and its data are fictional and synthetic.

## Feature by feature

| # | What the app depends on | Reconstructed from | Status | Confidence | FerretDB (OBSERVED) | AWS DocumentDB 8.0.2 (INFERRED from docs) |
|---|---|---|---|---|---|---|
| F10 | Plain CRUD by _id and simple filters | data + query log (610 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>1/1 probes equal<br>190/190 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html)<br>Since All versions |

## What differed on FerretDB

Nothing. Every probe and replayed operation matched.

## Latency

Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB 0.45 ms / 1.07 ms, FerretDB 0.75 ms / 1.75 ms.
Writes (70 replayed) p50/p95: MongoDB 1.27 ms / 1.71 ms, FerretDB 0.62 ms / 0.87 ms. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.

| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |
|---|---|---|---|---|---|
| F10 | orders: find | 40/150 | 0.37 ms / 1.52 ms | 0.51 ms / 2.20 ms | 1.4× |
| F10 | orders: find | 40/50 | 0.39 ms / 1.03 ms | 1.10 ms / 1.97 ms | 2.8× |
| other | orders: $match → $group | 30/30 | 0.60 ms / 0.70 ms | 0.76 ms / 1.39 ms | 1.3× |
| F10 | orders: find | 40/300 | 0.46 ms / 1.02 ms | 0.59 ms / 0.91 ms | 1.3× |

## Why the slow paths are slow

Plans from the MongoDB MCP Server's `explain` tool (`mongodb-mcp-server@3.2.2 explain`), run read-only against each engine on the slowest replayed queries.

**F10 on orders** (replay p95 1.03 ms on MongoDB, 1.97 ms on FerretDB)
- MongoDB: `IXSCAN(_id_) → FETCH → LIMIT`, 94 documents examined.
- FerretDB (Postgres plan): `Limit → Sort → Bitmap Heap Scan → Bitmap Index Scan(status_1)`, with 0 sequential scan(s) and 0 nested loop(s).

**other on orders** (replay p95 0.70 ms on MongoDB, 1.39 ms on FerretDB)
- MongoDB: `COUNT_SCAN(status_1) → GROUP`, 0 documents examined.
- FerretDB (Postgres plan): `Subquery Scan → Aggregate → Bitmap Heap Scan → Bitmap Index Scan(status_1)`, with 0 sequential scan(s) and 0 nested loop(s).

**F10 on orders** (replay p95 1.52 ms on MongoDB, 2.20 ms on FerretDB)
- MongoDB: `IXSCAN(customer_id_1) → FETCH → SORT`, 20 documents examined.
- FerretDB (Postgres plan): `Limit → Sort → Bitmap Heap Scan → Bitmap Index Scan(customer_id_1)`, with 0 sequential scan(s) and 0 nested loop(s).


## Cost over 12 months

| | FerretDB (self-managed, OBSERVED gaps) | AWS DocumentDB (INFERRED gaps) |
|---|---|---|
| Atlas today, per year | $36,442 | $36,442 |
| Target infrastructure, per year | $11,518 | $31,581 |
|   of which a separate search service | $0 | $0 |
| Infrastructure saved | $24,924 | $4,861 |
| Self-managed operations | -$11,520 | -$0 |
| Migration baseline (assumed hours) | 40 h | 40 h |
| Rewrites (Muse estimate, INFERRED) | 0 h | 0 h |
| Residual incident risk (assumed) | -$0 | -$0 |
| **Net over 12 months, base** | **$8,604** | **$61** |
| Net, best to worst case | $16,284 to -$7,716 | $1,981 to -$4,739 |
| Rewrite hours the savings can pay for | 72 h | 0 h |
| Payback | 4.3 months | 11.8 months |

**What would change the answer.** One-time rewrite costs don't grow with fleet size, but savings do.

| Scenario | FerretDB net (base) | FerretDB decision | DocumentDB net (base) | DocumentDB decision |
|---|---|---|---|---|
| 1 cluster(s) | -$1,449 | SAFE_BUT_NOT_WORTH_IT | -$3,585 | VERIFY_FIRST |
| 4 cluster(s) | $8,604 | GO | $61 | VERIFY_FIRST |
| 10 cluster(s) | $28,710 | GO | $7,353 | VERIFY_FIRST |
| 25 cluster(s) | $78,974 | GO | $25,582 | VERIFY_FIRST |
| Atlas billed per node (see below) | $81,487 | GO | $72,944 | VERIFY_FIRST |

**Check your Atlas invoice.** MongoDB's public sources disagree on whether the listed hourly price covers a whole default cluster or one node, and that changes the baseline threefold. The base case assumes the whole cluster, because the [pricing page](https://www.mongodb.com/pricing) says M10 starts at $56.94/month, which is its hourly rate with no multiplier. [The billing docs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/) describe the cost as applying to each data-bearing node. The last row shows that case.

List prices and every assumption are in [assumptions.yaml](../../assumptions.yaml), including 4 fictional clusters. DocumentDB uses the cheaper of its Standard and I/O-Optimized options, at equal RAM to Atlas M40 but half the vCPUs, which favors migrating.

## How far to trust this

- **Self-check:** MongoDB against itself gave 1/1 equal probes, 150/150 equal reads, and 70/70 equal writes with 0 final-state differences. So the differences above don't come from the comparison itself.
- **Probes:** 1 ran, **handwritten test fixtures, not generated by the model**. MongoDB is the oracle: a probe counts only if it ran cleanly on MongoDB and predicted its result. 0 probe(s) failed that check after retries and were excluded, not counted against the target.
- **Code evidence:** source is `static_scan_only`. Citations are checked by a program. 0 model citation(s) were dropped because the cited line didn't contain the quoted token.
- **Replay coverage:** the log has 640 entries. Not replayed: 40 insert: the profiler doesn't keep the documents. Reads are capped per operation shape.
- **Two different products:** FerretDB 2 runs on Postgres with an open-source DocumentDB extension. AWS DocumentDB is a separate engine with a similar name. FerretDB's results say nothing about AWS DocumentDB.
- **DocumentDB rows are INFERRED:** we read AWS's docs for engine 8.0.2 and compared them with this app's usage. Nothing ran there. Set `TARGET_URI` to a real cluster to replace them with observed results.
- **Fair by construction:** the same pipeline, thresholds and assumptions run on every workload. ShopLog was deliberately built to depend on MongoDB-specific features, and the control app wasn't.
- **Not modeled:** data transfer, backups, support plans and committed-use discounts. Rewrite hours are model estimates, and the risk inputs are assumptions.

Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `20261107T205444Z`
