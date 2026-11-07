# Migration decision: ShopLog

**Stay on MongoDB for ShopLog for now.** On FerretDB, 5 blocking gaps showed up (F4, F5, F6, F8). AWS DocumentDB documents support for most features but conflicts with this app's usage in 2 place(s), and nothing about it has been tested. Run these probes on a real cluster before considering it.

| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |
|---|---|---|---|---|---|
| FerretDB | OBSERVED | 5 | -$19,146 | Stay for now: blocking gaps exist, and the rewrite isn't priced yet | unavailable (SUBQ_API_KEY is not set) |
| AWS DocumentDB | INFERRED | 2 | -$2,939 | Not verified: test on a real cluster before deciding | unavailable (SUBQ_API_KEY is not set) |

The decision comes from measured gaps, list prices and stated assumptions. The SubQ probability is a separate second opinion on the same facts.

Tested: MongoDB 8.0.34 and FerretDB v2.7.0 (which reports itself as MongoDB 7.0.77). This app and its data are fictional and synthetic.

## Feature by feature

| # | What the app depends on | Reconstructed from | Status | Confidence | FerretDB (OBSERVED) | AWS DocumentDB 8.0.2 (INFERRED from docs) |
|---|---|---|---|---|---|---|
| F1 | $lookup sub-pipeline + $facet | query log (30 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>17/17 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/lookup.html)<br>Since Sub-pipeline correlated $lookup and $facet: 8.0.2 |
| F2 | $setWindowFields | query log (60 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>1/1 probes equal<br>40/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/setWindowFields.html)<br>The app uses $shift, which the docs list as unsupported |
| F3 | arrayFilters / positional $[<id>] updates | data + query log (55 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>1/1 probes equal<br>55/55 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/dollarIdentifier-update.html)<br>Since All versions |
| F4 | Multi-document transactions | query log (60 ops) + code | OBSERVED | 0.90 | ❌ Different result<br>0/1 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/transactions.html)<br>Since 4.0 |
| F5 | Change streams | query log (51 ops) + code | OBSERVED | 0.90 | ❌ Not supported<br>0/1 probes equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/change_streams.html)<br>Since All supported versions |
| F6 | Case-insensitive collation index | data + query log (110 ops) + code | OBSERVED | 0.97 | ❌ Not supported<br>0/1 probes equal<br>25/40 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/devguide/collation.html)<br>Since 8.0 |
| F7 | Partial index + TTL index | data + query log (70 ops) + code | OBSERVED | 0.97 | ⚪ Not tested | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/partial-index.html)<br>Since Partial index 5.0; TTL all versions |
| F8 | $jsonSchema validator | data + query log (5 ops) + code | OBSERVED | 0.97 | ❌ Different result<br>0/1 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/json-schema-validation.html)<br>Since 4.0 |
| F9 | $text search | data + query log (100 ops) + code | OBSERVED | 0.97 | 🟡 Same matches, different ranking<br>1/1 probes equal<br>0/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/text-search.html)<br>The app searches with exclusion terms (for example -refurbished), which the docs say aren't supported<br>The app's text index covers an array field, which the docs say isn't supported |
| F10 | Plain CRUD by _id and simple filters | data + query log (1,001 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>1/1 probes equal<br>487/487 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html)<br>Since All versions |
| X:graphLookup | graphLookup | code | INFERRED | unknown | ⚪ Not tested | ⚪ Not covered in our review |

## What differed on FerretDB

- **T.txn_abort** (❌ Different result): Aborted transaction leaves no trace. At step `read`, MongoDB gave `10` and FerretDB gave `7` (at `$.docs[0].n`).
- **T.collation_unique** (❌ Not supported): Case-insensitive unique index rejects a case variant. Step `index` failed with code 115: "Error in specification { "key" : { "email" : 1 }, "name" : "ci", "unique" : true, "collation" : { "locale" : "en", "strength" : 2 } } :: caused by :: createIndex.collatio".
- **T.validator** (❌ Different result): $jsonSchema validator rejects a bad document. At step `bad`, MongoDB gave `121` and FerretDB gave `<absent>` (at `$.error_code`).
- **T.change_stream** (❌ Not supported): A change stream delivers an update with the full document. Step `open` failed with code 115: "Stage $changeStream is not supported yet in native pipeline, full error: {'ok': 0.0, 'errmsg': 'Stage $changeStream is not supported yet in native pipeline', 'code': 115,".
- **Schema setup `customers.email_ci`** was refused with code 115: "Error in specification { "unique" : true, "name" : "email_ci_unique", "key" : { "email" : 1 }, "collation" : { "locale" : "en", "strength" : 2 } } :: caused by :: createI".
- **Replay `Sb98d4c91`** (F6, customers): 15 of 40 replayed operations differed (15 × different number of documents).
- **Replay `Se780f2b8`** (F9, products): 40 of 40 replayed operations differed (40 × same matches, ranked differently).

FerretDB's own [compatibility page](https://docs.ferretdb.io/migration/compatibility/) says `commitTransaction` and `abortTransaction` aren't implemented yet, so that gap is documented rather than hidden. The risk for an application is what happens at runtime: pymongo follows the driver specification and ignores errors from abort, so nothing tells the application its rollback did nothing.

## Latency

Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB 0.44 ms / 1.61 ms, FerretDB 0.57 ms / 3.48 ms.
Writes (350 replayed) p50/p95: MongoDB 1.29 ms / 1.87 ms, FerretDB 0.62 ms / 0.89 ms. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.

| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |
|---|---|---|---|---|---|
| F1 | orders: $match → $addFields → $lookup → $unwind | 7/20 (time budget reached) | 7.13 ms / 11.32 ms | 4,067 ms / 4,621 ms | 570.4× |
| F2 | orders: $match → $addFields → $setWindowFields → $project | 40/60 | 0.51 ms / 1.12 ms | 3.41 ms / 4.61 ms | 6.7× |
| F10 | orders: find | 1/1 | 1.48 ms / 1.48 ms | 3.03 ms / 3.03 ms | 2.0× |
| F1 | products: $lookup → $match → $group → $sort | 10/10 | 6.59 ms / 6.91 ms | 1.74 ms / 2.74 ms | 3.8× faster |
| F10 | orders: find | 40/40 | 0.88 ms / 1.47 ms | 1.03 ms / 2.25 ms | 1.2× |
| F6 | customers: find | 40/110 | 0.28 ms / 0.31 ms | 0.71 ms / 1.79 ms | 2.5× |
| F9 | products: find | 40/100 | 0.61 ms / 0.84 ms | 1.59 ms / 1.74 ms | 2.6× |
| F10 | carts: find | 40/70 | 0.27 ms / 0.30 ms | 0.35 ms / 1.42 ms | 1.3× |
| F10 | products: find | 30/30 | 0.45 ms / 0.63 ms | 0.56 ms / 0.71 ms | 1.2× |
| F10 | orders: find | 1/1 | 0.81 ms / 0.81 ms | 0.67 ms / 0.67 ms | 1.2× faster |
| F10 | orders: find | 40/100 | 0.51 ms / 0.81 ms | 0.47 ms / 0.60 ms | 1.1× faster |
| other | orders: $match → $group | 40/45 | 0.35 ms / 0.51 ms | 0.39 ms / 0.52 ms | 1.1× |
| F10 | orders: find | 40/270 | 0.29 ms / 0.36 ms | 0.38 ms / 0.48 ms | 1.3× |

## Cost over 12 months

| | FerretDB (self-managed, OBSERVED gaps) | AWS DocumentDB (INFERRED gaps) |
|---|---|---|
| Atlas today, per year | $36,442 | $36,442 |
| Target infrastructure, per year | $11,518 | $31,581 |
| Infrastructure saved | $24,924 | $4,861 |
| Self-managed operations | -$11,520 | -$0 |
| Migration baseline (assumed hours) | 40 h | 40 h |
| Rewrites (Muse estimate, INFERRED) | not estimated | not estimated |
| Residual incident risk (assumed) | -$27,750 | -$3,000 |
| **Net over 12 months, base** | **-$19,146** | **-$2,939** |
| Net, best to worst case | $7,034 to -$100,216 | $981 to -$14,739 |
| Rewrite hours the savings can pay for | 0 h | 0 h |
| Payback | Never at these numbers | 31.0 months |

**What would change the answer.** One-time rewrite costs don't grow with fleet size, but savings do.

| Scenario | FerretDB net (base) | FerretDB decision | DocumentDB net (base) | DocumentDB decision |
|---|---|---|---|---|
| 1 cluster(s) | -$29,199 | STAY_UNPRICED | -$6,585 | VERIFY_FIRST |
| 4 cluster(s) | -$19,146 | STAY_UNPRICED | -$2,939 | VERIFY_FIRST |
| 10 cluster(s) | $960 | STAY_UNPRICED | $4,353 | VERIFY_FIRST |
| 25 cluster(s) | $51,224 | STAY_UNPRICED | $22,582 | VERIFY_FIRST |
| Atlas billed per node (see below) | $53,737 | STAY_UNPRICED | $69,944 | VERIFY_FIRST |

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

- **X:graphLookup** (graphLookup) appears at [shoplog/legacy_reports.py:10](../../shoplog/legacy_reports.py#L10), but no operation in the query log used it. It may be dead code. Confidence: unknown (unknown: SUBQ_API_KEY is not set).

## How far to trust this

- **Self-check:** MongoDB against itself gave 8/8 equal probes, 382/382 equal reads, and 350/350 equal writes with 0 final-state differences. So the differences above don't come from the comparison itself.
- **Probes:** 8 ran, **handwritten test fixtures, not generated by the model**. MongoDB is the oracle: a probe counts only if it ran cleanly on MongoDB and predicted its result. 0 probe(s) failed that check after retries and were excluded, not counted against the target.
- **Code evidence:** source is `static_scan_only`. Citations are checked by a program. 0 model citation(s) were dropped because the cited line didn't contain the quoted token.
- **Replay coverage:** the log has 1,513 entries. Not replayed: 189 sync worker bookkeeping; 60 inside a transaction (covered by probes); 51 change stream (covered by probes); 5 insert: the profiler doesn't keep the documents; 1 cursor maintenance. Reads are capped per operation shape.
- **Two different products:** FerretDB 2 runs on Postgres with an open-source DocumentDB extension. AWS DocumentDB is a separate engine with a similar name. FerretDB's results say nothing about AWS DocumentDB.
- **DocumentDB rows are INFERRED:** we read AWS's docs for engine 8.0.2 and compared them with this app's usage. Nothing ran there. Set `TARGET_URI` to a real cluster to replace them with observed results.
- **Fair by construction:** the same pipeline, thresholds and assumptions run on every workload. ShopLog was deliberately built to depend on MongoDB-specific features, and the control app wasn't.
- **Not modeled:** data transfer, backups, support plans and committed-use discounts. Rewrite hours are model estimates, and the risk inputs are assumptions.

Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `20261107T204203Z`
