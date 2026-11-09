"""The evidence site renders what the pipeline wrote and reruns one probe on demand."""

from __future__ import annotations

import threading
import time

import pytest

from archaeologist.config import OUT_DIR
from archaeologist.evidence_site import build, pages, serve

PROFILE = "shoplog"


@pytest.fixture(scope="module")
def ctx():
    if not (OUT_DIR / PROFILE / "runs.json").exists():
        pytest.skip(f"run the pipeline first: out/{PROFILE}/runs.json is missing")
    return build.load_context(PROFILE)


def _shape(**kw):
    base = {"features": ["F1"], "different": 0, "target_errors": 0, "ranking_only": False}
    base.update(kw)
    return base


def _features(verdict="equal", probes=True):
    return {"F1": {"verdict": verdict, "probes": [{"probe_id": "p", "verdict": verdict}] if probes else []}}


def _wait(challenges, job):
    for _ in range(200):
        state = challenges.status(job)
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    return state


def test_shape_colour_red_on_probe_result_diff():
    assert pages.shape_colour(_shape(), _features("result_diff"), []) == "red"
    assert pages.shape_colour(_shape(different=2), _features(), []) == "red"
    assert pages.shape_colour(_shape(target_errors=1), _features(), []) == "red"
    assert pages.shape_colour(_shape(different=2, ranking_only=True), _features(), []) == "green"


def test_shape_colour_yellow_on_inferred_or_no_probe():
    items = [{"feature": "F1", "status": "INFERRED"}]
    assert pages.shape_colour(_shape(), _features(), items) == "yellow"
    assert pages.shape_colour(_shape(), _features(probes=False), []) == "yellow"


def test_shape_colour_green_otherwise():
    items = [{"feature": "F1", "status": "OBSERVED"}]
    assert pages.shape_colour(_shape(), _features(), items) == "green"


def test_render_all_has_one_finding_per_probe(ctx):
    rendered = build.render_all(PROFILE, live=False)
    assert sum(1 for k in rendered if k.startswith("findings/")) == len(ctx["runs"]["probes"])
    for name in ("index.html", "querylog.html", "planted.html", "strategy.html", "static.css"):
        assert name in rendered


def test_static_build_shows_cli_instead_of_button(ctx):
    rendered = build.render_all(PROFILE, live=False)
    page = next(v for k, v in rendered.items() if k.startswith("findings/"))
    assert "python -m archaeologist.probe_runner --probe" in page and "<button" not in page
    live = next(v for k, v in build.render_all(PROFILE, live=True).items() if k.startswith("findings/"))
    assert "<button" in live and "<script>" in live


def test_overview_tiles_cite_files(ctx):
    rendered = build.render_all(PROFILE, live=False)
    assert "out/shoplog/mutation_test.json" in rendered["index.html"]
    assert "out/shoplog/runs.json" in rendered["index.html"]


def test_missing_history_says_no_earlier_runs(ctx):
    probe_id = ctx["runs"]["probes"][0]["probe_id"]
    page = pages.render_finding({**ctx, "history": []}, probe_id, live=False)
    assert "No earlier runs recorded" in page


def test_finding_history_rows_come_from_history(ctx):
    probe_id = ctx["runs"]["probes"][0]["probe_id"]
    history = [{"run_id": "r-old", "strategy_version": "v000", "verdicts": {probe_id: "error"}}]
    page = pages.render_finding({**ctx, "history": history}, probe_id, live=False)
    assert "r-old" in page and "No earlier runs recorded" not in page


def test_load_context_tolerates_missing_files(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "out")
    empty = build.load_context("nothing")
    assert empty["runs"] is None and empty["history"] == [] and empty["strategy_versions"] == {}


def test_challenge_uses_own_namespaces(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve, "diff_probe", lambda s, t, p, namespaces: seen.update(ns=namespaces) or {"verdict": "equal"})
    monkeypatch.setattr(serve, "source_client", lambda: object())
    monkeypatch.setattr(serve, "target_client", lambda: object())
    monkeypatch.setattr(serve, "_load_probe", lambda profile, probe_id: {"probe_id": probe_id})
    challenges = serve.Challenges()
    job = challenges.start("T.txn_abort", serve.default_runner)
    assert _wait(challenges, job)["status"] == "done"
    assert seen["ns"] == ("chs", "cht")


def test_second_start_for_running_probe_returns_same_job():
    challenges = serve.Challenges()
    gate = threading.Event()
    first = challenges.start("p", lambda pid: gate.wait(2) and {"verdict": "equal"})
    assert challenges.start("p", lambda pid: {"verdict": "other"}) == first
    assert challenges.start("q", lambda pid: {"verdict": "equal"}) != first
    gate.set()
    assert _wait(challenges, first)["status"] == "done"
    assert challenges.start("p", lambda pid: {"verdict": "equal"}) != first


def test_live_finding_has_elements_the_poller_updates(ctx):
    probe_id = ctx["runs"]["probes"][0]["probe_id"]
    page = pages.render_finding(ctx, probe_id, live=True)
    for element in ('id="verdict"', 'id="difference"', 'id="compare-body"', 'id="challenge-status"'):
        assert element in page
    assert ".catch(" in page and "className" in page


def test_challenge_status_transitions():
    challenges = serve.Challenges()
    gate = threading.Event()
    job = challenges.start("p", lambda pid: gate.wait(2) and {"verdict": "equal"})
    assert challenges.status(job) == {"status": "running"}
    gate.set()
    assert _wait(challenges, job) == {"status": "done", "result": {"verdict": "equal"}}
    assert challenges.status("nope") == {"status": "unknown"}
    assert _wait(challenges, challenges.start("p", lambda pid: 1 / 0))["status"] == "error"


def test_overview_tiles_sum_shapes_and_show_unseen_only():
    runs = {
        "run_id": "R", "probes": [{"probe_id": "F1.a", "feature_id": "F1", "verdict": "equal"}],
        "replay": {"reads": {
            "shapes": [{"replayed": 10, "equal": 9, "different": 1, "target_errors": 0},
                       {"replayed": 5, "equal": 5, "different": 0, "target_errors": 2}],
            "unseen_only": {"shapes": 2, "replayed": 7, "equal": 6, "different": 1, "errors": 0},
        }},
    }
    html = pages.render_overview({"profile": "shoplog", "runs": runs, "mutation": None, "roster": None})
    assert "Recorded reads replayed" in html
    assert "14 equal, 1 different, 2 target errors" in html
    assert "Replayed on operations the model never saw" in html
    assert "6 equal, 1 different, 0 errors" in html and "replay.reads.unseen_only" in html
