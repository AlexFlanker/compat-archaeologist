"""Measures the agent against live Muse: does it route each phrasing right, and are its answers cited and honest?

Writes out/agent_eval.json. Run: .venv/bin/python -m scripts.eval_agent
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from archaeologist import meta_client
from archaeologist.config import OUT_DIR
from fetch_agent import answer, router

# (message, expected action, expected feature or None, memory feature or None)
ROUTING = [
    ("what's the verdict for shoplog?", "decision", None, None),
    ("should we migrate or not", "decision", None, None),
    ("show me how the decision flipped", "reversal", None, None),
    ("walk me through the paper case versus the evidence", "reversal", None, None),
    ("what breaks if we move to FerretDB?", "gaps", None, None),
    ("list the incompatibilities", "gaps", None, None),
    ("tell me about the transactions problem", "gap_detail", "F4", None),
    ("show the case-insensitive email index gap", "gap_detail", "F6", None),
    ("details on the schema validator", "gap_detail", "F8", None),
    # Ambiguous: "show me" and "tell me" readings are both fine, and Muse picks either from run to run.
    ("what about change streams?", ("gap_detail", "ask"), None, None),
    ("show me more about that one", "gap_detail", "F6", "F6"),
    ("which mongodb features does the app depend on", "ledger", None, None),
    ("how slow is the reporting query on ferretdb, show the query plans", "latency", None, None),
    ("rerun the assessment for the control app", "assess", None, None),
    ("hi, what can you do?", "help", None, None),
    ("迁移到 FerretDB 会出什么问题？", "gaps", None, None),
    ("给我看决策是怎么反转的", "reversal", None, None),
    ("事务那个问题具体是什么？", "gap_detail", "F4", None),
    ("why is the transaction gap silent?", "ask", None, None),
    ("what engineer hourly rate did you assume?", "ask", None, None),
    ("would it pay off with 25 clusters?", "ask", None, None),
    ("为什么这个是 silent 的？修复要花多少？", "ask", None, "F4"),
    ("how long would fixing that one take?", "ask", None, "F6"),
    ("Next: F6", "gap_detail", "F6", "F4"),
]

# (question, should the evidence cover it, a citation prefix that must appear or None)
ANSWERS = [
    ("Why does FerretDB lose the transaction rollback?", True, "probe:F4"),
    ("What engineer hourly rate does the cost model assume?", True, "assumption:"),
    ("At which step does the decision flip, and by how much?", True, "step:"),
    ("Would migrating pay off if we had 25 clusters?", True, "sensitivity:"),
    ("How many hidden business rules were recovered?", True, "rule"),
    ("Which cutover checks failed in the clean run?", True, "check:"),
    ("What does SubQ say about migrating to FerretDB?", True, "subq:"),
    ("如果迁到 FerretDB，邮箱登录会有什么问题？", True, "gap:FerretDB:F6"),
    ("What will MongoDB Atlas charge us in 2030?", False, None),
    ("What's the CEO's favorite database?", False, None),
    ("How long would migrating to Cassandra take?", False, None),
]


def _route_case(case):
    message, action, feature, memory_feature = case
    memory = router.Memory(feature=memory_feature, target="FerretDB" if memory_feature else None)
    started = time.perf_counter()
    decided = router.route(message, memory)
    ok = decided["action"] in (action if isinstance(action, tuple) else (action,)) and (feature is None or decided.get("feature") == feature)
    return {"message": message, "expected": {"action": action, "feature": feature}, "got": decided, "ok": ok,
            "seconds": round(time.perf_counter() - started, 2)}


def _answer_case(case):
    question, covered, must_cite = case
    started = time.perf_counter()
    result = answer.answer(question, "shoplog")
    ids = [item["id"] for item in result["citations"]]
    honest = result["answerable"] == covered
    cited = (not covered) or (bool(ids) and (must_cite is None or any(item.startswith(must_cite) for item in ids)))
    return {"question": question, "expected_covered": covered, "must_cite": must_cite, "answerable": result["answerable"],
            "via": result["via"], "citations": ids, "dropped": result["dropped"], "text": result["text"],
            "ok": honest and cited and result["via"] in {"muse", "rejected_uncited"}, "seconds": round(time.perf_counter() - started, 2)}


def main() -> None:
    if not meta_client.is_configured():
        raise SystemExit("META_API_KEY is not set; this eval calls live Muse.")
    with ThreadPoolExecutor(max_workers=4) as pool:
        routes = list(pool.map(_route_case, ROUTING))
        answers = list(pool.map(_answer_case, ANSWERS))
    for row in routes:
        print(f"{'ok  ' if row['ok'] else 'MISS'} route  {row['message'][:60]:<60} → {row['got']['action']} {row['got'].get('feature') or ''} ({row['got']['via']})")
    for row in answers:
        print(f"{'ok  ' if row['ok'] else 'MISS'} answer {row['question'][:60]:<60} → answerable={row['answerable']} cites={row['citations'][:3]}")
    dropped = sum(len(row["dropped"]) for row in answers)
    total_citations = dropped + sum(len(row["citations"]) for row in answers)
    summary = {
        "routing_accuracy": f"{sum(row['ok'] for row in routes)}/{len(routes)}",
        "answers_ok": f"{sum(row['ok'] for row in answers)}/{len(answers)}",
        "citations_valid": f"{total_citations - dropped}/{total_citations}",
        "routed_by_fallback": sum(row["got"]["via"] == "fallback" for row in routes),
        "median_route_seconds": sorted(row["seconds"] for row in routes)[len(routes) // 2],
        "median_answer_seconds": sorted(row["seconds"] for row in answers)[len(answers) // 2],
    }
    document = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": meta_client.model_name(),
                "summary": summary, "routing": routes, "answers": answers}
    (OUT_DIR / "agent_eval.json").write_text(json.dumps(document, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
