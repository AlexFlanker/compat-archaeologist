import json

import pytest

from archaeologist import heldout, improve, meta_client, mutants, mutation_test, probes, strategy


def c(h, t, ff, inv):
    return {"heldout_catch": h, "train_catch": t, "false_flips_max": ff, "invalid_probes": inv}


def test_decide_adopts_on_heldout_gain():
    assert improve.decide(c(3, 4, 0, 0), c(2, 4, 0, 0))[0] == "adopted"


def test_decide_adopts_on_tie_with_train_gain():
    assert improve.decide(c(2, 5, 0, 0), c(2, 4, 0, 0))[0] == "adopted"


def test_decide_rejects_more_false_flips():
    decision, reason = improve.decide(c(4, 4, 1, 0), c(2, 4, 0, 0))
    assert decision == "rolled_back" and reason == "false_flips_max rose 0 -> 1"


def test_decide_rejects_two_more_invalid_probes():
    decision, reason = improve.decide(c(4, 4, 0, 2), c(2, 4, 0, 0))
    assert decision == "rolled_back" and reason == "invalid_probes 0 -> 2 exceeds +1"


def test_decide_allows_one_more_invalid_probe():
    assert improve.decide(c(4, 4, 0, 1), c(2, 4, 0, 0))[0] == "adopted"


def test_decide_rejects_no_gain():
    decision, reason = improve.decide(c(2, 4, 0, 0), c(2, 4, 0, 0))
    assert decision == "rolled_back" and reason == "no held-out or train gain"


def test_decide_names_heldout_regression():
    assert improve.decide(c(1, 6, 0, 0), c(2, 4, 0, 0))[1] == "heldout_catch 2 -> 1"


# ---------- loop with fakes at the Muse / engine boundary ----------

CATALOG = [
    {"id": "m_train", "description": "TRAINDESC strips the thing", "feature_ids": ["F4"], "split": "train", "kind": "request"},
    {"id": "m_held", "description": "HELDDESC drops unmatched", "feature_ids": ["F1"], "split": "heldout", "kind": "request"},
]


def _mut_doc(version, train_caught, held_caught, flips=0, invalid=0):
    return {
        "strategy_version": version,
        "mutants": [
            {"id": "m_train", "split": "train", "feature_ids": ["F4"], "description": CATALOG[0]["description"], "caught": bool(train_caught)},
            {"id": "m_held", "split": "heldout", "feature_ids": ["F1"], "description": CATALOG[1]["description"], "caught": bool(held_caught)},
        ],
        "summary": {"train": {"planted": 1, "caught": train_caught}, "heldout": {"planted": 1, "caught": held_caught},
                    "false_flips_max": flips, "invalid_probes_max": invalid},
    }


def _probe(pid, feature, version="v000", status="valid", steps=None):
    return {"probe_id": pid, "feature_id": feature, "title": f"title {pid}", "behavior": "b", "status": status,
            "steps": steps or [{"op": "command", "label": pid}], "strategy_version": version}


@pytest.fixture
def env(tmp_path, monkeypatch):
    sdir = tmp_path / "strategy"
    sdir.mkdir()
    base = {"version": "v000", "parent": None, "created": "x", "system_prompt_additions": "", "per_feature_hints": {},
            "probe_count_overrides": {}, "required_checks": []}
    (sdir / "v000.json").write_text(json.dumps(base))
    (sdir / "roster.json").write_text(json.dumps({"active": "v000", "history": []}))
    monkeypatch.setattr(strategy, "STRATEGY_DIR", sdir)
    pdir = tmp_path / "probes"
    pdir.mkdir()
    odir = tmp_path / "out"
    (odir / "shoplog").mkdir(parents=True)
    monkeypatch.setattr(probes, "PROBES_DIR", pdir)
    monkeypatch.setattr(improve, "OUT_DIR", odir)
    (odir / "shoplog" / "ledger.json").write_text(json.dumps({"items": [{"id": "F1"}, {"id": "F4"}]}))
    active_probes = [_probe("F1.a", "F1"), _probe("F4.a", "F4"), _probe("F4.bad", "F4", status="invalid")]
    (pdir / "shoplog.json").write_text(json.dumps(active_probes))
    monkeypatch.setattr(mutants, "load_catalog", lambda: CATALOG)
    monkeypatch.setattr(heldout, "load_or_create", lambda profile: {"heldout_shape_ids": []})
    monkeypatch.setattr(improve, "load_log", lambda path: [])
    monkeypatch.setattr(improve, "query_log_path", lambda profile: tmp_path / "log")
    return tmp_path


def test_first_iteration_records_reference(env, monkeypatch):
    monkeypatch.setattr(mutation_test, "run", lambda profile, ps, repeats=3, mutation_ids=None: _mut_doc("v000", 0, 0))
    entry = improve.reference("shoplog", 2)
    history = strategy.roster()["history"]
    assert entry["decision"] == "reference" and history[0]["version"] == "v000"
    assert history[0]["train_catch"] == 0 and history[0]["invalid_probes"] == 1 and history[0]["probes"] == 2
    # a second call is a no-op
    monkeypatch.setattr(mutation_test, "run", lambda *a, **k: pytest.fail("must not re-measure"))
    assert improve.reference("shoplog", 2) == entry
    assert len(strategy.roster()["history"]) == 1


def _install_fakes(monkeypatch, candidate_doc, captured):
    docs = iter([_mut_doc("v000", 0, 0), candidate_doc])

    def run(profile, ps, repeats=3, mutation_ids=None):
        doc = next(docs)  # like the real runner, leave the document on disk
        (improve.OUT_DIR / profile / "mutation_test.json").write_text(json.dumps(doc))
        return doc

    monkeypatch.setattr(mutation_test, "run", run)

    def structured(**kwargs):
        captured.append(kwargs)
        return {"system_prompt_additions": "Add a $lookup with unmatched rows.", "per_feature_hints": {"F4": "cover it"},
                "probe_count_overrides": {}, "required_checks": []}

    monkeypatch.setattr(meta_client, "structured", structured)

    def generate(profile, ledger, strategy=None, only_features=None, base_probes=None, out_path=None):
        out = [p for p in base_probes if p["feature_id"] not in only_features]
        out += [_probe("F4.new", "F4", version=strategy["version"])]
        out_path.write_text(json.dumps(out))
        return out

    monkeypatch.setattr(probes, "generate", generate)
    ran = []
    monkeypatch.setattr(improve.diff, "run", lambda profile, ps, probe_origin="muse", **k: ran.append(ps) or {"run_id": "RUN1"})
    return ran


def test_iterate_adopts_and_runs_ferretdb(env, monkeypatch):
    captured = []
    ran = _install_fakes(monkeypatch, _mut_doc("v001", 1, 1), captured)
    entry = improve.iterate("shoplog", 2)
    assert entry["decision"] == "adopted" and entry["version"] == "v001" and entry["ferretdb_run_id"] == "RUN1"
    assert strategy.roster()["active"] == "v001"
    assert entry["generated_features"] == ["F4"]
    written = json.loads(probes.cache_path("shoplog").read_text())
    assert {p["probe_id"] for p in written} == {"F1.a", "F4.new"} and all(p["strategy_version"] == "v001" for p in written)
    assert [p["probe_id"] for p in ran[0]] == ["F1.a", "F4.new"]


def test_iterate_rolls_back_and_keeps_files(env, monkeypatch):
    captured = []
    before = probes.cache_path("shoplog").read_text()
    ran = _install_fakes(monkeypatch, _mut_doc("v001", 0, 0), captured)
    entry = improve.iterate("shoplog", 2)
    assert entry["decision"] == "rolled_back" and entry["ferretdb_run_id"] is None and not ran
    assert strategy.roster()["active"] == "v000"
    assert probes.cache_path("shoplog").read_text() == before
    assert json.loads((improve.OUT_DIR / "shoplog" / "mutation_test.json").read_text())["strategy_version"] == "v000"
    assert (env / "strategy" / "v001.json").exists() and (env / "probes" / "shoplog.candidate.json").exists()


def test_heldout_mutants_never_in_strategy_prompt(env, monkeypatch):
    captured = []
    _install_fakes(monkeypatch, _mut_doc("v001", 0, 0), captured)
    monkeypatch.setattr(meta_client, "LEAK_GUARD", None)
    improve.iterate("shoplog", 2)
    user = captured[0]["user"] + captured[0]["system"]
    assert captured[0]["purpose"] == "strategy_edit"
    assert "m_train" in user and "TRAINDESC" in user and "title F4.a" in user
    assert "m_held" not in user and "HELDDESC" not in user
    assert meta_client.LEAK_GUARD("see HELDDESC drops unmatched") and meta_client.LEAK_GUARD("m_held")
    monkeypatch.setattr(meta_client, "LEAK_GUARD", None)


# ---------- targeted regeneration in probes.generate ----------

def test_targeted_regeneration_keeps_other_probes(env, monkeypatch):
    monkeypatch.setattr(probes, "source_client", lambda **k: object())
    monkeypatch.setattr(heldout, "install_guard", lambda *a, **k: None)
    called = []

    def fake_item(item, source, strategy=None):
        called.append(item["id"])
        return [_probe(f"{item['id']}.new", item["id"])]

    monkeypatch.setattr(probes, "generate_for_item", fake_item)
    kept = _probe("F4.keep", "F4")
    base = [_probe("F1.old", "F1"), kept]
    out = probes.generate("shoplog", {"items": [{"id": "F1"}, {"id": "F4"}]}, strategy=strategy.active(),
                          only_features={"F1"}, base_probes=base, out_path=env / "cand.json")
    assert called == ["F1"]
    assert [p["probe_id"] for p in out] == ["F1.new", "F4.keep"] and out[1] is kept
    assert (env / "cand.json").exists()


def test_generate_installs_guard_before_muse(env, monkeypatch):
    order = []
    monkeypatch.setattr(probes, "source_client", lambda **k: object())
    monkeypatch.setattr(heldout, "install_guard", lambda doc, entries, extra_forbidden=(): order.append("guard"))
    monkeypatch.setattr(probes, "generate_for_item", lambda item, source, strategy=None: order.append("muse") or [])
    probes.generate("shoplog", {"items": [{"id": "F1"}]}, strategy=strategy.active())
    assert order == ["guard", "muse"]


def test_probe_stamps_hash_and_version(env, monkeypatch):
    item = {"id": "F4", "feature": "f"}
    raw = {"probes": [{"slug": "a", "title": "t", "behavior": "b", "steps": [{"op": "command", "label": "x", "command_ejson": "{}"}]}]}
    seen = []
    monkeypatch.setattr(meta_client, "structured", lambda **k: seen.append(k) or raw)
    monkeypatch.setattr(meta_client, "model_name", lambda: "m")
    monkeypatch.setattr(probes, "_validate_on_source", lambda probe, source: None)
    s = {**strategy.active(), "version": "v007", "per_feature_hints": {"F4": "HINT"}}
    (probe,) = probes.generate_for_item(item, object(), s)
    assert probe["strategy_version"] == "v007" and len(probe["probe_hash"]) == 8
    assert seen[0]["user"].endswith("Additional guidance for this feature: HINT")
