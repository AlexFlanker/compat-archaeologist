"""history.jsonl records runs against the current target only; an upgrade run's older target must not appear as a flip."""

import json

import pytest

from archaeologist import diff


@pytest.fixture
def offline_diff(tmp_path, monkeypatch):
    """diff.run with every engine call replaced: one probe, an empty workload."""
    monkeypatch.setattr(diff, "OUT_DIR", tmp_path)
    monkeypatch.setattr(diff, "source_client", lambda timeout_ms=0: object())
    monkeypatch.setattr(diff, "target_client", lambda timeout_ms=0: object())
    monkeypatch.setattr(diff, "server_info", lambda client: {})
    monkeypatch.setattr(diff, "diff_probe", lambda source, target, probe: {"probe_id": probe["probe_id"], "feature_id": probe["feature_id"], "verdict": "equal"})
    monkeypatch.setattr(diff, "load_log", lambda path: [])
    monkeypatch.setattr(diff, "query_log_path", lambda profile: tmp_path / "log")
    monkeypatch.setattr(diff, "partition", lambda entries: {"reads": [], "writes": [], "excluded": {}})
    monkeypatch.setattr(diff.heldout, "load_or_create", lambda profile: {"heldout_shape_ids": []})
    monkeypatch.setattr(diff, "replay_reads", lambda *a: {"shapes": [], "overall": {}})
    monkeypatch.setattr(diff, "seed_database", lambda *a: None)
    monkeypatch.setattr(diff, "replay_writes", lambda *a: {})
    return tmp_path / "shoplog" / "history.jsonl"


PROBES = [{"probe_id": "F4.a", "feature_id": "F4", "strategy_version": "v005"}]


def test_plain_run_appends_history(offline_diff):
    diff.run("shoplog", PROBES)
    (line,) = offline_diff.read_text().splitlines()
    assert json.loads(line)["verdicts"] == {"F4.a": "equal"} and json.loads(line)["strategy_version"] == "v005"


def test_named_output_run_does_not_append_history(offline_diff):
    diff.run("shoplog", PROBES, output_name="ferretdb-2.2.0")
    assert not offline_diff.exists()
    assert (offline_diff.parent / "runs_ferretdb-2.2.0.json").exists()
