# Migration decision: ShopLog Lite

*One-page brief for the CTO or VP Engineering. The evidence and method follow in the appendix.*

**The migration is fine for ShopLog Lite.** FerretDB matched MongoDB on everything this app does, and moving saves $8,604 over 12 months in the base case.

| | FerretDB (observed) | AWS DocumentDB (inferred) |
|---|---|---|
| Recommendation | Safe to migrate, and it pays off | Not verified: test on a real cluster before deciding |
| Confidence | High (measured; self-check clean) | Low: documentation only, nothing measured |
| Net over 12 months (base, best to worst) | $8,604 ($16,284 to -$7,716) | $61 ($1,981 to -$4,739) |
| Reversibility (100 = easy to go back) | 100/100 (OBSERVED) | 100/100 (INFERRED) |

**Top risks**

None blocking. Every probe, replayed operation and cutover check matched.

**12-month cost, base case** (yearly figures; best and worst cases in the appendix)

| | FerretDB | AWS DocumentDB |
|---|---|---|
| Staying on MongoDB Atlas | $36,442 | $36,442 |
| Target infrastructure | $11,518 | $31,581 |
| I/O billing | $0 | $0 (I/O-Optimized plan) |
| Scale-ups for slow paths (INFERRED) | $0 | $0 |
| Extra services (search) | $0 | $0 |
| Running it yourself | $11,520 | $0 |
| Migration and rewrites | $4,800 (5 person-days) | $4,800 (5 person-days) |
| Expected incident cost | $0 | $0 |
| **Net vs staying** | **$8,604** | **$61** |

**Measured on replay** (mid-size synthetic dataset, 1 MB; both engines local, same limits)

| Path | MongoDB p95 | FerretDB p95 | FerretDB over the timeout | Timeout used |
|---|---|---|---|---|
| Online | 0.73 ms | 1.76 ms | 0% of 120 | 1,000 ms |
| Reporting | 1.01 ms | 1.22 ms | 0% of 30 | 30,000 ms |
| Event-driven (change streams) | works | ⚪ Not tested | | |

**At production size (INFERRED)**: the data size at which each slow path reaches its timeout, projected from the measured 1.0 MB using the growth shape of its query plan. This assumes every collection grows by the same factor.
- F10 online query on orders (1 s timeout): MongoDB grows linear (index lookups) and reaches it at ~800 MB; FerretDB grows linear and reaches it at ~430 MB.
-  reporting query on orders (30 s timeout): MongoDB grows linear (index lookups) and reaches it at ~29 GB; FerretDB grows linear and reaches it at ~24 GB.
- At TB scale, both engines need work on reporting (for example pre-aggregation). FerretDB reaches the limit orders of magnitude sooner.

**Cutover fidelity**: a simulated full-load + CDC migration with nothing injected failed 0 of 5 checks on FerretDB.
In a drill with faults injected on purpose (a CDC gap and a dropped unique index), the verifier caught 1 of 1.

Reversibility deductions: FerretDB: none; AWS DocumentDB: none.

---

# Appendix: evidence and method

## Decision detail

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

## Cutover fidelity

A copier that works like a managed migration tool does a full load with indexes and validators, keeps the source taking live writes, then applies change events. A separate verifier reconciles: counts and content hashes, index and validator parity, the source oplog against the applied events, business rules, and day-one writes that MongoDB's constraints refuse. The same copier, MongoDB to MongoDB, passes every check.

| Check | Clean migration | Drill (faults injected) |
|---|---|---|
| orders: documents and content | ✅ | ❌ |
| orders.customer_id_1 index (plain) | ✅ | ✅ |
| orders.status_1 index (plain) | ✅ | ✅ |
| Change events applied vs writes in the source oplog | ✅ | ❌ |
| Business rule: orders with a negative total | ✅ | ✅ |
- Injected: CDC gap: 30 change events dropped mid-cutover. Caught: yes (30 of 30 affected documents found out of sync; oplog count check failed).

## Latency

Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB 0.45 ms / 0.77 ms, FerretDB 0.70 ms / 1.54 ms.
Writes (70 replayed) p50/p95: MongoDB 1.22 ms / 2.01 ms, FerretDB 0.70 ms / 1.05 ms. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.

| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |
|---|---|---|---|---|---|
| F10 | orders: find | 40/50 | 0.48 ms / 1.22 ms | 1.32 ms / 2.29 ms | 2.8× |
| other | orders: $match → $group | 30/30 | 0.64 ms / 1.01 ms | 0.78 ms / 1.22 ms | 1.2× |
| F10 | orders: find | 40/300 | 0.40 ms / 0.73 ms | 0.53 ms / 0.90 ms | 1.3× |
| F10 | orders: find | 40/150 | 0.41 ms / 0.61 ms | 0.49 ms / 0.76 ms | 1.2× |

## Why the slow paths are slow

Plans from the MongoDB MCP Server's `explain` tool (`mongodb-mcp-server@3.2.2 explain`), run read-only against each engine on the slowest replayed queries.

**F10 on orders** (replay p95 1.22 ms on MongoDB, 2.29 ms on FerretDB)
- MongoDB: `IXSCAN(_id_) → FETCH → LIMIT`, 94 documents examined.
- FerretDB (Postgres plan): `Limit → Sort → Bitmap Heap Scan → Bitmap Index Scan(status_1)`, with 0 sequential scan(s) and 0 nested loop(s).

**other on orders** (replay p95 1.01 ms on MongoDB, 1.22 ms on FerretDB)
- MongoDB: `COUNT_SCAN(status_1) → GROUP`, 0 documents examined.
- FerretDB (Postgres plan): `Subquery Scan → Aggregate → Bitmap Heap Scan → Bitmap Index Scan(status_1)`, with 0 sequential scan(s) and 0 nested loop(s).

**F10 on orders** (replay p95 0.73 ms on MongoDB, 0.90 ms on FerretDB)
- MongoDB: `EXPRESS_IXSCAN(_id_)`, 1 documents examined.
- FerretDB (Postgres plan): `Index Scan(_id_)`, with 0 sequential scan(s) and 0 nested loop(s).


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

Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `20261107T212527Z`
