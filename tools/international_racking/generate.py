#!/usr/bin/env python3
"""
International Racking — legacy dataset generator (stage 2).

Stdlib only, deterministic (seed 42). Implements docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md
exactly, including the Final locked decisions and the four stage-2 amendments:
  A1 finance revenue queries filter status {$in:[1,2,3]}
  A2 R9 PO rule only from 2018; earlier key-retail orders have normal instructions
  A3 R7 30-day expiry applies to status-0 of every year (status-0 planted yearly)
  A4 VD- accounts are never distributors

Outputs (agent-visible evidence only — never the ground truth):
  data/sales_leads.json, accounts.json, contacts.json, orders.json, query_log.txt
  evidence/legacy/order_status.php, pricing/distributor.js,
           sales/open_quotes.js, crm/newsletter.js

Usage: python3 tools/international_racking/generate.py [--seed N]
Writes ONLY agent-visible files, into datasets/international_racking/.

Originally generated with Meta Muse Code (muse-wonder-1.0); fixes by Claude Code.
Dates are written as MongoDB Extended JSON ({"$date": ...}): load with
`mongoimport --jsonArray` or `bson.json_util.loads` so they become real dates.
"""
import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parents[2] / "datasets" / "international_racking"  # agent-visible output only
DATA = BASE / "data"
EV = BASE / "evidence"

COMPANIES = ["Warehouse", "Logistics", "Distribution", "Storage", "Freight", "Retail Group",
             "Depot", "Supply", "Trading", "Industries"]
FIRST = ["Jan", "Pieter", "Marie", "Lucas", "Emma", "Thomas", "Laura", "Simon", "Julie", "David",
         "Sarah", "Kevin", "Anna", "Marc", "Sophie", "Nicolas", "Eva", "Hugo", "Lotte", "Fien",
         "James", "Emily", "Michael", "Jessica", "Daniel", "Ashley", "Matthew", "Amanda"]
LAST = ["Peeters", "Janssens", "Maes", "Jacobs", "Willems", "Claes", "Dubois", "Moreau",
        "Laurent", "Simon", "Michel", "Garcia", "Meyer", "Wagner", "Becker", "Hoffman",
        "Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor", "Clark"]
NORMAL_NOTES = ["Deliver to loading dock B", "Call driver on arrival", "Forklift needed on site",
                "Deliver after 08:00 only", "Use side entrance", "Palletised delivery, dock 3",
                "Ring bell at warehouse office", "Leave with site manager"]


def eur_text(cents: int) -> str:
    return f"{cents / 100:.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    DATA.mkdir(parents=True, exist_ok=True)
    for d in ["legacy", "pricing", "sales", "crm"]:
        (EV / d).mkdir(parents=True, exist_ok=True)

    # ---------- sales leads (R5: no LU lead) ----------
    leads = [
        {"_id": "BE-01", "name": "Wim Claes", "country": "BE"},
        {"_id": "NL-01", "name": "Sanne Willems", "country": "NL"},
        {"_id": "FR-01", "name": "Claire Dubois", "country": "FR"},
        {"_id": "DE-01", "name": "Jonas Meyer", "country": "DE"},
        {"_id": "US-01", "name": "James Smith", "country": "US", "from": "2021"},
    ]
    lead_by_country = {"BE": "BE-01", "NL": "NL-01", "FR": "FR-01", "DE": "DE-01", "US": "US-01", "LU": ""}

    # ---------- accounts: exactly 1,500, country counts as specified ----------
    country_plan = [("BE", 450), ("NL", 300), ("FR", 220), ("LU", 45), ("DE", 250), ("US", 235)]
    accounts = []
    vd_left = 140          # all inside BE quota, never distributors (A4)
    dist_left = 60         # all non-VD
    retail_left = 25       # key retail
    next_id = 10000
    for country, count in country_plan:
        for _ in range(count):
            is_vd = country == "BE" and vd_left > 0 and rng.random() < (vd_left / max(count, 1)) * 1.6
            # deterministic correction: force remaining VD near end of BE block handled after loop
            if is_vd:
                vd_left -= 1
                aid = f"VD-{140 - vd_left:04d}"
                ctype, tier = "end_user", None
            else:
                aid = next_id; next_id += 1
                if dist_left > 0 and country != "LU" and rng.random() < 0.045:
                    ctype, tier = "distributor", "D"; dist_left -= 1
                elif retail_left > 0 and rng.random() < 0.02:
                    ctype, tier = "retail_key", None; retail_left -= 1
                else:
                    ctype, tier = "end_user", None
            accounts.append({
                "_id": aid,
                "company_name": f"{rng.choice(LAST)} {rng.choice(COMPANIES)}",
                "country": country, "customer_type": ctype, "tier": tier,
                "sales_lead_id": lead_by_country[country],
            })
    # enforce exact VD / distributor / retail counts if random drift missed
    def recount():
        vd = sum(1 for a in accounts if isinstance(a["_id"], str))
        di = sum(1 for a in accounts if a["customer_type"] == "distributor")
        rk = sum(1 for a in accounts if a["customer_type"] == "retail_key")
        return vd, di, rk
    vd_n, di_n, rk_n = recount()
    # top-up distributors / retail from plain end_user non-VD accounts
    for a in accounts:
        if di_n >= 60: break
        if not isinstance(a["_id"], str) and a["customer_type"] == "end_user":
            a["customer_type"], a["tier"] = "distributor", "D"; di_n += 1
    for a in accounts:
        if rk_n >= 25: break
        if not isinstance(a["_id"], str) and a["customer_type"] == "end_user":
            a["customer_type"] = "retail_key"; rk_n += 1
    if vd_n != 140:  # convert earliest BE end_user accounts to VD
        need = 140 - vd_n
        for a in accounts:
            if need == 0: break
            if a["country"] == "BE" and not isinstance(a["_id"], str) and a["customer_type"] == "end_user":
                a["_id"] = f"VD-{vd_n + 1:04d}"; vd_n += 1; need -= 1
    accounts.sort(key=lambda a: str(a["_id"]))
    acc_by_id = {a["_id"]: a for a in accounts}

    # ---------- contacts: exactly 4,000 with exact deletion-flag counts (R6) ----------
    contacts = []
    plain_accounts = accounts[:]
    for i in range(4000):
        acc = plain_accounts[i % len(plain_accounts)]
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.lower().replace(' ', '.')}{i}@{(acc['company_name'].lower().replace(' ', '')[:14])}.example.com"
        contacts.append({"_id": 50000 + i, "account_id": acc["_id"], "email": email, "name": name})
    # exact flags: 150 Y, 200 is_deleted, 120 archived, 600 N (active), rest unflagged
    idx = list(range(4000)); rng.shuffle(idx)
    for j in idx[:150]: contacts[j]["deleted"] = "Y"
    for j in idx[150:350]: contacts[j]["is_deleted"] = True
    for j in idx[350:470]:
        contacts[j]["archived_at"] = f"20{rng.randint(22, 24)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T00:00:00Z"
    for j in idx[470:1070]: contacts[j]["deleted"] = "N"
    contacts_by_account = {}
    for c in contacts:
        contacts_by_account.setdefault(c["account_id"], []).append(c["email"])

    # ---------- orders: exactly 25,000 with country quotas ----------
    # US (SF) 3,500 · LU 600 (locked default) · rest split over BE/NL/FR/DE accounts
    eu_pool = [a for a in accounts if a["country"] in ("BE", "NL", "FR", "DE")]
    lu_pool = [a for a in accounts if a["country"] == "LU"]
    us_pool = [a for a in accounts if a["country"] == "US"]
    plan = []
    plan += [("US", a) for a in (us_pool[i % len(us_pool)] for i in range(3500))]
    plan += [("LU", a) for a in (lu_pool[i % len(lu_pool)] for i in range(600))]
    for i in range(25000 - 3500 - 600):
        plan.append(("EU", eu_pool[i % len(eu_pool)]))
    rng.shuffle(plan)

    start, end = date(2014, 1, 6), date(2024, 12, 20)
    span = (end - start).days
    cents_both = date(2019, 7, 1)      # R1 overlap week start
    cents_only = date(2019, 7, 8)      # R1 cents-only from here
    sf_from = date(2021, 6, 1)         # SF orders from June 2021
    status5_from = date(2021, 4, 1)    # R2

    def rand_date(country_tag):
        if country_tag == "US":
            d0 = sf_from
            return d0 + timedelta(days=rng.randint(0, (end - d0).days))
        return start + timedelta(days=rng.randint(0, span))

    orders = []
    status5_left = 400
    test_left = 199  # + hero = 200
    for i, (tag, acc) in enumerate(plan):
        odate = rand_date(tag)
        oid = 700000 + i
        is_sf = tag == "US"
        price_c = rng.randint(3000, 15000) * 100 // 100 * 1  # euros -> cents below
        price_c = rng.randint(300000, 1500000)
        # R3 + freight on LIST price (locked default): VD always 0
        if isinstance(acc["_id"], str) and acc["_id"].startswith("VD-"):
            freight_c = 0
        else:
            freight_c = 0 if price_c >= 500000 else 29000

        # status: plant status-0 every year (A3); status-5 only from Apr 2021
        r = rng.random()
        year_ok_s5 = odate >= status5_from
        if year_ok_s5 and status5_left > 0 and r < 0.022:
            status = 5; status5_left -= 1
        elif r < 0.155:
            status = 0
        elif r < 0.40:
            status = 1
        elif r < 0.58:
            status = 2
        elif r < 0.90:
            status = 3
        else:
            status = 4

        is_test = False
        if test_left > 0 and rng.random() < 0.008 and odate.year != 2016 or (test_left > 0 and i > 24800):
            is_test = True; test_left -= 1

        emails = contacts_by_account.get(acc["_id"], [f"info@acc{oid}.example.com"])
        contact_email = f"dev{rng.randint(1, 8)}@intl-racking.test" if is_test else rng.choice(emails)

        if is_sf:
            width = rng.choice([36, 48, 72]); load = rng.choice([500, 800, 1000])
        else:
            width = rng.choice([900, 1200, 1800]); load = rng.choice([150, 250, 350])
        order = {
            "_id": oid, "account_id": acc["_id"], "contact_email": contact_email,
            "country": acc["country"], "customer_type": acc["customer_type"], "tier": acc["tier"],
            "handled_by": "FR-01" if acc["country"] == "LU" else (lead_by_country[acc["country"]] or "FR-01"),
            "order_date": odate.isoformat(),
            "width": width, "depth": rng.choice([36, 48, 72] if is_sf else [900, 1200, 1800]),
            "height": rng.choice([36, 48, 72] if is_sf else [900, 1200, 1800]),
            "load_capacity": load, "status": status,
        }
        if odate >= date(2021, 1, 1):
            order["office"] = "SF" if is_sf else "ANT"
        if status == 5:
            order["credit_note_cents"] = round(price_c * 0.30)
        # R1 money eras
        if odate < cents_both:
            order["price"] = eur_text(price_c); order["freight"] = eur_text(freight_c)
        elif odate < cents_only:
            order["price"] = eur_text(price_c); order["freight"] = eur_text(freight_c)
            order["price_cents"] = price_c; order["freight_cents"] = freight_c
        else:
            order["price_cents"] = price_c; order["freight_cents"] = freight_c
        # R9 (A2): PO only for retail_key from 2018 onward
        if acc["customer_type"] == "retail_key" and odate >= date(2018, 1, 1):
            order["delivery_instructions"] = f"PO {rng.randint(1000, 9999)}-{rng.choice('ABCDE')}"
        elif rng.random() < 0.45:
            order["delivery_instructions"] = rng.choice(NORMAL_NOTES)
        orders.append(order)

    # force exact status-5 count if drift
    s5 = sum(1 for o in orders if o["status"] == 5)
    if s5 < 400:
        for o in orders:
            if s5 >= 400: break
            if o["order_date"] >= "2021-04-01" and o["status"] == 4:
                o["status"] = 5
                pc = o["price_cents"] if "price_cents" in o else int(float(o["price"]) * 100)
                o["credit_note_cents"] = round(pc * 0.30); s5 += 1
    # hero test order (R10): 100,000 mm shelf, 2016-09-14, pre-cents text price
    eu_acc = eu_pool[0]
    orders.append({
        "_id": 999999, "account_id": eu_acc["_id"], "contact_email": "dev2@intl-racking.test",
        "country": eu_acc["country"], "customer_type": eu_acc["customer_type"], "tier": eu_acc["tier"],
        "handled_by": lead_by_country[eu_acc["country"]], "order_date": "2016-09-14",
        "width": 1200, "depth": 900, "height": 100000, "load_capacity": 350, "status": 3,
        "price": "9999999.99", "freight": "0.00", "delivery_instructions": "TEST - configurator check, do not produce",
    })
    orders.sort(key=lambda o: o["_id"])

    # Claude fix: write dates as MongoDB Extended JSON so `mongoimport --jsonArray` (or
    # bson.json_util) loads them as real dates. As plain strings, every logged
    # `order_date:{$gte:ISODate(...)}` query would silently match nothing on a real database.
    # Converted only at write time: the generation logic above still compares strings.
    def as_date(s: str) -> dict:
        return {"$date": s if "T" in s else f"{s}T00:00:00Z"}
    for o in orders:
        o["order_date"] = as_date(o["order_date"])
    for c in contacts:
        if "archived_at" in c:
            c["archived_at"] = as_date(c["archived_at"])

    (DATA / "sales_leads.json").write_text(json.dumps(leads, indent=1) + "\n")
    (DATA / "accounts.json").write_text(json.dumps(accounts, indent=1) + "\n")
    (DATA / "contacts.json").write_text(json.dumps(contacts, indent=1) + "\n")
    (DATA / "orders.json").write_text(json.dumps(orders, indent=1) + "\n")

    # ---------- query log: ~2,000 ordinary + planted clues ----------
    def agg(match_inner: str, group_inner: str) -> str:
        # builds: db.orders.aggregate([{$match:{...}}, {$group:{...}}])
        return 'db.orders.aggregate([{$match:{' + match_inner + '}}, {$group:{' + group_inner + '}}])'

    rev_match = 'contact_email:{$not:/@intl-racking\\.test$/}, status:{$in:[1,2,3]}'
    rev_agg = agg(rev_match, '_id:null, revenue:{$sum:"$price_cents"}')
    lines = []
    for _ in range(2000):
        d = start + timedelta(days=rng.randint(0, span))
        ts = f"{d.isoformat()}T{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z"
        k = rng.random()
        if k < 0.50:
            lines.append(f"{ts} app       db.orders.find({{account_id:{rng.randint(10000, 11400)}}})")
        elif k < 0.68:
            lines.append(f"{ts} app       db.orders.find({{status:{rng.randint(0, 4)}}})")
        elif k < 0.80:  # A1: finance revenue — test-excluded AND status in [1,2,3]
            lines.append(ts + ' finance   ' + rev_agg)
        elif k < 0.90:
            lead = rng.choice(['BE-01', 'NL-01', 'FR-01', 'DE-01'])
            lines.append(f'{ts} sales_ops db.orders.find({{handled_by:"{lead}"}})')
        else:
            lines.append(f"{ts} app       db.contacts.find({{account_id:{rng.randint(10000, 11400)}}})")
    # planted clues: quarterly FR+LU commission (R5), PO (R9), revenue (R10+A1), US loads (R4)
    for y in range(2021, 2025):
        for m1, m2, y2 in [("01-01", "04-01", y), ("04-01", "07-01", y), ("07-01", "10-01", y), ("10-01", "01-01", y + 1)]:
            # Claude fix: exclude test orders (R10 consistency); run after the quarter closes
            match = ('contact_email:{$not:/@intl-racking\\.test$/}, country:{$in:["FR","LU"]}, status:{$in:[1,2,3]}, '
                     f'order_date:{{$gte:ISODate("{y}-{m1}"), $lt:ISODate("{y2}-{m2}")}}')
            lines.append(f"{y2}-{m2[:2]}-03T07:30:00Z sales_ops " + agg(match, '_id:"FR", revenue:{$sum:"$price_cents"}'))
    # Claude fix (R4): monthly Antwerp production-planning query for reinforced beams (>= 300 kg).
    # The planner converted 300 kg to 662 lb by hand for SF orders: the only place the unit
    # conversion ever existed. This is what lets the replay catch "loads are still kilos".
    for y in range(2021, 2025):
        for m in range(1, 13):
            if (y, m) < (2021, 7):
                continue
            lines.append(f"{y}-{m:02d}-02T06:15:00Z production db.orders.find({{status:{{$in:[1,2]}}, "
                         '$or:[{office:{$ne:"SF"}, load_capacity:{$gte:300}}, {office:"SF", load_capacity:{$gte:662}}]}, '
                         '{_id:1, office:1, load_capacity:1})')
    lines.append('2023-11-30T14:02:44Z finance   db.orders.find({customer_type:"retail_key", delivery_instructions:/^PO /}, {delivery_instructions:1, price_cents:1})')
    lines.append('2024-01-08T08:15:00Z finance   ' + agg(rev_match + ', order_date:{$gte:ISODate("2023-01-01")}', '_id:null, revenue:{$sum:"$price_cents"}'))
    lines.append('2023-05-17T15:40:12Z us_sales  db.orders.find({office:"SF", load_capacity:{$gte:1000}, status:{$in:[1,2]}})')
    lines.sort()
    (DATA / "query_log.txt").write_text("\n".join(lines) + "\n")

    # ---------- evidence fossils (verbatim from ground truth) ----------
    (EV / "legacy" / "order_status.php").write_text("""<?php
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
""")
    (EV / "pricing" / "distributor.js").write_text("""// Distributors get 22% off list.
// Keep the LIST price in `price` -- the sales commission report needs it!
function invoicedAmount(order) {
  const list = parseFloat(order.price);
  return order.tier === "D" ? +(list * 0.78).toFixed(2) : list;
}
""")
    (EV / "sales" / "open_quotes.js").write_text("""// steel prices move too fast -- quotes are only valid for 30 days
const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000;
function isOpenQuote(order, today = new Date()) {
  return order.status === 0 && (today - order.order_date) <= THIRTY_DAYS;
}
""")
    (EV / "crm" / "newsletter.js").write_text("""// active contacts for the quarterly product newsletter
const recipients = await db.collection("contacts")
  .find({ is_deleted: { $ne: true } })
  .project({ email: 1, name: 1 })
  .toArray();
""")

    vd = sum(1 for a in accounts if isinstance(a["_id"], str))
    print(f"accounts: {len(accounts)} (VD-: {vd}, distributors: {sum(1 for a in accounts if a['customer_type'] == 'distributor')}, retail_key: {sum(1 for a in accounts if a['customer_type'] == 'retail_key')})")
    print(f"contacts: {len(contacts)}")
    print(f"orders:   {len(orders)} (test: {sum(1 for o in orders if o['contact_email'].endswith('@intl-racking.test'))}, status5: {sum(1 for o in orders if o['status'] == 5)}, status0: {sum(1 for o in orders if o['status'] == 0)}, SF: {sum(1 for o in orders if o.get('office') == 'SF')}, LU: {sum(1 for o in orders if o['country'] == 'LU')})")
    print(f"query log: {len(lines)} lines  seed={args.seed}")


if __name__ == "__main__":
    main()
