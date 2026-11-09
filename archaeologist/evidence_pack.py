"""Compresses one assessment's output into facts with stable ids, so an answer can cite them and be checked.

The pack is built deterministically from out/<profile>/ and probes/<profile>.json; no model is involved.
"""

from __future__ import annotations

import json

from archaeologist.config import OUT_DIR, ROOT

MAX_CHARS = 60_000
TEXT_LIMIT = 320
SKIP_ASSUMPTION_KEYS = {"url", "basis_urls", "urls", "alternative_basis", "as_of"}


def _read(path):
    return json.loads(path.read_text()) if path.exists() else None


def _clip(value, limit: int = TEXT_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _money(value) -> str:
    if value is None:
        return "n/a"
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def _flatten(prefix: str, value, out: list) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in SKIP_ASSUMPTION_KEYS:
                continue
            _flatten(f"{prefix}.{key}" if prefix else key, inner, out)
    elif isinstance(value, list):
        if value and all(not isinstance(item, (dict, list)) for item in value):
            out.append((prefix, ", ".join(str(item) for item in value)))
    elif value is not None:
        out.append((prefix, value))


def _probe_text(probe: dict) -> str:
    detail = probe.get("detail") or {}
    text = f"{probe['title']}. Verdict on FerretDB: {probe['verdict']}."
    difference = detail.get("difference")
    if difference:
        text += f" At step '{detail.get('at_step')}', {difference.get('path')}: MongoDB {_clip(difference.get('source'), 60)}, FerretDB {_clip(difference.get('target'), 60)}."
    elif detail.get("message"):
        text += f" At step '{detail.get('at_step')}', FerretDB error {detail.get('code')}: {_clip(detail['message'], 120)}."
    if detail.get("silent"):
        text += " No error was raised (silent)."
    return text


def build(profile: str) -> dict:
    folder = OUT_DIR / profile
    tco = _read(folder / "tco.json") or {}
    ledger = _read(folder / "ledger.json") or {}
    runs = _read(folder / "runs.json") or {}
    cutover = _read(folder / "cutover_clean.json") or {}
    drill = _read(folder / "cutover_drill.json") or {}
    scoreboard = _read(folder / "scoreboard.json") or {}
    facts: list[dict] = []

    def add(fact_id: str, text: str) -> None:
        facts.append({"id": fact_id, "text": _clip(text)})

    for target, info in tco.get("targets", {}).items():
        net = info["economics"]["net"]
        add(f"decision:{target}", f"{target} ({info['evidence']}): {info['decision_text']}. 12-month net base {_money(net['base'])}, best {_money(net['best'])}, worst {_money(net['worst'])}.")
        base = info["economics"]["breakdown"]["base"]
        add(f"economics:{target}", f"{target} base case: infrastructure savings {_money(base['infra_savings'])}/yr, migration {base['migration_hours']} h, rewrites {base['rewrite_hours']:.0f} h, one-time {_money(base['one_time_cost'])}, residual incident risk {_money(base['residual_risk_cost'])}, operations {_money(base['ops_cost'])}.")
        for size, row in (info.get("sensitivity", {}).get("clusters") or {}).items():
            add(f"sensitivity:{target}:clusters={size}", f"With {size} cluster(s), {target} net base {_money(row['net_base'])}: {row['decision']}.")
        for gap in info["gaps"]:
            estimate = gap.get("estimate") or {}
            text = f"{gap['feature']} {gap['name']} on {target}: kind {gap['kind']}, verdict {gap['verdict']}, blocking {gap['blocking']}, confidence {gap['confidence']}."
            if estimate:
                text += f" Fix (Muse, inferred) {estimate.get('hours_low', 0):.0f}-{estimate.get('hours_high', 0):.0f} h, base {estimate.get('hours_base', 0):.0f} h: {estimate.get('approach', '')}"
            if gap["kind"] == "documented_conflict":
                text += " Conflicts: " + "; ".join(item["conflict"] for item in gap["evidence"])
            if gap["kind"] == "performance":
                text += " " + "; ".join(f"p95 MongoDB {item['source_p95_ms']} ms vs FerretDB {item['target_p95_ms']} ms" for item in gap["evidence"])
            add(f"gap:{target}:{gap['feature']}", text)

    walk = tco.get("reversal") or {}
    for index, step in enumerate(walk.get("steps", [])):
        change = "" if step["change"] is None else f"change {_money(step['change'])}, "
        flip = " The decision flips here." if walk.get("flipped_at") == index else ""
        add(f"step:{index}", f"Reversal step {index} ({walk.get('target')}): {step['step']}. {step['evidence']}. {change}running net {_money(step['net'])}, decision {step['decision']}.{flip}")

    subq = tco.get("subq") or {}
    for target, probability in (subq.get("probabilities") or {}).items():
        add(f"subq:{target}", f"SubQ Snap ({subq.get('model')}) P(migration to {target} is net-positive in 12 months) = {probability:.6f}.")
    if (walk.get("subq") or {}).get("on_paper") is not None:
        add("subq:on_paper", f"SubQ asked with paper facts only: P(net-positive) = {walk['subq']['on_paper']:.6f}; with the evidence: {walk['subq'].get('with_evidence')}.")

    for probe in runs.get("probes", []):
        add(f"probe:{probe['probe_id']}", _probe_text(probe))
    reads = (runs.get("replay") or {}).get("reads", {}).get("shapes", [])
    for shape in sorted(reads, key=lambda item: -(item.get("target_p95_ms") or 0))[:6]:
        add(f"replay:{shape.get('shape_id', shape['collection'])}", f"Replay of {', '.join(shape['features']) or 'other'} reads on {shape['collection']}: p95 MongoDB {shape.get('source_p95_ms')} ms, FerretDB {shape.get('target_p95_ms')} ms, {shape['replayed']} replayed.")

    for item in ledger.get("items", []):
        refs = sorted({source["ref"] for source in item["sources"] if source["type"] == "code"})[:4]
        kinds = sorted({source["type"] for source in item["sources"]})
        add(f"ledger:{item['id']}", f"{item['feature']} ({item['status']}, confidence {item['confidence']}, evidence {', '.join(kinds)}): {item['behavior']}. Code: {', '.join(refs) or 'none'}.")
    for behavior in ledger.get("behaviors", []):
        add(f"behavior:{behavior['id']}", f"{behavior['statement']} ({behavior['status']}, confidence {behavior['confidence']}).")

    if scoreboard:
        add("rules:summary", f"Hidden business rules recovered blind: {scoreboard['found']}/{scoreboard['total']} found, {scoreboard['labels_correct']}/{scoreboard['total']} labeled correctly. The answer key (docs/RULES.md) is never read by the extractors.")
        for row in scoreboard.get("rows", []):
            add(f"rule:{row['rule']}", f"{row['title']}: found {row['found']}, labeled {row['label']} (expected {row['expected_label']}).")

    for index, check in enumerate(cutover.get("checks", []), 1):
        add(f"check:{index}", f"Cutover rehearsal, clean run: {'PASS' if check['ok'] else 'FAIL'} [{check['area']}] {check['check']}.")
    for row in cutover.get("business_questions", []):
        add(f"question:{row['id']}", f"After one day on FerretDB, '{row['question']}' was {row['verdict']}.")
    if drill:
        failed = [check["check"] for check in drill.get("checks", []) if not check["ok"]]
        add("drill:summary", f"Cutover drill with injected faults ({_clip(drill.get('injected_faults'), 120)}): {len(failed)} checks failed, including: {'; '.join(failed[:6])}.")

    pairs: list = []
    _flatten("", tco.get("assumptions") or {}, pairs)
    for path, value in pairs[:120]:
        add(f"assumption:{path}", f"{path} = {value}")

    return {"profile": profile, "facts": facts}


def render(pack: dict) -> str:
    return "\n".join(f"[{fact['id']}] {fact['text']}" for fact in pack["facts"])


def check_citations(pack: dict, citations: list[str]) -> tuple[list[str], list[str]]:
    """Keeps citations that name a fact in the pack, in order and without repeats; returns (kept, dropped)."""
    known = {fact["id"] for fact in pack["facts"]}
    kept, dropped = [], []
    for citation in citations:
        cleaned = citation.strip().strip("[]")
        if cleaned not in known:
            # The model sometimes drops the kind prefix ("people.engineer_cost_per_hour"); accept it only when unambiguous.
            matches = [fact_id for fact_id in known if fact_id.endswith(":" + cleaned)]
            cleaned = matches[0] if len(matches) == 1 else cleaned
        (kept if cleaned in known else dropped).append(cleaned)
    return list(dict.fromkeys(kept)), dropped


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    result = build(parser.parse_args().profile)
    text = render(result)
    print(text)
    print(f"\n{len(result['facts'])} facts, {len(text):,} characters")
