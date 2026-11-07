# International Racking — Generation Summary (actual counts)

Generated 2026-11-07 by `tools/international_racking/generate.py` (seed 42, stdlib only), originally written by Meta Muse Code.
Spec: `docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md` (Final + stage-2 amendments A1–A4).
Re-running the generator reproduces byte-identical output (verified by MD5).

## What was generated

| File | Contents |
|---|---|
| `data/sales_leads.json` | 5 sales leads (BE, NL, FR, DE, US). No Luxembourg lead (R5) |
| `data/accounts.json` | 1,500 accounts |
| `data/contacts.json` | 4,000 contacts |
| `data/orders.json` | 25,001 orders/quotes (25,000 + hero test order) |
| `data/query_log.txt` | 2,061 log lines (2,000 ordinary + 61 planted clues) |
| `evidence/legacy/order_status.php` | Statuses 0–4 only (R2 fossil) |
| `evidence/pricing/distributor.js` | 22% distributor discount, line 5 = ×0.78 (R8) |
| `evidence/sales/open_quotes.js` | 30-day quote expiry (R7) |
| `evidence/crm/newsletter.js` | Partial deletion check, contains legacy bug (R6) |

## Actual counts vs targets

| What | Target | Actual |
|---|---|---|
| Accounts | ~1,500 | **1,500** (BE 450 · NL 300 · FR 220 · LU 45 · DE 250 · US 235) |
| VD- accounts | 140 | **140** (0 are distributors — A4; 2,380 VD orders, €0 freight on all) |
| Distributors / key retail | 60 / 25 | **60 / 25** (tier/type disagreements: 0) |
| Contacts | ~4,000 | **4,000** (deleted Y 150 · is_deleted 200 · archived 120 · deleted N/active 600) |
| Orders + quotes | ~25,000 | **25,001** |
| Test orders `@intl-racking.test` | ~200 | **200** incl. hero: 100,000 mm, €9,999,999.99, 2016-09-14 |
| Status-5 (credit note) | ~400 from Apr 2021 | **400**, 0 before Apr 2021, all with `credit_note_cents` |
| Status-0 quotes | zombie ~3,000 | **3,634** total, planted every year 2014–2024 (A3); **3,401 zombies** at 2024-06-30 (29 open), avg €9,047 = **€30.8M** |
| SF orders | ~3,500 from Jun 2021 | **3,500**, 0 before Jun 2021, dims 36/48/72 in, loads 500/800/1,000 lb |
| LU orders | ~600 (locked default) | **600**, all `handled_by: FR-01`, all 45 LU accounts have empty `sales_lead_id` |
| Overlap-week (both money formats) | ~60 | **47**, all dated 2019-07-01 → 07-07 |
| Query log | ~2,000 | **2,061**; 235 finance revenue aggregates, **all** filter `status:{$in:[1,2,3]}` (A1) and exclude `@intl-racking.test`; 16 quarterly FR+LU commission lines |

## Verification (independent check, all passed)

- R1: text-only 10,692 · cents-only 14,262 · both 47; no date outside overlap carries both.
- R3/R-freight: 0 freight-rule violations (free ≥ €5,000 list, else €290; VD always €0).
- R4: SF dims only in {36,48,72}; EU loads only {150,250,350} kg, widths {900,1200,1800} mm.
- R9 (A2): 124 pre-2018 key-retail orders, 0 with PO notes; all post-2018 key-retail orders carry `PO …`.
- Log syntax: 0 unbalanced lines.

## Known deltas from pitch text

- R7 pitch updated in the ground truth to **€31M** (actual €30.8M) — done.
- Overlap week is 47 orders vs ~60 target (sampling); R1 pitch "about 60" still fair, or regenerate with a different seed.

## Fixes by Claude (2026-11-07, after stage 2)

Verified with `check_ground_truth.py` (83 pass · 0 warnings · 0 failures). Three generator changes; accounts, contacts and sales leads are byte-identical, and the only order that changed is the hero.

1. **R10 pitch was false.** At €999,999.99, 2016 (€14.2M naive) never beat 2023 (€20.2M). The hero is now **€9,999,999.99**, so 2016 jumps to **€23.2M**, the "best year ever". Total fake revenue from test orders is now €11.3M.
2. **R4 trap was uncatchable.** A consistent "loads are still kilos" migration passes a mechanically translated replay of the single `us_sales` query. Added **42 monthly `production` queries** (Jul 2021 → Dec 2024) using `load_capacity >= 300` for Antwerp and `>= 662` for SF (300 kg in lb). Replaying it, the half-right migration returns **497 extra shelves** (all 500 lb).
3. **R5 commission queries counted test orders** (30 test orders sit on FR accounts, 2 on LU). They now exclude `@intl-racking.test` like every other revenue report, and run 3 days *after* each quarter closes (previously on the quarter's first days).
4. **Dates were plain strings.** Every logged `order_date:{$gte:ISODate(...)}` query would have matched nothing once loaded into MongoDB, so date-filtered replays (commission, revenue) would "pass" on empty results. `order_date` and `archived_at` are now written as Extended JSON `{"$date": ...}`; the data is otherwise identical.
5. **Moved the scoring side out of the agent's reach.** Agent inputs live in `datasets/international_racking/`; generator, checker and this summary live in `tools/international_racking/`; the answer key is in `docs/`.
