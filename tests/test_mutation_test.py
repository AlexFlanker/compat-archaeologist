import json
from pathlib import Path

import pytest
from pymongo import MongoClient

from archaeologist import mutation_test
from archaeologist.config import env


def _r(probe_id, feature_id, verdict):
    return {"probe_id": probe_id, "feature_id": feature_id, "verdict": verdict}


CATALOG = [
    {"id": "m1", "split": "train", "feature_ids": ["F4"], "description": "d1"},
    {"id": "m2", "split": "heldout", "feature_ids": ["F8"], "description": "d2"},
]


def test_caught_requires_every_repeat():
    repeats = [
        {"zero": [], "m1": [_r("p", "F4", "result_diff")], "m2": []},
        {"zero": [], "m1": [_r("p", "F4", "equal")], "m2": []},
    ]
    mutant = mutation_test.summarize(CATALOG, repeats)["mutants"][0]
    assert mutant["caught"] is False and mutant["flaky"] is True
    assert mutant["caught_each_repeat"] == [True, False]
    assert mutant["caught_by"] == ["p"]


def test_probe_must_match_feature_to_count():
    repeats = [{"zero": [], "m1": [_r("other", "F9", "result_diff")], "m2": []}]
    mutant = mutation_test.summarize(CATALOG, repeats)["mutants"][0]
    assert mutant["caught"] is False and mutant["caught_by"] == [] and mutant["no_probe_for_features"] is True


def test_false_flips_exclude_probe_invalid():
    repeats = [
        {"zero": [_r("a", "F1", "equal"), _r("b", "F2", "probe_invalid"), _r("c", "F3", "result_diff")], "m1": [], "m2": []},
        {"zero": [_r("a", "F1", "equal")], "m1": [], "m2": []},
    ]
    doc = mutation_test.summarize(CATALOG, repeats)
    assert doc["zero_mutation"] == {"false_flips_each_repeat": [1, 0], "false_flip_probes": ["c"], "invalid_each_repeat": [1, 0]}
    assert doc["summary"]["false_flips_max"] == 1 and doc["summary"]["invalid_probes_max"] == 1


def test_mutant_without_probes_is_reported_uncaught():
    doc = mutation_test.summarize([{"id": "x", "split": "heldout", "feature_ids": ["F99"], "description": ""}], [{"zero": [], "x": []}])
    assert doc["mutants"][0] == {
        "id": "x", "split": "heldout", "feature_ids": ["F99"], "description": "", "caught": False, "flaky": False,
        "no_probe_for_features": True, "caught_each_repeat": [False], "caught_by": [],
    }


def test_summary_splits_train_and_heldout():
    repeats = [{"zero": [], "m1": [_r("p", "F4", "result_diff")], "m2": [_r("q", "F8", "equal")]}]
    summary = mutation_test.summarize(CATALOG, repeats)["summary"]
    assert summary["planted"] == 2 and summary["caught"] == 1 and summary["flaky"] == 0
    assert summary["train"] == {"planted": 1, "caught": 1}
    assert summary["heldout"] == {"planted": 1, "caught": 0}


def test_unknown_mutation_id_is_rejected_before_running():
    with pytest.raises(ValueError, match="nope"):
        mutation_test.run("shoplog", [], repeats=1, mutation_ids=["abort_commits", "nope"])


def test_memo_section_states_heldout_numbers_and_mutants():
    from archaeologist import memo

    runs = {"replay": {"reads": {"unseen_only": {"shapes": 3, "replayed": 40, "equal": 38, "different": 2, "errors": 0}}}}
    doc = mutation_test.summarize(CATALOG, [{"zero": [], "m1": [_r("p", "F4", "result_diff")], "m2": []}])
    doc.update(run_id="R1", repeats=1)
    text = "\n".join(memo._verification_section(runs, {"created": "2026-11-09T08:30:31+00:00"}, doc))
    assert "## How we checked the checks" in text
    assert "sealed on 2026-11-09: the model saw the code; it never saw how these operations ran in production." in text
    assert "before the model wrote anything" not in text
    assert "40 recorded operations across 3 shapes: 38 equal, 2 different, 0 errors" in text
    assert "| m1 | train | yes | p |" in text and "| m2 | heldout | no | none |" in text


def test_memo_section_when_nothing_was_run():
    from archaeologist import memo

    text = "\n".join(memo._verification_section({"replay": {"reads": {}}}, None, None))
    assert "held-out slice was not sealed in this run" in text
    assert "planted-gap test was not run in this run (`--with-mutation-test`)" in text


@pytest.mark.integration
def test_run_catches_abort_commits_and_has_no_false_flips(tmp_path, monkeypatch):
    client = MongoClient(env("SOURCE_URI", "mongodb://127.0.0.1:27017/?directConnection=true"), serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB not reachable")
    monkeypatch.setattr(mutation_test, "OUT_DIR", tmp_path)
    fixture = json.loads((Path(__file__).parent / "fixtures" / "handwritten_probes.json").read_text())
    probes = [p for p in fixture if p["probe_id"] == "T.txn_abort"]
    doc = mutation_test.run("shoplog", probes, repeats=1, mutation_ids=["abort_commits"])
    assert doc["mutants"][0]["caught"] is True
    assert doc["zero_mutation"]["false_flips_each_repeat"] == [0]
    assert (tmp_path / "shoplog" / "mutation_test.json").exists()
