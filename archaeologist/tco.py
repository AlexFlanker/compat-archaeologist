"""Turns gaps and prices into a 12-month cost comparison and a decision for each target.

Infra prices are public. Rewrite effort is a model estimate and is labeled INFERRED. Every other
input is a stated assumption in assumptions.yaml.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import yaml

from archaeologist import meta_client, subq_client
from archaeologist.config import OUT_DIR, ROOT

SCENARIOS = {"best": "low", "base": "base", "worst": "high"}
FERRETDB = "FerretDB"
DOCUMENTDB = "AWS DocumentDB"

ESTIMATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["hours_low", "hours_base", "hours_high", "approach"],
    "properties": {
        "hours_low": {"type": "number"},
        "hours_base": {"type": "number"},
        "hours_high": {"type": "number"},
        "approach": {"type": "string"},
    },
}

ESTIMATE_PROMPT = """You are a senior engineer estimating work to make a legacy application run correctly on a MongoDB-compatible database
that lacks a behavior the application relies on. Estimate engineering hours to redesign, implement, test and roll out the change,
for a team that knows the code. Give low, base and high. Be realistic: changing transaction or uniqueness guarantees usually means
redesign and data-repair handling, not a small patch. A small fix is fine if the gap is small.
State the approach in one or two sentences. Base your estimate on the evidence given."""


def load_assumptions() -> dict:
    return yaml.safe_load((ROOT / "assumptions.yaml").read_text())


def _feature_confidence(ledger: dict, feature: str) -> float | None:
    for item in ledger["items"]:
        if item["id"] == feature:
            return item["confidence"]
    return None


def _feature_name(ledger: dict, feature: str) -> str:
    for item in ledger["items"]:
        if item["id"] == feature:
            return item["feature"]
    return feature


def observed_gaps(runs: dict, ledger: dict, assumptions: dict) -> list[dict]:
    """Gaps measured on the target: behavior that differs, and paths that got much slower."""
    rule = assumptions["decision"]
    gaps = []
    for feature, info in runs["features"].items():
        if info["verdict"] not in rule["blocking_verdicts"]:
            continue
        probes = [probe for probe in runs["probes"] if probe["feature_id"] == feature]
        failing = [probe for probe in probes if probe["verdict"] in rule["blocking_verdicts"]]
        silent = any(probe.get("detail", {}).get("silent") for probe in failing) or info["replay"]["different"] > 0
        confidence = _feature_confidence(ledger, feature)
        gaps.append(
            {
                "key": f"behavior:{feature}",
                "feature": feature,
                "name": _feature_name(ledger, feature),
                "kind": "silent" if silent else "loud",
                "verdict": info["verdict"],
                "confidence": confidence,
                "blocking": confidence is None or confidence >= rule["minimum_confidence_to_block"],
                "evidence": [
                    {"probe_id": probe["probe_id"], "verdict": probe["verdict"], "title": probe.get("title"), "detail": probe.get("detail")}
                    for probe in failing
                ],
                "replay": info["replay"],
                "schema_rejections": info["schema_rejections"],
            }
        )

    regression = rule["performance_regression"]
    for shape in runs["replay"]["reads"]["shapes"]:
        source, target = shape.get("source_p95_ms"), shape.get("target_p95_ms")
        if source is None or target is None:
            continue
        if target >= source * regression["minimum_ratio"] and target - source >= regression["minimum_added_ms"]:
            feature = (shape["features"] or ["F10"])[0]
            gaps.append(
                {
                    "key": f"performance:{shape['shape_id']}",
                    "feature": feature,
                    "name": f"Slow path on {shape['collection']} ({_feature_name(ledger, feature)})",
                    "kind": "performance",
                    "verdict": "performance",
                    "confidence": _feature_confidence(ledger, feature),
                    "blocking": True,
                    "evidence": [
                        {
                            "shape_id": shape["shape_id"],
                            "stages": shape["stages"],
                            "source_p95_ms": source,
                            "target_p95_ms": target,
                            "samples": shape["replayed"],
                        }
                    ],
                }
            )
    return gaps


def documented_gaps(docdb: dict, ledger: dict, assumptions: dict) -> list[dict]:
    rule = assumptions["decision"]
    gaps = []
    for feature, info in docdb.items():
        if info["verdict"] != "documented_conflict":
            continue
        confidence = _feature_confidence(ledger, feature)
        gaps.append(
            {
                "key": f"documented:{feature}",
                "feature": feature,
                "name": _feature_name(ledger, feature),
                "kind": "documented_conflict",
                "verdict": "documented_conflict",
                "confidence": confidence,
                "blocking": confidence is None or confidence >= rule["minimum_confidence_to_block"],
                "evidence": [{"conflict": conflict, "url": info["url"]} for conflict in info["conflicts"]],
            }
        )
    return gaps


def estimate_rewrites(profile: str, target: str, gaps: list[dict], ledger: dict) -> dict[str, dict]:
    """Model estimates, cached so a demo run gives the same numbers. Absent, never guessed, if Muse is down."""
    path = OUT_DIR / profile / "rewrite_estimates.json"
    cache = json.loads(path.read_text()) if path.exists() else {}
    missing = [gap for gap in gaps if f"{target}|{gap['key']}" not in cache] if meta_client.is_configured() else []

    def estimate(gap: dict):
        code_refs = [
            source["ref"]
            for item in ledger["items"]
            if item["id"] == gap["feature"]
            for source in item["sources"]
            if source["type"] == "code"
        ]
        try:
            return gap, meta_client.structured(
                purpose=f"rewrite_estimate:{target}:{gap['key']}",
                system=ESTIMATE_PROMPT,
                user=json.dumps(
                    {"target_engine": target, "gap": gap, "application_code_locations": code_refs[:12]},
                    default=str,
                    indent=1,
                )[:8000],
                schema=ESTIMATE_SCHEMA,
                schema_name="estimate",
            )
        except meta_client.MetaUnavailable as exc:
            print(f"  [tco] no estimate for {gap['key']}: {exc}")
            return gap, None

    changed = False
    with ThreadPoolExecutor(max_workers=4) as pool:
        for gap, result in pool.map(estimate, missing):
            if result is not None:
                cache[f"{target}|{gap['key']}"] = result
                changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, indent=2))
    return {key.split("|", 1)[1]: value for key, value in cache.items() if key.startswith(f"{target}|")}


def search_service_annual(assumptions: dict, clusters: int) -> float:
    search = assumptions["pricing"]["search_service_opensearch"]
    monthly = search["instances_per_cluster"] * (
        search["per_hour"] * assumptions["hours_per_month"] + search["storage_per_gb_month"] * search["storage_gb_per_instance"]
    )
    return round(monthly * assumptions["horizon_months"] * clusters, 2)


def annual_cost_lines(assumptions: dict, clusters: int, atlas_basis: str) -> dict[str, dict[str, float]]:
    """Yearly infrastructure and I/O per target, before scale-ups and extra services."""
    pricing, deployment = assumptions["pricing"], assumptions["deployment"]
    hours = assumptions["hours_per_month"]
    nodes = deployment["nodes_per_cluster"]
    gb = deployment["dataset_gb_per_cluster"]
    yearly = assumptions["horizon_months"] * clusters

    atlas_nodes = nodes if atlas_basis == "per_node" else 1
    atlas = pricing["atlas_m40"]["per_hour"] * atlas_nodes * hours

    ferretdb = pricing["ec2_r6g_large"]["per_hour"] * nodes * hours + pricing["ebs_gp3"]["per_gb_month"] * gb * nodes

    docdb = pricing["documentdb_r6g_large"]
    io_millions = deployment["average_iops_per_cluster"] * 3600 * hours / 1_000_000
    standard_infra = docdb["standard"]["per_hour"] * nodes * hours + docdb["standard"]["storage_per_gb_month"] * gb
    standard_io = docdb["standard"]["per_million_io"] * io_millions
    optimized_infra = docdb["io_optimized"]["per_hour"] * nodes * hours + docdb["io_optimized"]["storage_per_gb_month"] * gb
    if standard_infra + standard_io <= optimized_infra:
        documentdb = {"infrastructure": standard_infra, "io": standard_io, "plan": "Standard"}
    else:
        documentdb = {"infrastructure": optimized_infra, "io": 0.0, "plan": "I/O-Optimized"}

    def scaled(lines: dict) -> dict:
        return {key: (round(value * yearly, 2) if isinstance(value, float) else value) for key, value in lines.items()}

    return {
        "MongoDB Atlas": scaled({"infrastructure": float(atlas), "io": 0.0}),
        FERRETDB: scaled({"infrastructure": float(ferretdb), "io": 0.0}),
        DOCUMENTDB: scaled(documentdb),
    }


def annual_run_costs(assumptions: dict, clusters: int, atlas_basis: str) -> dict[str, float]:
    lines = annual_cost_lines(assumptions, clusters, atlas_basis)
    return {name: round(values["infrastructure"] + values["io"], 2) for name, values in lines.items()}


def _economics(assumptions: dict, target: str, gaps: list[dict], estimates: dict, clusters: int, atlas_basis: str) -> dict:
    lines = annual_cost_lines(assumptions, clusters, atlas_basis)
    atlas_total = lines["MongoDB Atlas"]["infrastructure"] + lines["MongoDB Atlas"]["io"]
    rate = assumptions["people"]["engineer_cost_per_hour"]
    months = assumptions["horizon_months"]
    blocking = [gap for gap in gaps if gap["blocking"]]
    search_cost = search_service_annual(assumptions, clusters) if any(gap["feature"] == "F11" for gap in blocking) else 0.0
    slow_paths = any(gap["kind"] == "performance" for gap in blocking)

    missing_estimates = [gap["key"] for gap in blocking if gap["key"] not in estimates]
    net, breakdown = {}, {}
    for scenario, level in SCENARIOS.items():
        scale_up = lines[target]["infrastructure"] * (assumptions["performance_scale_up_factor"][level] - 1) if slow_paths else 0.0
        target_total = lines[target]["infrastructure"] + lines[target]["io"] + search_cost + scale_up
        savings = atlas_total - target_total
        ops_hours = assumptions["self_managed_ops_hours_per_month_per_cluster"][level] if target == FERRETDB else 0
        ops_cost = ops_hours * clusters * months * rate
        migration_hours = assumptions["migration_baseline_hours"][level]
        rewrite_hours = sum(estimates[gap["key"]][f"hours_{level}"] for gap in blocking if gap["key"] in estimates)
        probabilities = assumptions["risk"]["residual_incident_probability"]
        risk = sum(probabilities[gap["kind"]] for gap in blocking) * assumptions["risk"]["incident_cost"][level]
        one_time = (migration_hours + rewrite_hours) * rate
        net[scenario] = round(savings - ops_cost - one_time - risk, 2)
        breakdown[scenario] = {
            "ops_cost": ops_cost,
            "migration_hours": migration_hours,
            "rewrite_hours": rewrite_hours,
            "one_time_cost": one_time,
            "residual_risk_cost": risk,
            "infra_savings": round(savings, 2),
            "lines": {
                "infrastructure": round(lines[target]["infrastructure"], 2),
                "io": round(lines[target]["io"], 2),
                "scale_ups": round(scale_up, 2),
                "extra_services": round(search_cost, 2),
                "operations": round(ops_cost, 2),
                "migration_and_rewrites": round(one_time, 2),
                "person_days": round((migration_hours + rewrite_hours) / 8, 1),
                "incidents": round(risk, 2),
                "plan": lines[target].get("plan"),
            },
        }

    base = breakdown["base"]
    infra_savings = base["infra_savings"]
    costs = {"MongoDB Atlas": round(atlas_total, 2), target: round(atlas_total - infra_savings, 2)}
    yearly_benefit = infra_savings - base["ops_cost"] - base["residual_risk_cost"]
    payback = round(base["one_time_cost"] / (yearly_benefit / 12), 1) if yearly_benefit > 0 else None
    affordable = yearly_benefit - base["migration_hours"] * rate
    return {
        "annual_costs": costs,
        "search_service_annual": search_cost,
        "infra_savings": infra_savings,
        "net": net,
        "breakdown": breakdown,
        "payback_months": payback,
        "break_even_rewrite_hours": round(affordable / rate, 1) if affordable > 0 else 0,
        "rewrite_estimates_missing": missing_estimates,
    }


def _decision(evidence: str, gaps: list[dict], economics: dict) -> str:
    blocking = [gap for gap in gaps if gap["blocking"]]
    pays_off = economics["net"]["base"] > 0
    if evidence == "INFERRED":
        return "VERIFY_FIRST"
    if not blocking:
        return "GO" if pays_off else "SAFE_BUT_NOT_WORTH_IT"
    if economics["rewrite_estimates_missing"]:
        return "STAY_UNPRICED"
    return "FIX_FIRST" if pays_off else "STAY"


DECISION_TEXT = {
    "GO": "Safe to migrate, and it pays off",
    "SAFE_BUT_NOT_WORTH_IT": "Safe to migrate, but it doesn't pay off within the horizon",
    "STAY": "Stay: fixing the gaps costs more than the migration saves",
    "STAY_UNPRICED": "Stay for now: blocking gaps exist, and the rewrite isn't priced yet",
    "FIX_FIRST": "Pays off only after the blocking gaps are fixed",
    "VERIFY_FIRST": "Not verified: test on a real cluster before deciding",
}


def _ask_subq(profile: str, targets: dict) -> dict:
    state = {
        "workload": "10-year-old e-commerce order system" if profile == "shoplog" else "Small order lookup service",
        "horizon_months": 12,
        "targets": {
            name: {
                "evidence_type": info["evidence"],
                "blocking_gaps": [{"feature": gap["name"], "kind": gap["kind"]} for gap in info["gaps"] if gap["blocking"]],
                "infra_savings_per_year_usd": info["economics"]["infra_savings"],
                "net_12_months_usd": info["economics"]["net"],
                "payback_months": info["economics"]["payback_months"],
            }
            for name, info in targets.items()
        },
    }
    questions = {
        name.replace(" ", "_"): {
            "instructions": f"Is migrating this workload to {name} net-positive over 12 months?",
            "criteria": {
                "true": "After rewrites, operating costs and incident risk, the migration leaves the company better off within 12 months",
                "false": "The migration costs more than it saves within 12 months, or risks outweigh the savings",
            },
        }
        for name in targets
    }
    answer = subq_client.ask_probabilities(purpose="migration_decision", state=state, questions=questions)
    if not answer["available"]:
        return {"available": False, "reason": answer["reason"]}
    return {
        "available": True,
        "model": answer["model"],
        "probabilities": {name: answer["answers"][name.replace(" ", "_")] for name in targets},
    }


def run(profile: str, runs: dict, ledger: dict, docdb: dict, ferretdb_documented: dict | None = None) -> dict:
    assumptions = load_assumptions()
    clusters = assumptions["deployment"]["clusters"]
    basis = assumptions["pricing"]["atlas_m40"]["basis"]
    alternative = assumptions["pricing"]["atlas_m40"]["alternative_basis"]

    target_specs = {
        FERRETDB: ("OBSERVED", observed_gaps(runs, ledger, assumptions) + documented_gaps(ferretdb_documented or {}, ledger, assumptions)),
        DOCUMENTDB: ("INFERRED", documented_gaps(docdb, ledger, assumptions)),
    }
    targets: dict[str, dict] = {}
    for name, (evidence, gaps) in target_specs.items():
        estimates = estimate_rewrites(profile, name, [gap for gap in gaps if gap["blocking"]], ledger)
        for gap in gaps:
            gap["estimate"] = estimates.get(gap["key"])
        economics = _economics(assumptions, name, gaps, estimates, clusters, basis)
        decision = _decision(evidence, gaps, economics)
        sensitivity = {
            "atlas_per_node": _summary(assumptions, name, evidence, gaps, estimates, clusters, alternative),
            "clusters": {str(count): _summary(assumptions, name, evidence, gaps, estimates, count, basis) for count in (1, 4, 10, 25)},
        }
        targets[name] = {
            "evidence": evidence,
            "gaps": gaps,
            "economics": economics,
            "decision": decision,
            "decision_text": DECISION_TEXT[decision],
            "sensitivity": sensitivity,
        }

    document = {"profile": profile, "assumptions": assumptions, "targets": targets, "subq": _ask_subq(profile, targets)}
    document["reversal"] = reversal(assumptions, targets[FERRETDB], clusters, basis)
    document["reversal"]["subq"] = _ask_subq_on_paper(profile, document["reversal"], document["subq"])
    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tco.json").write_text(json.dumps(document, indent=2, default=str))
    return document


GAP_STORY = {
    "F4": "Transactions don't roll back (checkout)",
    "F5": "Change streams unsupported (inventory sync)",
    "F6": "Case-insensitive email index refused (logins)",
    "F8": "Schema validator silently ignored (orders)",
    "F11": "No Atlas Search (storefront search)",
}
KIND_ORDER = {"silent": 0, "loud": 1, "performance": 2, "documented_conflict": 3, "cutover": 4}


def reversal(assumptions: dict, info: dict, clusters: int, basis: str) -> dict:
    """The same 12-month net, built step by step: from the paper case, through each finding.

    The steps add up exactly to the base-case net, so the walk is a decomposition, not a second model.
    """
    lines = annual_cost_lines(assumptions, clusters, basis)
    rate = assumptions["people"]["engineer_cost_per_hour"]
    incident = assumptions["risk"]["incident_cost"]["base"]
    probabilities = assumptions["risk"]["residual_incident_probability"]
    base = info["economics"]["breakdown"]["base"]
    paper = lines["MongoDB Atlas"]["infrastructure"] + lines["MongoDB Atlas"]["io"] - lines[FERRETDB]["infrastructure"] - lines[FERRETDB]["io"]
    running = paper - base["migration_hours"] * rate
    steps = [
        {
            "step": "On paper",
            "evidence": "Wire-compatible engine; infrastructure list prices; a standard migration effort",
            "change": None,
            "net": round(running, 2),
            "decision": "Migrate" if running > 0 else "Stay",
        }
    ]
    blocking = sorted((gap for gap in info["gaps"] if gap["blocking"]), key=lambda gap: (KIND_ORDER.get(gap["kind"], 9), gap["feature"]))
    for gap in blocking:
        estimate = gap.get("estimate") or {}
        cost = estimate.get("hours_base", 0) * rate + probabilities.get(gap["kind"], 0) * incident
        if gap["feature"] == "F11":
            cost += base["lines"]["extra_services"]
        if gap["kind"] == "performance":
            cost += base["lines"]["scale_ups"]
        running -= cost
        label = GAP_STORY.get(gap["feature"]) or (f"Reporting {gap['name'].split('(')[0].replace('Slow path on ', 'on ').strip()} far slower" if gap["kind"] == "performance" else gap["name"])
        steps.append(
            {
                "step": label,
                "evidence": f"{gap['kind'].replace('_', ' ')} · rewrite {estimate.get('hours_base', 0):.0f} h (Muse, inferred)",
                "change": round(-cost, 2),
                "net": round(running, 2),
                "decision": "Migrate" if running > 0 else "Stay",
            }
        )
    if base["ops_cost"]:
        running -= base["ops_cost"]
        steps.append({"step": "Running it yourself", "evidence": "Self-managed operations (assumption)", "change": round(-base["ops_cost"], 2), "net": round(running, 2), "decision": "Migrate" if running > 0 else "Stay"})
    flip = next((index for index, step in enumerate(steps) if index and step["decision"] != steps[index - 1]["decision"]), None)
    expected = info["economics"]["net"]["base"]
    return {
        "target": FERRETDB,
        "steps": steps,
        "flipped_at": flip,
        "adds_up": abs(running - expected) < 1.0,
        "final_net": round(running, 2),
    }


def _ask_subq_on_paper(profile: str, walk: dict, final: dict) -> dict:
    """The same question SubQ answered at the end, asked with only what the team knew on paper."""
    paper = walk["steps"][0]
    state = {
        "workload": "10-year-old e-commerce order system" if profile == "shoplog" else "Small order lookup service",
        "horizon_months": 12,
        "target": FERRETDB,
        "known_facts": [
            "The target engine is advertised as MongoDB wire-compatible",
            f"Infrastructure saves ${paper['net'] + 4800:,.0f} per year at list prices",
            "A standard migration is planned: data copy, cutover, regression tests",
        ],
        "net_12_months_usd": paper["net"],
    }
    answer = subq_client.ask_probabilities(
        purpose="migration_decision_on_paper",
        state=state,
        questions={
            "FerretDB": {
                "instructions": f"Is migrating this workload to {FERRETDB} net-positive over 12 months?",
                "criteria": {
                    "true": "After rewrites, operating costs and incident risk, the migration leaves the company better off within 12 months",
                    "false": "The migration costs more than it saves within 12 months, or risks outweigh the savings",
                },
            }
        },
    )
    return {
        "on_paper": answer["answers"]["FerretDB"] if answer.get("available") else None,
        "with_evidence": (final.get("probabilities") or {}).get(FERRETDB) if final.get("available") else None,
        "model": answer.get("model"),
    }


def _summary(assumptions, name, evidence, gaps, estimates, clusters, basis) -> dict:
    economics = _economics(assumptions, name, gaps, estimates, clusters, basis)
    return {"net_base": economics["net"]["base"], "infra_savings": economics["infra_savings"], "decision": _decision(evidence, gaps, economics)}
