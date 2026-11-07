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

## The fossils (presenter notes)

Each rule is a decision someone made years ago and nobody wrote down. The stories are fiction, like the company, but each one is consistent with the data.

| # | The fossil | Pitch line |
|---|---|---|
| R1 | **The founding customers (2016).** Marketing's first hire gave the earliest 137 customers a "legacy_vip" tier: 15% off forever, and no coupons on top. The tier stayed after everyone who knew why it existed had left. | "137 customers get 15% off forever, and nobody remembers why." |
| R2 | **The coupon farmers (2019).** People opened new accounts to reuse WELCOME10. Instead of fixing sign-up, a developer added a partial unique index. | "This business rule isn't in the code. It's an index." |
| R3 | **Free shipping at $75 (2017).** A summer promotion that never ended. The only written spec is a constant in the pricing file. | "Every order over $75 ships free, and the only spec is a number in one file." |
| R4 | **The tax that hid in the total (until 2021).** Before 2021, tax was folded into `total`. The new accounting system needed it separate, but only for new orders. | "Half the orders have no tax field, and that's not a bug." |
| R5 | **The great cents switch (2018).** A payments rewrite stored new orders in integer cents. The old orders kept float dollars, and every report since has had to know the difference. | "Two money formats, one revenue report, and one line of code between them." |
| R6 | **The state machine nobody drew.** Shipped orders can't be cancelled. The only place that says so is a dictionary in `orders.py`. | "The whole order lifecycle lives in one dictionary." |
| R7 | **The VIP refund window.** VIPs get 90 days to return, everyone else 30. It never shows up in the data until a VIP returns something on day 45. | "Some rules only exist in code, so we label them inferred, not observed." |
| R8 | **Clearance means final.** Over 8,000 clearance line items, and not one refunded. | "The data knows the rule even when the docs don't." |
| R9 | **Refund totals tell the truth.** A refunded order always carries a refund total above zero, and nothing else does. | "An invariant the migration must keep, even when the CDC drops events." |
| R10 | **The carts that delete themselves (2020).** A TTL index removes abandoned carts after 24 hours, but keeps converted ones for analytics. | "The database deletes customer data every night, and the migration plan forgot it." |

## Control app: "ShopLog Lite"

A small order-lookup service that uses only CRUD by `_id` and simple filters. It has no hidden rules beyond the shape of its data. The same pipeline runs on it to show that the agent can say "migration is fine".
