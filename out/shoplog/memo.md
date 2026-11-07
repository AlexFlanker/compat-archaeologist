# Migration decision: ShopLog

*One-page brief for the CTO or VP Engineering. The evidence and method follow in the appendix.*

**Stay on MongoDB for ShopLog for now.** On FerretDB, 6 blocking gaps showed up (F4, F5, F6, F8, F11). AWS DocumentDB documents support for most features but conflicts with this app's usage in 3 place(s), and nothing about it has been tested. Run these probes on a real cluster before considering it.

| | FerretDB (observed) | AWS DocumentDB (inferred) |
|---|---|---|
| Recommendation | Stay for now: blocking gaps exist, and the rewrite isn't priced yet | Not verified: test on a real cluster before deciding |
| Confidence | High (measured; self-check clean) | Low: documentation only, nothing measured |
| Net over 12 months (base, best to worst) | -$38,343 (-$5,404 to -$128,671) | -$16,377 (-$11,457 to -$31,677) |
| Reversibility (100 = easy to go back) | 15/100 (OBSERVED) | 75/100 (INFERRED) |

**Top risks**

1. **Checkout isn't atomic: stock can be reserved without an order, and a rollback does nothing** (FerretDB, silent). Evidence: probe `T.txn_abort`; code [shoplog/checkout.py:57](../../shoplog/checkout.py#L57).
2. **Logins and email uniqueness stop ignoring case: duplicate accounts, failed sign-ins** (FerretDB, silent). Evidence: probe `T.collation_unique`; 17/40 replayed ops differ; code [shoplog/customers.py:9](../../shoplog/customers.py#L9), [shoplog/schema.py:9](../../shoplog/schema.py#L9).
3. **Invalid orders get written: the schema validator isn't enforced** (FerretDB, silent). Evidence: probe `T.validator`; code [shoplog/schema.py:12](../../shoplog/schema.py#L12), [shoplog/schema.py:42](../../shoplog/schema.py#L42).
4. **Constraints don't survive a clean cutover: customers.email_ci_unique index (unique, collation), orders $jsonSchema validator** (FerretDB, cutover). Evidence: cutover check, clean run: 6 of 23 checks failed.
5. **Queries on orders run 526× slower ($lookup sub-pipeline + $facet)** (FerretDB, performance). Evidence: replay p95 7.85 ms → 4126.54 ms; code [shoplog/analytics.py:21](../../shoplog/analytics.py#L21), [shoplog/analytics.py:33](../../shoplog/analytics.py#L33).

**12-month cost, base case** (yearly figures; best and worst cases in the appendix)

| | FerretDB | AWS DocumentDB |
|---|---|---|
| Staying on MongoDB Atlas | $36,442 | $36,442 |
| Target infrastructure | $11,518 | $31,581 |
| I/O billing | $0 | $0 (I/O-Optimized plan) |
| Scale-ups for slow paths (INFERRED) | $5,759 | $0 |
| Extra services (search) | $11,938 | $11,938 |
| Running it yourself | $11,520 | $0 |
| Migration and rewrites | $4,800 (5 person-days); rewrites not estimated yet | $4,800 (5 person-days); rewrites not estimated yet |
| Expected incident cost | $29,250 | $4,500 |
| **Net vs staying** | **-$38,343** | **-$16,377** |

**Measured on replay** (synthetic dataset, 38 MB; both engines local, same limits)

| Path | MongoDB p95 | FerretDB p95 | FerretDB over the timeout | Timeout used |
|---|---|---|---|---|
| Online | 1.19 ms | 3.59 ms | 0% of 367 | 1,000 ms |
| Reporting | 8.03 ms | 4,127 ms | 0% of 17 | 30,000 ms |
| Event-driven (change streams) | works | ❌ Not supported | | |

**At production size (INFERRED)**: the data size at which each slow path reaches its timeout, projected from the measured 38.4 MB using the growth shape of its query plan. This assumes every collection grows by the same factor.
- F1 reporting query on orders (30 s timeout): MongoDB grows linear (index lookups) and reaches it at ~147 GB; FerretDB grows quadratic (a sequential scan inside a nested loop) and reaches it at ~100 MB.
- At TB scale both engines need work on reporting, such as pre-aggregation, but FerretDB hits the limit orders of magnitude sooner.

**Cutover fidelity**: a simulated full-load + CDC migration with nothing injected failed 6 of 23 checks on FerretDB: customers.email_ci_unique index (unique, collation), orders $jsonSchema validator, Business rule: emails that collide when case is ignored, Business rule: orders that break the order schema.
In a drill with faults injected on purpose (CDC gap: 30 change events dropped mid-cutover; Copier skipped unique index coupon_once_per_customer), the verifier caught 2 of 2.

Reversibility deductions: FerretDB: -30 No change streams on the target: moving back needs a full reload with downtime, -25 The target accepts writes MongoDB refuses, so that data needs cleaning before moving back, -20 5 code rewrite(s) make the two versions of the app diverge, -10 A separate search service would need to be retired or kept; AWS DocumentDB: -5 Documented limit: $setWindowFields, -5 Documented limit: $text search, -5 Documented limit: Atlas Search ($search with a search index), -10 A separate search service would need to be retired or kept.

---

# Appendix: evidence and method

## Decision detail

| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |
|---|---|---|---|---|---|
| FerretDB | OBSERVED | 6 | -$38,343 | Stay for now: blocking gaps exist, and the rewrite isn't priced yet | unavailable (SUBQ_API_KEY is not set) |
| AWS DocumentDB | INFERRED | 3 | -$16,377 | Not verified: test on a real cluster before deciding | unavailable (SUBQ_API_KEY is not set) |

The decision comes from measured gaps, list prices and stated assumptions. The SubQ probability is a separate second opinion on the same facts.

Tested: MongoDB 8.0.34 and FerretDB v2.7.0 (which reports itself as MongoDB 7.0.77). This app and its data are fictional and synthetic.

## Feature by feature

| # | What the app depends on | Reconstructed from | Status | Confidence | FerretDB (OBSERVED) | AWS DocumentDB 8.0.2 (INFERRED from docs) |
|---|---|---|---|---|---|---|
| F1 | $lookup sub-pipeline + $facet | query log (30 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>17/17 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/lookup.html)<br>Since Sub-pipeline correlated $lookup and $facet: 8.0.2 |
| F2 | $setWindowFields | query log (60 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>1/1 probes equal<br>40/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/setWindowFields.html)<br>The app uses $shift, which the docs list as unsupported |
| F3 | arrayFilters / positional $[<id>] updates | data + query log (56 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>1/1 probes equal<br>56/56 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/dollarIdentifier-update.html)<br>Since All versions |
| F4 | Multi-document transactions | query log (60 ops) + code | OBSERVED | 0.90 | ❌ Different result<br>0/1 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/transactions.html)<br>Since 4.0 |
| F5 | Change streams | query log (57 ops) + code | OBSERVED | 0.90 | ❌ Not supported<br>0/1 probes equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/change_streams.html)<br>Since All supported versions |
| F6 | Case-insensitive collation index | data + query log (110 ops) + code | OBSERVED | 0.97 | ❌ Not supported<br>0/1 probes equal<br>23/40 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/devguide/collation.html)<br>Since 8.0 |
| F7 | Partial index + TTL index | data + query log (70 ops) + code | OBSERVED | 0.97 | ⚪ Not tested | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/partial-index.html)<br>Since Partial index 5.0; TTL all versions |
| F8 | $jsonSchema validator | data + query log (5 ops) + code | OBSERVED | 0.97 | ❌ Different result<br>0/1 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/json-schema-validation.html)<br>Since 4.0 |
| F9 | $text search | data + query log (100 ops) + code | OBSERVED | 0.97 | 🟡 Same matches, different ranking<br>1/1 probes equal<br>0/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/text-search.html)<br>The app searches with exclusion terms (for example -refurbished), which the docs say aren't supported<br>The app's text index covers an array field, which the docs say isn't supported |
| F10 | Plain CRUD by _id and simple filters | data + query log (1,034 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>1/1 probes equal<br>504/504 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html)<br>Since All versions |
| F11 | Atlas Search ($search with a search index) | code | INFERRED | unknown | 🟠 INFERRED: [not supported per FerretDB docs](https://docs.ferretdb.io/migration/compatibility/)<br>The compatibility page lists no $search stage or search index commands. Locally, FerretDB also refused $listSearchIndexes (code 40324). | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/search.html)<br>The app uses Atlas Search, which DocumentDB doesn't provide; a separate search service is needed |
| X:graphLookup | graphLookup | code | INFERRED | unknown | ⚪ Not tested | ⚪ Not covered in our review |

## What differed on FerretDB

- **T.txn_abort** (❌ Different result): Aborted transaction leaves no trace. At step `read`, MongoDB gave `10` and FerretDB gave `7` (at `$.docs[0].n`).
- **T.collation_unique** (❌ Not supported): Case-insensitive unique index rejects a case variant. Step `index` failed with code 115: "Error in specification { "key" : { "email" : 1 }, "name" : "ci", "unique" : true, "collation" : { "locale" : "en", "strength" : 2 } } :: caused by :: createIndex.collatio".
- **T.validator** (❌ Different result): $jsonSchema validator rejects a bad document. At step `bad`, MongoDB gave `121` and FerretDB gave `<absent>` (at `$.error_code`).
- **T.change_stream** (❌ Not supported): A change stream delivers an update with the full document. Step `open` failed with code 115: "Stage $changeStream is not supported yet in native pipeline, full error: {'ok': 0.0, 'errmsg': 'Stage $changeStream is not supported yet in native pipeline', 'code': 115,".
- **Schema setup `customers.email_ci`** was refused with code 115: "Error in specification { "unique" : true, "name" : "email_ci_unique", "key" : { "email" : 1 }, "collation" : { "locale" : "en", "strength" : 2 } } :: caused by :: createI".
- **Replay `Sb98d4c91`** (F6, customers): 17 of 40 replayed operations differed (17 × different number of documents).
- **Replay `Se780f2b8`** (F9, products): 40 of 40 replayed operations differed (40 × same matches, ranked differently).

FerretDB's own [compatibility page](https://docs.ferretdb.io/migration/compatibility/) says `commitTransaction` and `abortTransaction` aren't implemented yet, so that gap is documented rather than hidden. The risk for an application is what happens at runtime: pymongo follows the driver specification and ignores errors from abort, so nothing tells the application its rollback did nothing.

## Cutover fidelity

A copier that works like a managed migration tool does a full load with indexes and validators, keeps the source taking live writes, then applies change events. A separate verifier reconciles: counts and content hashes, index and validator parity, the source oplog against the applied events, business rules, and day-one writes that MongoDB's constraints refuse. The same copier, MongoDB to MongoDB, passes every check.

| Check | Clean migration | Drill (faults injected) |
|---|---|---|
| carts: documents and content | ✅ | ❌ |
| carts.active_cart_expiry index (partialFilterExpression, expireAfterSeconds) | ✅ | ✅ |
| carts.customer_id_1 index (plain) | ✅ | ✅ |
| customers: documents and content | ✅ | ❌ |
| customers.email_ci_unique index (unique, collation) | ❌ {"source": {"key": {"email": 1}, "unique": true, "collation": {"locale": "en", "caseLevel": false, "caseFirst": "off", " | ❌ |
| inventory: documents and content | ✅ | ❌ |
| orders: documents and content | ✅ | ❌ |
| orders.customer_id_1_placed_at_-1 index (plain) | ✅ | ✅ |
| orders.status_1_placed_at_-1 index (plain) | ✅ | ✅ |
| orders.placed_at_1 index (plain) | ✅ | ✅ |
| orders.coupon_once_per_customer index (unique, partialFilterExpression) | ✅ | ❌ |
| orders $jsonSchema validator | ❌ {"source_has_validator": true, "target_has_validator": false} | ❌ |
| products: documents and content | ✅ | ✅ |
| products.product_search index (weights) | ✅ | ✅ |
| products.category_1 index (plain) | ✅ | ✅ |
| Change events applied vs writes in the source oplog | ✅ | ❌ |
| Business rule: customers who used the welcome coupon twice | ✅ | ❌ |
| Business rule: emails that collide when case is ignored | ❌ {"source": 0, "target_at_cutover": 0, "target_after_day_one_writes": 1} | ❌ |
| Business rule: orders whose refund total contradicts their status | ✅ | ✅ |
| Business rule: orders that break the order schema | ❌ {"source": 0, "target_at_cutover": 0, "target_after_day_one_writes": 1} | ❌ |
| Day-one write refused: Second use of the welcome coupon by the same customer | ✅ | ❌ |
| Day-one write refused: Sign-up with an existing email in different case | ❌ {"source": "refused", "target": "accepted"} | ❌ |
| Day-one write refused: Order with an unknown status and a negative total | ❌ {"source": "refused", "target": "accepted"} | ❌ |
- Injected: CDC gap: 30 change events dropped mid-cutover. Caught: yes (21 of 27 affected documents found out of sync; oplog count check failed).
- Injected: Copier skipped unique index coupon_once_per_customer. Caught: yes (orders.coupon_once_per_customer index (unique, partialFilterExpression)).

## Latency

Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB 0.41 ms / 7.08 ms, FerretDB 0.60 ms / 6.26 ms.
Writes (353 replayed) p50/p95: MongoDB 1.15 ms / 1.78 ms, FerretDB 0.58 ms / 0.90 ms. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.

| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |
|---|---|---|---|---|---|
| F1 | orders: $match → $addFields → $lookup → $unwind | 7/20 (time budget reached) | 6.90 ms / 7.85 ms | 3,970 ms / 4,127 ms | 575.4× |
| F10 | orders: find | 15/15 | 8.18 ms / 38.19 ms | 8.02 ms / 32.50 ms | 1.0× faster |
| F2 | orders: $match → $addFields → $setWindowFields → $project | 40/60 | 0.45 ms / 0.61 ms | 3.49 ms / 3.99 ms | 7.8× |
| F10 | orders: find | 1/1 | 1.50 ms / 1.50 ms | 3.23 ms / 3.23 ms | 2.2× |
| F1 | products: $lookup → $match → $group → $sort | 10/10 | 7.52 ms / 8.03 ms | 2.04 ms / 2.34 ms | 3.7× faster |
| F9 | products: find | 40/100 | 0.63 ms / 0.91 ms | 1.61 ms / 1.88 ms | 2.6× |
| F10 | orders: find | 40/40 | 0.67 ms / 1.19 ms | 0.84 ms / 1.41 ms | 1.3× |
| F6 | customers: find | 40/110 | 0.26 ms / 0.29 ms | 0.67 ms / 0.81 ms | 2.6× |
| other | orders: $match → $group | 40/46 | 0.35 ms / 0.60 ms | 0.44 ms / 0.64 ms | 1.3× |
| F10 | products: find | 30/30 | 0.46 ms / 0.63 ms | 0.57 ms / 0.64 ms | 1.2× |
| F10 | orders: find | 1/1 | 0.85 ms / 0.85 ms | 0.59 ms / 0.59 ms | 1.4× faster |
| F10 | orders: find | 40/100 | 0.36 ms / 0.44 ms | 0.47 ms / 0.55 ms | 1.3× |
| F10 | carts: find | 40/70 | 0.30 ms / 0.37 ms | 0.40 ms / 0.46 ms | 1.3× |
| F10 | orders: find | 40/270 | 0.28 ms / 0.33 ms | 0.36 ms / 0.42 ms | 1.3× |

## Why the slow paths are slow

Plans from the MongoDB MCP Server's `explain` tool (`mongodb-mcp-server@3.2.2 explain`), run read-only against each engine on the slowest replayed queries.

**F1 on orders** (replay p95 7.85 ms on MongoDB, 4,127 ms on FerretDB)
- MongoDB: `IXSCAN(status_1_placed_at_-1) → FETCH → PROJECTION_SIMPLE → $addFields → $lookup(indexes=['_id_'], collection scans=0, docs examined=309) → $facet`, 306 documents examined.
- FerretDB (Postgres plan): `Aggregate → ProjectSet → Subquery Scan → Nested Loop → Index Scan(status_1_placed_at_-1) → Aggregate → Seq Scan → Aggregate → Function Scan(bson_lookup_unwind) → Append → Aggregate → Sort → Subquery Scan → Aggregate → CTE Scan → Aggregate → Sort → Subquery Scan → Aggregate → CTE Scan → Aggregate → Limit → Sort → CTE Scan`, with 1 sequential scan(s) and 1 nested loop(s).

**F2 on orders** (replay p95 0.61 ms on MongoDB, 3.99 ms on FerretDB)
- MongoDB: `IXSCAN(customer_id_1_placed_at_-1) → FETCH → PROJECTION_SIMPLE → $addFields → $sort → $_internalSetWindowFields → $project`, 13 documents examined.
- FerretDB (Postgres plan): `Subquery Scan → WindowAgg → WindowAgg → WindowAgg → WindowAgg → Sort → Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan(status_1_placed_at_-1) → Bitmap Index Scan(customer_id_1_placed_at_-1)`, with 0 sequential scan(s) and 0 nested loop(s).

**F10 on orders** (replay p95 1.50 ms on MongoDB, 3.23 ms on FerretDB)
- MongoDB: `IXSCAN(_id_) → FETCH → PROJECTION_DEFAULT → LIMIT`, 179 documents examined.
- FerretDB (Postgres plan): `Limit → Sort → Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan(status_1_placed_at_-1) → Bitmap Index Scan(_id_)`, with 0 sequential scan(s) and 0 nested loop(s).


## What Atlas observed

No Atlas evidence was collected for this run. `scripts/atlas_evidence.py` seeds an Atlas cluster, runs the workload, and collects Performance Advisor output through the MongoDB MCP Server.

## Cost over 12 months

| | FerretDB (self-managed, OBSERVED gaps) | AWS DocumentDB (INFERRED gaps) |
|---|---|---|
| Atlas today, per year | $36,442 | $36,442 |
| Target infrastructure, per year | $29,214 | $43,518 |
|   of which a separate search service | $11,938 | $11,938 |
| Infrastructure saved | $7,227 | -$7,077 |
| Self-managed operations | -$11,520 | -$0 |
| Migration baseline (assumed hours) | 40 h | 40 h |
| Rewrites (Muse estimate, INFERRED) | not estimated | not estimated |
| Residual incident risk (assumed) | -$29,250 | -$4,500 |
| **Net over 12 months, base** | **-$38,343** | **-$16,377** |
| Net, best to worst case | -$5,404 to -$128,671 | -$11,457 to -$31,677 |
| Rewrite hours the savings can pay for | 0 h | 0 h |
| Payback | Never at these numbers | Never at these numbers |

**What would change the answer.** One-time rewrite costs don't grow with fleet size, but savings do.

| Scenario | FerretDB net (base) | FerretDB decision | DocumentDB net (base) | DocumentDB decision |
|---|---|---|---|---|
| 1 cluster(s) | -$35,123 | STAY_UNPRICED | -$11,069 | VERIFY_FIRST |
| 4 cluster(s) | -$38,343 | STAY_UNPRICED | -$16,377 | VERIFY_FIRST |
| 10 cluster(s) | -$44,781 | STAY_UNPRICED | -$26,991 | VERIFY_FIRST |
| 25 cluster(s) | -$60,878 | STAY_UNPRICED | -$53,528 | VERIFY_FIRST |
| Atlas billed per node (see below) | $34,541 | STAY_UNPRICED | $56,507 | VERIFY_FIRST |

**Check your Atlas invoice.** MongoDB's public sources disagree on whether the listed hourly price covers a whole default cluster or one node, and that changes the baseline threefold. The base case assumes the whole cluster, because the [pricing page](https://www.mongodb.com/pricing) says M10 starts at $56.94/month, which is its hourly rate with no multiplier. [The billing docs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/) describe the cost as applying to each data-bearing node. The last row shows that case.

List prices and every assumption are in [assumptions.yaml](../../assumptions.yaml), including 4 fictional clusters. DocumentDB uses the cheaper of its Standard and I/O-Optimized options, at equal RAM to Atlas M40 but half the vCPUs, which favors migrating.

## Reconstructed business behavior

Recovered without any documentation. 29 of 29 are OBSERVED in the data. The rest appear only in code, so they are INFERRED.

| ID | Behavior | Status | Confidence | Evidence |
|---|---|---|---|---|
| B01 | status=cancelled ⇒ refund_total = 0 [orders] | OBSERVED | unknown | INV-001 (n=1231) |
| B02 | status=cancelled ⇒ delivered_at is absent [orders] | OBSERVED | unknown | INV-002 (n=1231) |
| B03 | status=delivered ⇒ refund_total = 0 [orders] | OBSERVED | unknown | INV-003 (n=21681) |
| B04 | status=delivered ⇒ delivered_at is present [orders] | OBSERVED | unknown | INV-004 (n=21681) |
| B05 | status=paid ⇒ delivered_at is absent [orders] | OBSERVED | unknown | INV-005 (n=43) |
| B06 | status=paid ⇒ tax is present [orders] | OBSERVED | unknown | INV-006 (n=43) |
| B07 | status=partially_refunded ⇒ shipping = 0 [orders] | OBSERVED | unknown | INV-007 (n=646) |
| B08 | status=partially_refunded ⇒ refund_total > 0 [orders] | OBSERVED | unknown | INV-008 (n=646) |
| B09 | status=partially_refunded ⇒ delivered_at is present [orders] | OBSERVED | unknown | INV-009 (n=646) |
| B10 | status=placed ⇒ delivered_at is absent [orders] | OBSERVED | unknown | INV-010 (n=48) |
| B11 | status=placed ⇒ tax is present [orders] | OBSERVED | unknown | INV-011 (n=48) |
| B12 | status=refunded ⇒ refund_total > 0 [orders] | OBSERVED | unknown | INV-012 (n=1145) |
| B13 | status=refunded ⇒ delivered_at is present [orders] | OBSERVED | unknown | INV-013 (n=1145) |
| B14 | status=shipped ⇒ refund_total = 0 [orders] | OBSERVED | unknown | INV-014 (n=206) |
| B15 | status=shipped ⇒ delivered_at is absent [orders] | OBSERVED | unknown | INV-015 (n=206) |
| B16 | status=shipped ⇒ tax is present [orders] | OBSERVED | unknown | INV-016 (n=206) |
| B17 | customers.tier=gold ⇒ discount > 0 [orders] | OBSERVED | unknown | INV-017 (n=3004) |
| B18 | customers.tier=legacy_vip ⇒ discount > 0 [orders] | OBSERVED | unknown | INV-018 (n=681) |
| B19 | customers.tier=legacy_vip ⇒ coupon_code is absent [orders] | OBSERVED | unknown | INV-019 (n=681) |
| B20 | customers.tier=silver ⇒ discount > 0 [orders] | OBSERVED | unknown | INV-020 (n=6209) |
| B21 | subtotal is double before ~2018-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-021 (n=25000) |
| B22 | discount is double before ~2018-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-022 (n=25000) |
| B23 | shipping is double before ~2018-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-023 (n=25000) |
| B24 | total is double before ~2018-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-024 (n=25000) |
| B25 | refund_total is double before ~2018-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-025 (n=25000) |
| B26 | tax is absent before ~2021-01-01 and int from then on (placed_at) [orders] | OBSERVED | unknown | INV-026 (n=25000) |
| B27 | shipping=0 when tax ≥ 637, otherwise shipping=795 [orders] | OBSERVED | unknown | INV-027 (n=14794) |
| B28 | shipping=0 when subtotal - discount ≥ 7500, otherwise shipping=795 [orders] | OBSERVED | unknown | INV-028 (n=20924) |
| B29 | category=clearance ⇒ refund_status=none [orders.items[]] | OBSERVED | unknown | INV-029 (n=8399) |

### Code that never ran

- **F11** (Atlas Search ($search with a search index)) appears at [shoplog/catalog.py:10](../../shoplog/catalog.py#L10), [shoplog/catalog.py:22](../../shoplog/catalog.py#L22), [shoplog/schema.py:106](../../shoplog/schema.py#L106), but no operation in the query log used it. It may be dead code. Confidence: unknown (unknown: SUBQ_API_KEY is not set).
- **X:graphLookup** (graphLookup) appears at [shoplog/legacy_reports.py:10](../../shoplog/legacy_reports.py#L10), but no operation in the query log used it. It may be dead code. Confidence: unknown (unknown: SUBQ_API_KEY is not set).

## How far to trust this

- **Self-check:** MongoDB against itself gave 8/8 equal probes, 382/382 equal reads, and 350/350 equal writes with 0 final-state differences. So the differences above don't come from the comparison itself.
- **Probes:** 8 ran, **handwritten test fixtures, not generated by the model**. MongoDB is the oracle: a probe counts only if it ran cleanly on MongoDB and predicted its result. 0 probe(s) failed that check after retries and were excluded, not counted against the target.
- **Code evidence:** source is `static_scan_only`. Citations are checked by a program. 0 model citation(s) were dropped because the cited line didn't contain the quoted token.
- **Replay coverage:** the log has 1,554 entries. Not replayed: 205 sync worker bookkeeping; 60 inside a transaction (covered by probes); 57 change stream (covered by probes); 5 insert: the profiler doesn't keep the documents; 1 cursor maintenance. Reads are capped per operation shape.
- **Two different products:** FerretDB 2 runs on Postgres with an open-source DocumentDB extension. AWS DocumentDB is a separate engine with a similar name. FerretDB's results say nothing about AWS DocumentDB.
- **DocumentDB rows are INFERRED:** we read AWS's docs for engine 8.0.2 and compared them with this app's usage. Nothing ran there. Set `TARGET_URI` to a real cluster to replace them with observed results.
- **Fair by construction:** the same pipeline, thresholds and assumptions run on every workload. ShopLog was deliberately built to depend on MongoDB-specific features, and the control app wasn't.
- **Not modeled:** data transfer, backups, support plans and committed-use discounts. Rewrite hours are model estimates, and the risk inputs are assumptions.

Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `20261107T212822Z`
