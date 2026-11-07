"""What a CTO needs on one page: top risks, latency by path, scale extrapolation, reversibility.

Measured numbers come from the runs. Anything projected beyond the measured dataset is marked INFERRED.
"""

from __future__ import annotations

from archaeologist.features import FEATURES
from archaeologist.tco import DOCUMENTDB, FERRETDB

FEATURE_NAMES = {key: value["name"] for key, value in FEATURES.items()}

IMPACT = {
    "F1": "Reporting aggregations ($lookup sub-pipelines, $facet) behave differently or slow down",
    "F2": "Running totals and window functions in account pages",
    "F3": "Line-item refunds update the wrong elements",
    "F4": "Checkout isn't atomic: stock can be reserved without an order, and a rollback does nothing",
    "F5": "Event-driven inventory sync stops: change streams aren't available",
    "F6": "Logins and email uniqueness stop ignoring case: duplicate accounts, failed sign-ins",
    "F7": "Coupons can be reused and carts stop expiring",
    "F8": "Invalid orders get written: the schema validator isn't enforced",
    "F9": "Product search returns different rankings",
    "F10": "Plain reads and writes",
    "F11": "Storefront search needs a separate search service",
}
RISK_WEIGHT = {"silent": 100, "cutover": 90, "performance": 80, "loud": 70, "documented_conflict": 50}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(fraction * len(ordered)))], 2)


def path_stats(runs: dict, assumptions: dict) -> dict:
    """p95 and timeout rate on the replay, per path."""
    limits = {
        "online": assumptions["service_levels"]["online_timeout_ms"],
        "reporting": assumptions["service_levels"]["reporting_timeout_ms"],
    }
    stats = {}
    for path, limit in limits.items():
        shapes = [shape for shape in runs["replay"]["reads"]["shapes"] if shape.get("path") == path]
        source = [value for shape in shapes for value in shape.get("source_ms_samples", [])]
        target = [value for shape in shapes for value in shape.get("target_ms_samples", [])]
        errors = sum(shape["target_errors"] for shape in shapes)
        over = sum(1 for value in target if value > limit) + errors
        attempts = len(target) + errors
        stats[path] = {
            "limit_ms": limit,
            "operations": attempts,
            "source_p95_ms": _percentile(source, 0.95),
            "target_p95_ms": _percentile(target, 0.95),
            "target_timeout_rate": round(over / attempts, 3) if attempts else None,
            "source_timeout_rate": round(sum(1 for value in source if value > limit) / len(source), 3) if source else None,
        }
    event = runs["features"].get("F5", {})
    stats["event-driven"] = {"verdict": event.get("verdict", "no_probe")}
    return stats


def _crossing_gb(measured_ms: float, limit_ms: float, measured_bytes: int, quadratic: bool) -> float:
    """Data size at which a query reaches the timeout, if it grows with the data as its plan suggests."""
    ratio = limit_ms / max(measured_ms, 0.01)
    factor = ratio**0.5 if quadratic else ratio
    return measured_bytes * factor / 1e9


def extrapolate(explain: dict | None, measured_bytes: int | None, assumptions: dict, runs: dict | None = None) -> dict:
    """INFERRED: when does each slow path hit its timeout as the data grows, given its plan's shape?"""
    if not explain or not explain.get("comparisons") or not measured_bytes:
        return {"available": False}
    paths = {shape["shape_id"]: shape.get("path", "online") for shape in (runs or {}).get("replay", {}).get("reads", {}).get("shapes", [])}
    limits = {"online": assumptions["service_levels"]["online_timeout_ms"], "reporting": assumptions["service_levels"]["reporting_timeout_ms"]}
    rows = []
    for comparison in explain["comparisons"]:
        source_ms, target_ms = comparison["replay_p95_ms"]["source"], comparison["replay_p95_ms"]["target"]
        target_plan, source_plan = comparison["target"], comparison["source"]
        quadratic = bool(target_plan.get("nested_loops")) and bool(target_plan.get("full_scans"))
        path = paths.get(comparison["shape_id"], "online")
        # A keyed lookup with a limit does the same work at any data size, so only paths whose work grows are projected.
        if path != "reporting" and not source_plan.get("full_scans") and not target_plan.get("full_scans"):
            continue
        limit = limits.get(path, limits["online"])
        rows.append(
            {
                "features": comparison["features"],
                "collection": comparison["collection"],
                "path": path,
                "limit_ms": limit,
                "measured": {"source_ms": source_ms, "target_ms": target_ms},
                "target_growth": "quadratic (a sequential scan inside a nested loop)" if quadratic else "linear",
                "source_growth": "linear (collection scan)" if source_plan.get("full_scans") else "linear (index lookups)",
                "source_timeout_at_gb": round(_crossing_gb(source_ms, limit, measured_bytes, False), 1),
                "target_timeout_at_gb": round(_crossing_gb(target_ms, limit, measured_bytes, quadratic), 2),
            }
        )
    return {"available": True, "status": "INFERRED", "measured_mb": round(measured_bytes / 1e6, 1), "rows": rows}


def reversibility(name: str, info: dict, runs: dict, cutover: dict | None, assumptions: dict) -> dict:
    """Starts at 100 and subtracts what makes going back to MongoDB harder. Each deduction is listed."""
    weights = assumptions["reversibility_deductions"]
    deductions = []
    blocking = [gap for gap in info["gaps"] if gap["blocking"]]
    if name == FERRETDB:
        if runs["features"].get("F5", {}).get("verdict") in {"unsupported", "error"}:
            deductions.append((weights["no_change_streams_on_target"], "No change streams on the target: moving back needs a full reload with downtime"))
        drift = [check for check in (cutover or {}).get("failed", []) if check["area"] in {"constraints", "invariants"}]
        if drift or any(gap["kind"] == "silent" for gap in blocking):
            deductions.append((weights["constraint_drift"], "The target accepts writes MongoDB refuses, so that data needs cleaning before moving back"))
    else:
        for gap in blocking:
            if gap["kind"] == "documented_conflict":
                deductions.append((weights["documented_limit"], f"Documented limit: {gap['name']}"))
    rewrites = [gap for gap in blocking if gap["kind"] in {"silent", "loud", "performance"}]
    if rewrites:
        deductions.append((min(weights["per_rewrite"] * len(rewrites), weights["max_rewrites"]), f"{len(rewrites)} code rewrite(s) make the two versions of the app diverge"))
    if any(gap["feature"] == "F11" for gap in blocking):
        deductions.append((weights["extra_service"], "A separate search service would need to be retired or kept"))
    score = max(0, 100 - sum(points for points, _ in deductions))
    return {"score": score, "status": "OBSERVED" if name == FERRETDB else "INFERRED", "deductions": [{"points": points, "reason": reason} for points, reason in deductions]}


def top_risks(tco: dict, ledger: dict, cutover: dict | None, limit: int = 5) -> list[dict]:
    code_refs = {
        item["id"]: [source["ref"] for source in item["sources"] if source["type"] == "code" and source.get("origin") != "static_scan"][:2]
        or [source["ref"] for source in item["sources"] if source["type"] == "code"][:2]
        for item in ledger["items"]
    }
    risks = []
    for target, info in tco["targets"].items():
        for gap in info["gaps"]:
            if not gap["blocking"]:
                continue
            evidence = [f"probe `{probe['probe_id']}`" for probe in gap["evidence"] if probe.get("probe_id")][:2]
            if gap.get("replay", {}).get("different"):
                evidence.append(f"{gap['replay']['different']}/{gap['replay']['replayed']} replayed ops differ")
            title = IMPACT.get(gap["feature"], gap["name"])
            if gap["kind"] == "performance":
                item = gap["evidence"][0]
                evidence.append(f"replay p95 {item['source_p95_ms']} ms → {item['target_p95_ms']} ms")
                ratio = item["target_p95_ms"] / max(item["source_p95_ms"], 0.01)
                title = f"{gap['name'].replace('Slow path on ', 'Queries on ').split(' (')[0]} run {ratio:,.0f}× slower ({FEATURE_NAMES.get(gap['feature'], gap['feature'])})"
            if gap["kind"] == "documented_conflict":
                evidence.extend(f"[AWS docs]({item['url']})" for item in gap["evidence"][:1])
            risks.append(
                {
                    "target": target,
                    "feature": gap["feature"],
                    "title": title,
                    "kind": gap["kind"],
                    "weight": RISK_WEIGHT.get(gap["kind"], 40) + (5 if target == FERRETDB else 0),
                    "evidence": evidence,
                    "code": code_refs.get(gap["feature"], []),
                }
            )
    lost = [check for check in (cutover or {}).get("failed", []) if check["area"] == "constraints" and "Day-one" not in check["check"]]
    if lost:
        risks.append(
            {
                "target": FERRETDB,
                "feature": "cutover",
                "title": f"Constraints don't survive a clean cutover: {', '.join(check['check'] for check in lost[:3])}",
                "kind": "cutover",
                "weight": RISK_WEIGHT["cutover"] + 5,
                "evidence": [f"cutover check, clean run: {len((cutover or {}).get('failed', []))} of {len((cutover or {}).get('checks', []))} checks failed"],
                "code": [],
            }
        )
    seen, ranked = set(), []
    for risk in sorted(risks, key=lambda item: -item["weight"]):
        key = (risk["feature"], risk["kind"] == "documented_conflict")
        if key in seen:
            continue
        seen.add(key)
        ranked.append(risk)
    return ranked[:limit]


def confidence_label(name: str, info: dict, self_check: dict | None, subq: dict) -> str:
    if info["evidence"] == "INFERRED":
        return "Low: documentation only, nothing measured"
    clean_self_check = bool(self_check) and all(probe["verdict"] == "equal" for probe in self_check["probes"])
    level = "High" if clean_self_check else "Medium"
    reason = "measured; self-check clean" if clean_self_check else "measured; self-check not run"
    if subq.get("available"):
        reason += f"; SubQ P(net-positive) {subq['probabilities'][name]:.2f}"
    return f"{level} ({reason})"


_ = DOCUMENTDB
