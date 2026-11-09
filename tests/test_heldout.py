import json
import math
import shutil

import pytest

from archaeologist import heldout, meta_client, replay
from archaeologist.config import EVIDENCE_DIR
from archaeologist.extract_querylog import clean_command, load_log


@pytest.fixture(scope="module")
def summary():
    return json.loads((EVIDENCE_DIR / "shoplog_querylog_summary.json").read_text())


@pytest.fixture(scope="module")
def entries():
    return load_log(EVIDENCE_DIR / "query_log.jsonl")


@pytest.fixture(scope="module")
def doc(summary, entries):
    return heldout.split(summary, entries)


@pytest.fixture(autouse=True)
def _reset_guard():
    yield
    meta_client.LEAK_GUARD = None


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    shutil.copy(EVIDENCE_DIR / "shoplog_querylog_summary.json", evidence)
    monkeypatch.setattr(heldout, "EVIDENCE_DIR", evidence)
    monkeypatch.setattr(heldout, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(heldout, "query_log_path", lambda profile: EVIDENCE_DIR / "query_log.jsonl")
    return tmp_path


def test_split_is_deterministic(summary, entries):
    first, second = heldout.split(summary, entries), heldout.split(summary, entries)
    assert {k: v for k, v in first.items() if k != "created"} == {k: v for k, v in second.items() if k != "created"}


def test_split_holds_out_thirty_percent_of_replayable_reads(doc):
    assert len(doc["heldout_shape_ids"]) == math.ceil(0.3 * len(doc["population_shape_ids"]))
    assert set(doc["heldout_shape_ids"]).isdisjoint(doc["train_shape_ids"])
    assert set(doc["heldout_shape_ids"]) <= set(doc["population_shape_ids"])


def test_every_feature_keeps_a_train_shape(summary, doc):
    for feature in {f for s in summary["shapes"] for f in s["features"]}:
        assert any(feature in s["features"] for s in summary["shapes"] if s["id"] in doc["train_shape_ids"])


def test_load_or_create_writes_then_reuses(workspace):
    created = heldout.load_or_create("shoplog")
    assert (workspace / "out" / "shoplog" / "heldout.json").exists()
    assert heldout.load_or_create("shoplog") == created


def test_changed_seed_raises(workspace):
    created = heldout.load_or_create("shoplog")
    path = workspace / "out" / "shoplog" / "heldout.json"
    path.write_text(json.dumps({**created, "seed": 1}))
    with pytest.raises(heldout.HeldOutMismatch):
        heldout.load_or_create("shoplog")


def test_stale_heldout_file_raises(workspace):
    created = heldout.load_or_create("shoplog")
    path = workspace / "out" / "shoplog" / "heldout.json"
    path.write_text(json.dumps({**created, "population_shape_ids": created["population_shape_ids"] + ["Sdeadbeef"]}))
    with pytest.raises(heldout.StaleHeldOut, match="Sdeadbeef"):
        heldout.load_or_create("shoplog")


def test_guard_fires_on_indented_json(doc, entries):
    guard = heldout.guard_for(doc, entries)
    text = json.dumps([{"command": clean_command(e)} for e in entries if heldout.is_heldout(e, doc)][:1], indent=1, default=str)
    assert guard(text) is not None


def test_guard_ignores_train_entries(doc, entries):
    guard = heldout.guard_for(doc, entries)
    train = [e for e in entries if not heldout.is_heldout(e, doc)]
    assert train
    text = json.dumps([{"command": clean_command(e)} for e in train[:5]], indent=1, default=str)
    assert guard(text) is None


def test_guard_blocks_extra_forbidden_strings(doc, entries):
    guard = heldout.guard_for(doc, entries, extra_forbidden=["secret  phrase"])
    assert guard("see the secret\nphrase here") is not None


def test_install_guard_sets_meta_client_hook(doc, entries):
    heldout.install_guard(doc, entries)
    assert callable(meta_client.LEAK_GUARD)


def test_structured_raises_before_network(monkeypatch):
    monkeypatch.setattr(meta_client, "LEAK_GUARD", lambda text: "shape S1")
    monkeypatch.setattr(meta_client, "_record", lambda entry: None)
    with pytest.raises(meta_client.PromptBlocked):
        meta_client.structured(purpose="t", system="", user="x", schema={}, schema_name="t")


def test_unseen_rollup_counts_only_heldout_shapes():
    shapes = [
        {"heldout": True, "replayed": 4, "equal": 3, "different": 1, "target_errors": 0},
        {"heldout": False, "replayed": 9, "equal": 9, "different": 0, "target_errors": 0},
    ]
    assert replay.unseen_rollup(shapes) == {"shapes": 1, "replayed": 4, "equal": 3, "different": 1, "errors": 0}
