"""The agent pipeline, end to end: evidence, ledger, probes, differential run, cost, decision memo."""

from __future__ import annotations

import argparse
import json
import time

from rich.console import Console
from rich.table import Table

from archaeologist import business_questions, cutover, docdb_inferred, explain_compare, extract_data, extract_querylog, ledger as ledger_module, memo, meta_client, probes, scoreboard, store, subq_client, tco, upgrade
from archaeologist.config import OUT_DIR, ROOT, source_client, target_client
from archaeologist.diff import run as run_diff
from scripts.seed import PROFILES, database_names, seed_database

console = Console(width=132)

VERDICT_STYLE = {
    "equal": "[green]equal[/]",
    "ranking_diff": "[yellow]ranking differs[/]",
    "result_diff": "[bold red]RESULT DIFF[/]",
    "unsupported": "[bold red]UNSUPPORTED[/]",
    "error": "[bold red]ERROR[/]",
    "timeout": "[bold red]TIMEOUT[/]",
    "probe_invalid": "[dim]unverified[/]",
    "no_probe": "[dim]no probe[/]",
}


def _step(title: str) -> None:
    console.rule(f"[bold]{title}")


def _ensure_seeded(profile: str) -> None:
    names = database_names(profile)
    settings = PROFILES[profile]
    report_path = OUT_DIR / f"seed_report_{profile}.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    for label, make_client in (("source", source_client), ("target", target_client)):
        client = make_client(timeout_ms=300_000)
        if client[names["base"]].orders.estimated_document_count() >= settings["orders"] and label in report:
            continue
        console.print(f"  seeding {label}…")
        report[label] = [seed_database(client, names["base"], profile, settings["orders"])]
    OUT_DIR.mkdir(exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))


def _ledger_table(ledger: dict) -> Table:
    table = Table(title="Usage-Spec Ledger", expand=True)
    for column in ("Item", "Feature", "Sources", "Status", "Confidence", "Basis"):
        table.add_column(column)
    for item in ledger["items"]:
        kinds = sorted({source["type"] for source in item["sources"]})
        status = "[green]OBSERVED[/]" if item["status"] == "OBSERVED" else "[yellow]INFERRED[/]"
        confidence = f"{item['confidence']:.2f}" if item["confidence"] is not None else "unknown"
        table.add_row(item["id"], item["feature"], ", ".join(kinds), status, confidence, item["confidence_basis"])
    return table


def _side_by_side(ledger: dict, runs: dict, docdb: dict) -> Table:
    table = Table(title="MongoDB vs FerretDB (OBSERVED) vs AWS DocumentDB (INFERRED from docs)", expand=True)
    table.add_column("Item")
    table.add_column("Feature")
    table.add_column("Probes equal")
    table.add_column("Replay equal")
    table.add_column("FerretDB")
    table.add_column("DocumentDB 8.0.2 docs")
    for item in ledger["items"]:
        info = runs["features"].get(item["id"], {"verdict": "no_probe", "probes": [], "replay": {"replayed": 0, "equal": 0}})
        probe_list = info["probes"]
        same = sum(1 for probe in probe_list if probe["verdict"] == "equal")
        replay = info["replay"]
        documented = docdb.get(item["id"], {}).get("verdict", "")
        documented_text = {
            "documented_conflict": "[yellow]conflicts with usage[/]",
            "documented_supported": "[blue]says supported[/]",
        }.get(documented, "[dim]not reviewed[/]")
        table.add_row(
            item["id"],
            item["feature"],
            f"{same}/{len(probe_list)}" if probe_list else "-",
            f"{replay['equal']}/{replay['replayed']}" if replay["replayed"] else "-",
            VERDICT_STYLE.get(info["verdict"], info["verdict"]),
            documented_text,
        )
    return table


def _latency_table(runs: dict) -> Table:
    table = Table(title="Replay latency (client side, same machine, same limits)", expand=True)
    for column in ("Feature", "Collection", "Stages", "n", "MongoDB p50/p95 ms", "FerretDB p50/p95 ms"):
        table.add_column(column)
    for shape in sorted(runs["replay"]["reads"]["shapes"], key=lambda item: -(item.get("target_p95_ms") or 0))[:6]:
        table.add_row(
            ", ".join(shape["features"]) or "-",
            shape["collection"],
            " → ".join(shape["stages"][:3]) or "find",
            str(shape["replayed"]),
            f"{shape['source_p50_ms']} / {shape['source_p95_ms']}",
            f"{shape['target_p50_ms']} / {shape['target_p95_ms']}",
        )
    return table


def _decision_table(result: dict) -> Table:
    table = Table(title="Decision", expand=True)
    for column in ("Target", "Evidence", "Blocking gaps", "Infra saved/yr", "Net 12 mo (base)", "P(net-positive)", "Decision"):
        table.add_column(column)
    subq = result["subq"]
    for name, info in result["targets"].items():
        probability = f"{subq['probabilities'][name]:.2f}" if subq.get("available") else "unavailable"
        table.add_row(
            name,
            info["evidence"],
            str(sum(1 for gap in info["gaps"] if gap["blocking"])),
            f"${info['economics']['infra_savings']:,.0f}",
            f"${info['economics']['net']['base']:,.0f}",
            probability,
            info["decision_text"],
        )
    return table


def _load_or_generate_probes(profile: str, ledger: dict, refresh: bool) -> tuple[list[dict], list[dict], str]:
    path = probes.cache_path(profile)
    if refresh or not path.exists():
        if meta_client.is_configured():
            console.print(f"  generating probes with [bold]{meta_client.model_name()}[/]…")
            probes.generate(profile, ledger)
        elif not path.exists():
            fixture = json.loads((ROOT / "tests" / "fixtures" / "handwritten_probes.json").read_text())
            allowed = {item["id"] for item in ledger["items"]}
            console.print("  [yellow]META_API_KEY is not set: using handwritten test fixtures instead of generated probes[/]")
            return [probe for probe in fixture if probe["feature_id"] in allowed], [], "handwritten_fixture"
    return probes.load_cached(profile), probes.load_invalid(profile), "muse"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="shoplog")
    parser.add_argument("--refresh-probes", action="store_true", help="Generate probes with Muse even if cached ones exist")
    parser.add_argument("--live", action="store_true", help="Call Muse again for code evidence and reconciliation instead of using saved results")
    parser.add_argument("--no-live-code", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--with-self-check", action="store_true", help="Run MongoDB against itself first")
    parser.add_argument("--with-upgrade", action="store_true", help="Also run against the older target version (docker compose --profile upgrade)")
    arguments = parser.parse_args()

    profile = arguments.profile
    started = time.perf_counter()
    console.print(f"[bold]Compat Archaeologist[/] · profile [bold]{profile}[/]")
    console.print(
        f"  Meta: {meta_client.model_name() if meta_client.is_configured() else '[yellow]not configured[/]'} · "
        f"SubQ: {subq_client.model_name() if subq_client.is_configured() else '[yellow]not configured[/]'}"
    )
    _ensure_seeded(profile)

    _step("1. Evidence: data, query log, code")
    data = extract_data.run(profile)
    summary = extract_querylog.run(profile)
    console.print(
        f"  data: {sum(info['documents'] for info in data['collections'].values()):,} documents, "
        f"{len(data['invariants'])} invariants mined"
    )
    console.print(f"  query log: {summary['total_entries']:,} operations in {len(summary['shapes'])} shapes; stages {list(summary['stages'])}")

    _step("2. Usage-Spec Ledger")
    ledger = ledger_module.run(profile, refresh_code=arguments.live)
    console.print(f"  code evidence from: [bold]{ledger['code_evidence_source']}[/]; citations dropped: {ledger['citations_dropped']}")
    console.print(_ledger_table(ledger))
    observed = sum(1 for behavior in ledger["behaviors"] if behavior["status"] == "OBSERVED")
    console.print(f"  business behaviors reconstructed: {len(ledger['behaviors'])} ({observed} observed in data)")
    rules = scoreboard.score(profile, ledger) if profile == "shoplog" else None
    if rules:
        console.print(
            f"  [bold]hidden rules recovered blind: {rules['found']}/{rules['total']}[/]"
            + (f" (+{rules['partly']} partly)" if rules["partly"] else "")
            + f", labels correct {rules['labels_correct']}/{rules['total']} (answer key: docs/RULES.md, never read by the pipeline)"
        )

    _step("3. Probes")
    active, invalid, origin = _load_or_generate_probes(profile, ledger, arguments.refresh_probes)
    console.print(f"  {len(active)} probes from {origin}; {len(invalid)} rejected by MongoDB as oracle")

    self_check = None
    self_path = OUT_DIR / profile / "self_check.json"
    if arguments.with_self_check:
        _step("3b. Self-check: MongoDB against itself must match exactly")
        self_check = run_diff(profile, active, self_check=True, probe_origin=origin)
    elif self_path.exists():
        self_check = json.loads(self_path.read_text())

    _step("4. Differential run: same probes, same workload, both engines")
    runs = run_diff(profile, active, probe_origin=origin)
    feature_ids = [item["id"] for item in ledger["items"]]
    docdb = docdb_inferred.assess(profile, feature_ids)
    ferretdb_documented = docdb_inferred.assess_ferretdb(feature_ids)
    console.print(_side_by_side(ledger, runs, docdb))
    console.print(_latency_table(runs))

    _step("4b. Query plans for the slowest paths (MongoDB MCP Server: explain)")
    plans = explain_compare.run(profile, runs)
    for comparison in plans.get("comparisons", []):
        console.print(f"  [bold]{', '.join(comparison['features'])}[/] {comparison['collection']}")
        console.print(f"    MongoDB : {comparison['source'].get('plan')}")
        console.print(f"    FerretDB: {comparison['target'].get('plan', '')[:150]}")
    if not plans.get("available"):
        console.print(f"  [yellow]skipped[/]: {plans.get('reason')}")

    _step("4c. Cutover fidelity: full load + CDC, then an independent reconciliation")
    cutover_clean = cutover.run(profile, "clean")
    cutover_drill = cutover.run(profile, "drill")
    console.print(f"  clean migration: {len(cutover_clean['failed'])} of {len(cutover_clean['checks'])} checks failed")
    for check in cutover_clean["failed"]:
        console.print(f"    [red]✗[/] {check['check']}")
    for fault in cutover_drill["injected_faults"]:
        console.print(f"  drill: injected {fault['fault']} → {'[green]caught[/]' if fault['caught'] else '[red]missed[/]'} ({fault['evidence']})")

    _step("4e. Business questions: the numbers the business runs on, asked of both systems")
    questions = business_questions.run(profile)
    after = cutover_clean.get("business_questions", [])
    console.print(f"  same untouched data: {questions['same']}/{len(questions['rows'])} answers match")
    if after:
        changed = [row for row in after if row["verdict"] != "same"]
        console.print(f"  after a clean cutover and one morning of traffic: [bold]{len(changed)}/{len(after)} answers differ[/]")
        for row in changed:
            console.print(f"    {row['question']} {row.get('explanation', '')}")
    if (cutover_clean.get("day_one_checkouts") or {}).get("source"):
        console.print(f"  same 12 checkouts: MongoDB {cutover_clean['day_one_checkouts']['source']} · FerretDB {cutover_clean['day_one_checkouts']['target']}")

    upgrade_summary = upgrade.load(profile)
    if arguments.with_upgrade:
        _step("4d. Upgrade check: same probes and replay on an older target version")
        try:
            upgrade_summary = upgrade.run(profile, active, runs, origin)
            console.print(f"  {upgrade_summary['summary']}")
        except Exception as exc:  # The older engine is optional; its absence shouldn't stop the memo.
            console.print(f"  [yellow]skipped[/]: older target not reachable ({type(exc).__name__})")

    _step("5. Cost and decision")
    result = tco.run(profile, runs, ledger, docdb, ferretdb_documented)
    console.print(_decision_table(result))

    _step("6. Decision memo")
    measured_bytes = source_client().get_database(profile).command("dbStats").get("dataSize")
    text = memo.write(
        profile, ledger, runs, self_check, docdb, result, invalid, ferretdb_documented, plans,
        cutover=cutover_clean, drill=cutover_drill, measured_bytes=measured_bytes, upgrade=upgrade_summary,
        questions=questions, scoreboard=rules,
    )
    console.print(f"  wrote out/{profile}/memo.md")
    saved = store.save(profile, runs["run_id"], ledger, runs, result, text)
    console.print(f"  Atlas: {'saved to ' + saved['database'] + '.' + saved['collection'] if saved['saved'] else '[yellow]skipped[/] (' + saved['reason'] + ')'}")
    console.print(f"[bold]Done in {time.perf_counter() - started:.0f}s[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
