"""Writes the decision memo. Each claim links to the evidence behind it. No number is rounded into a story."""

from __future__ import annotations

import json

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
    if "code" in kinds:
        labels.append("code")
    return " + ".join(labels)


def _confidence(record: dict) -> str:
    return f"{record['confidence']:.2f}" if record["confidence"] is not None else "unknown"


def _ferretdb_cell(feature: str, runs: dict) -> str:
    info = runs["features"].get(feature)
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
    return (
        f"**Stay on MongoDB for {app} for now.** On FerretDB, {len(blocking)} blocking gaps showed up ({names or 'performance only'}). "
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


def write(profile: str, ledger: dict, runs: dict, self_check: dict | None, docdb: dict, tco: dict, invalid_probes: list[dict]) -> str:
    app = APP_NAME.get(profile, profile)
    ferretdb, documentdb = tco["targets"][FERRETDB], tco["targets"][DOCUMENTDB]
    engines = runs["engines"]
    subq = tco["subq"]
    lines = [f"# Migration decision: {app}", "", _headline(profile, tco), ""]

    lines += ["| Target | Evidence | Blocking gaps | Net over 12 months (base) | Decision | SubQ Snap: P(net-positive) |", "|---|---|---|---|---|---|"]
    for name, info in tco["targets"].items():
        probability = f"{subq['probabilities'][name]:.2f}" if subq.get("available") else f"unavailable ({subq.get('reason')})"
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
            f"| {item['id']} | {item['feature']} | {_source_summary(item)} | {item['status']} | {_confidence(item)} | {_ferretdb_cell(item['id'], runs)} | {_docdb_cell(item['id'], docdb)} |"
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
