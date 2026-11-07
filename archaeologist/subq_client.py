"""Asks SubQ Snap for a probability. Reports failure rather than inventing a number."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import requests

from archaeologist.config import LOGS_DIR, env

URL = "https://api.subq.ai/v1/systemone"
DEFAULT_MODEL = "subq-snap-1.1"


def is_configured() -> bool:
    return bool(env("SUBQ_API_KEY"))


def model_name() -> str:
    return env("SUBQ_MODEL", DEFAULT_MODEL)


def _record(entry: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / "subq_calls.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def ask_probabilities(*, purpose: str, state, questions: dict[str, dict]) -> dict:
    """Asks several yes/no questions about one state.

    Returns {"available": True, "model": ..., "answers": {name: probability}} or
    {"available": False, "reason": ...}. Callers must carry the second form through as is.
    """
    key = env("SUBQ_API_KEY")
    if not key:
        return {"available": False, "reason": "SUBQ_API_KEY is not set"}

    model = model_name()
    body = {
        "model": model,
        "state": state,
        "questions": {name: {"type": "noul", **question} for name, question in questions.items()},
    }
    started = time.perf_counter()
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "purpose": purpose, "model": model}
    try:
        response = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=60)
    except requests.RequestException as exc:
        _record({**entry, "status": type(exc).__name__})
        return {"available": False, "reason": f"request failed: {type(exc).__name__}"}

    entry["latency_s"] = round(time.perf_counter() - started, 2)
    if response.status_code != 200:
        _record({**entry, "status": f"http_{response.status_code}"})
        return {"available": False, "reason": f"SubQ returned {response.status_code}"}

    answers = response.json().get("answers", {})
    try:
        probabilities = {name: float(answers[name]["noul"]) for name in questions}
    except (KeyError, TypeError, ValueError):
        _record({**entry, "status": "unexpected_response_shape"})
        return {"available": False, "reason": "SubQ response did not contain the expected answers"}

    _record({**entry, "status": "ok", "answers": probabilities})
    print(f"  [subq] {model} · {purpose} · {entry['latency_s']}s · {probabilities}", flush=True)
    return {"available": True, "model": model, "answers": probabilities}
