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
            children.append(_gap_button(profile, target, gap, f"Dig into {gap['feature']}"))
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


EXAMPLE_QUESTIONS = [
    "Why does FerretDB lose the transaction rollback?",
    "What would change the verdict?",
    "How was the rewrite cost for F6 estimated?",
    "Which business rules survived the cutover?",
]


def answer_card(profile: str, result: dict, tco: dict | None) -> tuple[str, dict]:
    """A grounded answer: the text, the facts it cites, and buttons into the cited gaps or the reversal walk."""
    children = [{"type": "text", "value": result["text"], "style": "body"}]
    buttons = []
    if result["citations"]:
        children += [{"type": "divider"}, {"type": "heading", "value": "Evidence cited", "level": 3}]
        for citation in result["citations"][:6]:
            children.append({"type": "text", "style": "muted", "value": f"[{citation['id']}] {_clip(citation['text'], 200)}"})
            kind, _, rest = citation["id"].partition(":")
            if kind == "gap" and tco and len(buttons) < 3:
                target, _, feature = rest.rpartition(":")
                gap = next((item for name, item in blocking_gaps(tco) if name == target and item["feature"] == feature), None)
                if gap and not any(button["label"].endswith(feature) and target in button["label"] for button in buttons):
                    buttons.append(_gap_button(profile, target, gap, f"Dig into {feature} ({target})"))
            elif kind == "step" and not any(button["label"] == "See the reversal" for button in buttons):
                buttons.append({"type": "button", "label": "See the reversal", "primary": False, "action": {"selection": {"action": "view", "view": "reversal", "profile": profile}}})
    elif not result["answerable"]:
        for question in EXAMPLE_QUESTIONS[:3]:
            buttons.append({"type": "button", "label": question, "primary": False, "action": {"selection": {"action": "ask", "question": question, "profile": profile}}})
    if result.get("dropped"):
        children.append({"type": "text", "style": "muted", "value": f"{len(result['dropped'])} citation(s) the model gave didn't match any evidence and were removed."})
    children += [{"type": "divider"}, {"type": "group", "direction": "row", "gap": 8, "children": buttons + [_back_button(profile)]}]
    title = "Answer from the evidence" if result["answerable"] else "Not covered by the evidence"
    root = _section(title, f"{APP_NAME.get(profile, profile)}: only facts from this assessment, each one cited", children)
    return result["text"], metadata("custom", {"root": root}, 680)


def help_card(profile: str) -> tuple[str, dict]:
    children = [
        {"type": "text", "style": "body", "value": "I assessed whether ShopLog, a 10-year-old MongoDB app, can move to FerretDB or AWS DocumentDB, and ShopLog Lite as a control. Ask me anything about the result, or open a view."},
        {"type": "heading", "value": "Try asking", "level": 3},
    ]
    for question in EXAMPLE_QUESTIONS:
        children.append({"type": "button", "label": question, "primary": False, "action": {"selection": {"action": "ask", "question": question, "profile": profile}}})
    children += [{"type": "divider"}, {"type": "group", "direction": "row", "gap": 8, "children": [
        {"type": "button", "label": "What breaks", "primary": True, "action": {"selection": {"action": "view", "view": "gaps", "profile": profile}}},
        {"type": "button", "label": "How the evidence reversed it", "primary": False, "action": {"selection": {"action": "view", "view": "reversal", "profile": profile}}},
    ]}, _back_button(profile)]
    root = _section("Compat Archaeologist", "Before you migrate off MongoDB", children)
    return "I can show the decision, each gap with its evidence, or answer questions from the evidence.", metadata("custom", {"root": root}, 640)


def _gap_button(profile: str, target: str, gap: dict, label: str, primary: bool = False) -> dict:
    selection = {"action": "view", "view": "gap", "profile": profile, "target": target, "gap": gap["key"]}
    return {"type": "button", "label": label, "primary": primary, "action": {"selection": selection}}


# Cutover checks that exercise each feature, matched on the check's own wording.
CUTOVER_LINKS = {
    "F6": ("collation", "case is ignored", "different case"),
    "F7": ("partialFilterExpression", "expireAfterSeconds", "coupon"),
    "F8": ("$jsonSchema", "order schema", "unknown status"),
    "F9": ("weights",),
}
TEXT_LIMIT = 240


def load_bundle(profile: str) -> dict:
    """Everything a detail card or an answer may cite, read once from the pipeline output."""
    from archaeologist.config import OUT_DIR, ROOT

    def read(path):
        return json.loads(path.read_text()) if path.exists() else None

    folder = OUT_DIR / profile
    definitions = read(ROOT / "probes" / f"{profile}.json") or []
    return {
        "profile": profile,
        "tco": read(folder / "tco.json"),
        "ledger": read(folder / "ledger.json"),
        "runs": read(folder / "runs.json"),
        "cutover": read(folder / "cutover_clean.json"),
        "scoreboard": read(folder / "scoreboard.json"),
        "probes": {probe["probe_id"]: probe for probe in definitions},
    }


def blocking_gaps(tco: dict) -> list[tuple[str, dict]]:
    """FerretDB first (observed), then the inferred target, in the order the reversal walk uses."""
    from archaeologist.tco import KIND_ORDER

    ordered = []
    for target, info in sorted(tco["targets"].items(), key=lambda pair: pair[1]["evidence"] != "OBSERVED"):
        gaps = sorted((gap for gap in info["gaps"] if gap["blocking"]), key=lambda gap: (KIND_ORDER.get(gap["kind"], 9), gap["feature"]))
        ordered += [(target, gap) for gap in gaps]
    return ordered


def find_gap(tco: dict, feature: str) -> tuple[str, str] | None:
    for target, gap in blocking_gaps(tco):
        if gap["feature"].lower() == feature.lower() or gap["key"] == feature:
            return target, gap["key"]
    return None


def _clip(value, limit: int = TEXT_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _reversal_step(tco: dict, target: str, gap: dict) -> tuple[int, dict] | None:
    walk = tco.get("reversal") or {}
    if walk.get("target") != target:
        return None
    steps = walk.get("steps", [])
    for index, step in enumerate(steps):
        if step.get("gap_key") == gap["key"]:
            return index, step
    # Walks written before steps carried gap_key: the steps follow the blocking gaps in order.
    keys = [item["key"] for name, item in blocking_gaps(tco) if name == target]
    if gap["key"] in keys and keys.index(gap["key"]) + 1 < len(steps):
        index = keys.index(gap["key"]) + 1
        return index, steps[index]
    return None


def _probe_children(bundle: dict, evidence: dict) -> list[dict]:
    run = next((probe for probe in (bundle.get("runs") or {}).get("probes", []) if probe["probe_id"] == evidence["probe_id"]), None)
    detail = evidence.get("detail") or {}
    at_step = detail.get("at_step")
    children = [{"type": "heading", "value": f"Probe {evidence['probe_id']}: {evidence['verdict']}", "level": 3},
                {"type": "text", "value": evidence["title"], "style": "muted"}]
    difference = detail.get("difference")
    if difference:
        children.append({"type": "text", "style": "body", "value":
                         f"At step '{at_step}', {difference.get('path')}: MongoDB: {_clip(difference.get('source'), 80)} | FerretDB: {_clip(difference.get('target'), 80)}"})
    elif detail.get("message"):
        children.append({"type": "text", "style": "body", "value": f"At step '{at_step}', FerretDB returned error {detail.get('code')}: {_clip(detail['message'], 160)}. MongoDB completed the same step."})
    definition = bundle["probes"].get(evidence["probe_id"]) or {}
    step = next((item for item in definition.get("steps", []) if item.get("label") == at_step), None)
    if step:
        sent = step.get("command_ejson") or f"{step.get('op')} (driver operation)"
        children.append({"type": "text", "style": "muted", "value": f"Command at that step: {_clip(sent)}"})
    if run and run.get("source", {}).get("outcome"):
        children.append({"type": "text", "style": "muted", "value": f"Run outcome: MongoDB {run['source']['outcome']}, FerretDB {run['target']['outcome']}."})
    return children


def gap_detail_card(profile: str, bundle: dict, target: str, key: str) -> tuple[str, dict]:
    """One blocking gap with everything behind it: what diverged, what was sent, where the code is, what it costs."""
    tco = bundle["tco"]
    ordered = blocking_gaps(tco)
    position = next((index for index, (name, gap) in enumerate(ordered) if name == target and gap["key"] == key), None)
    if position is None:
        narration, card = gaps_card(profile, tco)
        return f"{key} is not a blocking gap for {target}. {narration}", card
    _, gap = ordered[position]
    observed = tco["targets"][target]["evidence"] == "OBSERVED"
    badges = [{"type": "badge", "label": f"{target} ({tco['targets'][target]['evidence']})", "variant": "info"},
              {"type": "badge", "label": gap["kind"].replace("_", " "), "variant": "warning" if gap["kind"] in {"silent", "performance"} else "info"},
              {"type": "badge", "label": f"Confidence {gap['confidence']:.2f}", "variant": "info"}]
    children = [{"type": "group", "direction": "row", "gap": 8, "children": badges}]
    if gap["kind"] == "silent":
        children.append({"type": "text", "style": "emphasis", "value": "Silent: at least one probe got a wrong result with no error, so the application can't tell it went wrong."})

    if gap["kind"] == "performance":
        for item in gap["evidence"]:
            ratio = item["target_p95_ms"] / item["source_p95_ms"] if item["source_p95_ms"] else None
            children.append({"type": "text", "style": "body", "value":
                             f"Replayed shape {item['shape_id']} ({' → '.join(item['stages'])}): p95 MongoDB {item['source_p95_ms']} ms | FerretDB {item['target_p95_ms']} ms"
                             + (f", {ratio:,.0f}× slower" if ratio else "") + f" over {item['samples']} samples."})
    elif gap["kind"] == "documented_conflict":
        for item in gap["evidence"]:
            children.append({"type": "text", "style": "body", "value": f"{item['conflict']}. Source: {item['url']}"})
        if not observed:
            children.append({"type": "text", "style": "muted", "value": "Inferred from documentation; nothing was measured on this engine."})
    else:
        for evidence in gap["evidence"][:3]:
            children.append({"type": "divider"})
            children += _probe_children(bundle, evidence)
    replay = gap.get("replay") or {}
    if replay.get("replayed"):
        children.append({"type": "text", "style": "body", "value": f"Workload replay: {replay['different']} of {replay['replayed']} recorded operations returned different results, {replay['errors']} errored."})
    for rejection in gap.get("schema_rejections") or []:
        children.append({"type": "text", "style": "body", "value": f"Schema step {rejection['step']} was refused (code {rejection.get('code')})."})

    item = next((entry for entry in (bundle.get("ledger") or {}).get("items", []) if entry["id"] == gap["feature"]), None)
    if item:
        refs = []
        for source in sorted((s for s in item["sources"] if s["type"] == "code"), key=lambda s: s.get("origin") == "static_scan"):
            if source["ref"] not in refs:
                refs.append(source["ref"])
        children.append({"type": "divider"})
        children.append({"type": "heading", "value": "Where the app depends on it", "level": 3})
        children.append({"type": "text", "style": "body", "value": f"{item['behavior'][:200]}"})
        if refs:
            children.append({"type": "text", "style": "muted", "value": "Code: " + ", ".join(refs[:4])})

    checks = [check for check in (bundle.get("cutover") or {}).get("checks", [])
              if any(word.lower() in check["check"].lower() for word in CUTOVER_LINKS.get(gap["feature"], ()))]
    if checks and observed:
        children.append({"type": "divider"})
        children.append({"type": "heading", "value": "Cutover rehearsal", "level": 3})
        for check in checks[:5]:
            children.append({"type": "text", "style": "body", "value": f"{'PASS' if check['ok'] else 'FAIL'}: {check['check']}"})

    estimate = gap.get("estimate")
    step = _reversal_step(tco, target, gap)
    if estimate or step:
        children.append({"type": "divider"})
        children.append({"type": "heading", "value": "What it costs", "level": 3})
    if estimate:
        children.append({"type": "text", "style": "body", "value": f"Fix (Muse, inferred): {estimate['hours_low']:.0f} to {estimate['hours_high']:.0f} h, base {estimate['hours_base']:.0f} h. {_clip(estimate['approach'], 320)}"})
    if step:
        index, entry = step
        flipped = (tco["reversal"].get("flipped_at") == index)
        children.append({"type": "text", "style": "emphasis" if flipped else "body", "value":
                         f"Step {index} of the reversal: {_money(entry['change'])}, running net {_money(entry['net'])}" + (". The decision flips here." if flipped else ".")})

    nav = []
    if position > 0:
        previous_target, previous = ordered[position - 1]
        nav.append(_gap_button(profile, previous_target, previous, f"Previous: {previous['feature']}"))
    if position + 1 < len(ordered):
        next_target, upcoming = ordered[position + 1]
        nav.append(_gap_button(profile, next_target, upcoming, f"Next: {upcoming['feature']}", primary=True))
    nav.append({"type": "button", "label": "All gaps", "primary": False, "action": {"selection": {"action": "view", "view": "gaps", "profile": profile}}})
    children += [{"type": "divider"}, {"type": "group", "direction": "row", "gap": 8, "children": nav}, _back_button(profile)]

    root = _section(f"{gap['feature']}: {gap['name']}", f"{APP_NAME.get(profile, profile)} on {target}, gap {position + 1} of {len(ordered)}", children)
    narration = f"{gap['feature']} on {target}: {gap['name']} ({gap['kind'].replace('_', ' ')})."
    if step:
        narration += f" It moves the 12-month net by {_money(step[1]['change'])}."
    return narration, metadata("custom", {"root": root}, 720)
