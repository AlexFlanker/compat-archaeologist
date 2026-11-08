"""Cards must follow the documented shape, or ASI:One silently falls back to plain text."""

from __future__ import annotations

import json

import pytest

from archaeologist.config import OUT_DIR
from fetch_agent import cards

REQUIRED_KEYS = {"card_protocol_version", "requires_card_interaction", "card_kind", "card_payload"}


def _load(profile: str, name: str):
    path = OUT_DIR / profile / name
    if not path.exists():
        pytest.skip(f"run the pipeline first: {path} is missing")
    return json.loads(path.read_text())


def _check(card: dict, kind: str) -> dict:
    assert REQUIRED_KEYS <= set(card)
    assert all(isinstance(value, str) for value in card.values())
    assert card["card_kind"] == kind and card["card_protocol_version"] == "1"
    assert len(card["card_payload"].encode()) <= cards.MAX_PAYLOAD_BYTES
    return json.loads(card["card_payload"])


@pytest.mark.parametrize("profile", ["shoplog", "lite"])
def test_decision_card_shape(profile):
    _, card = cards.decision_card(profile, _load(profile, "tco.json"), _load(profile, "ledger.json"))
    payload = _check(card, "detail")
    assert payload["title"] and all({"label", "value"} == set(row) for row in payload["summary_rows"])
    assert payload["sub_options"]["name"] == "view"
    assert all({"label", "selection"} <= set(cta) for cta in payload["ctas"])


@pytest.mark.parametrize("profile", ["shoplog", "lite"])
def test_custom_cards_shape(profile):
    for narration, card in (
        cards.gaps_card(profile, _load(profile, "tco.json")),
        cards.ledger_card(profile, _load(profile, "ledger.json")),
        cards.latency_card(profile, _load(profile, "runs.json"), None),
    ):
        payload = _check(card, "custom")
        assert narration and payload["root"]["type"] == "section"


def test_headline_says_migration_is_fine_for_control():
    assert "the migration is fine" in cards.headline("lite", _load("lite", "tco.json"))


def test_oversized_payload_is_refused():
    with pytest.raises(ValueError):
        cards.metadata("custom", {"root": {"type": "text", "value": "x" * (70 * 1024)}})


def test_reversal_card_marks_the_flip():
    import json as _json
    from fetch_agent import cards as _cards
    tco = {"reversal": {"target": "FerretDB", "flipped_at": 1, "steps": [
        {"step": "On paper", "evidence": "list prices", "change": None, "net": 20000.0, "decision": "Migrate"},
        {"step": "Transactions don't roll back", "evidence": "silent", "change": -26000.0, "net": -6000.0, "decision": "Stay"},
    ], "subq": {"on_paper": 0.9995, "with_evidence": 0.0001}}}
    narration, card = _cards.reversal_card("shoplog", tco)
    payload = card["card_payload"]
    assert "flips" in narration and "Decision flips here" in payload
    assert ">0.999" in payload and "<0.001" in payload
    assert _json.loads(payload)["root"]["type"] == "section"


def test_reversal_intent():
    from fetch_agent.agent import _intent
    assert _intent("Show: How the evidence reversed it")["view"] == "reversal"
    assert _intent("Run the control app (view: How the evidence reversed it)") == {"action": "assess", "profile": "lite"}
