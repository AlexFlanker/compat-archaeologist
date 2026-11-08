"""ASI:One Interactive Card payloads built from pipeline results. Pure functions, so they can be tested."""

from __future__ import annotations

import json

from archaeologist.brief import probability as format_probability

REPO_URL = "https://github.com/AlexFlanker/compat-archaeologist"
MAX_PAYLOAD_BYTES = 64 * 1024
APP_NAME = {"shoplog": "ShopLog", "lite": "ShopLog Lite"}
DECISION_BADGE = {
    "GO": "success",
    "SAFE_BUT_NOT_WORTH_IT": "info",
    "VERIFY_FIRST": "warning",
    "FIX_FIRST": "warning",
    "STAY": "warning",
    "STAY_UNPRICED": "warning",
}


def metadata(kind: str, payload: dict, width: int = 600) -> dict[str, str]:
    encoded = json.dumps(payload)
    if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("card payload exceeds 64 KB")
    return {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": kind,
        "card_payload": encoded,
        "preferred_drawer_width_px": str(width),
    }


def _money(value: float) -> str:
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def decision_card(profile: str, tco: dict, ledger: dict, scoreboard: dict | None = None, cutover: dict | None = None) -> tuple[str, dict]:
    """The headline: one row per target, with the views a decision maker asks for next."""
    rows = []
    for name, info in tco["targets"].items():
        blocking = sum(1 for gap in info["gaps"] if gap["blocking"])
        rows.append({"label": f"{name} ({info['evidence']})", "value": f"{info['decision_text']}. Net 12 months: {_money(info['economics']['net']['base'])}. Blocking gaps: {blocking}."})
    subq = tco.get("subq", {})
    if subq.get("available"):
        rows.append({"label": "SubQ Snap: P(net-positive)", "value": ", ".join(f"{name} {format_probability(probability)}" for name, probability in subq["probabilities"].items())})
    walk = tco.get("reversal")
    if walk:
        first, last = walk["steps"][0], walk["steps"][-1]
        flip = walk["flipped_at"]
        story = f"On paper {_money(first['net'])} ({first['decision']}) → with evidence {_money(last['net'])} ({last['decision']})"
        if flip:
            story += f"; flipped at: {walk['steps'][flip]['step']}"
        rows.insert(0, {"label": "How the decision reversed" if flip else "Paper case vs evidence", "value": story})
    observed = sum(1 for item in ledger["items"] if item["status"] == "OBSERVED")
    rows.append({"label": "Usage spec reconstructed", "value": f"{len(ledger['items'])} items ({observed} observed), {len(ledger.get('behaviors', []))} business behaviors"})
    if scoreboard:
        rows.append({"label": "Hidden rules recovered blind", "value": f"{scoreboard['found']}/{scoreboard['total']} found, {scoreboard['labels_correct']}/{scoreboard['total']} labeled correctly"})
    after = (cutover or {}).get("business_questions", [])
    if after:
        changed = sum(1 for row in after if row["verdict"] != "same")
        rows.append({"label": "Business numbers after cutover", "value": f"{changed} of {len(after)} answers differ after one day on FerretDB"})
    payload = {
        "title": f"Migration decision: {APP_NAME.get(profile, profile)}",
        "summary_rows": rows,
        "sub_options": {
            "name": "view",
            "kind": "radio",
            "label": "Show me",
            "choices": [
                {"value": "reversal", "label": "How the evidence reversed it", "secondary_text": "Paper case, then each finding, step by step"},
                {"value": "gaps", "label": "What breaks", "secondary_text": "Each gap with its evidence"},
                {"value": "ledger", "label": "Usage-Spec Ledger", "secondary_text": "What the app really depends on"},
                {"value": "latency", "label": "Latency and query plans", "secondary_text": "Replay on both engines"},
            ],
        },
        "ctas": [
            {"label": "Show", "selection": {"action": "view", "profile": profile}, "primary": True},
            {"label": "Run the control app", "selection": {"action": "assess", "profile": "lite"}},
        ],
    }
    narration = headline(profile, tco)
    return narration, metadata("detail", payload)


def headline(profile: str, tco: dict) -> str:
    ferretdb = tco["targets"]["FerretDB"]
    app = APP_NAME.get(profile, profile)
    blocking = [gap for gap in ferretdb["gaps"] if gap["blocking"]]
    memo_link = f"{REPO_URL}/blob/main/out/{profile}/memo.md"
    if ferretdb["decision"] == "GO":
        text = f"{app}: the migration is fine. FerretDB matched MongoDB on everything this app does."
    elif ferretdb["decision"] == "SAFE_BUT_NOT_WORTH_IT":
        text = f"{app}: safe to migrate, but it doesn't pay off in 12 months at this size."
    else:
        text = f"{app}: stay on MongoDB for now. FerretDB showed {len(blocking)} blocking gaps, and AWS DocumentDB is unverified."
    return f"{text} Full memo with evidence: {memo_link}"


def _section(title: str, subtitle: str, children: list[dict]) -> dict:
    return {"type": "section", "title": title, "subtitle": subtitle, "children": children}


def gaps_card(profile: str, tco: dict) -> tuple[str, dict]:
    items = []
    for target, info in tco["targets"].items():
        for gap in info["gaps"]:
            if not gap["blocking"]:
                continue
            variant = "warning" if gap["kind"] in {"silent", "performance"} else "info"
            detail = _gap_detail(gap)
            estimate = gap.get("estimate")
            children = [
                {"type": "group", "direction": "row", "gap": 8, "children": [
                    {"type": "badge", "label": target, "variant": "info"},
                    {"type": "badge", "label": gap["kind"], "variant": variant},
                ]},
                {"type": "heading", "value": f"{gap['feature']}: {gap['name']}", "level": 3},
                {"type": "text", "value": detail, "style": "body"},
            ]
            if estimate:
                children.append({"type": "text", "value": f"Rewrite estimate (Muse, inferred): {estimate['hours_base']:.0f} h. {estimate['approach']}", "style": "muted"})
            items.append({"children": children})
    found = len(items)
    if not items:
        items.append({"children": [{"type": "text", "value": "No blocking gaps. Every probe and replayed operation matched.", "style": "emphasis"}]})
    root = _section(
        f"What breaks: {APP_NAME.get(profile, profile)}",
        "FerretDB rows are observed by probes and replay. DocumentDB rows are inferred from AWS docs.",
        [{"type": "list", "items": items[:12]}, {"type": "divider"}, _back_button(profile)],
    )
    narration = f"{found} blocking gap(s) found." if found else "No blocking gaps found."
    return narration, metadata("custom", {"root": root}, 680)


def _gap_detail(gap: dict) -> str:
    if gap["kind"] == "performance":
        evidence = gap["evidence"][0]
        return f"p95 {evidence['source_p95_ms']} ms on MongoDB vs {evidence['target_p95_ms']} ms on FerretDB ({evidence['samples']} replayed)."
    if gap["kind"] == "documented_conflict":
        return "; ".join(item["conflict"] for item in gap["evidence"])
    parts = []
    for probe in gap["evidence"][:2]:
        detail = probe.get("detail") or {}
        if detail.get("difference"):
            difference = detail["difference"]
            parts.append(f"{probe['title']}: MongoDB gave {difference.get('source')}, FerretDB gave {difference.get('target')}.")
        elif detail.get("message"):
            parts.append(f"{probe['title']}: failed with code {detail.get('code')}.")
    replay = gap.get("replay") or {}
    if replay.get("different"):
        parts.append(f"{replay['different']} of {replay['replayed']} replayed operations returned different results.")
    for rejection in gap.get("schema_rejections") or []:
        parts.append(f"Schema step {rejection['step']} was refused (code {rejection.get('code')}).")
    if gap["kind"] == "silent":
        parts.append("No error is raised, so the application can't tell.")
    return " ".join(parts) or gap["verdict"]


def ledger_card(profile: str, ledger: dict) -> tuple[str, dict]:
    items = []
    for item in ledger["items"]:
        kinds = sorted({source["type"] for source in item["sources"]})
        confidence = f"{item['confidence']:.2f}" if item["confidence"] is not None else "unknown"
        items.append(
            {
                "children": [
                    {"type": "group", "direction": "row", "gap": 8, "children": [
                        {"type": "heading", "value": f"{item['id']} {item['feature']}", "level": 3},
                        {"type": "badge", "label": item["status"], "variant": "success" if item["status"] == "OBSERVED" else "warning"},
                    ]},
                    {"type": "text", "value": f"Evidence: {', '.join(kinds)}. Confidence {confidence} ({item['confidence_basis']}).", "style": "muted"},
                ]
            }
        )
    root = _section(
        "Usage-Spec Ledger",
        "OBSERVED: seen at runtime or in the catalog. INFERRED: only in code.",
        [{"type": "list", "items": items[:16]}, {"type": "divider"}, _back_button(profile)],
    )
    return f"The app depends on {len(ledger['items'])} MongoDB features.", metadata("custom", {"root": root}, 640)


def latency_card(profile: str, runs: dict, explain: dict | None) -> tuple[str, dict]:
    children = []
    for shape in sorted(runs["replay"]["reads"]["shapes"], key=lambda item: -(item.get("target_p95_ms") or 0))[:5]:
        if shape.get("source_p50_ms") is None or shape.get("target_p50_ms") is None:
            continue
        children.append({"type": "text", "value": f"{', '.join(shape['features']) or 'other'} on {shape['collection']}: p50 {shape['source_p50_ms']} ms on MongoDB vs {shape['target_p50_ms']} ms on FerretDB ({shape['replayed']} replayed)", "style": "body"})
    for comparison in (explain or {}).get("comparisons", [])[:2]:
        children.append({"type": "divider"})
        children.append({"type": "heading", "value": f"Plan: {', '.join(comparison['features'])} on {comparison['collection']}", "level": 3})
        children.append({"type": "text", "value": f"MongoDB: {comparison['source'].get('plan', '')[:300]}", "style": "muted"})
        children.append({"type": "text", "value": f"FerretDB (Postgres): {comparison['target'].get('plan', '')[:300]}", "style": "muted"})
    children += [{"type": "divider"}, _back_button(profile)]
    root = _section("Latency and query plans", "Both engines in Docker on one machine, same limits, alternating order.", children)
    return "Replay latency and the query plans behind the slowest paths.", metadata("custom", {"root": root}, 720)


def reversal_card(profile: str, tco: dict) -> tuple[str, dict]:
    """The paper case, then each finding as a step that moves the 12-month net, with the flip marked."""
    walk = tco.get("reversal")
    if not walk:
        root = _section("How the evidence reversed it", "No reversal data for this assessment.", [_back_button(profile)])
        return "No reversal data for this assessment.", metadata("custom", {"root": root}, 680)
    flip = walk["flipped_at"]
    items = []
    for index, step in enumerate(walk["steps"]):
        flipped = flip is not None and index == flip
        badges = [{"type": "badge", "label": f"Step {index}", "variant": "info"},
                  {"type": "badge", "label": step["decision"], "variant": "success" if step["decision"] == "Migrate" else "warning"}]
        if flipped:
            badges.append({"type": "badge", "label": "Decision flips here", "variant": "warning"})
        change = "" if step["change"] is None else f"{_money(step['change'])} → "
        items.append({"children": [
            {"type": "group", "direction": "row", "gap": 8, "children": badges},
            {"type": "heading", "value": step["step"], "level": 3},
            {"type": "text", "value": f"{change}running net {_money(step['net'])} over 12 months", "style": "emphasis" if flipped else "body"},
            {"type": "text", "value": step["evidence"], "style": "muted"},
        ]})
    children = [{"type": "list", "items": items}]
    subq = walk.get("subq") or {}
    if subq.get("on_paper") is not None and subq.get("with_evidence") is not None:
        children += [{"type": "divider"}, {"type": "heading", "value": "Same question to SubQ, twice", "level": 3},
                     {"type": "text", "value": f"Paper facts only: P(net-positive) {format_probability(subq['on_paper'])}. With our evidence: {format_probability(subq['with_evidence'])}. SubQ is only as good as its facts; this is the evidence that changed its answer.", "style": "body"}]
    children += [{"type": "divider"}, _back_button(profile)]
    first, last = walk["steps"][0], walk["steps"][-1]
    subtitle = f"{walk['target']}: {_money(first['net'])} ({first['decision']}) on paper → {_money(last['net'])} ({last['decision']}) with evidence"
    root = _section("How the evidence reversed the decision", subtitle, children)
    narration = (f"On paper {_money(first['net'])}, migrate. First finding: {walk['steps'][flip]['step']}, and the decision flips. Final: {_money(last['net'])}, {last['decision'].lower()}."
                 if flip else f"The evidence kept the decision: {_money(first['net'])} on paper, {_money(last['net'])} with evidence.")
    return narration, metadata("custom", {"root": root}, 720)


def _back_button(profile: str) -> dict:
    return {"type": "button", "label": "Back to decision", "primary": False, "action": {"selection": {"action": "decision", "profile": profile}}}
