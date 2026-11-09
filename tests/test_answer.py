"""Grounded answers: invented citations are removed, uncited answers are replaced."""

from __future__ import annotations

import json

import pytest

from archaeologist import meta_client
from archaeologist.config import OUT_DIR
from fetch_agent import answer, cards


@pytest.fixture(autouse=True)
def _needs_output():
    if not (OUT_DIR / "shoplog" / "tco.json").exists():
        pytest.skip("run the pipeline first")


def _reply(monkeypatch, reply):
    monkeypatch.setattr(meta_client, "structured", lambda **kwargs: reply)


def test_cited_answer_keeps_only_real_citations(monkeypatch):
    _reply(monkeypatch, {"answer": "Rollback leaves stock at 8 instead of 10.", "citations": ["probe:F4.txn-abort-rollback", "gap:FerretDB:F4", "probe:invented"], "answerable": True})
    result = answer.answer("why?", "shoplog")
    assert [item["id"] for item in result["citations"]] == ["probe:F4.txn-abort-rollback", "gap:FerretDB:F4"]
    assert result["dropped"] == ["probe:invented"] and result["via"] == "muse"
    _, card = cards.answer_card("shoplog", result, json.loads((OUT_DIR / "shoplog" / "tco.json").read_text()))
    assert "Dig into F4 (FerretDB)" in card["card_payload"] and "removed" in card["card_payload"]


def test_uncited_answer_is_never_sent(monkeypatch):
    _reply(monkeypatch, {"answer": "FerretDB will cost $3 million.", "citations": ["made:up"], "answerable": True})
    result = answer.answer("how much?", "shoplog")
    assert "$3 million" not in result["text"] and result["via"] == "rejected_uncited"


def test_model_down_says_so(monkeypatch):
    def down(**kwargs):
        raise meta_client.MetaUnavailable("timeout")

    monkeypatch.setattr(meta_client, "structured", down)
    result = answer.answer("why?", "shoplog")
    assert result["via"] == "unavailable" and "can't reach" in result["text"]


def test_the_prompt_carries_the_pack(monkeypatch):
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return {"answer": "n/a", "citations": [], "answerable": False}

    monkeypatch.setattr(meta_client, "structured", capture)
    answer.answer("anything", "shoplog")
    assert "[step:1]" in seen["system"] and seen["timeout_s"] == answer.ANSWER_TIMEOUT_S


def test_help_card_shape():
    _, card = cards.help_card("shoplog")
    assert card["card_kind"] == "custom" and "Try asking" in card["card_payload"]
