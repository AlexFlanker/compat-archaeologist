"""Explains why the slowest replayed queries differ, by comparing query plans on both engines.

Plans come from the MongoDB MCP Server's explain tool, run read-only against each engine. MongoDB
returns its own plan stages. FerretDB returns the Postgres plan underneath it. The two can't be
compared node for node, so each is reduced to what drives cost: scans, joins and index use.
"""

from __future__ import annotations

import json
import re

from bson import json_util
from bson.json_util import RELAXED_JSON_OPTIONS

from archaeologist import mcp_tools
from archaeologist.config import OUT_DIR, env
from archaeologist.extract_querylog import clean_command, load_log, query_log_path, shape_of
from archaeologist.replay import _shape_id

SLOWEST = 3
PLAN_CHILDREN = ("inputStage", "inputStages", "queryPlan", "outerStage", "innerStage", "thenStage", "elseStage")


def _payload(text: str):
    """The tool wraps results in untrusted-data tags. This reads the JSON inside as data only."""
    candidates = re.findall(r">\s*(\{.*?\})\s*</untrusted-user-data", text, re.S)
    for candidate in reversed(candidates):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _number(value):
    """The MCP server serializes 64-bit integers as {low, high}; this restores the number."""
    if isinstance(value, dict) and "low" in value and "high" in value:
        return (value["high"] << 32) + (value["low"] & 0xFFFFFFFF)
    return value


def _walk_mongo(node, stages: list[str]) -> None:
    if isinstance(node, dict):
        if "stage" in node:
            label = node["stage"]
            if node.get("indexName"):
                label += f"({node['indexName']})"
            if node.get("strategy"):
                label += f"[{node['strategy']}]"
            stages.append(label)
        for key in PLAN_CHILDREN:
            if key in node:
                _walk_mongo(node[key], stages)
    elif isinstance(node, list):
        for inner in node:
            _walk_mongo(inner, stages)


def summarize_mongodb(plan: dict) -> dict:
    stages: list[str] = []
    summary: dict = {"engine_format": "MongoDB plan"}
    if "stages" in plan:
        for stage in plan["stages"]:
            name = next(iter(stage))
            body = stage[name]
            if name == "$cursor":
                cursor_stages: list[str] = []
                _walk_mongo(body.get("queryPlanner", {}).get("winningPlan", {}), cursor_stages)
                stages.extend(reversed(cursor_stages))
                execution = body.get("executionStats", {})
                summary["docs_examined"] = _number(execution.get("totalDocsExamined"))
                summary["time_ms"] = _number(execution.get("executionTimeMillis"))
            else:
                label = name
                if name == "$lookup":
                    label += (
                        f"(indexes={stage.get('indexesUsed')}, collection scans={_number(stage.get('collectionScans'))}, "
                        f"docs examined={_number(stage.get('totalDocsExamined'))})"
                    )
                stages.append(label)
    else:
        plan_stages: list[str] = []
        _walk_mongo(plan.get("queryPlanner", {}).get("winningPlan", {}), plan_stages)
        stages.extend(reversed(plan_stages))
        execution = plan.get("executionStats", {})
        summary["docs_examined"] = _number(execution.get("totalDocsExamined"))
        summary["keys_examined"] = _number(execution.get("totalKeysExamined"))
        summary["time_ms"] = _number(execution.get("executionTimeMillis"))
    summary["plan"] = " → ".join(stages)
    summary["full_scans"] = sum(1 for stage in stages if stage.startswith("COLLSCAN"))
    return summary


def _walk_postgres(node, nodes: list[str]) -> None:
    if isinstance(node, dict):
        if "Node Type" in node:
            label = node["Node Type"]
            if node.get("Index Name"):
                label += f"({node['Index Name']})"
            elif node.get("Function Name"):
                label += f"({node['Function Name']})"
            nodes.append(label)
        for inner in node.get("Plans", []):
            _walk_postgres(inner, nodes)
    elif isinstance(node, list):
        for inner in node:
            _walk_postgres(inner, nodes)


def summarize_postgres(plan: dict) -> dict:
    root = plan.get("queryPlanner", {}).get("Plan") or plan.get("Plan") or plan
    nodes: list[str] = []
    _walk_postgres(root, nodes)
    return {
        "engine_format": "Postgres plan (FerretDB)",
        "plan": " → ".join(nodes),
        "full_scans": sum(1 for node in nodes if node.startswith("Seq Scan")),
        "nested_loops": sum(1 for node in nodes if node.startswith("Nested Loop")),
        "estimated_total_cost": root.get("Total Cost") if isinstance(root, dict) else None,
        "time_ms": root.get("Actual Total Time") if isinstance(root, dict) else None,
    }


def _method(command: dict) -> tuple[str, list[dict]]:
    plain = json.loads(json_util.dumps(command, json_options=RELAXED_JSON_OPTIONS))
    if "aggregate" in plain:
        return plain["aggregate"], [{"name": "aggregate", "arguments": {"pipeline": plain["pipeline"]}}]
    arguments = {key: plain[key] for key in ("filter", "projection", "sort", "limit") if key in plain}
    return plain["find"], [{"name": "find", "arguments": arguments}]


def slowest_shapes(runs: dict, count: int = SLOWEST) -> list[dict]:
    shapes = [shape for shape in runs["replay"]["reads"]["shapes"] if shape.get("target_p95_ms") and shape.get("source_p95_ms")]
    return sorted(shapes, key=lambda shape: -(shape["target_p95_ms"] - shape["source_p95_ms"]))[:count]


def run(profile: str, runs: dict) -> dict:
    entries = load_log(query_log_path(profile))
    by_shape = {}
    for entry in entries:
        by_shape.setdefault(_shape_id(shape_of(entry)), entry)

    targets = slowest_shapes(runs)
    calls = []
    for shape in targets:
        collection, method = _method(clean_command(by_shape[shape["shape_id"]]))
        calls.append(
            (
                "explain",
                {"connectionId": "preconfigured", "database": profile, "collection": collection, "method": method, "verbosity": "executionStats"},
            )
        )

    comparisons = []
    try:
        source_results = mcp_tools.call_tools(env("SOURCE_URI"), calls, label="MongoDB")
        target_results = mcp_tools.call_tools(env("TARGET_URI"), calls, label="FerretDB")
    except mcp_tools.MCPUnavailable as exc:
        document = {"available": False, "reason": str(exc), "comparisons": []}
    else:
        for shape, source_result, target_result in zip(targets, source_results, target_results):
            source_plan = (_payload(source_result.get("text", "")) or {}).get("plan")
            target_plan = (_payload(target_result.get("text", "")) or {}).get("plan")
            comparisons.append(
                {
                    "shape_id": shape["shape_id"],
                    "features": shape["features"],
                    "collection": shape["collection"],
                    "stages": shape["stages"],
                    "replay_p95_ms": {"source": shape["source_p95_ms"], "target": shape["target_p95_ms"]},
                    "source": summarize_mongodb(source_plan) if source_plan else {"error": source_result.get("text", "")[:300]},
                    "target": summarize_postgres(target_plan) if target_plan else {"error": target_result.get("text", "")[:300]},
                }
            )
        document = {"available": True, "tool": f"{mcp_tools.SERVER_PACKAGE} explain", "comparisons": comparisons}

    out_dir = OUT_DIR / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "explain.json").write_text(json.dumps(document, indent=2, default=str))
    return document


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    arguments = parser.parse_args()
    result = run(arguments.profile, json.loads((OUT_DIR / arguments.profile / "runs.json").read_text()))
    for comparison in result["comparisons"]:
        print(f"\n{comparison['features']} {comparison['collection']} p95 {comparison['replay_p95_ms']}")
        print("  MongoDB :", json.dumps(comparison["source"])[:400])
        print("  FerretDB:", json.dumps(comparison["target"])[:400])
    if not result["available"]:
        print("unavailable:", result["reason"])
