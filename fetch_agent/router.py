"""Decides what a chat message asks for. Button clicks are matched exactly; prose goes to Muse; keywords are the fallback.

ASI:One delivers some card clicks as JSON and some as prose (the button label, sometimes with the selected
radio option), so known button labels are matched before any model is asked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from archaeologist import meta_client

ACTIONS = ["decision", "reversal", "gaps", "gap_detail", "ledger", "latency", "assess", "ask", "help"]
PROFILES = ["shoplog", "lite"]
TARGETS = ["FerretDB", "AWS DocumentDB"]
ROUTER_TIMEOUT_S = 20
# At Muse's default effort a route took ~14 s (~1,700 reasoning tokens); "low" ~10 s; "minimal" ~3 s with the
# same 21/21 on scripts/eval_agent.py (2026-11-08).
ROUTER_EFFORT = "minimal"

FEATURE = re.compile(r"\b(F\d{1,2})\b", re.IGNORECASE)
GAP_BUTTON = re.compile(r"^\s*(dig into|next:|previous:)\s*(F\d{1,2})\b", re.IGNORECASE)

ROUTER_PROMPT = """You route messages for Compat Archaeologist, an agent that has assessed whether a MongoDB app can move to FerretDB or AWS DocumentDB.
Two assessments exist: "shoplog" (ShopLog, a 10-year-old e-commerce order system; verdict: stay) and "lite" (ShopLog Lite, a small CRUD control app; verdict: go).

Pick one action:
- decision: the headline verdict or summary card
- reversal: how the evidence reversed the paper case, the step-by-step walk, where the decision flips
- gaps: the list of what breaks
- gap_detail: one specific gap (set feature to its id, e.g. F4). Features: F1 $lookup+$facet (slow reporting), F2 $setWindowFields, F3 arrayFilters, F4 transactions, F5 change streams, F6 case-insensitive collation index (emails/logins), F7 partial+TTL index, F8 $jsonSchema validator, F9 $text search, F10 plain CRUD, F11 Atlas Search
- ledger: the usage-spec ledger, what MongoDB features the app depends on
- latency: replay latency and query plans
- assess: run a new assessment now (only when the user explicitly asks to run, rerun or assess)
- ask: any other question about the assessment, evidence, costs, assumptions, rules, cutover, method; includes why/how/what-if questions
- help: greetings, or questions about what the agent can do

Set profile to "" unless the user names an app. Set target to "" unless the user names an engine.
Use gap_detail only when the user wants to open or see a gap ("show", "tell me about", "details", "what is the X problem").
A question that asks why, how, how much, how long, whether or what if is "ask", even when it is about one gap; set feature to that gap.
If the user refers to "this gap", "that one", "it" and a current gap is given, use that gap's feature.
The user may write in any language."""

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ACTIONS},
        "feature": {"type": "string"},
        "profile": {"type": "string", "enum": PROFILES + [""]},
        "target": {"type": "string", "enum": TARGETS + [""]},
    },
    "required": ["action", "feature", "profile", "target"],
    "additionalProperties": False,
}


@dataclass
class Memory:
    """What one sender last looked at, so follow-ups like "why is that one silent?" resolve."""

    profile: str = "shoplog"
    target: str | None = None
    gap: str | None = None
    feature: str | None = None
    history: list[dict] = field(default_factory=list)

    def remember_turn(self, question: str, answer: str) -> None:
        self.history = (self.history + [{"q": question[:300], "a": answer[:600]}])[-3:]


def _from_selection(selection: dict, memory: Memory) -> dict:
    action = selection.get("action", "decision")
    route = {"action": action, "profile": selection.get("profile") or memory.profile, "via": "card"}
    if action == "view":
        view = selection.get("view") or "gaps"
        route["action"] = "gap_detail" if view == "gap" else view
        route["target"], route["gap"] = selection.get("target"), selection.get("gap")
    if action == "ask":
        route["question"] = selection.get("question") or ""
    return route


def keyword_route(text: str, memory: Memory) -> dict:
    """The original keyword rules, plus feature ids. Used for known button labels and when Muse is unavailable."""
    lowered = text.lower()
    named = "lite" if any(word in lowered for word in ("lite", "control", "simple", "crud")) else ("shoplog" if "shoplog" in lowered else None)
    profile = named or memory.profile
    route = {"profile": profile, "via": "keywords"}
    if "run the control app" in lowered:
        return {**route, "action": "assess", "profile": "lite"}
    if GAP_BUTTON.match(text) or (FEATURE.search(text) and len(text) < 40):
        return {**route, "action": "gap_detail", "feature": FEATURE.search(text).group(1).upper()}
    if "all gaps" in lowered:
        return {**route, "action": "gaps"}
    if "back to decision" in lowered:
        return {**route, "action": "decision"}
    if any(word in lowered for word in ("revers", "flip", "on paper", "paper case")):
        return {**route, "action": "reversal"}
    if "assess" in lowered or "rerun" in lowered:
        return {**route, "action": "assess"}
    for view in ("gaps", "ledger", "latency"):
        if view in lowered or (view == "gaps" and "break" in lowered):
            return {**route, "action": view}
    if any(word in lowered for word in ("cached", "last result", "decision", "summary")):
        return {**route, "action": "decision"}
    if named and len(lowered) < 40:
        return {**route, "action": "decision"}
    return {**route, "action": "help"}


BUTTON_LABELS = ("run the control app", "all gaps", "back to decision", "show:", "show (")


def route(text: str, memory: Memory) -> dict:
    """Returns {action, profile, via, [feature], [target], [gap]}."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            selection = json.loads(stripped)
        except json.JSONDecodeError:
            selection = None
        if isinstance(selection, dict):
            return _from_selection(selection, memory)
    lowered = stripped.lower()
    if GAP_BUTTON.match(stripped) or lowered.startswith(BUTTON_LABELS) or lowered in {"show", "assess"}:
        return keyword_route(stripped, memory)

    context = {"message": stripped, "current_app": memory.profile, "current_gap": memory.feature, "current_target": memory.target}
    try:
        decided = meta_client.structured(
            purpose="agent_route", system=ROUTER_PROMPT, user=json.dumps(context, ensure_ascii=False),
            schema=ROUTER_SCHEMA, schema_name="route", max_tokens=2000, timeout_s=ROUTER_TIMEOUT_S, attempts=2, reasoning_effort=ROUTER_EFFORT,
        )
    except meta_client.MetaUnavailable:
        return keyword_route(stripped, memory) | {"via": "fallback"}
    if decided.get("action") not in ACTIONS:
        return keyword_route(stripped, memory) | {"via": "fallback"}
    result = {"action": decided["action"], "profile": decided.get("profile") or memory.profile, "via": "muse"}
    feature = (decided.get("feature") or "").strip().upper()
    if FEATURE.fullmatch(feature):
        result["feature"] = feature
    elif result["action"] == "gap_detail" and memory.feature:
        result["feature"] = memory.feature
    if decided.get("target"):
        result["target"] = decided["target"]
    if result["action"] == "gap_detail" and "feature" not in result:
        result["action"] = "gaps"
    return result
