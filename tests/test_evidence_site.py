"""The evidence site renders what the pipeline wrote and reruns one probe on demand."""

from __future__ import annotations

import json
import socket
import sys
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
    assert empty["provenance"] is None


class _FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_challenge_uses_own_namespaces(monkeypatch):
    seen = {}
    monkeypatch.setattr(serve, "diff_probe", lambda s, t, p, namespaces: seen.update(ns=namespaces) or {"verdict": "equal"})
    monkeypatch.setattr(serve, "source_client", _FakeClient)
    monkeypatch.setattr(serve, "target_client", _FakeClient)
    monkeypatch.setattr(serve, "_load_probe", lambda profile, probe_id: {"probe_id": probe_id})
    challenges = serve.Challenges()
    job = challenges.start("T.txn_abort", serve.default_runner)
    assert _wait(challenges, job)["status"] == "done"
    assert seen["ns"] == ("chs", "cht")


def test_challenge_closes_both_clients_even_when_the_rerun_fails(monkeypatch):
    clients = [_FakeClient(), _FakeClient()]
    monkeypatch.setattr(serve, "source_client", lambda: clients[0])
    monkeypatch.setattr(serve, "target_client", lambda: clients[1])
    monkeypatch.setattr(serve, "_load_probe", lambda profile, probe_id: {"probe_id": probe_id})

    def boom(*args, **kwargs):
        raise RuntimeError("engine went away")

    monkeypatch.setattr(serve, "diff_probe", boom)
    with pytest.raises(RuntimeError):
        serve.default_runner("T.txn_abort")
    assert all(client.closed for client in clients)


def test_taken_port_exits_with_a_message(monkeypatch, capsys):
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = taken.getsockname()[1]
    monkeypatch.setattr(serve.build, "render_all", lambda profile, live: {})
    monkeypatch.setattr(sys, "argv", ["serve", "--port", str(port)])
    try:
        with pytest.raises(SystemExit) as stopped:
            serve.main()
    finally:
        taken.close()
    assert stopped.value.code == 1
    captured = capsys.readouterr()
    assert f"port {port} in use; pass --port" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


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
    clean = {"total": 30, "post_seal": 8, "checked_pre_seal": 22, "unchecked": 0}
    html = pages.render_overview({"profile": "shoplog", "runs": runs, "mutation": None, "roster": None, "provenance": clean})
    assert "Recorded reads replayed" in html
    assert "14 equal, 1 different, 2 target errors" in html
    assert "Replayed on operations the model never saw" in html
    assert "6 equal, 1 different, 0 errors" in html and "replay.reads.unseen_only" in html


def test_strategy_page_marks_unmeasured_rows():
    measured = {"version": "v004", "parent": "v000", "decision": "rolled_back", "train_catch": 6, "heldout_catch": 3,
                "false_flips_max": 0, "invalid_probes": 0, "probes": 30, "reason": "no gain", "measured": True}
    copied = {**measured, "version": "v006", "heldout_catch": 4, "reason": "generation failed: X", "measured": False}
    legacy = {key: value for key, value in measured.items() if key != "measured"}
    html = pages.render_strategy({"roster": {"active": "v000", "history": [measured, copied, legacy]}, "strategy_versions": {}})
    rows = html.split("<tr>")[2:5]
    assert "<td>3</td>" in rows[0] and "not measured" not in rows[0]
    assert rows[1].count("<td>not measured</td>") == 5 and "<td>4</td>" not in rows[1]
    assert "not measured" not in rows[2]


HOSTILE = "../../escape/x y"


def _hostile_ctx():
    result = {"probe_id": HOSTILE, "feature_id": "F1", "verdict": "result_diff", "source": {"steps": []}, "target": {"steps": []}}
    mutation = {"summary": {"caught": 1, "planted": 1, "flaky": 0, "train": {"caught": 1, "planted": 1}, "heldout": {"caught": 0, "planted": 0},
                            "false_flips_max": 0, "invalid_probes_max": 0},
                "mutants": [{"id": "m", "split": "train", "feature_ids": ["F1"], "description": "d", "caught": True, "flaky": False, "caught_by": [HOSTILE]}]}
    return {"profile": "shoplog", "runs": {"run_id": "R", "probes": [result], "replay": {"reads": {"shapes": []}}}, "ledger": None,
            "heldout": None, "provenance": None, "mutation": mutation, "history": [], "probes": [], "roster": None,
            "strategy_versions": {}, "catalog": []}


def test_finding_path_keeps_safe_characters_only():
    assert pages.finding_path("X:graphLookup.graph-lookup_1") == "findings/X:graphLookup.graph-lookup_1.html"
    assert pages.finding_path(HOSTILE) == "findings/.._.._escape_x_y.html"


def test_hostile_probe_id_cannot_escape_the_site(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "load_context", lambda profile: _hostile_ctx())
    out = tmp_path / "site"
    names = build.write("shoplog", out)
    assert "findings/.._.._escape_x_y.html" in names
    assert all((out / name).resolve().is_relative_to(out.resolve()) for name in names)
    assert not (tmp_path / "escape").exists()
    assert 'href="findings/.._.._escape_x_y.html"' in (out / "index.html").read_text()
    assert 'href="findings/.._.._escape_x_y.html"' in (out / "planted.html").read_text()


def test_challenge_endpoint_finds_a_sanitised_page(monkeypatch):
    from http.server import ThreadingHTTPServer
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    rendered = {pages.finding_path(HOSTILE): "<p>page</p>"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.make_handler(rendered, serve.Challenges(), lambda probe_id: {"verdict": "equal"}))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/challenge/{quote(HOSTILE, safe='')}"
        with urlopen(Request(url, method="POST"), timeout=5) as response:
            assert response.status == 200 and json.loads(response.read())["job_id"]
    finally:
        server.shutdown()
        server.server_close()


def _unseen_runs():
    return {"run_id": "R", "probes": [], "replay": {"reads": {"shapes": [], "unseen_only": {"shapes": 1, "replayed": 3, "equal": 3, "different": 0, "errors": 0}}}}


def test_overview_tile_makes_no_unseen_claim_when_probes_predate_the_seal():
    mixed = {"total": 2, "post_seal": 0, "checked_pre_seal": 0, "unchecked": 2}
    html = pages.render_overview({"profile": "lite", "runs": _unseen_runs(), "mutation": None, "roster": None, "provenance": mixed})
    assert "Replayed on the sealed slice (2 probes predate the seal, unchecked)" in html
    assert "never saw" not in html
    assert "3 equal, 0 different, 0 errors" in html


def test_overview_tile_makes_no_unseen_claim_without_provenance():
    html = pages.render_overview({"profile": "lite", "runs": _unseen_runs(), "mutation": None, "roster": None})
    assert "never saw" not in html and "Replayed on the sealed slice" in html


def test_load_context_computes_probe_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "ROOT", tmp_path)
    monkeypatch.setattr(build, "OUT_DIR", tmp_path / "out")
    (tmp_path / "out" / "p").mkdir(parents=True)
    (tmp_path / "probes").mkdir()
    (tmp_path / "out" / "p" / "heldout.json").write_text('{"created": "2026-11-09T08:30:31+00:00", "heldout_shape_ids": []}')
    (tmp_path / "probes" / "p.json").write_text(
        '[{"probe_id": "a", "generated_at": "2026-11-09T09:00:00+00:00", "prompt_guarded": true}, {"probe_id": "b"}]'
    )
    assert build.load_context("p")["provenance"] == {"total": 2, "post_seal": 1, "checked_pre_seal": 0, "unchecked": 1}
