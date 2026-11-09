"""timing.json is written whether the pipeline finishes or fails, and says which."""

import json
import sys

import pytest

from archaeologist import pipeline


@pytest.fixture
def timing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "OUT_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["pipeline", "--profile", "lite"])
    return tmp_path / "lite" / "timing.json"


def test_finished_run_records_ok(timing, monkeypatch):
    monkeypatch.setattr(pipeline, "_run", lambda arguments, started: 0)
    assert pipeline.main() == 0
    doc = json.loads(timing.read_text())
    assert doc["status"] == "ok" and isinstance(doc["seconds"], int) and doc["finished_at"].endswith("+00:00")


def test_failed_run_records_failed_and_reraises(timing, monkeypatch):
    def boom(arguments, started):
        raise RuntimeError("engine went away")

    monkeypatch.setattr(pipeline, "_run", boom)
    with pytest.raises(RuntimeError, match="engine went away"):
        pipeline.main()
    assert json.loads(timing.read_text())["status"] == "failed"
