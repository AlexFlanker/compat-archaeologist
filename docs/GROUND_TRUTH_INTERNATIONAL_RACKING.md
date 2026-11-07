# International Racking — The Legacy Business (Ground Truth / Answer Key)

> ⚠️ **This is the ANSWER KEY. Never give this file to the agent.**
> The agent only sees the evidence: the database, the query logs and the code snippets.
> This file says what it *should* find, so we can score it.
>
> International Racking is a **fictional company** invented today for this hackathon. All data is synthetic, so we own it.
> Industry knowledge is welcome. Real company or customer data is not.

> **Locked decisions — Final (explicitly accepted by the human, 2026-11-07).**
> Settled by the human; recorded here so the generator and scoring stay unambiguous.
> 1. **Role in the project:** International Racking is a **second, optional dataset**, used to show that the archaeology carries over to an app the pipeline has never seen. The main demo is ShopLog (see the [README](../README.md)). This dataset isn't wired into the pipeline yet. An earlier draft company (NorthPeak) was parked and is not in this repo.
> 2. **Plain language:** the team does not know manufacturing/logistics deeply. Presenter-facing pitch and Q&A wording stays plain; avoid trade jargon the presenter cannot defend live.
> 3. **Generator defaults:** (a) free-freight line is checked against the stored **list price**; (b) `tier` is `null` unless distributor (`"D"`), `customer_type` and `tier` must never disagree; (c) generator guarantees **~600 Luxembourg orders** so the R5 pattern has zero-exception weight; (d) San Francisco orders are **invoiced in EUR** from Antwerp — add no currency field.
> Status: Final. Changes to this section need a new explicit decision.
> **Amendments (human, 2026-11-07, stage 2):** (1) finance revenue queries filter `status: {$in: [1,2,3]}`; (2) R9 PO rule applies only from 2018 onward, earlier key-retail orders have normal instructions; (3) R7 30-day expiry applies to status-0 records of every year; (4) VD- accounts are never distributors. Reflected in R3/R7/R9, the data model, and the query-log notes below.

---

## The company in 30 seconds

**International Racking** is a B2B manufacturer of **made-to-order metal racking and shelving**, founded in 2014 in Antwerp, Belgium, by Pieter Vos. It sells to warehouses, logistics firms, retailers and distributors across Belgium, the Netherlands, France, Luxembourg and Germany. In 2021 it opened a **US sales office in San Francisco, California**. Over 10 years it has had about **1,500 business customers** and **25,000 orders**, typically worth €3,000–15,000 each.

A freelancer built the first order system on MongoDB in 2014. Five developers have come and gone since. **Nobody wrote anything down.** International Racking now wants to move to a modern system, and the new CTO wants AI to do the migration.

**Pitch line:** *"Every business decision International Racking ever made left a fossil in its data. Our agent digs them up, and proves it got them right."*

---

## The rock layers (timeline)

| Year | What happened in the business | The fossil it left in the data | Rule |
|---|---|---|---|
| 2014 | Founded in Antwerp. A freelancer builds the order system | Prices as text in euros (`"4249.50"`). Status codes 0–4. Sizes in mm, loads in kg, with **no unit stored** | R1, R2, R4 |
| 2014 → 2023 | Three developers, three ways to "delete" a contact | `deleted: "Y"`, then `is_deleted: true`, then `archived_at` | R6 |
| 2016 | First Luxembourg customers. **No Luxembourg sales lead is ever hired.** When problems come up, the French sales lead steps in, because Luxembourg clients speak French | Luxembourg accounts have no sales lead, yet every Luxembourg order is handled by the French lead | R5 |
| 2016 | Distributors get 22% off list price | The discount only exists in the pricing code | R8 |
| 2016 | A developer tests the configurator with a **100-metre-tall shelf** for €9,999,999.99 | Test orders mixed in with real ones | R10 |
| 2017 | Quotes expire after 30 days because steel prices move too fast | Expiry is never stored, only computed in sales code | R7 |
| 2018 | International Racking buys its distributor **Vandaele Distribution**. Contract clause: those accounts get **free freight forever** | 140 accounts with IDs starting `VD-` | R3 |
| 2018 | Big retail chains demand their purchase-order (PO) number on every invoice | Sales staff type it into the delivery-instructions box | R9 |
| 2019 | A new developer switches money to whole cents | `price` (text euros) becomes `price_cents` (integer). One week has both | R1 |
| 2021 | **Steel price crisis**: cancelled orders keep the customer's deposit as a credit note | A new status code 5 that nobody documented | R2 |
| 2021 | **San Francisco office opens**: US orders are entered in **inches and pounds** in the same fields | Same fields, different units, still no unit stored | R4 |

---

## The 10 hidden rules

**Legend:** ⭐ = star of the live demo · 🪤 = trap (the obvious guess is wrong, and the replay check must overturn it)

| # | Name | Where the clue is | Agent should label it | Difficulty |
|---|---|---|---|---|
| R1 | The Great Cents Switch | Data | Observed (format) + Inferred (unit) | Easy, with a twist |
| R2 | The Missing Status 5 | Code + data | 0–4 Observed · 5 Inferred | Medium |
| R3 | ⭐ The Vandaele Handshake | Data only | Inferred | Medium |
| R4 | ⭐🪤 Pounds or Kilos? | Data + query log | Inferred | Hard |
| R5 | ⭐🪤 Who Owns Luxembourg? | Data + query log | What: Inferred → Observed · Why: Inferred only | Hard |
| R6 | The Ghost Contacts | Data + code | Partly Observed, partly Inferred | Medium |
| R7 | The Zombie Quotes | Code | Observed | Medium |
| R8 | The Distributor Discount | Code | Observed | Easy (warm-up) |
| R9 | The PO Number in the Delivery Notes | Data + query log | Inferred → upgraded to Observed | Medium |
| R10 | ⭐ The 100-Metre Shelf | Query log | Observed | Medium |

---

### R1 — The Great Cents Switch
- **Story:** In July 2019 a new developer decided that storing money as text was "unprofessional" and switched to whole cents. He never converted the old orders. For the week of the switchover, the system wrote both formats.
- **The hidden rule:**
  - Orders **before 2019-07-01** store `price` and `freight` as **text in euros** (`"4249.50"`).
  - From **2019-07-08** they store `price_cents` and `freight_cents` as **integers in cents** (`424950`).
  - From **2019-07-01 to 2019-07-07** orders carry **both**. It's the same amount written twice, not two charges.
- **Where the clues are:** The data shows the field names and types, their sizes, and the dates.
- **Agent should label it:** Observed (two formats are visible) + Inferred (that `424950` means €4,249.50, and that the overlap week isn't double).
- **What a naive AI migration breaks:** It ignores the text prices, so revenue from 2014 to 2019 drops to **€0**. Or it adds both fields during the overlap week and **double-counts** about 60 orders.
- **Test question (replay):** *"What was our revenue in each year from 2014 to 2024?"*
- **Pitch line:** *"Five years of revenue, gone in one line of code."*

### R2 — The Missing Status 5
- **Story:** The 2014 code defines order statuses 0–4. In April 2021, during the steel price crisis, many customers cancelled. Instead of refunding their 30% deposit, International Racking issued a **credit note** for future orders. A developer added status 5 for this and left the company two months later.
- **The hidden rule:** 0 = Quote · 1 = Confirmed (deposit paid) · 2 = In production · 3 = Shipped · 4 = Cancelled (deposit refunded) · **5 = Cancelled, deposit kept as a credit note**. Status-5 orders always have a `credit_note_cents` field and never appear before April 2021.
- **Where the clues are:** The code snippet lists 0–4. The data shows the status-5 pattern.
- **Agent should label it:** 0–4 Observed (from code) · 5 Inferred (from data).
- **What a naive AI migration breaks:** It maps status 5 to "Unknown" or merges it with "Cancelled". The money the company still owes customers in credit notes **disappears from the books**.
- **Test question (replay):** *"How much do we owe customers in open credit notes?"*
- **Pitch line (MBA angle):** *"A credit note is a debt. Lose status 5 and a liability vanishes from your balance sheet."*

### R3 — ⭐ The Vandaele Handshake
- **Story:** In 2018 International Racking bought its biggest distributor, Vandaele Distribution. To keep Vandaele's customers, the acquisition contract promised them **free freight forever**. The developer imported those accounts with IDs starting `VD-` and built the rule into checkout code that no longer exists. The contract sits in a drawer, and nobody in IT has read it.
- **The hidden rule:** Accounts whose ID starts with `VD-` **never pay freight**, whatever the order size. **VD- accounts are never distributors**: their `tier` is never `"D"` and `customer_type` is never `"distributor"` (the 60 distributor accounts are all non-VD), so the R8 discount never interacts with the freight promise.
- **Where the clues are:** Only the data. Every order from a `VD-` account has freight of 0, even small orders, with no exceptions.
- **Agent should label it:** Inferred, with high confidence because the pattern has zero exceptions.
- **What a naive AI migration breaks:** The new system "cleans up" IDs into plain numbers, the prefix is lost, and 140 accounts start paying €290 freight on every order under €5,000.
- **Test question (replay):** *"How much freight did Vandaele accounts pay in 2023?"* (Correct answer: €0.)
- **Pitch line:** *"140 of our oldest accounts. A clause in an acquisition contract. Breached by a migration nobody checked."*

### R4 — ⭐🪤 Pounds or Kilos? (THE TRAP)
- **Story:** In 2021 International Racking opened a sales office in **San Francisco**. US sales staff typed sizes in **inches** and load ratings in **pounds** into the **same fields** Europe used for millimetres and kilograms. No unit was ever stored.
- **The hidden rule:** For orders with `office: "SF"`, `width` / `depth` / `height` are in **inches** and `load_capacity` is in **pounds**. Everything else is millimetres and kilograms.
- **Where the clues are:**
  - **Data:**
    - US sizes are round inch numbers: 36, 48, 72.
    - EU sizes are round millimetre numbers: 900, 1200, 1800.
    - A 36-millimetre-wide shelf doesn't exist.
  - **Query log:** every month since July 2021, Antwerp's production planner pulls all open orders that need reinforced beams. The query uses `load_capacity >= 300` for Antwerp orders but `>= 662` for SF orders. 662 = 300 kg in pounds. The planner did the conversion by hand, and that monthly query is **the only place the conversion ever existed**.
- **Agent should label it:** Inferred. The log never says "pounds"; the agent has to work out that 662 ÷ 300 = 2.2046, the pounds-per-kilo ratio.
- **🪤 The trap:** Spotting the inches is easy, because a 3.6 cm shelf is obviously impossible. The tempting half-right guess is **"sizes are inches, but loads are still kilos"**, because "500" is a perfectly normal load in kilos too. Replaying the production planner's query exposes it: the old system returns its list, and the half-right migration returns **497 extra shelves**, all rated 500 lb. While investigating the mismatch, the agent spots the 662 and **overturns its own guess**. **This is the live "aha" moment of the demo.**
- **⚙️ Note for the engineer:** a unit mistake applied consistently survives a replay whose queries are translated *mechanically* (the US "≥ 1000" query matches either way). The replay must compare against the **business question** ("which shelves can hold 300 kg?") or against a mixed-unit query like the production one, or this trap can never fire.
- **What a naive AI migration breaks:** A shelf rated for **500 lb (227 kg)** is migrated as **500 kg**, more than twice its real capacity. A warehouse manager loads it accordingly.
- **Test question (replay):** *"Which shelves can safely hold a 300 kg pallet?"*
- **Pitch line:** *"A migration that turns 500 pounds into 500 kilos isn't a bug. It's a warehouse collapse."* Bonus for the room: *"...and our fictional company opened its US office right here in San Francisco."*

### R5 — ⭐🪤 Who Owns Luxembourg?
- **Story:** International Racking started selling in Luxembourg in 2016 but **never hired a Luxembourg sales lead**. Whenever a Luxembourg customer had a problem, the **French sales lead** stepped in, because most Luxembourg clients speak French. Over time that simply became how things worked: Luxembourg is part of the French team. Nobody ever wrote it down, and the org chart still has no Luxembourg box.
- **The hidden rule:** Accounts in Luxembourg (`country: "LU"`) belong to the **French sales team**. The French sales lead (`FR-01`) handles their orders and problems, and Luxembourg revenue counts toward France's targets and commission.
- **Where the clues are:**
  - **Data:** the `sales_leads` list has **no Luxembourg entry**, and Luxembourg accounts have an **empty** `sales_lead_id`. Yet **every** Luxembourg order has `handled_by: "FR-01"`.
  - **Query log:** the quarterly commission report groups `country: {$in: ["FR", "LU"]}` together as "France".
- **Agent should label it:**
  - **What** (Luxembourg belongs to the French team): Inferred from the data, then **upgraded to Observed** when it finds the commission query.
  - **Why** (shared French language): it can **only ever be Inferred**. It's a hypothesis the agent may propose with lower confidence. Bonus point if it does.
- **🪤 The trap:** The obvious, textbook guess is **"Luxembourg is Benelux, so it belongs with Belgium."** It's logical, and it's wrong. Replaying the commission report shows France's numbers don't match, and the agent has to overturn its guess.
- **What a naive AI migration breaks:** The new CRM requires every account to have an owner and assigns owners by geography: Luxembourg → Benelux → the Belgian sales lead.
  - The French lead's commission **drops overnight**, and your best salesperson starts updating their LinkedIn.
  - Luxembourg clients start getting calls from a Dutch-speaking rep in Antwerp.
  - The regional profit-and-loss reports are wrong for both France and Belgium.
- **Test questions (replay):** *"What revenue counts toward the French team's 2023 commission?"* · *"Who handles a complaint from a Luxembourg customer?"*
- **Pitch line:** *"Not every legacy rule lives in the code. Some live in the org chart, and in who speaks French."*

### R6 — The Ghost Contacts
- **Story:** Three developers, three ways to "delete" a customer contact, and nobody cleaned up.
  - 2014: `deleted: "Y"`. Watch out: `deleted: "N"` means **active**.
  - 2018: `is_deleted: true`.
  - 2022: `archived_at: <date>`.
- **The hidden rule:** A contact is gone if **any** of these is true: `deleted == "Y"`, `is_deleted == true`, or `archived_at` exists.
- **Where the clues are:** The data has three different fields. A 2019 code snippet only checks `is_deleted`, which is partial evidence.
- **Agent should label it:** `is_deleted` Observed (from code) · the other two Inferred.
- **What a naive AI migration breaks:** If it checks only one convention, hundreds of contacts who asked to be deleted get marketing emails again. If it reads `deleted: "N"` as deleted, about 600 active contacts vanish.
- **Test question (replay):** *"How many active contacts can we email?"*
- **Pitch line:** *"Emailing people who asked to be deleted isn't a bug. Under GDPR it can cost up to 4% of global revenue."*
- **🎤 Judge Q&A bonus:** The 2019 code itself ignores the old `deleted: "Y"` flag, so **the old system already had this bug.** Should a migration copy old bugs? Our agent **flags** it instead of silently "fixing" it, because proving equivalence means knowing exactly where you're *choosing* to differ.

### R7 — The Zombie Quotes
- **Story:** Quotes and orders live in the same collection, and a quote is just status 0. Since 2017, quotes **expire after 30 days**, because steel prices move too fast to honour old quotes. The expiry was never stored anywhere; the sales code just hides old quotes.
- **The hidden rule:** A status-0 record older than 30 days is an **expired quote**, not an open opportunity. **This applies to status-0 records of every year, including years before the 2017 snippet was written** — the snippet documents the rule, it does not start it. The generator must plant status-0 records in every year, 2014–2024.
- **Where the clues are:** The code snippet (`sales/open_quotes.js`).
- **Agent should label it:** Observed, with a citation to the file and line.
- **What a naive AI migration breaks:** About 3,000 expired quotes come back to life as "open". The sales pipeline looks **millions of euros bigger** than it is, and the revenue forecast is wrong.
- **Test question (replay):** *"What is the total value of open quotes on 2024-06-30?"*
- **Pitch line (MBA angle):** *"Your pipeline looks €31M bigger than it is. Good luck explaining that forecast to the board."* (Actual generated data: 3,401 zombie quotes × €9,047 average = €30.8M at the 2024-06-30 snapshot. See `tools/international_racking/GENERATION_SUMMARY.md`.)

### R8 — The Distributor Discount (warm-up)
- **Story:** Since 2016, distributors (`tier: "D"`) get **22% off** list price. Each order stores the **list price**. The discount only happens inside the pricing code.
- **The hidden rule:** For distributor orders, **the amount actually invoiced = list price × 0.78**.
- **Where the clues are:** The code snippet (`pricing/distributor.js`).
- **Agent should label it:** Observed, with a citation to the file and line.
- **What a naive AI migration breaks:** It reports distributor revenue at list price, which overstates it by 28%.
- **Test question (replay):** *"How much did distributors actually pay us in 2020?"*
- **Pitch line:** Shows the agent **cites its evidence**: *"Found in pricing/distributor.js, line 5."*

### R9 — The PO Number in the Delivery Notes
- **Story:** From 2018, big retail chains rejected any invoice without their purchase-order number on it. There was no field for it, so sales staff typed it into the **delivery-instructions** box. Finance has relied on it ever since.
- **The hidden rule:** For key retail accounts (`customer_type: "retail_key"`) **on orders from 2018 onward**, `delivery_instructions` holds a **PO number** (`"PO 4471-A"`), not instructions. Key-retail orders **before 2018 have normal delivery instructions** — the generator must plant those too, or the agent will learn a rule with no start date.
- **Where the clues are:**
  - **Data:** every key-retail delivery instruction **from 2018 onward** starts with "PO "; pre-2018 ones read as ordinary instructions.
  - **Query log:** finance searches `delivery_instructions` for `/^PO /`.
- **Agent should label it:** Inferred, then upgraded to Observed when it finds the finance query.
- **What a naive AI migration breaks:** The PO number gets printed on the truck driver's delivery note and **dropped from the invoice**. The retail chains reject the invoices, and payment is delayed for weeks.
- **Test question (replay):** *"List all key-retail orders in Q4 2023 with their PO numbers."*
- **Pitch line (MBA angle):** *"No PO number, no payment. That's a working-capital problem, not an IT problem."*

### R10 — ⭐ The 100-Metre Shelf
- **Story:** In September 2016 a developer tested the shelf configurator by ordering a shelf **100 metres tall** (taller than the Statue of Liberty) for **€9,999,999.99**, under a fake `@intl-racking.test` email. It was never deleted, and neither were about 200 other test orders. Finance learned to exclude them from every report.
- **The hidden rule:** Orders from emails ending in `@intl-racking.test` are **test orders** and must be left out of revenue and order counts.
- **Where the clues are:** The query log. Every finance revenue query excludes that email domain.
- **Agent should label it:** Observed (the logs show the exclusion every time).
- **What a naive AI migration breaks:** It counts the test orders, and 2016 jumps to €23.2M, "the best year in company history", ahead of the real record (2023, €20.2M). About €11M of it is fake.
- **Test question (replay):** *"What was our revenue in 2016?"*
- **Pitch line:** *"A naive migration says 2016 was our best year ever. It was a developer ordering a shelf taller than the Statue of Liberty."*

---

## Evidence pack: what the agent DOES get
*(Suggested format. The engineer has the final say on field names.)*

### Suggested data model (old system, MongoDB)

**`sales_leads`**
- `_id`: `"BE-01"`, `"NL-01"`, `"FR-01"`, `"DE-01"`, `"US-01"` (from 2021). **No Luxembourg lead** (R5).
- `name`, `country`

**`accounts`** (business customers)
- `_id`: integer (`10234`), or text `"VD-0457"` for Vandaele accounts
- `company_name`, `country` (`"BE"` | `"NL"` | `"FR"` | `"LU"` | `"DE"` | `"US"`)
- `customer_type` (`"end_user"` | `"retail_key"` | `"distributor"`), `tier` (`"D"` for distributors, otherwise `null`; the two must never disagree. **VD- accounts are never distributors** — `tier` is never `"D"` for them)
- `sales_lead_id`: **empty for Luxembourg accounts**

**`contacts`** (people at those companies)
- `account_id`, `email`, `name`
- Deletion flags, which vary by era: `deleted: "Y"/"N"` · `is_deleted: true` · `archived_at: Date`

**`orders`** (quotes and orders together)
- `_id`, `account_id`, `contact_email`, `country`, `customer_type`, `tier` (copied from the account)
- `handled_by`: sales lead ID. Always `"FR-01"` for Luxembourg orders.
- `office`: missing before 2021 (meaning Antwerp) | `"ANT"` | `"SF"`
- `order_date`: a date
- `width`, `depth`, `height`, `load_capacity`: numbers, **with no unit stored**
- `price`, `freight`: text in euros (before 2019-07-08) | `price_cents`, `freight_cents`: integers (from 2019-07-01; both formats during the overlap week)
- Freight (official price list, not a hidden rule): free on orders ≥ €5,000, otherwise €290. `VD-` accounts are the exception (R3).
- `status`: 0–5 · `credit_note_cents` (status 5 only)
- `delivery_instructions`: text

### Code snippets: the "fossils"

`legacy/order_status.php` (2014). Covers R2 statuses 0–4 only; status 5 is deliberately missing.
```php
<?php
// order status codes -- DO NOT CHANGE, the production planning app reads these
function status_label($code) {
  switch ($code) {
    case 0: return "Quote";
    case 1: return "Confirmed";
    case 2: return "In production";
    case 3: return "Shipped";
    case 4: return "Cancelled";
    default: return "Unknown";
  }
}
```

`pricing/distributor.js` (2016). Covers R8, and shows that `price` was text (R1).
```js
// Distributors get 22% off list.
// Keep the LIST price in `price` -- the sales commission report needs it!
function invoicedAmount(order) {
  const list = parseFloat(order.price);
  return order.tier === "D" ? +(list * 0.78).toFixed(2) : list;
}
```

`sales/open_quotes.js` (2017). Covers R7.
```js
// steel prices move too fast -- quotes are only valid for 30 days
const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000;
function isOpenQuote(order, today = new Date()) {
  return order.status === 0 && (today - order.order_date) <= THIRTY_DAYS;
}
```

`crm/newsletter.js` (2019). Partial evidence for R6, and contains the old bug.
```js
// active contacts for the quarterly product newsletter
const recipients = await db.collection("contacts")
  .find({ is_deleted: { $ne: true } })
  .project({ email: 1, name: 1 })
  .toArray();
```

### Query log samples: clue lines to plant among about 2,000 ordinary queries
```
2023-04-03T07:30:00Z sales_ops db.orders.aggregate([{$match:{contact_email:{$not:/@intl-racking\.test$/}, country:{$in:["FR","LU"]}, status:{$in:[1,2,3]}, order_date:{$gte:ISODate("2023-01-01"), $lt:ISODate("2023-04-01")}}}, {$group:{_id:"FR", revenue:{$sum:"$price_cents"}}}])
2023-05-02T06:15:00Z production db.orders.find({status:{$in:[1,2]}, $or:[{office:{$ne:"SF"}, load_capacity:{$gte:300}}, {office:"SF", load_capacity:{$gte:662}}]}, {_id:1, office:1, load_capacity:1})
2023-11-30T14:02:44Z finance   db.orders.find({customer_type:"retail_key", delivery_instructions:/^PO /}, {delivery_instructions:1, price_cents:1})
2024-01-08T08:15:00Z finance   db.orders.aggregate([{$match:{contact_email:{$not:/@intl-racking\.test$/}, status:{$in:[1,2,3]}, order_date:{$gte:ISODate("2023-01-01")}}}, {$group:{_id:null, revenue:{$sum:"$price_cents"}}}])
2023-05-17T15:40:12Z us_sales  db.orders.find({office:"SF", load_capacity:{$gte:1000}, status:{$in:[1,2]}})
```
Notes:
- Add many similar finance queries over the years that exclude `@intl-racking.test`. R10 should be a *repeated* pattern, not a one-off. **Every finance revenue query also filters `status: {$in: [1,2,3]}`** (confirmed / in production / shipped only — quotes and cancelled orders are never revenue).
- Repeat the `sales_ops` FR+LU commission query every quarter, run 3 days after the quarter closes. It excludes test orders like every other revenue report. The replay uses it to catch the R5 trap.
- Repeat the `production` reinforced-beam query every month from July 2021 (42 lines). Its 300 kg / 662 lb split is what the replay uses to catch the R4 trap.

### Target counts for the data generator
These keep the demo numbers consistent. Update the pitch lines once the real data exists.

| What | Target |
|---|---|
| Business accounts | ~1,500 (including 60 distributors — **all non-VD** — and 25 key retail chains) |
| Accounts by country | BE ~450 · NL ~300 · FR ~220 · **LU ~45** · DE ~250 · US ~235 |
| Sales leads | 5 (BE, NL, FR, DE, US). **None for Luxembourg** |
| Vandaele `VD-` accounts | 140 |
| Contacts | ~4,000; deleted: 150 `deleted:"Y"` · 200 `is_deleted:true` · 120 `archived_at`, plus ~600 `deleted:"N"` (active) |
| Orders and quotes, 2014–2024 | ~25,000, typical value €3,000–15,000 |
| Expired "zombie" quotes | ~3,000 |
| Test orders (`@intl-racking.test`) | ~200, including **one 100,000 mm shelf at €9,999,999.99 on 2016-09-14** |
| Status-5 (credit note) orders | ~400, all from April 2021 onward |
| San Francisco (US) orders | ~3,500, from June 2021; sizes 36/48/72 in, loads 500/800/1,000 lb |
| EU sizes and loads | 900/1200/1800 mm; 150/250/350 kg per shelf level |
| Overlap-week orders (2019-07-01 → 07-07) | ~60 |

---

## Demo plan (3 minutes), if this dataset becomes a demo

> **Status: not built.** The pipeline can't run this plan yet. It has no loader for this dataset, no adapter for the shell-text query log, and no hypothesis → business-question replay → overturn loop. Don't present any of this as working. The live demo uses ShopLog.

**Show live:**
1. **R8 (warm-up):** the agent finds the distributor discount and cites the file and line.
2. **R4 (the trap):** the agent spots the inches but guesses the loads are still kilos. The replay finds mismatches, and the agent overturns its guess. *This is the loop working in front of the judges, with real safety stakes, in a San Francisco office.*
3. **R10 (laugh line):** "2016 was our best year ever... thanks to a shelf taller than the Statue of Liberty."

**Closing insight:** R5, *"Not every legacy rule lives in the code. Some live in the org chart."* The agent found that Luxembourg belongs to the French team and labelled the *why* as a hypothesis.

**Tell, don't show:** R3, the Vandaele contract clause, as the business hook in the intro.

**End on the scoreboard** (filled in live from the real run):

| Rule | Found? | Label correct? | Replay matches? |
|---|---|---|---|
| R1 The Great Cents Switch | | | |
| R2 The Missing Status 5 | | | |
| R3 The Vandaele Handshake | | | |
| R4 Pounds or Kilos? 🪤 | | | |
| R5 Who Owns Luxembourg? 🪤 | | | |
| R6 The Ghost Contacts | | | |
| R7 The Zombie Quotes | | | |
| R8 The Distributor Discount | | | |
| R9 The PO Number in the Delivery Notes | | | |
| R10 The 100-Metre Shelf | | | |
| **Total** | **_ / 10** | **_ / 10** | **_ % of queries** |

## Judge Q&A cheat sheet
- **"How do you know it's actually right?"** We planted 10 rules ourselves and scored the agent blind against this answer key. On top of that, every migration is proven by replaying real queries on the old and new systems.
- **"Is this real company data?"** No. It's fully synthetic and created today, so we own all of it, and that's exactly what lets us *measure* accuracy.
- **"Can an agent really find an *organizational* rule?"** Yes, see R5. The *what* leaves traces: who handled the orders, and how the reports group countries. The *why* (language) can't be proven from data, so the agent labels it a hypothesis. That's the observed-vs-inferred split working as designed.
- **"Should a migration preserve old bugs?"** See R6. The agent flags bugs instead of silently fixing them, because equivalence means knowing exactly where you choose to differ.
- **"Who buys this?"** Mid-size manufacturers and distributors running 10–20-year-old order systems or ERPs, plus the consultancies that run their migrations. Every company that grew through acquisitions, new countries and new offices has fossils like these.
