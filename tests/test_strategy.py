import json

import pytest

from archaeologist import probes, strategy


@pytest.fixture
def tmp_roster(tmp_path, monkeypatch):
    monkeypatch.setattr(strategy, "STRATEGY_DIR", tmp_path)
    base = {"version": "v000", "parent": None, "created": "x", "system_prompt_additions": "", "per_feature_hints": {},
            "probe_count_overrides": {}, "required_checks": []}
    (tmp_path / "v000.json").write_text(json.dumps(base))
    (tmp_path / "roster.json").write_text(json.dumps({"active": "v000", "history": []}))
    return tmp_path


def _entry(version, decision):
    return {"version": version, "parent": "v000", "decision": decision, "reason": "r", "measured": True}


def test_record_refuses_an_entry_without_the_measured_flag(tmp_roster):
    entry = _entry("v001", "rolled_back")
    del entry["measured"]
    with pytest.raises(ValueError, match="measured"):
        strategy.record(entry)
    assert json.loads((tmp_roster / "roster.json").read_text())["history"] == []


def test_committed_roster_marks_copied_numbers_as_not_measured():
    """Entries whose numbers were copied from the reference (no candidate was measured) say so."""
    history = json.loads((strategy.STRATEGY_DIR / "roster.json").read_text())["history"]
    unmeasured = [
        entry["version"] for entry in history
        if entry["reason"] == "edit targets no generatable feature" or entry["reason"].startswith(("generation failed", "measurement failed"))
    ]
    assert unmeasured == ["v001", "v002", "v003", "v006", "v007"]
    assert all(entry.get("measured") is False for entry in history if entry["version"] in unmeasured)
    assert all(entry.get("measured", True) is True for entry in history if entry["version"] not in unmeasured)


def test_v000_renders_base_prompt_unchanged(tmp_roster):
    assert strategy.render_system("BASE", strategy.active()) == "BASE"


def test_additions_appended_and_hint_and_count_override(tmp_roster):
    s = {**strategy.active(), "system_prompt_additions": "Be strict.", "per_feature_hints": {"F1": "use $lookup"},
         "probe_count_overrides": {"F1": 2}, "required_checks": ["read back the write"]}
    rendered = strategy.render_system("BASE", s)
    assert rendered.startswith("BASE\n\nBe strict.")
    assert "Every probe must also:" in rendered and "read back the write" in rendered
    assert strategy.hint_for(s, "F1") == "use $lookup" and strategy.hint_for(s, "F2") == ""
    assert strategy.count_for(s, {"id": "F1"}) == 2
    assert strategy.count_for(s, {"id": "F4"}) == probes.probe_count_for({"id": "F4"})


def test_next_version_and_save(tmp_roster):
    assert strategy.next_version() == "v001"
    path = strategy.save({**strategy.active(), "version": "v001", "parent": "v000"})
    assert path.name == "v001.json" and strategy.load("v001")["parent"] == "v000"
    assert strategy.next_version() == "v002"


def test_record_adopted_updates_active(tmp_roster):
    strategy.record(_entry("v001", "adopted"))
    roster = json.loads((tmp_roster / "roster.json").read_text())
    assert roster["active"] == "v001" and len(roster["history"]) == 1


def test_record_rolled_back_keeps_active(tmp_roster):
    strategy.record(_entry("v001", "rolled_back"))
    roster = json.loads((tmp_roster / "roster.json").read_text())
    assert roster["active"] == "v000" and roster["history"][0]["decision"] == "rolled_back"
