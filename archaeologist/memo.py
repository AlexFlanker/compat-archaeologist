"""Writes the decision memo. Each claim links to the evidence behind it. No number is rounded into a story."""

from __future__ import annotations

import json

from archaeologist import brief
from archaeologist.config import OUT_DIR
from archaeologist.features import FEATURES
from archaeologist.tco import DOCUMENTDB, FERRETDB

VERDICT_LABEL = {
    "equal": "✅ Same behavior",
    "ranking_diff": "🟡 Same matches, different ranking",
    "result_diff": "❌ Different result",
    "unsupported": "❌ Not supported",
    "error": "❌ Errors",
    "timeout": "❌ Times out",
    "probe_invalid": "⚪ Unverified (probe failed on MongoDB)",
    "no_probe": "⚪ Not tested",
}
APP_NAME = {"shoplog": "ShopLog", "lite": "ShopLog Lite"}


def _money(value: float | None) -> str:
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _ms(value) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f} ms" if value >= 100 else f"{value:.2f} ms"


def _code_link(ref: str) -> str:
    path, _, line = ref.partition(":")
    return f"[{ref}](../../{path}#L{line})"


def _source_summary(item: dict) -> str:
    kinds = {source["type"] for source in item["sources"]}
    labels = []
    if "data" in kinds:
        labels.append("data")
    if "querylog" in kinds:
        ops = sum(source.get("count", 0) for source in item["sources"] if source["type"] == "querylog")
        labels.append(f"query log ({ops:,} ops)")
    if "atlas-performance-advisor" in kinds:
        labels.append("Atlas Performance Advisor")
    if "code" in kinds:
        labels.append("code")
    return " + ".join(labels)


def _confidence(record: dict) -> str:
    return f"{record['confidence']:.2f}" if record["confidence"] is not None else "unknown"


def _ferretdb_cell(feature: str, runs: dict, documented: dict | None = None) -> str:
    info = runs["features"].get(feature)
    if documented and feature in documented:
        entry = documented[feature]
        return f"🟠 INFERRED: [not supported per FerretDB docs]({entry['url']})<br>{entry['limits']}"
    if not info:
        return VERDICT_LABEL["no_probe"]
    label = VERDICT_LABEL.get(info["verdict"], info["verdict"])
    probes = info["probes"]
    if probes:
        same = sum(1 for probe in probes if probe["verdict"] == "equal")
        label += f"<br>{same}/{len(probes)} probes equal"
    replay = info["replay"]
    if replay["replayed"]:
        label += f"<br>{replay['equal']}/{replay['replayed']} replayed ops equal"
    if any(probe.get("detail", {}).get("silent") for probe in runs["probes"] if probe["feature_id"] == feature):
        label += "<br>**Silent: no error raised**"
    return label


def _docdb_cell(feature: str, docdb: dict) -> str:
    info = docdb.get(feature)
    if not info:
        return "⚪ Not reviewed"
    if info["verdict"] == "documented_conflict":
        return f"🟠 [Docs conflict with this app's usage]({info['url']})<br>" + "<br>".join(info["conflicts"])
    if info["verdict"] == "documented_supported":
        return f"🔵 [Documented as supported]({info['url']})<br>Since {info['since']}"
    return "⚪ Not covered in our review"


def _probe_findings(runs: dict) -> list[str]:
    lines = []
    for probe in runs["probes"]:
        verdict = probe["verdict"]
        if verdict in {"equal", "probe_invalid"}:
            continue
        detail = probe.get("detail", {})
        heading = f"- **{probe['probe_id']}** ({VERDICT_LABEL.get(verdict, verdict)}): {probe.get('title') or ''}."
        if detail.get("difference"):
            difference = detail["difference"]
            heading += (
                f" At step `{detail.get('at_step')}`, MongoDB gave `{difference.get('source')}` and FerretDB gave `{difference.get('target')}`"
                f" (at `{difference.get('path')}`)."
            )
        elif detail.get("message"):
            heading += f" Step `{detail.get('at_step')}` failed with code {detail.get('code')}: \"{str(detail['message'])[:170]}\"."
        lines.append(heading)
    return lines


def _latency_rows(runs: dict) -> list[str]:
    rows = []
    shapes = sorted(runs["replay"]["reads"]["shapes"], key=lambda shape: -(shape.get("target_p95_ms") or 0))
    for shape in shapes:
        source, target = shape.get("source_p50_ms"), shape.get("target_p50_ms")
        if source is None or target is None:
            continue
        ratio = target / source if source else None
        ratio_text = f"{ratio:,.1f}×" if ratio and ratio >= 1 else (f"{1 / ratio:,.1f}× faster" if ratio else "n/a")
        features = ", ".join(shape["features"]) or "other"
        stages = " → ".join(shape["stages"][:4]) or "find"
        note = " (time budget reached)" if shape["budget_exhausted"] else ""
        rows.append(
            f"| {features} | {shape['collection']}: {stages} | {shape['replayed']}/{shape['in_log']}{note} "
            f"| {_ms(source)} / {_ms(shape.get('source_p95_ms'))} | {_ms(target)} / {_ms(shape.get('target_p95_ms'))} | {ratio_text} |"
        )
    return rows


def _headline(profile: str, tco: dict) -> str:
    ferretdb, docdb = tco["targets"][FERRETDB], tco["targets"][DOCUMENTDB]
    app = APP_NAME.get(profile, profile)
    blocking = [gap for gap in ferretdb["gaps"] if gap["blocking"]]
    if ferretdb["decision"] == "GO":
        return f"**The migration is fine for {app}.** FerretDB matched MongoDB on everything this app does, and moving saves {_money(ferretdb['economics']['net']['base'])} over 12 months in the base case."
    if ferretdb["decision"] == "SAFE_BUT_NOT_WORTH_IT":
        return f"**The migration is technically safe for {app}, but it doesn't pay off in 12 months at this size.** FerretDB matched MongoDB on everything this app does."
    names = ", ".join(sorted({gap["feature"] for gap in blocking if gap["kind"] != "performance"}, key=lambda key: int(key[1:]) if key[1:].isdigit() else 99))
    slow = sum(1 for gap in blocking if gap["kind"] == "performance")
    if slow:
        names = (names + ", " if names else "") + (f"{slow} slow reporting path" + ("s" if slow > 1 else ""))
    return (
        f"**Stay on MongoDB for {app} for now.** On FerretDB, {len(blocking)} blocking gaps showed up ({names or 'none'}). "
        f"AWS DocumentDB documents support for most features but conflicts with this app's usage in {sum(1 for gap in docdb['gaps'] if gap['blocking'])} place(s), "
        "and nothing about it has been tested. Run these probes on a real cluster before considering it."
    )


def _cost_section(tco: dict) -> list[str]:
    lines = [
        "| | FerretDB (self-managed, OBSERVED gaps) | AWS DocumentDB (INFERRED gaps) |",
        "|---|---|---|",
    ]
    ferretdb, docdb = tco["targets"][FERRETDB], tco["targets"][DOCUMENTDB]
    atlas = ferretdb["economics"]["annual_costs"]["MongoDB Atlas"]

    def row(label, getter):
        lines.append(f"| {label} | {getter(ferretdb)} | {getter(docdb)} |")

    row("Atlas today, per year", lambda info: _money(atlas))
    row("Target infrastructure, per year", lambda info: _money(info["economics"]["annual_costs"][FERRETDB if info is ferretdb else DOCUMENTDB]))
    row("  of which a separate search service", lambda info: _money(info["economics"].get("search_service_annual", 0)))
    row("Infrastructure saved", lambda info: _money(info["economics"]["infra_savings"]))
    row("Self-managed operations", lambda info: f"-{_money(info['economics']['breakdown']['base']['ops_cost'])}")
    row("Migration baseline (assumed hours)", lambda info: f"{info['economics']['breakdown']['base']['migration_hours']} h")
    row("Rewrites (Muse estimate, INFERRED)", lambda info: (f"{info['economics']['breakdown']['base']['rewrite_hours']:,.0f} h" if not info["economics"]["rewrite_estimates_missing"] else "not estimated"))
    row("Residual incident risk (assumed)", lambda info: f"-{_money(info['economics']['breakdown']['base']['residual_risk_cost'])}")
    row("**Net over 12 months, base**", lambda info: f"**{_money(info['economics']['net']['base'])}**")
    row("Net, best to worst case", lambda info: f"{_money(info['economics']['net']['best'])} to {_money(info['economics']['net']['worst'])}")
    row("Rewrite hours the savings can pay for", lambda info: f"{info['economics']['break_even_rewrite_hours']:,.0f} h")
    row("Payback", lambda info: f"{info['economics']['payback_months']} months" if info["economics"]["payback_months"] else "Never at these numbers")

    lines += ["", "**What would change the answer.** One-time rewrite costs don't grow with fleet size, but savings do.", ""]
    lines += ["| Scenario | FerretDB net (base) | FerretDB decision | DocumentDB net (base) | DocumentDB decision |", "|---|---|---|---|---|"]
    for clusters, ferret_summary in ferretdb["sensitivity"]["clusters"].items():
        doc_summary = docdb["sensitivity"]["clusters"][clusters]
        lines.append(f"| {clusters} cluster(s) | {_money(ferret_summary['net_base'])} | {ferret_summary['decision']} | {_money(doc_summary['net_base'])} | {doc_summary['decision']} |")
    ferret_alt, doc_alt = ferretdb["sensitivity"]["atlas_per_node"], docdb["sensitivity"]["atlas_per_node"]
    lines.append(f"| Atlas billed per node (see below) | {_money(ferret_alt['net_base'])} | {ferret_alt['decision']} | {_money(doc_alt['net_base'])} | {doc_alt['decision']} |")
    lines += [
        "",
        "**Check your Atlas invoice.** MongoDB's public sources disagree on whether the listed hourly price covers a whole default cluster or one node, and that changes the baseline threefold. "
        "The base case assumes the whole cluster, because the [pricing page](https://www.mongodb.com/pricing) says M10 starts at $56.94/month, which is its hourly rate with no multiplier. "
        "[The billing docs](https://www.mongodb.com/docs/atlas/billing/cluster-configuration-costs/) describe the cost as applying to each data-bearing node. The last row shows that case.",
    ]
    return lines


def _explain_section(explain: dict | None) -> list[str]:
    if not explain:
        return []
    lines = ["", "## Why the slow paths are slow", ""]
    if not explain.get("available"):
        return lines + [f"Query plans weren't collected: {explain.get('reason')}."]
    lines.append(f"Plans from the MongoDB MCP Server's `explain` tool (`{explain['tool']}`), run read-only against each engine on the slowest replayed queries.")
    lines.append("")
    for comparison in explain["comparisons"]:
        source, target = comparison["source"], comparison["target"]
        p95 = comparison["replay_p95_ms"]
        lines.append(f"**{', '.join(comparison['features']) or 'other'} on {comparison['collection']}** (replay p95 {_ms(p95['source'])} on MongoDB, {_ms(p95['target'])} on FerretDB)")
        lines.append(f"- MongoDB: `{source.get('plan', source.get('error'))}`, {source.get('docs_examined')} documents examined.")
        lines.append(
            f"- FerretDB (Postgres plan): `{target.get('plan', target.get('error'))}`, with {target.get('full_scans')} sequential scan(s) and {target.get('nested_loops')} nested loop(s)."
        )
        lines.append("")
    return lines


def _atlas_section(atlas: dict | None) -> list[str]:
    lines = ["", "## What Atlas observed", ""]
    if not atlas:
        return lines + ["No Atlas evidence was collected for this run. `scripts/atlas_evidence.py` seeds an Atlas cluster, runs the workload, and collects Performance Advisor output through the MongoDB MCP Server."]
    lines.append(f"Collected from Atlas cluster `{atlas['cluster']}` through the MongoDB MCP Server. Atlas served as an evidence source only and wasn't part of the latency comparison.")
    lines.append(f"- Performance Advisor: {'available' if atlas['advisor_available'] else 'not available'}; {atlas['slow_operations']} slow operation(s) in its log.")
    for feature, info in atlas["slow_by_feature"].items():
        lines.append(f"  - {feature}: {info['count']} slow operation(s), slowest {info['max_ms']} ms.")
    for index in atlas["suggested_indexes"][:5]:
        lines.append(f"- Suggested index: `{json.dumps(index, default=str)[:200]}`")
    for advice in atlas["schema_advice"][:5]:
        lines.append(f"- Schema advice: `{json.dumps(advice, default=str)[:200]}`")
    for index in atlas["search_indexes"]:
        lines.append(f"- Atlas Search index `{index['name']}` on {', '.join(index['fields'])} (status {index['status']}).")
    return lines


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _outcomes(counts: dict) -> str:
    return ", ".join(f"{count} {outcome}" for outcome, count in sorted(counts.items()))


def _scoreboard_section(scoreboard: dict | None) -> list[str]:
    if not scoreboard:
        return []
    lines = ["", "## Scoreboard: hidden rules", "", "| Rule | Found | Label | Expected | Evidence |", "|---|---|---|---|---|"]
    for row in scoreboard["rows"]:
        evidence = "; ".join(f"{item['ref']}: {item['text'][:90]}" for item in row["evidence"][:2]) or ""
        lines.append(f"| {row['rule']} {row['title']} | {row['found']} | {row['label'] or ''} | {row['expected_label']} | {evidence} |")
    lines.append("")
    lines.append("Matching uses fixed patterns per rule, not a model's judgment, so a paraphrase the pattern misses counts as missed.")
    return lines


def _questions_section(questions: dict | None, cutover: dict | None) -> list[str]:
    if not questions:
        return []
    after = {row["id"]: row for row in (cutover or {}).get("business_questions", [])}
    lines = ["", "## Business questions", "", "| Question | Same data, both engines | After clean cutover and day one |", "|---|---|---|"]
    for row in questions["rows"]:
        later = after.get(row["id"])
        lines.append(f"| {row['question']} | {row['verdict']} | {later['verdict'] if later else 'n/a'} |")
    return lines


def _brief(profile, ledger, runs, self_check, tco, cutover, drill, explain, measured_bytes, upgrade, questions=None, scoreboard=None, edge=None) -> list[str]:
    assumptions = tco["assumptions"]
    app = APP_NAME.get(profile, profile)
    targets = tco["targets"]
    lines = [
        f"# Migration decision: {app}",
        "",
        "*One-page brief for the CTO or VP Engineering. The evidence and method follow in the appendix.*",
        "",
        _headline(profile, tco),
        "",
        *_reversal_lines(tco.get("reversal")),
        "| | " + " | ".join(f"{name} ({info['evidence'].lower()})" for name, info in targets.items()) + " |",
        "|---|" + "---|" * len(targets),
        "| Recommendation | " + " | ".join(info["decision_text"] for info in targets.values()) + " |",
        "| Confidence | " + " | ".join(brief.confidence_label(name, info, self_check, tco["subq"]) for name, info in targets.items()) + " |",
        "| Net over 12 months (base, best to worst) | "
        + " | ".join(f"{_money(info['economics']['net']['base'])} ({_money(info['economics']['net']['best'])} to {_money(info['economics']['net']['worst'])})" for info in targets.values())
        + " |",
    ]
    reversal = {name: brief.reversibility(name, info, runs, cutover, assumptions) for name, info in targets.items()}
    lines.append("| Reversibility (100 = easy to go back) | " + " | ".join(f"{reversal[name]['score']}/100 ({reversal[name]['status']})" for name in targets) + " |")

    risks = brief.top_risks(tco, ledger, cutover)
    lines += ["", "**Top risks**", ""]
    if not risks:
        lines.append("None blocking. Every probe, replayed operation and cutover check matched.")
    for number, risk in enumerate(risks, start=1):
        code = ", ".join(_code_link(ref) for ref in risk["code"])
        evidence = "; ".join(risk["evidence"] + ([f"code {code}"] if code else []))
        lines.append(f"{number}. **{risk['title']}** ({risk['target']}, {risk['kind'].replace('_', ' ')}). Evidence: {evidence or 'see appendix'}.")

    lines += ["", "**12-month cost, base case** (yearly figures; best and worst cases in the appendix)", ""]
    lines += ["| | " + " | ".join(targets) + " |", "|---|" + "---|" * len(targets)]
    atlas = next(iter(targets.values()))["economics"]["annual_costs"]["MongoDB Atlas"]
    lines.append("| Staying on MongoDB Atlas | " + " | ".join(_money(atlas) for _ in targets) + " |")
    labels = [
        ("infrastructure", "Target infrastructure"),
        ("io", "I/O billing"),
        ("scale_ups", "Scale-ups for slow paths (INFERRED)"),
        ("extra_services", "Extra services (search)"),
        ("operations", "Running it yourself"),
        ("migration_and_rewrites", "Migration and rewrites"),
        ("incidents", "Expected incident cost"),
    ]
    for key, label in labels:
        cells = []
        for info in targets.values():
            base_lines = info["economics"]["breakdown"]["base"]["lines"]
            cell = _money(base_lines[key])
            if key == "migration_and_rewrites":
                cell += f" ({base_lines['person_days']:.0f} person-days)"
                if info["economics"]["rewrite_estimates_missing"]:
                    cell += "; rewrites not estimated yet"
            if key == "io" and base_lines.get("plan"):
                cell += f" ({base_lines['plan']} plan)"
            cells.append(cell)
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("| **Net vs staying** | " + " | ".join(f"**{_money(info['economics']['net']['base'])}**" for info in targets.values()) + " |")

    paths = brief.path_stats(runs, assumptions)
    lines += [
        "",
        f"**Measured on replay** (synthetic dataset, {_mb(measured_bytes)}; both engines local, same limits)",
        "",
        "| Path | MongoDB p95 | FerretDB p95 | FerretDB over the timeout | Timeout used |",
        "|---|---|---|---|---|",
    ]
    for path in ("online", "reporting"):
        stat = paths[path]
        lines.append(f"| {path.title()} | {_ms(stat['source_p95_ms'])} | {_ms(stat['target_p95_ms'])} | {_pct(stat['target_timeout_rate'])} of {stat['operations']} | {stat['limit_ms']:,} ms |")
    if any(item["id"] == "F5" for item in ledger["items"]):
        lines.append(f"| Event-driven (change streams) | works | {VERDICT_LABEL.get(paths['event-driven']['verdict'], paths['event-driven']['verdict'])} | | |")
    else:
        lines.append("| Event-driven (change streams) | not used by this app | | | |")

    projection = brief.extrapolate(explain, measured_bytes, assumptions, runs)
    if projection["available"]:
        lines += [
            "",
            f"**At production size (INFERRED)**: the data size at which each slow path reaches its timeout, projected from the measured {projection['measured_mb']} MB using the growth shape of its query plan. This assumes every collection grows by the same factor.",
        ]
        for row in projection["rows"]:
            lines.append(
                f"- {', '.join(row['features']) or 'Aggregation'} {row['path']} query on {row['collection']} ({row['limit_ms'] // 1000:,} s timeout): "
                f"MongoDB grows {row['source_growth']} and reaches it at ~{_size(row['source_timeout_at_gb'])}; "
                f"FerretDB grows {row['target_growth']} and reaches it at ~{_size(row['target_timeout_at_gb'])}."
            )
        if any(row["target_timeout_at_gb"] * 10 < row["source_timeout_at_gb"] for row in projection["rows"]):
            lines.append("- At TB scale both engines need work on reporting, such as pre-aggregation, but FerretDB hits the limit orders of magnitude sooner.")
        elif projection["rows"]:
            lines.append("- Both engines grow the same way here, so scale doesn't change the comparison.")

    if questions:
        same = questions["same"]
        lines += ["", f"**Business questions**: on untouched data, {same} of {len(questions['rows'])} answers match on FerretDB."]
        after = (cutover or {}).get("business_questions", [])
        changed = [row for row in after if row["verdict"] != "same"]
        if after:
            lines[-1] += f" After a clean cutover and one morning of traffic, {len(changed)} of {len(after)} differ" + (":" if changed else ".")
            for row in changed:
                lines.append(f"- {row['question']} {row.get('explanation', 'Values differ')}.")
        checkouts = (cutover or {}).get("day_one_checkouts") or {}
        if checkouts.get("source"):
            lines.append(
                f"- The same 12 checkouts: MongoDB {_outcomes(checkouts['source'])}; FerretDB {_outcomes(checkouts['target'])}."
                + (" Error 59 means commit isn't implemented: the shopper is told it failed, but the order was already written." if any("59" in key for key in checkouts["target"]) else "")
            )

    if edge and edge.get("available"):
        if edge["different"] == 0:
            lines += [
                "",
                f"**Edge cases**: {edge['generator']} designed {edge['cases']} awkward documents for ShopLog's fields: dates before 1970 and in year 9999, int64 beyond 2^53, -0.0, NaN, Decimal128 money, Unicode, null versus missing. MongoDB refereed. "
                f"FerretDB stored, matched, sorted and typed all {edge['valid']} exactly as MongoDB did. The gaps above are in behavior, not in storing data.",
            ]
        else:
            lines += ["", f"**Edge cases**: {edge['different']} of {edge['valid']} documents designed by {edge['generator']} behaved differently on FerretDB: {edge['counts']}."]

    if scoreboard:
        lines += [
            "",
            f"**Hidden rules recovered blind**: {scoreboard['found']} of {scoreboard['total']} found"
            + (f" (+{scoreboard['partly']} partly)" if scoreboard["partly"] else "")
            + f", {scoreboard['labels_correct']} of {scoreboard['total']} labeled correctly (OBSERVED or INFERRED). Scored against [docs/RULES.md](../../docs/RULES.md), which the pipeline never reads. Code evidence for this run: {scoreboard['code_evidence_source']}."
            + " One scoring pattern was fixed after the first run with Muse; the change log is in `archaeologist/scoreboard.py`.",
        ]

    if cutover:
        engine_failures = [check["check"] for check in cutover["failed"]]
        lines += [
            "",
            f"**Cutover fidelity**: a simulated full-load + CDC migration with nothing injected failed {len(cutover['failed'])} of {len(cutover['checks'])} checks on FerretDB"
            + (f": {', '.join(engine_failures[:4])}." if engine_failures else "."),
        ]
        if drill and drill.get("injected_faults"):
            caught = sum(1 for fault in drill["injected_faults"] if fault["caught"])
            faults = "; ".join(fault["fault"] for fault in drill["injected_faults"])
            lines.append(f"In a drill with faults injected on purpose ({faults}), the verifier caught {caught} of {len(drill['injected_faults'])}.")
    if upgrade:
        lines += ["", f"**Upgrade check**: {upgrade['summary']}"]
    lines += [
        "",
        f"Reversibility deductions: " + "; ".join(f"{name}: " + (", ".join(f"-{item['points']} {item['reason']}" for item in reversal[name]["deductions"]) or "none") for name in targets) + ".",
        "",
        "---",
        "",
        "# Appendix: evidence and method",
        "",
    ]
    return lines


def _size(gb: float) -> str:
    if gb >= 1000:
        return f"{gb / 1000:,.1f} TB"
    if gb >= 1:
        return f"{gb:,.0f} GB"
    return f"{gb * 1000:,.0f} MB"


def _reversal_lines(walk: dict | None) -> list[str]:
    if not walk:
        return []
    steps = walk["steps"]
    title = "**How the evidence reversed the decision** (FerretDB, 12-month net, base case)" if walk["flipped_at"] else "**What the evidence did to the paper case** (FerretDB, 12-month net, base case)"
    lines = [title, "", "| Step | What the agent found | Change | Running net | Decision |", "|---|---|---|---|---|"]
    for index, step in enumerate(steps):
        change = "" if step["change"] is None else _money(step["change"])
        decision = f"**{step['decision']}** ← flips here" if index == walk["flipped_at"] else step["decision"]
        lines.append(f"| {index} | {step['step']} ({step['evidence']}) | {change} | {_money(step['net'])} | {decision} |")
    subq = walk.get("subq") or {}
    if subq.get("on_paper") is not None and subq.get("with_evidence") is not None:
        lines += ["", f"SubQ Snap, asked the same question: P(net-positive) **{brief.probability(subq['on_paper'])}** with only the paper facts, **{brief.probability(subq['with_evidence'])}** with the evidence."]
    if not walk["flipped_at"]:
        lines += ["", "No finding blocked this app, so the evidence confirmed the paper case."]
    return lines + [""]


def _mb(measured_bytes) -> str:
    return f"{measured_bytes / 1e6:.0f} MB" if measured_bytes else "size not measured"


def _duration(ms: float) -> str:
    seconds = ms / 1000
    if seconds < 1:
        return f"{ms:.0f} ms"
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:,.0f} days"


def _cutover_section(cutover: dict | None, drill: dict | None) -> list[str]:
    if not cutover:
        return []
    lines = [
        "",
        "## Cutover fidelity",
        "",
        "A copier that works like a managed migration tool does a full load with indexes and validators, keeps the source taking live writes, then applies change events. "
        "A separate verifier reconciles: counts and content hashes, index and validator parity, the source oplog against the applied events, business rules, and day-one writes that MongoDB's constraints refuse. "
        "The same copier, MongoDB to MongoDB, passes every check.",
        "",
        "| Check | Clean migration | Drill (faults injected) |",
        "|---|---|---|",
    ]
    drill_by_name = {check["check"]: check for check in (drill or {}).get("checks", [])}
    for check in cutover["checks"]:
        other = drill_by_name.get(check["check"])
        drill_cell = ("✅" if other["ok"] else "❌") if other else ""
        lines.append(f"| {check['check']} | {'✅' if check['ok'] else '❌ ' + json.dumps(check['evidence'], default=str)[:120]} | {drill_cell} |")
    for fault in (drill or {}).get("injected_faults", []):
        lines.append(f"- Injected: {fault['fault']}. Caught: {'yes' if fault['caught'] else 'no'} ({fault['evidence']}).")
    return lines


def write(
    profile: str,
    ledger: dict,
    runs: dict,
    self_check: dict | None,
    docdb: dict,
    tco: dict,
    invalid_probes: list[dict],
    ferretdb_documented: dict | None = None,
    explain: dict | None = None,
    cutover: dict | None = None,
    drill: dict | None = None,
    measured_bytes: int | None = None,
    upgrade: dict | None = None,
    questions: dict | None = None,
    scoreboard: dict | None = None,
    edge: dict | None = None,
) -> str:
    app = APP_NAME.get(profile, profile)
    ferretdb, documentdb = tco["targets"][FERRETDB], tco["targets"][DOCUMENTDB]
    engines = runs["engines"]
    subq = tco["subq"]
    lines = _brief(profile, ledger, runs, self_check, tco, cutover, drill, explain, measured_bytes, upgrade, questions, scoreboard, edge)

    lines += ["## Decision detail", "", "| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |", "|---|---|---|---|---|---|"]
    for name, info in tco["targets"].items():
        probability = brief.probability(subq['probabilities'][name]) if subq.get("available") else f"unavailable ({subq.get('reason')})"
        lines.append(
            f"| {name} | {info['evidence']} | {sum(1 for gap in info['gaps'] if gap['blocking'])} | {_money(info['economics']['net']['base'])} | {info['decision_text']} | {probability} |"
        )
    lines += [
        "",
        f"The decision comes from measured gaps, list prices and stated assumptions. The SubQ probability is a separate second opinion on the same facts{', from ' + subq['model'] if subq.get('available') else ''}.",
        "",
        f"Tested: MongoDB {engines['source']['reported_version']} and FerretDB {engines['target'].get('ferretdb_version')} (which reports itself as MongoDB {engines['target']['reported_version']}). "
        "This app and its data are fictional and synthetic.",
        "",
        "## Feature by feature",
        "",
        "| # | What the app depends on | Reconstructed from | Status | Confidence | FerretDB (OBSERVED) | AWS DocumentDB 8.0.2 (INFERRED from docs) |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in ledger["items"]:
        lines.append(
            f"| {item['id']} | {item['feature']} | {_source_summary(item)} | {item['status']} | {_confidence(item)} | {_ferretdb_cell(item['id'], runs, ferretdb_documented)} | {_docdb_cell(item['id'], docdb)} |"
        )

    findings = _probe_findings(runs)
    rejections = runs.get("schema_rejections", [])
    lines += ["", "## What differed on FerretDB", ""]
    if findings or rejections:
        lines += findings
        for rejection in rejections:
            lines.append(f"- **Schema setup `{rejection['step']}`** was refused with code {rejection.get('code')}: \"{str(rejection.get('error'))[:170]}\".")
        for shape in runs["replay"]["reads"]["shapes"]:
            if shape["different"]:
                kinds = ", ".join(f"{count} × {kind}" for kind, count in shape["difference_kinds"].items())
                lines.append(f"- **Replay `{shape['shape_id']}`** ({', '.join(shape['features'])}, {shape['collection']}): {shape['different']} of {shape['replayed']} replayed operations differed ({kinds}).")
    else:
        lines.append("Nothing. Every probe and replayed operation matched.")
    if profile == "shoplog":
        lines += [
            "",
            "FerretDB's own [compatibility page](https://docs.ferretdb.io/migration/compatibility/) says `commitTransaction` and `abortTransaction` aren't implemented yet, so that gap is documented rather than hidden. "
            "The risk for an application is what happens at runtime: pymongo follows the driver specification and ignores errors from abort, so nothing tells the application its rollback did nothing.",
        ]
    lines += _cutover_section(cutover, drill)
    lines += _questions_section(questions, cutover)
    lines += _scoreboard_section(scoreboard)
    if upgrade:
        lines += ["", "## Upgrade check", "", upgrade["summary"], ""] + upgrade.get("table", [])

    overall = runs["replay"]["reads"]["overall"]
    writes = runs["replay"]["writes"]
    lines += [
        "",
        "## Latency",
        "",
        f"Both engines ran in Docker on one machine, with the same CPU and memory limits, the same data, and alternating order. FerretDB's image holds Postgres and FerretDB in one container, so they share its limit. Overall read p50/p95: MongoDB {_ms(overall['source_p50_ms'])} / {_ms(overall['source_p95_ms'])}, FerretDB {_ms(overall['target_p50_ms'])} / {_ms(overall['target_p95_ms'])}.",
        f"Writes ({writes['replayed']} replayed) p50/p95: MongoDB {_ms(writes['source_p50_ms'])} / {_ms(writes['source_p95_ms'])}, FerretDB {_ms(writes['target_p50_ms'])} / {_ms(writes['target_p95_ms'])}. MongoDB here also writes an oplog as a replica set. These are local numbers for comparison, not production estimates.",
        "",
        "| Feature | Operation | Samples | MongoDB p50 / p95 | FerretDB p50 / p95 | FerretDB compared at p50 |",
        "|---|---|---|---|---|---|",
    ]
    lines += _latency_rows(runs)
    lines += _explain_section(explain)
    if profile == "shoplog":
        lines += _atlas_section(ledger.get("atlas"))

    lines += ["", "## Cost over 12 months", ""]
    lines += _cost_section(tco)
    lines += [
        "",
        f"List prices and every assumption are in [assumptions.yaml](../../assumptions.yaml), including {tco['assumptions']['deployment']['clusters']} fictional clusters. DocumentDB uses the cheaper of its Standard and I/O-Optimized options, at equal RAM to Atlas M40 but half the vCPUs, which favors migrating.",
    ]
    estimated = [gap for info in tco["targets"].values() for gap in info["gaps"] if gap.get("estimate")]
    if estimated:
        lines += ["", "### Rewrite estimates (Muse, INFERRED)", "", "| Target | Gap | Low / base / high hours | Approach |", "|---|---|---|---|"]
        for name, info in tco["targets"].items():
            for gap in info["gaps"]:
                estimate = gap.get("estimate")
                if estimate:
                    lines.append(f"| {name} | {gap['name']} | {estimate['hours_low']:.0f} / {estimate['hours_base']:.0f} / {estimate['hours_high']:.0f} | {estimate['approach']} |")

    behaviors = ledger.get("behaviors", [])
    if behaviors:
        observed = sum(1 for behavior in behaviors if behavior["status"] == "OBSERVED")
        lines += [
            "",
            "## Reconstructed business behavior",
            "",
            f"Recovered without any documentation. {observed} of {len(behaviors)} are OBSERVED in the data. The rest appear only in code, so they are INFERRED.",
            "",
            "| ID | Behavior | Status | Confidence | Evidence |",
            "|---|---|---|---|---|",
        ]
        for behavior in behaviors:
            references = []
            for source in behavior["sources"]:
                if source["type"] == "code":
                    references.append(_code_link(source["ref"]))
                else:
                    references.append(f"{source['ref']} (n={source.get('count')})")
            lines.append(f"| {behavior['id']} | {behavior['statement']} | {behavior['status']} | {_confidence(behavior)} | {', '.join(references[:4])} |")

    inferred_items = [item for item in ledger["items"] if item["status"] == "INFERRED"]
    if inferred_items:
        lines += ["", "### Code that never ran", ""]
        for item in inferred_items:
            refs = ", ".join(_code_link(source["ref"]) for source in item["sources"] if source["type"] == "code")
            lines.append(f"- **{item['id']}** ({item['feature']}) appears at {refs}, but no operation in the query log used it. It may be dead code. Confidence: {_confidence(item)} ({item['confidence_basis']}).")

    lines += ["", "## How far to trust this", ""]
    if self_check:
        self_probes = self_check["probes"]
        equal = sum(1 for probe in self_probes if probe["verdict"] == "equal")
        reads = sum(shape["replayed"] for shape in self_check["replay"]["reads"]["shapes"])
        read_equal = sum(shape["equal"] for shape in self_check["replay"]["reads"]["shapes"])
        self_writes = self_check["replay"]["writes"]
        lines.append(
            f"- **Self-check:** MongoDB against itself gave {equal}/{len(self_probes)} equal probes, {read_equal}/{reads} equal reads, and {self_writes['equal']}/{self_writes['replayed']} equal writes with {len(self_writes['final_state_differences'])} final-state differences. So the differences above don't come from the comparison itself."
        )
    else:
        lines.append("- **Self-check:** not run for this memo.")
    origin = {"muse": f"generated by {ledger.get('code_model') or 'Meta Muse'}", "handwritten_fixture": "**handwritten test fixtures, not generated by the model**"}.get(runs.get("probe_origin"), runs.get("probe_origin"))
    lines.append(f"- **Probes:** {len(runs['probes'])} ran, {origin}. MongoDB is the oracle: a probe counts only if it ran cleanly on MongoDB and predicted its result. {len(invalid_probes)} probe(s) failed that check after retries and were excluded, not counted against the target.")
    code_source = ledger.get("code_evidence_source")
    lines.append(f"- **Code evidence:** source is `{code_source}`. Citations are checked by a program. {ledger.get('citations_dropped', 0)} model citation(s) were dropped because the cited line didn't contain the quoted token.")
    excluded = "; ".join(f"{count} {reason}" for reason, count in runs["replay"]["excluded"].items())
    lines.append(f"- **Replay coverage:** the log has {runs['replay']['in_log']:,} entries. Not replayed: {excluded or 'none'}. Reads are capped per operation shape.")
    lines.append("- **Two different products:** FerretDB 2 runs on Postgres with an open-source DocumentDB extension. AWS DocumentDB is a separate engine with a similar name. FerretDB's results say nothing about AWS DocumentDB.")
    lines.append(f"- **DocumentDB rows are INFERRED:** we read AWS's docs for engine 8.0.2 and compared them with this app's usage. Nothing ran there. Set `TARGET_URI` to a real cluster to replace them with observed results.")
    lines.append("- **Fair by construction:** the same pipeline, thresholds and assumptions run on every workload. ShopLog was deliberately built to depend on MongoDB-specific features, and the control app wasn't.")
    lines.append("- **Not modeled:** data transfer, backups, support plans and committed-use discounts. Rewrite hours are model estimates, and the risk inputs are assumptions.")
    lines += ["", f"Evidence: [ledger](ledger.json) · [runs](runs.json) · [cost model](tco.json) · run `{runs['run_id']}`", ""]

    text = "\n".join(lines)
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "memo.md").write_text(text)
    return text


def load_json(path):
    return json.loads(path.read_text()) if path.exists() else None


# Referenced so the feature table stays the single source of names.
_ = FEATURES
