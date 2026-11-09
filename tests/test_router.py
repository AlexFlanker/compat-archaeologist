"""Routing: exact button matches, Muse for prose, keywords whenever Muse can't answer."""

from __future__ import annotations

import pytest

from archaeologist import meta_client
from fetch_agent import router
from fetch_agent.router import Memory, route


@pytest.fixture
def muse(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        return fake.reply

    fake.reply = {"action": "ask", "feature": "", "profile": "", "target": ""}
    monkeypatch.setattr(meta_client, "structured", fake)
    fake.calls = calls
    return fake


def test_card_json_skips_the_model(muse):
    decided = route('{"action": "view", "view": "gap", "profile": "shoplog", "target": "FerretDB", "gap": "behavior:F4"}', Memory())
    assert decided == {"action": "gap_detail", "profile": "shoplog", "via": "card", "target": "FerretDB", "gap": "behavior:F4"}
    assert not muse.calls


@pytest.mark.parametrize("label", ["Dig into F6", "Next: F6", "Previous: F6", "Dig into F6 (FerretDB)"])
def test_gap_buttons_as_prose_skip_the_model(muse, label):
    decided = route(label, Memory())
    assert decided["action"] == "gap_detail" and decided["feature"] == "F6"
    assert not muse.calls


def test_ask_button_json_carries_the_question(muse):
    decided = route('{"action": "ask", "question": "What would change the verdict?", "profile": "shoplog"}', Memory())
    assert decided["action"] == "ask" and decided["question"] == "What would change the verdict?"


def test_prose_goes_to_muse_with_the_current_gap(muse):
    muse.reply = {"action": "gap_detail", "feature": "", "profile": "", "target": ""}
    memory = Memory(feature="F4", target="FerretDB", gap="behavior:F4")
    decided = route("tell me more about that one", memory)
    assert decided == {"action": "gap_detail", "profile": "shoplog", "via": "muse", "feature": "F4"}
    assert '"current_gap": "F4"' in muse.calls[0]["user"]


def test_gap_detail_without_any_feature_becomes_the_gap_list(muse):
    muse.reply = {"action": "gap_detail", "feature": "", "profile": "", "target": ""}
    assert route("show me a gap", Memory())["action"] == "gaps"


def test_fallback_when_muse_is_unavailable(monkeypatch):
    def down(**kwargs):
        raise meta_client.MetaUnavailable("no key")

    monkeypatch.setattr(meta_client, "structured", down)
    assert route("what breaks on the control app?", Memory()) | {} == {"profile": "lite", "via": "fallback", "action": "gaps"}
    assert route("hello there", Memory())["action"] == "help"


def test_invalid_model_output_falls_back(muse):
    muse.reply = {"action": "delete_everything", "feature": "", "profile": "", "target": ""}
    assert route("show the reversal please", Memory())["via"] == "fallback"


def test_unknown_prose_never_starts_a_pipeline_run(monkeypatch):
    monkeypatch.setattr(meta_client, "structured", lambda **kwargs: (_ for _ in ()).throw(meta_client.MetaUnavailable("x")))
    assert route("can you explain this to my boss", Memory())["action"] != "assess"


def test_memory_keeps_three_turns():
    memory = Memory()
    for index in range(5):
        memory.remember_turn(f"q{index}", "a")
    assert [turn["q"] for turn in memory.history] == ["q2", "q3", "q4"]


def test_router_schema_matches_actions():
    assert router.ROUTER_SCHEMA["properties"]["action"]["enum"] == router.ACTIONS
