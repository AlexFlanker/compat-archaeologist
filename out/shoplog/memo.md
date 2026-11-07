# Migration decision: ShopLog

*One-page brief for the CTO or VP Engineering. The evidence and method follow in the appendix.*

**Stay on MongoDB for ShopLog for now.** On FerretDB, 6 blocking gaps showed up (F4, F5, F6, F8, F11). AWS DocumentDB documents support for most features but conflicts with this app's usage in 3 place(s), and nothing about it has been tested. Run these probes on a real cluster before considering it.

| | FerretDB (observed) | AWS DocumentDB (inferred) |
|---|---|---|
| Recommendation | Stay: fixing the gaps costs more than the migration saves | Not verified: test on a real cluster before deciding |
| Confidence | High (measured; self-check clean; SubQ P(net-positive) 0.00) | Low: documentation only, nothing measured |
| Net over 12 months (base, best to worst) | -$98,823 (-$36,124 to -$234,031) | -$41,337 (-$24,897 to -$74,877) |
| Reversibility (100 = easy to go back) | 15/100 (OBSERVED) | 75/100 (INFERRED) |

**Top risks**

1. **Checkout isn't atomic: stock can be reserved without an order, and a rollback does nothing** (FerretDB, silent). Evidence: probe `F4.txn-abort-rollback`; probe `F4.txn-commit-atomic`; code [shoplog/checkout.py:57](../../shoplog/checkout.py#L57).
2. **Logins and email uniqueness stop ignoring case: duplicate accounts, failed sign-ins** (FerretDB, silent). Evidence: probe `F6.ci-lookup`; probe `F6.ci-unique`; 17/40 replayed ops differ; code [shoplog/customers.py:9](../../shoplog/customers.py#L9), [shoplog/schema.py:60](../../shoplog/schema.py#L60).
3. **Invalid orders get written: the schema validator isn't enforced** (FerretDB, silent). Evidence: probe `F8.orders-required-fields`; probe `F8.orders-enum-total`; code [shoplog/schema.py:12](../../shoplog/schema.py#L12).
4. **Constraints don't survive a clean cutover: customers.email_ci_unique index (unique, collation), orders $jsonSchema validator** (FerretDB, cutover). Evidence: cutover check, clean run: 6 of 23 checks failed.
5. **Queries on orders run 430× slower ($lookup sub-pipeline + $facet)** (FerretDB, performance). Evidence: replay p95 9.97 ms → 4289.43 ms; code [shoplog/analytics.py:33](../../shoplog/analytics.py#L33).

**12-month cost, base case** (yearly figures; best and worst cases in the appendix)

| | FerretDB | AWS DocumentDB |
|---|---|---|
| Staying on MongoDB Atlas | $36,442 | $36,442 |
| Target infrastructure | $11,518 | $31,581 |
| I/O billing | $0 | $0 (I/O-Optimized plan) |
| Scale-ups for slow paths (INFERRED) | $5,759 | $0 |
| Extra services (search) | $11,938 | $11,938 |
| Running it yourself | $11,520 | $0 |
| Migration and rewrites | $65,280 (68 person-days) | $29,760 (31 person-days) |
| Expected incident cost | $29,250 | $4,500 |
| **Net vs staying** | **-$98,823** | **-$41,337** |

**Measured on replay** (synthetic dataset, 38 MB; both engines local, same limits)

| Path | MongoDB p95 | FerretDB p95 | FerretDB over the timeout | Timeout used |
|---|---|---|---|---|
| Online | 1.89 ms | 4.02 ms | 0% of 367 | 1,000 ms |
| Reporting | 9.97 ms | 4,289 ms | 0% of 17 | 30,000 ms |
| Event-driven (change streams) | works | ❌ Not supported | | |

**At production size (INFERRED)**: the data size at which each slow path reaches its timeout, projected from the measured 38.4 MB using the growth shape of its query plan. This assumes every collection grows by the same factor.
- F1 reporting query on orders (30 s timeout): MongoDB grows linear (index lookups) and reaches it at ~116 GB; FerretDB grows quadratic (a sequential scan inside a nested loop) and reaches it at ~100 MB.
- At TB scale both engines need work on reporting, such as pre-aggregation, but FerretDB hits the limit orders of magnitude sooner.

**Business questions**: on untouched data, 8 of 8 answers match on FerretDB. After a clean cutover and one morning of traffic, 5 of 8 differ:
- What was revenue in each year? 2026: orders 8,919 → 8,920, revenue_cents 614,753,084 → 614,753,079.
- How many orders are in each status? FerretDB has extra group(s) lost_in_mail.
- How many customers ordered in the last 90 days? customers 3,653 → 3,654.
- How many units are on hand across all products? on_hand 499,078 → 499,066, reserved 66 → 78.
- How many customers are in each loyalty tier? bronze: customers 3,040 → 3,041.
- The same 12 checkouts: MongoDB 6 completed, 6 refused: out of stock; FerretDB 6 error 59, 6 refused: out of stock. Error 59 means commit isn't implemented: the shopper is told it failed, but the order was already written.

**Hidden rules recovered blind**: 10 of 10 found, 10 of 10 labeled correctly (OBSERVED or INFERRED). Scored against [docs/RULES.md](../../docs/RULES.md), which the pipeline never reads. Code evidence for this run: muse_cached. One scoring pattern was fixed after the first run with Muse; the change log is in `archaeologist/scoreboard.py`.

**Cutover fidelity**: a simulated full-load + CDC migration with nothing injected failed 6 of 23 checks on FerretDB: customers.email_ci_unique index (unique, collation), orders $jsonSchema validator, Business rule: emails that collide when case is ignored, Business rule: orders that break the order schema.
In a drill with faults injected on purpose (CDC gap: 30 change events dropped mid-cutover; Copier skipped unique index coupon_once_per_customer), the verifier caught 2 of 2.

**Upgrade check**: FerretDB v2.2.0 → v2.7.0: 0 feature(s) fixed, 0 regressed, 5 still failing (F4, F5, F6, F8, F9). The slowest path went from 4287.32 ms to 4360.0 ms p95 (MongoDB 16.13 ms). A newer version number didn't change the decision. It did get faster elsewhere: F2 on orders 32.4 → 3.8 ms.

Reversibility deductions: FerretDB: -30 No change streams on the target: moving back needs a full reload with downtime, -25 The target accepts writes MongoDB refuses, so that data needs cleaning before moving back, -20 5 code rewrite(s) make the two versions of the app diverge, -10 A separate search service would need to be retired or kept; AWS DocumentDB: -5 Documented limit: $setWindowFields, -5 Documented limit: $text search, -5 Documented limit: Atlas Search ($search with a search index), -10 A separate search service would need to be retired or kept.

---

# Appendix: evidence and method

## Decision detail

| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |
|---|---|---|---|---|---|
| FerretDB | OBSERVED | 6 | -$98,823 | Stay: fixing the gaps costs more than the migration saves | 0.00 |
| AWS DocumentDB | INFERRED | 3 | -$41,337 | Not verified: test on a real cluster before deciding | 0.00 |

The decision comes from measured gaps, list prices and stated assumptions. The SubQ probability is a separate second opinion on the same facts, from subq-snap-1.1.

Tested: MongoDB 8.0.34 and FerretDB v2.7.0 (which reports itself as MongoDB 7.0.77). This app and its data are fictional and synthetic.

## Feature by feature

| # | What the app depends on | Reconstructed from | Status | Confidence | FerretDB (OBSERVED) | AWS DocumentDB 8.0.2 (INFERRED from docs) |
|---|---|---|---|---|---|---|
| F1 | $lookup sub-pipeline + $facet | query log (30 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>3/3 probes equal<br>17/17 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/lookup.html)<br>Since Sub-pipeline correlated $lookup and $facet: 8.0.2 |
| F2 | $setWindowFields | query log (60 ops) + code | OBSERVED | 0.90 | ✅ Same behavior<br>3/3 probes equal<br>40/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/setWindowFields.html)<br>The app uses $shift, which the docs list as unsupported |
| F3 | arrayFilters / positional $[<id>] updates | data + query log (56 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>3/3 probes equal<br>56/56 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/dollarIdentifier-update.html)<br>Since All versions |
| F4 | Multi-document transactions | query log (60 ops) + code | OBSERVED | 0.90 | ❌ Not supported<br>0/3 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/transactions.html)<br>Since 4.0 |
| F5 | Change streams | query log (57 ops) + code | OBSERVED | 0.90 | ❌ Not supported<br>0/3 probes equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/change_streams.html)<br>Since All supported versions |
| F6 | Case-insensitive collation index | data + query log (110 ops) + code | OBSERVED | 0.97 | ❌ Not supported<br>0/3 probes equal<br>23/40 replayed ops equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/devguide/collation.html)<br>Since 8.0 |
| F7 | Partial index + TTL index | data + query log (70 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>3/3 probes equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/partial-index.html)<br>Since Partial index 5.0; TTL all versions |
| F8 | $jsonSchema validator | data + query log (5 ops) + code | OBSERVED | 0.97 | ❌ Different result<br>0/3 probes equal<br>**Silent: no error raised** | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/json-schema-validation.html)<br>Since 4.0 |
| F9 | $text search | data + query log (100 ops) + code | OBSERVED | 0.97 | 🟡 Same matches, different ranking<br>3/3 probes equal<br>0/40 replayed ops equal | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/text-search.html)<br>The app searches with exclusion terms (for example -refurbished), which the docs say aren't supported<br>The app's text index covers an array field, which the docs say isn't supported |
| F10 | Plain CRUD by _id and simple filters | data + query log (1,034 ops) + code | OBSERVED | 0.97 | ✅ Same behavior<br>2/2 probes equal<br>504/504 replayed ops equal | 🔵 [Documented as supported](https://docs.aws.amazon.com/documentdb/latest/developerguide/mongo-apis.html)<br>Since All versions |
| F11 | Atlas Search ($search with a search index) | code | INFERRED | 0.96 | 🟠 INFERRED: [not supported per FerretDB docs](https://docs.ferretdb.io/migration/compatibility/)<br>The compatibility page lists no $search stage or search index commands. Locally, FerretDB also refused $listSearchIndexes (code 40324). | 🟠 [Docs conflict with this app's usage](https://docs.aws.amazon.com/documentdb/latest/developerguide/search.html)<br>The app uses Atlas Search, which DocumentDB doesn't provide; a separate search service is needed |
| X:graphLookup | graphLookup | code | INFERRED | 0.57 | ✅ Same behavior<br>1/1 probes equal | ⚪ Not covered in our review |

## What differed on FerretDB

- **F4.txn-abort-rollback** (❌ Different result): Abort rolls back multi-line reservations and order. At step `check-inv-rolled-back`, MongoDB gave `10` and FerretDB gave `8` (at `$.docs[0].stock`).
- **F4.txn-commit-atomic** (❌ Not supported): Commit atomically persists reservations order and cart. Step `commit` failed with code 59: "no such command: 'commitTransaction', full error: {'ok': 0.0, 'errmsg': "no such command: 'commitTransaction'", 'code': 59, 'codeName': 'CommandNotFound'}".
- **F4.txn-isolation-uncommitted** (❌ Different result): Uncommitted reservation invisible outside transaction. At step `read-outside-sees-old`, MongoDB gave `10` and FerretDB gave `8` (at `$.docs[0].stock`).
- **F5.cs-filter-update-replace** (❌ Not supported): Change stream filtered to update and replace only. Step `open-filtered` failed with code 115: "Stage $changeStream is not supported yet in native pipeline, full error: {'ok': 0.0, 'errmsg': 'Stage $changeStream is not supported yet in native pipeline', 'code': 115,".
- **F5.cs-fulldocument-lookup** (❌ Not supported): Change stream fullDocument updateLookup on update. Step `open-lookup` failed with code 115: "Stage $changeStream is not supported yet in native pipeline, full error: {'ok': 0.0, 'errmsg': 'Stage $changeStream is not supported yet in native pipeline', 'code': 115,".
- **F5.cs-event-per-change** (❌ Not supported): Change stream one event per change with non-blocking poll. Step `open-filtered` failed with code 115: "Stage $changeStream is not supported yet in native pipeline, full error: {'ok': 0.0, 'errmsg': 'Stage $changeStream is not supported yet in native pipeline', 'code': 115,".
- **F6.ci-lookup** (❌ Different result): Case-insensitive email lookup with collation. At step `find-upper-with-collation`, MongoDB gave `1` and FerretDB gave `0` (at `$.docs.length`).
- **F6.ci-unique** (❌ Not supported): Case-insensitive unique email via collation index. Step `create-ci-unique-index` failed with code 115: "Error in specification { "key" : { "email" : 1 }, "name" : "email_ci_unique", "unique" : true, "collation" : { "locale" : "en", "strength" : 2 } } :: caused by :: createI".
- **F6.ci-gating** (❌ Different result): Collation controls case sensitivity of email match. At step `find-with-collation-matches`, MongoDB gave `1` and FerretDB gave `0` (at `$.docs.length`).
- **F8.orders-required-fields** (❌ Different result): $jsonSchema requires customer_id, status, items, total and placed_at on orders. At step `insert missing customer_id fails`, MongoDB gave `121` and FerretDB gave `<absent>` (at `$.error_code`).
- **F8.orders-enum-total** (❌ Different result): $jsonSchema enum and minimum on orders status and total. At step `insert bad status fails`, MongoDB gave `121` and FerretDB gave `<absent>` (at `$.error_code`).
- **F8.orders-types-update** (❌ Different result): $jsonSchema bsonType on items and placed_at plus update validation on orders. At step `insert items not array fails`, MongoDB gave `121` and FerretDB gave `<absent>` (at `$.error_code`).
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

## Business questions

| Question | Same data, both engines | After clean cutover and day one |
|---|---|---|
| What was revenue in each year? | same | different |
| How much was refunded each year? | same | same |
| How many orders are in each status? | same | different |
| How many customers ordered in the last 90 days? | same | different |
| Which 5 products sold the most units in 2025? | same | same |
| How many welcome coupons were redeemed? | same | same |
| How many units are on hand across all products? | same | different |
| How many customers are in each loyalty tier? | same | different |

## Scoreboard: hidden rules

| Rule | Found | Label | Expected | Evidence |
|---|---|---|---|---|
| R1 Tier discounts; legacy_vip never takes a coupon | yes | OBSERVED | OBSERVED | INV-019: [orders] customers.tier=legacy_vip ⇒ coupon_code is absent; B31 (INV-017, INV-018, INV-020): Tier discount is bronze 0%, silver 5%, gold 10%, legacy_vip 15% off subtotal, so silver, g |
| R2 WELCOME10 works once per customer | yes | OBSERVED | OBSERVED | catalog:orders.coupon_once_per_customer: orders.coupon_once_per_customer on ['customer_id', 'coupon_code'] is unique and partial on |
| R3 Free shipping from $75 of discounted merchandise | yes | OBSERVED | OBSERVED | INV-028: [orders] shipping=0 when subtotal - discount ≥ 7500, otherwise shipping=795; B35 (INV-027, INV-028, shoplog/pricing.py:18): Discounted merchandise (subtotal - discount) of 7500 or more gets free shipping (shipping  |
| R4 8.5% tax on merchandise; no tax field before 2021 | yes | OBSERVED | OBSERVED | INV-026: [orders] tax is absent before ~2021-01-01 and int from then on (placed_at); B36 (shoplog/pricing.py:19): Tax is 8.5% (850 bps) applied to discounted merchandise excluding shipping |
| R5 Float dollars before 2018, integer cents after | yes | OBSERVED | OBSERVED | INV-021: [orders] subtotal is double before ~2018-01-01 and int from then on (placed_at); INV-024: [orders] total is double before ~2018-01-01 and int from then on (placed_at) |
| R6 Order state machine; only placed or paid can be cancelled | yes | OBSERVED | OBSERVED | B25 (shoplog/orders.py:11): An order can be cancelled only from 2 prior statuses: placed or paid, never after shipped |
| R7 Refund window: 30 days, 90 for legacy_vip | yes | INFERRED | INFERRED | B37 (shoplog/refunds.py:7): Orders in the legacy_vip tier may be refunded up to 90 days after delivered_at; B38 (shoplog/refunds.py:8): Orders in all other tiers may be refunded up to 30 days after delivered_at |
| R8 Clearance items are final sale | yes | OBSERVED | OBSERVED | INV-029: [orders.items[]] category=clearance ⇒ refund_status=none; B39 (INV-029, shoplog/refunds.py:9): Items in the clearance category are final sale and are never eligible for refund, so clear |
| R9 refund_total > 0 exactly when refunded | yes | OBSERVED | OBSERVED | INV-008: [orders] status=partially_refunded ⇒ refund_total > 0; INV-012: [orders] status=refunded ⇒ refund_total > 0 |
| R10 Active carts expire after 24 hours; converted carts are kept | yes | OBSERVED | OBSERVED | catalog:carts.active_cart_expiry: carts.active_cart_expiry on ['expires_at'] is partial on {"status": "active"}; catalog:carts.active_cart_expiry: carts.active_cart_expiry is TTL, expireAfterSeconds=0 |

Matching uses fixed patterns per rule, not a model's judgment, so a paraphrase the pattern misses counts as missed.

## Upgrade check

FerretDB v2.2.0 → v2.7.0: 0 feature(s) fixed, 0 regressed, 5 still failing (F4, F5, F6, F8, F9). The slowest path went from 4287.32 ms to 4360.0 ms p95 (MongoDB 16.13 ms). A newer version number didn't change the decision. It did get faster elsewhere: F2 on orders 32.4 → 3.8 ms.

| Feature | FerretDB v2.2.0 | FerretDB v2.7.0 |
|---|---|---|
| F1 | equal | equal |
| F2 | equal | equal |
| F3 | equal | equal |
| F4 | result_diff | result_diff |
| F5 | unsupported | unsupported |
| F6 | unsupported | unsupported |
| F8 | result_diff | result_diff |
| F9 | ranking_diff | ranking_diff |
| F10 | equal | equal |

| Slow path | Collection | v2.2.0 p95 | v2.7.0 p95 | MongoDB p95 |
|---|---|---|---|---|
| F1 | orders | 4287.32 ms | 4360.0 ms | 16.13 ms |
| F10 | orders | 41.06 ms | 34.64 ms | 41.16 ms |
| F2 | orders | 32.43 ms | 3.77 ms | 0.78 ms |

AWS DocumentDB version differences (INFERRED from docs, not measured):

- F1, 5.0 → 8.0.2: Correlated $lookup sub-pipelines and $facet are documented from 8.0.2 (planner v3).
- F2, 5.0 → 8.0.2: $setWindowFields is documented from 8.0.2; $shift is still unsupported.
- F6, 5.0 → 8.0: Collation is documented only on 8.0.

## Latency

Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB 0.50 ms / 7.78 ms, FerretDB 0.75 ms / 7.02 ms.
Writes (353 replayed) p50/p95: MongoDB 1.30 ms / 1.90 ms, FerretDB 0.64 ms / 1.03 ms. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.

| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |
|---|---|---|---|---|---|
| F1 | orders: $match → $addFields → $lookup → $unwind | 7/20 (time budget reached) | 7.78 ms / 9.97 ms | 4,026 ms / 4,289 ms | 517.4× |
| F10 | orders: find | 15/15 | 8.89 ms / 54.86 ms | 9.43 ms / 38.49 ms | 1.1× |
| F2 | orders: $match → $addFields → $setWindowFields → $project | 40/60 | 0.63 ms / 1.15 ms | 3.60 ms / 4.22 ms | 5.7× |
| F10 | orders: find | 1/1 | 1.35 ms / 1.35 ms | 3.81 ms / 3.81 ms | 2.8× |
| F9 | products: find | 40/100 | 0.89 ms / 1.89 ms | 1.80 ms / 2.68 ms | 2.0× |
| F1 | products: $lookup → $match → $group → $sort | 10/10 | 7.92 ms / 8.60 ms | 2.21 ms / 2.67 ms | 3.6× faster |
| F6 | customers: find | 40/110 | 0.31 ms / 0.73 ms | 0.74 ms / 1.95 ms | 2.4× |
| F10 | orders: find | 40/40 | 0.84 ms / 1.68 ms | 1.07 ms / 1.89 ms | 1.3× |
| F10 | orders: find | 40/270 | 0.40 ms / 0.95 ms | 0.57 ms / 1.65 ms | 1.4× |
| F10 | orders: find | 40/100 | 0.48 ms / 1.24 ms | 0.55 ms / 1.09 ms | 1.1× |
| F10 | products: find | 30/30 | 0.41 ms / 0.54 ms | 0.51 ms / 0.75 ms | 1.2× |
| other | orders: $match → $group | 40/46 | 0.36 ms / 0.63 ms | 0.40 ms / 0.66 ms | 1.1× |
| F10 | orders: find | 1/1 | 0.79 ms / 0.79 ms | 0.58 ms / 0.58 ms | 1.4× faster |
| F10 | carts: find | 40/70 | 0.31 ms / 0.37 ms | 0.38 ms / 0.46 ms | 1.2× |

## Why the slow paths are slow

Plans from the MongoDB MCP Server's `explain` tool (`mongodb-mcp-server@3.2.2 explain`), run read-only against each engine on the slowest replayed queries.

**F1 on orders** (replay p95 9.97 ms on MongoDB, 4,289 ms on FerretDB)
- MongoDB: `IXSCAN(status_1_placed_at_-1) → FETCH → PROJECTION_SIMPLE → $addFields → $lookup(indexes=['_id_'], collection scans=0, docs examined=309) → $facet`, 306 documents examined.
- FerretDB (Postgres plan): `Aggregate → ProjectSet → Subquery Scan → Nested Loop → Index Scan(status_1_placed_at_-1) → Aggregate → Seq Scan → Aggregate → Function Scan(bson_lookup_unwind) → Append → Aggregate → Sort → Subquery Scan → Aggregate → CTE Scan → Aggregate → Sort → Subquery Scan → Aggregate → CTE Scan → Aggregate → Limit → Sort → CTE Scan`, with 1 sequential scan(s) and 1 nested loop(s).

**F2 on orders** (replay p95 1.15 ms on MongoDB, 4.22 ms on FerretDB)
- MongoDB: `IXSCAN(customer_id_1_placed_at_-1) → FETCH → PROJECTION_SIMPLE → $addFields → $sort → $_internalSetWindowFields → $project`, 13 documents examined.
- FerretDB (Postgres plan): `Subquery Scan → WindowAgg → WindowAgg → WindowAgg → WindowAgg → Sort → Bitmap Heap Scan → BitmapAnd → Bitmap Index Scan(status_1_placed_at_-1) → Bitmap Index Scan(customer_id_1_placed_at_-1)`, with 0 sequential scan(s) and 0 nested loop(s).

**F10 on orders** (replay p95 1.35 ms on MongoDB, 3.81 ms on FerretDB)
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
| Rewrites (Muse estimate, INFERRED) | 504 h | 208 h |
| Residual incident risk (assumed) | -$29,250 | -$4,500 |
| **Net over 12 months, base** | **-$98,823** | **-$41,337** |
| Net, best to worst case | -$36,124 to -$234,031 | -$24,897 to -$74,877 |
| Rewrite hours the savings can pay for | 0 h | 0 h |
| Payback | Never at these numbers | Never at these numbers |

**What would change the answer.** One-time rewrite costs don't grow with fleet size, but savings do.

| Scenario | FerretDB net (base) | FerretDB decision | DocumentDB net (base) | DocumentDB decision |
|---|---|---|---|---|
| 1 cluster(s) | -$95,603 | STAY | -$36,029 | VERIFY_FIRST |
| 4 cluster(s) | -$98,823 | STAY | -$41,337 | VERIFY_FIRST |
| 10 cluster(s) | -$105,261 | STAY | -$51,951 | VERIFY_FIRST |
| 25 cluster(s) | -$121,358 | STAY | -$78,488 | VERIFY_FIRST |
| Atlas billed per node (see below) | -$25,939 | STAY | $31,547 | VERIFY_FIRST |

**Check your Atlas invoice.** MongoDB's public sources disagree on whether the listed hourly price covers a whole default cluster or one node, and that changes the baseline threefold. The base case assumes the whole cluster, because the [pricing page](https://www.mongodb.com/pricing) says M10 starts at $56.94/month, which is its hourly rate with no multiplier. [The billing docs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/) describe the cost as applying to each data-bearing node. The last row shows that case.

List prices and every assumption are in [assumptions.yaml](../../assumptions.yaml), including 4 fictional clusters. DocumentDB uses the cheaper of its Standard and I/O-Optimized options, at equal RAM to Atlas M40 but half the vCPUs, which favors migrating.

### Rewrite estimates (Muse, INFERRED)

| Target | Gap | Low / base / high hours | Approach |
|---|---|---|---|
| FerretDB | Multi-document transactions | 80 / 160 / 280 | Replace the multi-document transaction in shoplog/checkout.py:57 with an application saga: idempotent pending-order insert, conditional inventory decrements, then finalize/cancel with compensating rollbacks plus a background reconciler and repair for partial stocks/orders. |
| FerretDB | Change streams | 40 / 80 / 140 | Replace the $changeStream watcher in shoplog/inventory_sync.py with indexed polling on updatedAt/_id plus a persisted checkpoint and idempotent apply, adding tombstones/indexes to cover filtered update/replace and fullDocument lookup. Add catch-up/backfill verification and lag/duplicate testing before rollout. |
| FerretDB | Case-insensitive collation index | 24 / 48 / 80 | Replace collation-based matching/uniqueness with a normalized lowercase email field with a standard unique index and update all lookups/writes to use it. Backfill and de-duplicate existing customers with conflicting case variants, then verify case-insensitive signup/login paths. |
| FerretDB | $jsonSchema validator | 16 / 28 / 48 | Replace DB-level $jsonSchema enforcement with a shared application validator for orders inserts/updates covering required fields, enum/minimum and bsonTypes. Audit and repair any invalid orders already written to FerretDB and add regression tests verifying rejections without relying on error 121. |
| FerretDB | Slow path on orders ($lookup sub-pipeline + $facet) | 24 / 48 / 80 | Split the $facet into separate count and page queries and replace the $lookup sub-pipeline with simple indexed lookups plus application-side join in shoplog/analytics.py, with covering indexes and p95/parity validation on FerretDB. |
| FerretDB | Atlas Search ($search with a search index) | 72 / 140 / 250 | Deploy a dedicated search service (OpenSearch/Elasticsearch) alongside FerretDB with async catalog sync plus full backfill, and rewrite the $search aggregations in catalog.py to query it with parity-tested ranking. Backfill existing data and cut over with dual-run validation before removing the Atlas index from schema.py. |
| AWS DocumentDB | $setWindowFields | 8 / 20 / 40 | Replace the $setWindowFields/$shift pipeline in shoplog/analytics.py with a DocumentDB-compatible sorted query plus application-side shift/lead-lag computation preserving partition and sort semantics. Validate output parity against MongoDB including boundary/null cases and performance, then roll out. |
| AWS DocumentDB | $text search | 24 / 48 / 80 | Flatten array values plus text fields into a denormalized plain-string search field with write-path maintenance and one-time backfill, indexed instead of the array. Rewrite $text queries to DocumentDB-supported regex/contains queries with application-side filtering for exclusion (-term) operators and regression tests for relevance. |
| AWS DocumentDB | Atlas Search ($search with a search index) | 80 / 140 / 240 | Replace Atlas $search with Amazon OpenSearch Service: provision index, build change-stream sync with full backfill, and rewrite catalog.py queries and schema index definition to query OpenSearch with relevance-parity testing. |

## Reconstructed business behavior

Recovered without any documentation. 9 of 50 are OBSERVED in the data. The rest appear only in code, so they are INFERRED.

| ID | Behavior | Status | Confidence | Evidence |
|---|---|---|---|---|
| B01 | Cancelled orders are excluded from the revenue report (only orders with status not equal to cancelled count) | INFERRED | 0.77 | [shoplog/analytics.py:18](../../shoplog/analytics.py#L18) |
| B02 | Dollar totals stored as double were migrated to integer cents by multiplying by 100 and rounding to 0 decimals: subtotal, discount, shipping, total and refund_total are double before ~2018-01-01 and int from then on | OBSERVED | 0.90 | INV-021 (n=25000), INV-022 (n=25000), INV-023 (n=25000), INV-024 (n=25000) |
| B03 | Revenue report returns the 5 largest orders by total_cents | INFERRED | 0.79 | [shoplog/analytics.py:49](../../shoplog/analytics.py#L49) |
| B04 | Customer timeline maintains a 90-day rolling spend total per customer ordered by placed_at | INFERRED | 0.68 | [shoplog/analytics.py:86](../../shoplog/analytics.py#L86) |
| B05 | A SKU is low-stock when on_hand is less than the given threshold and low-stock SKUs are counted per category | INFERRED | 0.84 | [shoplog/analytics.py:65](../../shoplog/analytics.py#L65) |
| B06 | A cart's lifetime is 24 hours after its last touch/update | INFERRED | 0.97 | [shoplog/carts.py:7](../../shoplog/carts.py#L7) |
| B07 | Touching a cart marks it active and resets its expiry to 24 hours from now | INFERRED | 0.99 | [shoplog/carts.py:19](../../shoplog/carts.py#L19) |
| B08 | Only the customer's cart with status active counts as the current active cart | INFERRED | 1.00 | [shoplog/carts.py:27](../../shoplog/carts.py#L27) |
| B09 | A cart can only be discarded while it is still active; converted carts are not deleted by discard | INFERRED | 0.96 | [shoplog/carts.py:31](../../shoplog/carts.py#L31) |
| B10 | Converting a cart marks it converted and records the order_id that fulfilled it | INFERRED | 1.00 | [shoplog/carts.py:37](../../shoplog/carts.py#L37) |
| B11 | Storefront search returns at most 20 products by default | INFERRED | 0.95 | [shoplog/catalog.py:8](../../shoplog/catalog.py#L8) |
| B12 | Autocomplete returns at most 8 name suggestions by default | INFERRED | 0.82 | [shoplog/catalog.py:20](../../shoplog/catalog.py#L20) |
| B13 | Browsing products in a category returns at most 40 products by default | INFERRED | 0.48 | [shoplog/catalog.py:40](../../shoplog/catalog.py#L40) |
| B14 | Storefront name/description/tags text matching tolerates at most 1 edit (fuzzy maxEdits 1) | INFERRED | 0.79 | [shoplog/catalog.py:11](../../shoplog/catalog.py#L11) |
| B15 | Order items are assigned sequential line_id values starting at 1 (index + 1) | INFERRED | 0.97 | [shoplog/checkout.py:32](../../shoplog/checkout.py#L32) |
| B16 | Each new order line starts with refunded amount 0 | INFERRED | 0.99 | [shoplog/checkout.py:39](../../shoplog/checkout.py#L39) |
| B17 | Every new order starts with refund_total of 0 and only orders whose status is delivered or partially_refunded can be refunded, so cancelled, shipped and delivered orders with no refund have refund_total = 0 | OBSERVED | 0.90 | INV-001 (n=1231), INV-003 (n=21681), INV-014 (n=206), [shoplog/checkout.py:48](../../shoplog/checkout.py#L48) |
| B18 | Checkout fails with OutOfStock unless exactly 1 inventory document is updated per order line, meaning on_hand must cover the ordered qty | INFERRED | 1.00 | [shoplog/checkout.py:64](../../shoplog/checkout.py#L64) |
| B19 | New customers start with 0 loyalty points | INFERRED | 0.93 | [shoplog/customers.py:14](../../shoplog/customers.py#L14) |
| B20 | Only 2 operation types — update and replace — on inventory create inventory_events entries; inserts and deletes are ignored | INFERRED | 0.91 | [shoplog/inventory_sync.py:7](../../shoplog/inventory_sync.py#L7) |
| B21 | Referral chain lookup follows referred_by links up to 3 levels deep by default | INFERRED | 0.71 | [shoplog/legacy_reports.py:6](../../shoplog/legacy_reports.py#L6) |
| B22 | An order can move to paid only from 1 prior status: placed | INFERRED | 1.00 | [shoplog/orders.py:8](../../shoplog/orders.py#L8) |
| B23 | An order can move to shipped only from 1 prior status: paid | INFERRED | 1.00 | [shoplog/orders.py:9](../../shoplog/orders.py#L9) |
| B24 | An order can move to delivered only from 1 prior status: shipped | INFERRED | 1.00 | [shoplog/orders.py:10](../../shoplog/orders.py#L10) |
| B25 | An order can be cancelled only from 2 prior statuses: placed or paid, never after shipped | INFERRED | 1.00 | [shoplog/orders.py:11](../../shoplog/orders.py#L11) |
| B26 | When an order is marked delivered, delivered_at must be set to the same transition time as updated_at, so delivered_at is present for delivered, partially_refunded and refunded orders and absent for placed, paid, shipped and cancelled orders | OBSERVED | 0.90 | INV-002 (n=1231), INV-004 (n=21681), INV-005 (n=43), INV-009 (n=646) |
| B27 | Every status change must append 1 history entry with the new status and timestamp while updating status | INFERRED | 1.00 | [shoplog/orders.py:22](../../shoplog/orders.py#L22) |
| B28 | Customer order history returns at most 20 orders, newest placed_at first with _id tie-breaker | INFERRED | 1.00 | [shoplog/orders.py:31](../../shoplog/orders.py#L31) |
| B29 | Search for orders containing a SKU returns at most 100 orders placed since the given date, newest first | INFERRED | 0.99 | [shoplog/orders.py:42](../../shoplog/orders.py#L42) |
| B30 | Listing open orders by a status returns at most 50 orders, newest placed_at first | INFERRED | 1.00 | [shoplog/orders.py:50](../../shoplog/orders.py#L50) |
| B31 | Tier discount is bronze 0%, silver 5%, gold 10%, legacy_vip 15% off subtotal, so silver, gold and legacy_vip orders have discount > 0 | OBSERVED | 0.90 | INV-017 (n=3004), INV-018 (n=681), INV-020 (n=6209), [shoplog/pricing.py:3](../../shoplog/pricing.py#L3) |
| B32 | Coupon code WELCOME10 is worth 1000 off merchandise | INFERRED | 0.98 | [shoplog/pricing.py:4](../../shoplog/pricing.py#L4) |
| B33 | legacy_vip tier cannot use coupons - any coupon is ignored for legacy_vip, so legacy_vip orders have coupon_code absent | OBSERVED | 0.90 | INV-019 (n=681), [shoplog/pricing.py:13](../../shoplog/pricing.py#L13) |
| B34 | Total discount (tier + coupon) is capped at subtotal so merchandise never goes negative | INFERRED | 1.00 | [shoplog/pricing.py:16](../../shoplog/pricing.py#L16) |
| B35 | Discounted merchandise (subtotal - discount) of 7500 or more gets free shipping (shipping 0), otherwise shipping is 795 flat, corresponding to tax >= 637 vs shipping 795 | OBSERVED | 0.90 | INV-027 (n=14794), INV-028 (n=20924), [shoplog/pricing.py:18](../../shoplog/pricing.py#L18) |
| B36 | Tax is 8.5% (850 bps) applied to discounted merchandise excluding shipping | INFERRED | 1.00 | [shoplog/pricing.py:19](../../shoplog/pricing.py#L19) |
| B37 | Orders in the legacy_vip tier may be refunded up to 90 days after delivered_at | INFERRED | 0.87 | [shoplog/refunds.py:7](../../shoplog/refunds.py#L7) |
| B38 | Orders in all other tiers may be refunded up to 30 days after delivered_at | INFERRED | 0.98 | [shoplog/refunds.py:8](../../shoplog/refunds.py#L8) |
| B39 | Items in the clearance category are final sale and are never eligible for refund, so clearance line items have refund_status=none | OBSERVED | 0.90 | INV-029 (n=8399), [shoplog/refunds.py:9](../../shoplog/refunds.py#L9) |
| B40 | Only line items whose refund_status is none can be refunded; already refunded lines are excluded | INFERRED | 1.00 | [shoplog/refunds.py:29](../../shoplog/refunds.py#L29) |
| B41 | After a refund the order status becomes partially_refunded if refundable lines remain, otherwise it becomes refunded, so partially_refunded and refunded orders have refund_total > 0 | OBSERVED | 0.90 | INV-008 (n=646), INV-012 (n=1145), [shoplog/refunds.py:60](../../shoplog/refunds.py#L60) |
| B42 | An order must include all 5 fields: customer_id, status, items, total and placed_at | INFERRED | 1.00 | [shoplog/schema.py:14](../../shoplog/schema.py#L14) |
| B43 | Each order must contain at least 1 item | INFERRED | 1.00 | [shoplog/schema.py:28](../../shoplog/schema.py#L28) |
| B44 | Order total must be 0 or greater | INFERRED | 1.00 | [shoplog/schema.py:29](../../shoplog/schema.py#L29) |
| B45 | Order status must be one of 7 values: placed, paid, shipped, delivered, cancelled, partially_refunded, refunded | INFERRED | 0.97 | [shoplog/schema.py:18](../../shoplog/schema.py#L18) |
| B46 | A customer can use the same coupon_code only once; orders without a coupon_code are exempt | INFERRED | 1.00 | [shoplog/schema.py:54](../../shoplog/schema.py#L54) |
| B47 | Customer email addresses must be unique ignoring case (English collation strength 2) | INFERRED | 0.99 | [shoplog/schema.py:9](../../shoplog/schema.py#L9) |
| B48 | Only carts with status active automatically expire at expires_at (expireAfterSeconds 0) | INFERRED | 1.00 | [shoplog/schema.py:67](../../shoplog/schema.py#L67) |
| B49 | Product text search ranks name with weight 10, tags with weight 5 and description with weight 1 | INFERRED | 0.98 | [shoplog/schema.py:77](../../shoplog/schema.py#L77) |
| B50 | Tax is absent before ~2021-01-01 and present as int from then on (placed_at) | OBSERVED | 0.99 | INV-026 (n=25000) |

### Code that never ran

- **F11** (Atlas Search ($search with a search index)) appears at [shoplog/catalog.py:10](../../shoplog/catalog.py#L10), [shoplog/catalog.py:22](../../shoplog/catalog.py#L22), [shoplog/schema.py:106](../../shoplog/schema.py#L106), but no operation in the query log used it. It may be dead code. Confidence: 0.96 (subq-snap-1.1, single source).
- **X:graphLookup** (graphLookup) appears at [shoplog/legacy_reports.py:10](../../shoplog/legacy_reports.py#L10), but no operation in the query log used it. It may be dead code. Confidence: 0.57 (subq-snap-1.1, single source).

## How far to trust this

- **Self-check:** MongoDB against itself gave 30/30 equal probes, 397/397 equal reads, and 353/353 equal writes with 0 final-state differences. So the differences above don't come from the comparison itself.
- **Probes:** 30 ran, generated by muse-spark-1.3. MongoDB is the oracle: a probe counts only if it ran cleanly on MongoDB and predicted its result. 0 probe(s) failed that check after retries and were excluded, not counted against the target.
- **Code evidence:** source is `muse_cached`. Citations are checked by a program. 0 model citation(s) were dropped because the cited line didn't contain the quoted token.
- **Replay coverage:** the log has 1,554 entries. Not replayed: 205 sync worker bookkeeping; 60 inside a transaction (covered by probes); 57 change stream (covered by probes); 5 insert: the profiler doesn't keep the documents; 1 cursor maintenance. Reads are capped per operation shape.
- **Two different products:** FerretDB 2 runs on Postgres with an open-source DocumentDB extension. AWS DocumentDB is a separate engine with a similar name. FerretDB's results say nothing about AWS DocumentDB.
- **DocumentDB rows are INFERRED:** we read AWS's docs for engine 8.0.2 and compared them with this app's usage. Nothing ran there. Set `TARGET_URI` to a real cluster to replace them with observed results.
- **Fair by construction:** the same pipeline, thresholds and assumptions run on every workload. ShopLog was deliberately built to depend on MongoDB-specific features, and the control app wasn't.
- **Not modeled:** data transfer, backups, support plans and committed-use discounts. Rewrite hours are model estimates, and the risk inputs are assumptions.

Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `20261107T224024Z`
