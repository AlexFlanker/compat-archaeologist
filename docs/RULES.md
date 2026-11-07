# ShopLog business rules (fictional ground truth)

ShopLog is a fictional, 10-year-old e-commerce order system. None of this comes from a real company.

This file is the answer key. The extractors never read it, and the code carries no comments explaining these rules. At the end of a run, we score how many of them the pipeline recovered from evidence alone.

**Teammate: edit freely until 1:00 PM.** Change numbers or wording here, and tell the builder. After 1:00 PM the evidence gets regenerated once and frozen.

| # | Rule | Where it should show up |
|---|---|---|
| R1 | Tier discount on merchandise: bronze 0%, silver 5%, gold 10%, legacy_vip 15%. A legacy_vip order never takes a coupon. | Code; data (`legacy_vip` orders never carry `coupon_code`) |
| R2 | Coupon `WELCOME10` takes $10 off and works once per customer. | Partial unique index on `(customer_id, coupon_code)`; code |
| R3 | Shipping is free once discounted merchandise reaches $75. Below that it's a flat $7.95. | Code; data |
| R4 | Tax is 8.5% of discounted merchandise, and shipping is untaxed. Orders before 2021 have no `tax` field, because tax was folded into `total`. | Code; data (`tax` missing exactly before 2021) |
| R5 | Before 2018, money is stored as float dollars. From 2018 on, it's integer cents. Reports normalize both. | Data (mixed types split by date); query log (`$type` in pipelines); code |
| R6 | Order states: placed → paid → shipped → delivered. Only placed or paid can be cancelled. Only delivered or partially_refunded can move to a refund state. | Code; data |
| R7 | Refund window: 30 days after delivery, 90 days for legacy_vip. | Code |
| R8 | Clearance items are final sale and can't be refunded. | Code; data (clearance lines are never refunded) |
| R9 | `refund_total` is 0 unless the order is partially_refunded or refunded, and then it's above 0. `refunded` means every refundable line is refunded. | Data; code |
| R10 | Active carts expire 24 hours after the last touch. Converted carts are kept. | Partial TTL index on `carts`; code |

## Control app: "ShopLog Lite"

A small order-lookup service that uses only CRUD by `_id` and simple filters. It has no hidden rules beyond the shape of its data. The same pipeline runs on it to show that the agent can say "migration is fine".
