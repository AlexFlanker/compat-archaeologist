"""Versioned probe-writing strategies and the roster that records every adoption decision.

A strategy is the part of probe generation a model may change: extra system-prompt text,
per-feature hints, probe counts and required checks. Files live in strategy/vNNN.json; the
roster (strategy/roster.json) names the active one and keeps every evaluation, rejections included.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from archaeologist.config import ROOT

STRATEGY_DIR = ROOT / "strategy"


def _roster_path() -> Path:
    return STRATEGY_DIR / "roster.json"


def roster() -> dict:
    return json.loads(_roster_path().read_text())


def load(version: str) -> dict:
    return json.loads((STRATEGY_DIR / f"{version}.json").read_text())


def active() -> dict:
    return load(roster()["active"])


def next_version() -> str:
    numbers = [int(m.group(1)) for path in STRATEGY_DIR.glob("v*.json") if (m := re.fullmatch(r"v(\d+)\.json", path.name))]
    return "v%03d" % (max(numbers, default=-1) + 1)


def save(strategy: dict) -> Path:
    path = STRATEGY_DIR / f"{strategy['version']}.json"
    path.write_text(json.dumps(strategy, indent=2) + "\n")
    return path


def record(entry: dict) -> None:
    doc = roster()
    doc["history"].append(entry)
    if entry["decision"] == "adopted":
        doc["active"] = entry["version"]
    _roster_path().write_text(json.dumps(doc, indent=2) + "\n")


def render_system(base: str, strategy: dict) -> str:
    """`base` is the already-formatted system prompt; v000 leaves it unchanged."""
    text = base
    additions = strategy.get("system_prompt_additions", "")
    if additions:
        text += "\n\n" + additions
    checks = strategy.get("required_checks", [])
    if checks:
        text += "\n\nEvery probe must also:\n" + "\n".join(f"- {check}" for check in checks)
    return text


def hint_for(strategy: dict, feature_id: str) -> str:
    return strategy.get("per_feature_hints", {}).get(feature_id, "")


def count_for(strategy: dict, item: dict) -> int:
    from archaeologist import probes

    override = strategy.get("probe_count_overrides", {}).get(item["id"])
    return override if override else probes.probe_count_for(item)
