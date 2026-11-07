#!/usr/bin/env python3
"""
International Racking: ground-truth checker.

Verifies that every hidden rule (R1-R10) in docs/GROUND_TRUTH_INTERNATIONAL_RACKING.md
is actually planted in the generated data, with zero exceptions where the story
needs them, that the numbers behind the pitch lines are true, and that the
agent's inputs don't leak the answers (or any API key).

Stdlib only. Read-only: it never modifies the data.

Usage:
  python3 tools/international_racking/check_ground_truth.py [generated_folder]

Default folder: datasets/international_racking
Exit code: 0 = no FAIL, 1 = at least one FAIL, 2 = data not found.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "datasets" / "international_racking"

# Facts from the answer key
SWITCH_START = datetime(2019, 7, 1)    # R1: overlap week starts (both formats)
SWITCH_END = datetime(2019, 7, 8)      # R1: cents only from here
STATUS5_START = datetime(2021, 4, 1)   # R2
FREIGHT_THRESHOLD = 500_000            # €5,000 in cents, checked on LIST price
FREIGHT_FEE = 29_000                   # €290 in cents
SF_START = datetime(2021, 6, 1)        # R4
PO_START = datetime(2018, 1, 1)        # R9
TEST_DOMAIN = "@intl-racking.test"     # R10
HERO_DATE = "2016-09-14"               # R10
QUOTE_ASOF = datetime(2024, 6, 30)     # R7 test question date
REAL = {1, 2, 3}                       # statuses that count as revenue

TARGETS = {
    "accounts": 1500, "vd_accounts": 140, "lu_accounts": 45, "lu_orders": 600,
    "distributors": 60, "retail_key": 25, "contacts": 4000,
    "del_Y": 150, "del_N": 600, "is_deleted": 200, "archived": 120,
    "orders": 25000, "zombies": 3000, "test_orders": 200, "status5": 400,
    "sf_orders": 3500, "overlap": 60,
}

results = []  # (rule, level, message)


def add(rule, level, msg):
    results.append((rule, level, msg))


def check(rule, cond, pass_msg, fail_msg, level="FAIL"):
    add(rule, "PASS" if cond else level, pass_msg if cond else fail_msg)
    return cond


def near(rule, label, actual, target, tol=0.25):
    lo, hi = target * (1 - tol), target * (1 + tol)
    check(rule, lo <= actual <= hi,
          f"{label}: {actual:,} (target ~{target:,})",
          f"{label}: {actual:,}, target ~{target:,} (off by more than {int(tol*100)}%)",
          level="WARN")


def eur(cents):
    return f"€{cents / 100:,.0f}"


def examples(items, key="_id", n=3):
    ids = [str(x.get(key)) for x in items[:n]]
    return f" e.g. _id {', '.join(ids)}" if ids else ""


# ---------- loading ----------

def load_records(path):
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text[0] == "[":
        return json.loads(text)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            lists = [v for v in obj.values() if isinstance(v, list)]
            return lists[0] if len(lists) == 1 else [obj]
    except json.JSONDecodeError:
        pass
    return [json.loads(line) for line in text.splitlines() if line.strip()]  # JSON Lines


def find_file(root, keywords, exts):
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts and any(k in p.name.lower() for k in keywords):
            return p
    return None


def parse_dt(v):
    if v is None:
        return None
    if isinstance(v, dict) and "$date" in v:
        v = v["$date"]
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).replace(tzinfo=None)
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def money(o, field):
    """Cents from either era: `<field>_cents` (int) or `<field>` (text euros)."""
    c, t = o.get(f"{field}_cents"), o.get(field)
    if c is not None:
        return int(c)
    if t is not None:
        try:
            return round(float(t) * 100)
        except (TypeError, ValueError):
            return None
    return None


def empty(v):
    return v is None or v == "" or v == []


# ---------- the checks ----------

def run(folder):
    paths = {
        "leads": find_file(folder, ["lead"], {".json", ".jsonl"}),
        "accounts": find_file(folder, ["account"], {".json", ".jsonl"}),
        "contacts": find_file(folder, ["contact"], {".json", ".jsonl"}),
        "orders": find_file(folder, ["order"], {".json", ".jsonl"}),
        "log": find_file(folder, ["query", "log"], {".txt", ".log", ".jsonl"}),
        "status_php": find_file(folder, ["order_status"], {".php"}),
        "distributor_js": find_file(folder, ["distributor"], {".js"}),
        "quotes_js": find_file(folder, ["open_quotes"], {".js"}),
        "newsletter_js": find_file(folder, ["newsletter"], {".js"}),
    }
    for name, p in paths.items():
        check("FILES", p is not None, f"{name}: {p.relative_to(folder) if p else ''}",
              f"{name}: file not found under {folder}")
    if not all(paths[k] for k in ("accounts", "orders")):
        return

    leads = load_records(paths["leads"]) if paths["leads"] else []
    accounts = load_records(paths["accounts"])
    contacts = load_records(paths["contacts"]) if paths["contacts"] else []
    orders = load_records(paths["orders"])
    log_lines = paths["log"].read_text(encoding="utf-8").splitlines() if paths["log"] else []
    code = {k: paths[k].read_text(encoding="utf-8") if paths[k] else ""
            for k in ("status_php", "distributor_js", "quotes_js", "newsletter_js")}

    acct = {a.get("_id"): a for a in accounts}
    for o in orders:
        o["_dt"] = parse_dt(o.get("order_date"))
        o["_price"] = money(o, "price")
        o["_freight"] = money(o, "freight")
        o["_acct"] = acct.get(o.get("account_id"), {})
        o["_test"] = str(o.get("contact_email", "")).lower().endswith(TEST_DOMAIN)

    undated = [o for o in orders if o["_dt"] is None]
    check("DATA", not undated, f"all {len(orders):,} orders have a readable order_date",
          f"{len(undated)} orders have an unreadable order_date{examples(undated)}")
    orders = [o for o in orders if o["_dt"] is not None]
    real = [o for o in orders if o.get("status") in REAL and not o["_test"]]
    near("DATA", "accounts", len(accounts), TARGETS["accounts"])
    near("DATA", "orders and quotes", len(orders), TARGETS["orders"])
    dmin, dmax = min(o["_dt"] for o in orders), max(o["_dt"] for o in orders)
    add("DATA", "INFO", f"orders run from {dmin:%Y-%m-%d} to {dmax:%Y-%m-%d}")
    has_currency = [o for o in orders if "currency" in o]
    check("DATA", not has_currency, "no currency field (locked decision: all invoiced in EUR)",
          f"{len(has_currency)} orders carry a currency field; locked decision says none",
          level="WARN")

    # R1: The Great Cents Switch
    pre = [o for o in orders if o["_dt"] < SWITCH_START]
    post = [o for o in orders if o["_dt"] >= SWITCH_END]
    overlap = [o for o in orders if SWITCH_START <= o["_dt"] < SWITCH_END]
    bad_pre = [o for o in pre if not isinstance(o.get("price"), str) or "price_cents" in o]
    bad_post = [o for o in post if not isinstance(o.get("price_cents"), int) or "price" in o]
    bad_ovl = [o for o in overlap
               if not (isinstance(o.get("price"), str) and isinstance(o.get("price_cents"), int)
                       and round(float(o["price"]) * 100) == o["price_cents"])]
    check("R1", not bad_pre, f"{len(pre):,} pre-2019-07 orders store price as euro text only",
          f"{len(bad_pre)} pre-switch orders don't have text-only prices{examples(bad_pre)}")
    check("R1", not bad_post, f"{len(post):,} post-switch orders store price_cents only",
          f"{len(bad_post)} post-switch orders don't have cents-only prices{examples(bad_post)}")
    check("R1", overlap and not bad_ovl,
          f"{len(overlap)} overlap-week orders carry both formats with equal amounts",
          f"overlap week: {len(overlap)} orders, {len(bad_ovl)} without both equal formats"
          f"{examples(bad_ovl)}")
    near("R1", "overlap-week orders", len(overlap), TARGETS["overlap"], tol=0.5)

    # R2: The Missing Status 5
    statuses = Counter(o.get("status") for o in orders)
    check("R2", set(statuses) <= {0, 1, 2, 3, 4, 5}, f"status codes used: {sorted(statuses)}",
          f"unexpected status codes: {sorted(set(statuses) - {0, 1, 2, 3, 4, 5})}")
    s5 = [o for o in orders if o.get("status") == 5]
    s5_no_credit = [o for o in s5 if "credit_note_cents" not in o]
    s5_early = [o for o in s5 if o["_dt"] < STATUS5_START]
    credit_not5 = [o for o in orders if "credit_note_cents" in o and o.get("status") != 5]
    check("R2", s5 and not s5_no_credit, f"all {len(s5)} status-5 orders have credit_note_cents",
          f"{len(s5_no_credit)} status-5 orders lack credit_note_cents{examples(s5_no_credit)}")
    check("R2", not s5_early, "no status-5 order before April 2021",
          f"{len(s5_early)} status-5 orders before April 2021{examples(s5_early)}")
    check("R2", not credit_not5, "credit_note_cents only appears on status 5",
          f"{len(credit_not5)} non-status-5 orders have credit_note_cents{examples(credit_not5)}")
    near("R2", "status-5 orders", len(s5), TARGETS["status5"])
    php = code["status_php"]
    check("R2", all(f"case {i}" in php for i in range(5)) and "case 5" not in php,
          "order_status.php lists 0-4 and hides 5",
          "order_status.php must list cases 0-4 and NOT case 5")

    # R3: The Vandaele Handshake (+ the official freight price list)
    vd_ids = {a["_id"] for a in accounts if str(a.get("_id", "")).startswith("VD-")}
    near("R3", "VD- accounts", len(vd_ids), TARGETS["vd_accounts"], tol=0.1)
    vd_orders = [o for o in orders if o.get("account_id") in vd_ids and o["_freight"] is not None]
    vd_paid = [o for o in vd_orders if o["_freight"] != 0]
    check("R3", vd_orders and not vd_paid, f"all {len(vd_orders):,} VD- orders have freight 0",
          f"{len(vd_paid)} VD- orders paid freight; the rule needs zero exceptions{examples(vd_paid)}")
    vd_small = [o for o in vd_orders if (o["_price"] or 0) < FREIGHT_THRESHOLD]
    check("R3", len(vd_small) >= 30,
          f"{len(vd_small):,} VD- orders are under €5,000, so free freight can't be explained by the threshold",
          f"only {len(vd_small)} VD- orders under €5,000; the agent can't tell R3 from the normal threshold")
    vd_dist = [a for a in accounts if a["_id"] in vd_ids
               and (a.get("customer_type") == "distributor" or a.get("tier") == "D")]
    check("R3", not vd_dist, "no VD- account is a distributor (keeps R3 separate from R8)",
          f"{len(vd_dist)} VD- accounts are distributors; this muddles R3 with R8", level="WARN")
    others = [o for o in orders if o.get("account_id") not in vd_ids
              and o["_freight"] is not None and o["_price"] is not None]
    off_list = [o for o in others
                if o["_freight"] != (0 if o["_price"] >= FREIGHT_THRESHOLD else FREIGHT_FEE)]
    check("R3", not off_list,
          f"all {len(others):,} other orders follow the price list (free ≥ €5,000 list, else €290)",
          f"{len(off_list)} non-VD orders break the freight price list{examples(off_list)}")

    # R4: Pounds or Kilos?
    sf = [o for o in orders if o.get("office") == "SF"]
    eu = [o for o in orders if o.get("office") != "SF" and not o["_test"]]
    near("R4", "San Francisco orders", len(sf), TARGETS["sf_orders"])
    sf_early = [o for o in sf if o["_dt"] < SF_START]
    check("R4", not sf_early, "no SF order before June 2021",
          f"{len(sf_early)} SF orders before June 2021{examples(sf_early)}", level="WARN")
    sf_big = [o for o in sf if any((o.get(f) or 0) > 150 for f in ("width", "depth", "height"))]
    eu_small = [o for o in eu if any(0 < (o.get(f) or 300) < 300 for f in ("width", "depth", "height"))]
    check("R4", not sf_big, "all SF sizes look like inches (≤ 150)",
          f"{len(sf_big)} SF orders have sizes > 150, which don't look like inches{examples(sf_big)}")
    check("R4", not eu_small, "all EU sizes look like millimetres (≥ 300)",
          f"{len(eu_small)} EU orders have sizes < 300, which look like inches{examples(eu_small)}")
    sf_loads = Counter(o.get("load_capacity") for o in sf)
    eu_loads = Counter(o.get("load_capacity") for o in eu)
    add("R4", "INFO", f"SF load values: {dict(sorted(sf_loads.items(), key=lambda x: str(x[0])))}")
    add("R4", "INFO", f"EU load values: {dict(sorted(eu_loads.items(), key=lambda x: str(x[0])))}")
    plausible_kg = all(isinstance(v, (int, float)) and 100 <= v <= 1500 for v in sf_loads)
    check("R4", sf and plausible_kg, "SF loads (in lb) also look like plausible kg values, so the trap works",
          "SF loads aren't all in the 100-1,500 range, so 'still kilos' wouldn't be tempting")
    us_ids = {a["_id"] for a in accounts if a.get("country") == "US"}
    mism = [o for o in orders if (o.get("account_id") in us_ids) != (o.get("office") == "SF")]
    check("R4", not mism, "US accounts ⇔ SF office orders line up exactly",
          f"{len(mism)} orders where US account and SF office disagree{examples(mism)}", level="WARN")
    us_q = [l for l in log_lines if '"SF"' in l and "load_capacity" in l]
    check("R4", us_q, f"{len(us_q)} queries on SF load_capacity in the log",
          "no SF load_capacity query in the log")
    # A unit mistake applied consistently survives a mechanically translated replay.
    # The trap is only catchable through a query that mixes units: 300 kg for EU vs 662 lb for SF.
    prod_q = [l for l in log_lines if '"SF"' in l and "662" in l and "300" in l]
    check("R4", len(prod_q) >= 6,
          f"{len(prod_q)} production queries use 300 for EU vs 662 for SF (300 kg in lb): the replay can catch the trap",
          f"only {len(prod_q)} mixed-unit (300 kg / 662 lb) production queries; the replay can't catch 'loads still kilos'",
          level="FAIL" if not prod_q else "WARN")

    # R5: Who Owns Luxembourg?
    lead_ids = {str(l.get("_id")) for l in leads}
    lead_cty = {str(l.get("country")) for l in leads}
    check("R5", "FR-01" in lead_ids, "sales lead FR-01 exists", "sales lead FR-01 is missing")
    check("R5", "LU" not in lead_cty and not any(i.startswith("LU") for i in lead_ids),
          "no Luxembourg sales lead exists", "a Luxembourg sales lead exists; the rule needs none")
    lu_ids = {a["_id"] for a in accounts if a.get("country") == "LU"}
    near("R5", "Luxembourg accounts", len(lu_ids), TARGETS["lu_accounts"])
    lu_with_lead = [a for a in accounts if a["_id"] in lu_ids and not empty(a.get("sales_lead_id"))]
    check("R5", not lu_with_lead, "every Luxembourg account has an empty sales_lead_id",
          f"{len(lu_with_lead)} Luxembourg accounts have a sales lead set{examples(lu_with_lead)}")
    no_lead = [a for a in accounts if a["_id"] not in lu_ids and empty(a.get("sales_lead_id"))]
    check("R5", not no_lead, "every non-Luxembourg account has a sales lead (Luxembourg stands out)",
          f"{len(no_lead)} non-Luxembourg accounts also have no sales lead; the signal is blurred",
          level="WARN")
    lu_orders = [o for o in orders if o.get("account_id") in lu_ids]
    lu_not_fr = [o for o in lu_orders if o.get("handled_by") != "FR-01"]
    lu_to_be = [o for o in lu_orders if o.get("handled_by") == "BE-01"]
    near("R5", "Luxembourg orders", len(lu_orders), TARGETS["lu_orders"])
    check("R5", lu_orders and not lu_not_fr, f"all {len(lu_orders)} Luxembourg orders handled by FR-01",
          f"{len(lu_not_fr)} Luxembourg orders not handled by FR-01 ({len(lu_to_be)} by BE-01)"
          f"{examples(lu_not_fr)}")
    off_owner = [o for o in orders if o.get("account_id") not in lu_ids and o.get("_acct")
                 and o.get("handled_by") != o["_acct"].get("sales_lead_id")]
    check("R5", not off_owner, "all other orders are handled by their account's own sales lead",
          f"{len(off_owner)} non-Luxembourg orders handled by someone other than their account's lead",
          level="WARN")
    fr_lu_q = [l for l in log_lines if '"FR"' in l and '"LU"' in l]
    check("R5", len(fr_lu_q) >= 4, f"{len(fr_lu_q)} commission queries group FR + LU in the log",
          f"only {len(fr_lu_q)} FR+LU commission queries in the log (want one per quarter)",
          level="FAIL" if not fr_lu_q else "WARN")

    # R6: The Ghost Contacts
    if contacts:
        dY = [c for c in contacts if c.get("deleted") == "Y"]
        dN = [c for c in contacts if c.get("deleted") == "N"]
        isd = [c for c in contacts if c.get("is_deleted") is True]
        arc = [c for c in contacts if not empty(c.get("archived_at"))]
        near("R6", "contacts", len(contacts), TARGETS["contacts"])
        near("R6", 'deleted:"Y"', len(dY), TARGETS["del_Y"])
        near("R6", 'deleted:"N" (active)', len(dN), TARGETS["del_N"])
        near("R6", "is_deleted:true", len(isd), TARGETS["is_deleted"])
        near("R6", "archived_at", len(arc), TARGETS["archived"])
        multi = [c for c in contacts if sum([c.get("deleted") == "Y", c.get("is_deleted") is True,
                                             not empty(c.get("archived_at"))]) > 1]
        check("R6", not multi, "no contact uses two deletion conventions at once",
              f"{len(multi)} contacts carry more than one deletion flag{examples(multi)}", level="WARN")
    else:
        add("R6", "FAIL", "no contacts file, so R6 can't be found")
    nl = code["newsletter_js"]
    check("R6", "is_deleted" in nl and "archived_at" not in nl and '"Y"' not in nl and "'Y'" not in nl,
          "newsletter.js checks only is_deleted (partial evidence, with the old bug)",
          "newsletter.js must check is_deleted only")

    # R7: The Zombie Quotes
    quotes = [o for o in orders if o.get("status") == 0]
    cutoff = QUOTE_ASOF - timedelta(days=30)
    zombies = [o for o in quotes if o["_dt"] < cutoff]
    open_q = [o for o in quotes if cutoff <= o["_dt"] <= QUOTE_ASOF]
    near("R7", "expired (zombie) quotes on 2024-06-30", len(zombies), TARGETS["zombies"])
    check("R7", open_q, f"{len(open_q)} genuinely open quotes on 2024-06-30 (the test question has an answer)",
          "no open quotes in the 30 days before 2024-06-30; move the R7 test date or add recent quotes",
          level="WARN")
    check("R7", "30" in code["quotes_js"] and "status" in code["quotes_js"],
          "open_quotes.js encodes the 30-day expiry", "open_quotes.js doesn't show the 30-day rule")

    # R8: The Distributor Discount
    disagree = [a for a in accounts if (a.get("customer_type") == "distributor") != (a.get("tier") == "D")]
    check("R8", not disagree, "customer_type and tier never disagree (locked decision b)",
          f"{len(disagree)} accounts where customer_type and tier disagree{examples(disagree)}")
    bad_tier = [a for a in accounts if a.get("tier") not in (None, "D")]
    check("R8", not bad_tier, "tier is null unless distributor", f"{len(bad_tier)} accounts with odd tier values",
          level="WARN")
    near("R8", "distributor accounts", sum(a.get("tier") == "D" for a in accounts), TARGETS["distributors"])
    tier_copy = [o for o in orders if o.get("_acct") and o.get("tier") != o["_acct"].get("tier")]
    check("R8", not tier_copy, "every order's tier matches its account",
          f"{len(tier_copy)} orders whose tier differs from their account{examples(tier_copy)}", level="WARN")
    dl = code["distributor_js"].splitlines()
    check("R8", len(dl) >= 5 and "0.78" in dl[4], 'distributor.js has the 0.78 discount on line 5 (the demo cites "line 5")',
          'distributor.js: the 0.78 discount is not on line 5; fix the file or the demo line')

    # R9: The PO Number in the Delivery Notes
    rk_ids = {a["_id"] for a in accounts if a.get("customer_type") == "retail_key"}
    near("R9", "key retail accounts", len(rk_ids), TARGETS["retail_key"])
    is_po = lambda o: str(o.get("delivery_instructions") or "").startswith("PO ")
    rk_new = [o for o in orders if o.get("account_id") in rk_ids and o["_dt"] >= PO_START]
    rk_old = [o for o in orders if o.get("account_id") in rk_ids and o["_dt"] < PO_START]
    rk_new_bad = [o for o in rk_new if not is_po(o)]
    check("R9", rk_new and not rk_new_bad, f"all {len(rk_new):,} key-retail orders since 2018 hold a PO number",
          f"{len(rk_new_bad)} key-retail orders since 2018 have no PO number{examples(rk_new_bad)}")
    rk_old_po = [o for o in rk_old if is_po(o)]
    check("R9", not rk_old_po, f"{len(rk_old)} key-retail orders before 2018 have normal instructions",
          f"{len(rk_old_po)} key-retail orders before 2018 already hold PO numbers", level="WARN")
    other_po = [o for o in orders if o.get("account_id") not in rk_ids and is_po(o)]
    check("R9", not other_po, "no other customer type has PO numbers in delivery instructions",
          f"{len(other_po)} non-key-retail orders hold PO numbers{examples(other_po)}")
    check("R9", any("delivery_instructions" in l and "PO" in l for l in log_lines),
          "finance PO query is in the log", "no finance query on delivery_instructions /^PO / in the log")

    # R10: The 100-Metre Shelf
    tests = [o for o in orders if o["_test"]]
    near("R10", "test orders", len(tests), TARGETS["test_orders"])
    hero = [o for o in orders if (o["_price"] or 0) >= 90_000_000]
    h = hero[0] if len(hero) == 1 else None
    check("R10", h is not None, "exactly one giant (€900k+) order exists",
          f"{len(hero)} orders at €900k+ (want exactly one, the 100-metre shelf)")
    if h:
        check("R10", h["_test"], "the giant order uses a test email", "the giant order is not a test-email order")
        check("R10", h["_dt"].strftime("%Y-%m-%d") == HERO_DATE, f"the giant order is dated {HERO_DATE}",
              f"the giant order is dated {h['_dt']:%Y-%m-%d}, answer key says {HERO_DATE}", level="WARN")
        check("R10", h.get("height") == 100000, "the giant order is 100,000 mm tall",
              f"the giant order's height is {h.get('height')}, not 100000", level="WARN")
        check("R10", h.get("status") in REAL,
              "the giant order has a revenue status (1-3), so a naive report counts it",
              f"the giant order has status {h.get('status')}, so revenue reports ignore it anyway")
    rev_q = [l for l in log_lines if "$sum" in l and "price" in l]
    rev_q_excl = [l for l in rev_q if "intl-racking" in l]
    check("R10", len(rev_q_excl) >= 5, f"{len(rev_q_excl)} revenue queries exclude the test domain (repeated pattern)",
          f"only {len(rev_q_excl)} revenue queries exclude the test domain (want a repeated pattern)")
    check("R10", len(rev_q_excl) == len(rev_q), "every revenue query in the log excludes test orders",
          f"{len(rev_q) - len(rev_q_excl)} revenue queries in the log do NOT exclude test orders "
          "(contradicting evidence)", level="WARN")
    no_status = [l for l in rev_q if "status" not in l]
    check("R10", not no_status, "every revenue query filters on status (revenue = real orders only)",
          f"{len(no_status)} revenue queries don't filter on status, so they count quotes and cancellations",
          level="WARN")

    # Leaks: the agent's inputs must not give answers away
    agent_dirs = [d for d in (folder / "data", folder / "evidence") if d.is_dir()] or [folder]
    agent_files = [p for d in agent_dirs for p in d.rglob("*") if p.is_file()]
    key_files = [p for p in agent_files if "ground_truth" in p.name.lower() or "answer" in p.name.lower()]
    check("LEAKS", not key_files, "no answer-key file among the agent's inputs",
          f"answer-key-like file(s) in the agent's inputs: {[p.name for p in key_files]}")
    all_code = "\n".join(code.values())
    for rule, pattern, why in [
        ("R3", r"VD-", "mentions VD- accounts"),
        ("R4", r"\b(lbs?|pounds?|inch(es)?|kg|kilo)", "mentions units"),
        ("R5", r"\bLU\b|Luxembourg", "mentions Luxembourg"),
        ("R2", r"credit", "mentions credit notes"),
    ]:
        check("LEAKS", not re.search(pattern, all_code, re.I), f"code snippets don't give away {rule}",
              f"code snippets {why}, which gives {rule} away")
    log_text = "\n".join(log_lines)
    for rule, pattern, why in [("R3", r"VD-", "mentions VD-"),
                               ("R4", r"\b(lbs?|pounds?|inch(es)?|kilo)", "mentions units")]:
        check("LEAKS", not re.search(pattern, log_text, re.I), f"query log doesn't give away {rule}",
              f"query log {why}, which changes {rule}'s expected label to Observed", level="WARN")
    secret = re.compile(r"sk-[A-Za-z0-9_\-]{16,}|api[_ -]?key\s*[:=]", re.I)
    leaked = [p for p in folder.rglob("*") if p.is_file() and p.stat().st_size < 50_000_000
              and secret.search(p.read_text(encoding="utf-8", errors="ignore"))]
    check("LEAKS", not leaked, "no API-key-like strings anywhere in the generated folder",
          f"API-key-like string found in: {[str(p.relative_to(folder)) for p in leaked]} (value not shown)")

    # Numbers for the pitch: the real values, computed from the data
    rev = defaultdict(int)
    rev_naive = defaultdict(int)
    for o in orders:
        if o.get("status") in REAL and o["_price"] is not None:
            rev_naive[o["_dt"].year] += o["_price"]
            if not o["_test"]:
                rev[o["_dt"].year] += o["_price"]
    if rev_naive:
        best = max(rev_naive, key=rev_naive.get)
        check("PITCH", best == 2016,
              "R10 pitch holds: with test orders counted, 2016 becomes the 'best year ever'",
              f"R10 pitch line is FALSE: with test orders counted, the best year is {best} "
              f"({eur(rev_naive[best])}), not 2016 ({eur(rev_naive.get(2016, 0))}). "
              "Raise the giant order's price or change the pitch line", level="WARN")
    pre_rev = sum(v for y, v in rev.items() if y < 2019)
    add("PITCH", "INFO", f"R1: revenue stored as euro text (2014-2018) that a naive migration would drop: {eur(pre_rev)}")
    add("PITCH", "INFO", f"R2: open credit notes (status 5): {eur(sum(o.get('credit_note_cents', 0) for o in s5))}")
    add("PITCH", "INFO", f"R3: freight VD- accounts would wrongly pay: {eur(len(vd_small) * FREIGHT_FEE)} "
                         f"across {len(vd_small):,} orders")
    wrongly_safe = [o for o in sf if isinstance(o.get("load_capacity"), (int, float))
                    and o["load_capacity"] >= 300 > o["load_capacity"] * 0.4536]
    add("PITCH", "INFO", f"R4: SF shelves a naive migration would wrongly approve for a 300 kg pallet: "
                         f"{len(wrongly_safe):,}")
    replay_extra = [o for o in wrongly_safe if o.get("status") in (1, 2)]
    add("PITCH", "INFO", f"R4: replaying the production query, a 'loads still kilos' migration returns "
                         f"{len(replay_extra):,} extra shelves (all rated 500 lb = 227 kg)")
    lu23 = sum(o["_price"] or 0 for o in real if o.get("account_id") in lu_ids and o["_dt"].year == 2023)
    add("PITCH", "INFO", f"R5: 2023 Luxembourg revenue that would move from France to Belgium: {eur(lu23)}")
    if contacts:
        ghosts = [c for c in contacts if c.get("is_deleted") is not True
                  and (c.get("deleted") == "Y" or not empty(c.get("archived_at")))]
        add("PITCH", "INFO", f"R6: deleted contacts the old newsletter code would still email: {len(ghosts):,}")
    add("PITCH", "INFO", f"R7: zombie-quote pipeline inflation on 2024-06-30: "
                         f"{eur(sum(o['_price'] or 0 for o in zombies))} ({len(zombies):,} quotes)")
    d20 = sum(o["_price"] or 0 for o in real if o.get("tier") == "D" and o["_dt"].year == 2020)
    add("PITCH", "INFO", f"R8: 2020 distributor revenue at list {eur(d20)} vs actually invoiced {eur(d20 * 0.78)}")
    add("PITCH", "INFO", f"R10: fake revenue from test orders: {eur(sum(rev_naive.values()) - sum(rev.values()))}")


# ---------- report ----------

ICON = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌", "INFO": "ℹ️ "}
ORDER = ["FILES", "DATA", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "LEAKS", "PITCH"]


def main():
    folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_DIR
    if not folder.is_dir():
        print(f"❌ Folder not found: {folder}\n   Has the generator been run yet?")
        sys.exit(2)
    print(f"International Racking ground-truth check\nData: {folder}\n")
    run(folder)
    for section in ORDER:
        rows = [r for r in results if r[0] == section]
        if not rows:
            continue
        print(f"── {section} " + "─" * (60 - len(section)))
        for _, level, msg in rows:
            print(f"  {ICON[level]} {msg}")
        print()
    c = Counter(level for _, level, _ in results)
    print(f"Summary: {c['PASS']} pass · {c['WARN']} warnings · {c['FAIL']} failures")
    sys.exit(1 if c["FAIL"] else 0)


if __name__ == "__main__":
    main()
