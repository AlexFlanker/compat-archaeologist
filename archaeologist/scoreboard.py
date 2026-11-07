"""Blind scoring: how many of ShopLog's hidden rules did the pipeline recover, and did it label them right?

The extractors never read docs/RULES.md. This scorer does, so it lives apart from them. It matches
recovered evidence against fixed patterns, one set per rule, rather than asking a model to judge.
A paraphrase a pattern misses counts as missed, so the score errs low, not high.
"""

from __future__ import annotations

import json
import re

from archaeologist.config import EVIDENCE_DIR, OUT_DIR

# For each rule: the label it should get, then patterns over three kinds of recovered evidence.
# "partial" patterns show part of the rule but not all of it.
SIGNATURES = {
    "R1": {
        "title": "Tier discounts; legacy_vip never takes a coupon",
        "expected": "OBSERVED",
        "data": [r"customers\.tier=legacy_vip ⇒ coupon_code is absent"],
        "text": [r"legacy_vip.*coupon|coupon.*legacy_vip", r"(gold|silver).{0,40}(10|5) ?%"],
        "partial": [r"customers\.tier=(gold|silver|legacy_vip) ⇒ discount > 0"],
    },
    "R2": {
        "title": "WELCOME10 works once per customer",
        "expected": "OBSERVED",
        "catalog": [r"\['customer_id', 'coupon_code'\] is unique and partial on \{\"coupon_code\": \{\"\$exists\": true\}\}"],
        "text": [r"WELCOME10.{0,80}(once|first order|one per customer)|once per customer"],
    },
    "R3": {
        "title": "Free shipping from $75 of discounted merchandise",
        "expected": "OBSERVED",
        "data": [r"shipping=0 when subtotal - discount ≥ 7500"],
        "text": [r"(free shipping|shipping (is )?free).{0,60}(\$?75|7500)|(\$?75|7500).{0,60}free shipping"],
    },
    "R4": {
        "title": "8.5% tax on merchandise; no tax field before 2021",
        "expected": "OBSERVED",
        "data": [r"tax is absent before ~2021"],
        "text": [r"8\.5 ?%|850 ?(bps|basis)"],
    },
    "R5": {
        "title": "Float dollars before 2018, integer cents after",
        "expected": "OBSERVED",
        "data": [r"total is double before ~2018"],
        "text": [r"(float|double|dollars).{0,80}cents|cents.{0,80}(float|double|dollars)"],
    },
    "R6": {
        "title": "Order state machine; only placed or paid can be cancelled",
        "expected": "OBSERVED",
        "text": [r"(only|can).{0,40}(placed|paid).{0,40}cancel|cancel.{0,60}(placed|paid)", r"(allowed|valid) (status )?transitions?"],
        "partial": [r"status=cancelled ⇒ delivered_at is absent"],
    },
    "R7": {
        "title": "Refund window: 30 days, 90 for legacy_vip",
        "expected": "INFERRED",
        "text": [r"30.{0,40}days?.{0,80}90|90.{0,40}days?.{0,80}(legacy_vip|vip)|refund window"],
    },
    "R8": {
        "title": "Clearance items are final sale",
        "expected": "OBSERVED",
        "data": [r"category=clearance ⇒ refund_status=none"],
        "text": [r"clearance.{0,60}(final sale|not refundable|non-refundable|can.?t be refunded|cannot be refunded)"],
    },
    "R9": {
        "title": "refund_total > 0 exactly when refunded",
        "expected": "OBSERVED",
        "data": [r"status=(partially_refunded|refunded) ⇒ refund_total > 0"],
        "text": [r"refund_total.{0,60}(greater than|> ?0|above zero|positive)"],
    },
    "R10": {
        "title": "Active carts expire after 24 hours; converted carts are kept",
        "expected": "OBSERVED",
        "catalog": [r"carts\.\w+ is TTL", r"carts\.\w+ on .* is partial on \{\"status\": \"active\"\}"],
        "text": [r"24 ?(h|hours?).{0,60}cart|cart.{0,60}24 ?(h|hours?)"],
    },
}


def _evidence(profile: str, ledger: dict) -> list[dict]:
    data_profile = json.loads((EVIDENCE_DIR / f"{profile}_data_profile.json").read_text())
    items = []
    for invariant in data_profile["invariants"]:
        items.append({"kind": "data", "text": f"[{invariant['scope']}] {invariant['rule']}", "ref": invariant["id"], "status": "OBSERVED"})
    for signals in data_profile["feature_signals"].values():
        for signal in signals:
            items.append({"kind": "catalog", "text": signal["detail"], "ref": signal["ref"], "status": "OBSERVED"})
    for behavior in ledger.get("behaviors", []):
        refs = [source["ref"] for source in behavior["sources"]]
        items.append({"kind": "text", "text": behavior["statement"], "ref": behavior["id"] + " (" + ", ".join(refs[:3]) + ")", "status": behavior["status"]})
    return items


def score(profile: str, ledger: dict) -> dict:
    evidence = _evidence(profile, ledger)
    rows = []
    for rule_id, signature in SIGNATURES.items():
        full, partial = [], []
        for item in evidence:
            patterns = signature.get(item["kind"], [])
            if item["kind"] == "text":
                patterns = patterns + signature.get("data", [])
            if any(re.search(pattern, item["text"], re.IGNORECASE) for pattern in patterns):
                full.append(item)
            elif any(re.search(pattern, item["text"], re.IGNORECASE) for pattern in signature.get("partial", [])):
                partial.append(item)
        matches = full or partial
        label = None
        if matches:
            label = "OBSERVED" if any(item["status"] == "OBSERVED" for item in matches) else "INFERRED"
        rows.append(
            {
                "rule": rule_id,
                "title": signature["title"],
                "found": "yes" if full else ("partly" if partial else "no"),
                "expected_label": signature["expected"],
                "label": label,
                "label_correct": label == signature["expected"] if label else None,
                "evidence": [{"ref": item["ref"], "text": item["text"][:160]} for item in matches[:3]],
            }
        )
    found = sum(1 for row in rows if row["found"] == "yes")
    partly = sum(1 for row in rows if row["found"] == "partly")
    labels = sum(1 for row in rows if row["label_correct"])
    document = {
        "profile": profile,
        "answer_key": "docs/RULES.md",
        "code_evidence_source": ledger.get("code_evidence_source"),
        "found": found,
        "partly": partly,
        "labels_correct": labels,
        "total": len(rows),
        "rows": rows,
    }
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "scoreboard.json").write_text(json.dumps(document, indent=2))
    return document


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    arguments = parser.parse_args()
    result = score(arguments.profile, json.loads((OUT_DIR / arguments.profile / "ledger.json").read_text()))
    print(f"found {result['found']}/{result['total']} (+{result['partly']} partly), labels correct {result['labels_correct']}/{result['total']}; code evidence: {result['code_evidence_source']}")
    for row in result["rows"]:
        print(f"  {row['rule']:4} {row['found']:6} label={row['label']} expected={row['expected_label']}  {row['evidence'][0]['ref'] if row['evidence'] else ''}")
