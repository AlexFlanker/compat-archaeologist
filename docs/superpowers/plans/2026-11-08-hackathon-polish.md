# Hackathon Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Compat Archaeologist a sealed held-out workload slice with a planted-gap mutation test, a versioned probe-strategy loop with an adopt/rollback roster, and a local evidence UI with a live Challenge rerun.

**Architecture:** Three new modules plug into the existing probe pipeline without changing the decision rule or the memo's structure. `heldout.py` seals query shapes and guards every Muse prompt; `mutants.py` wraps a real MongoDB client to inject one named incompatibility, and `mutation_test.py` measures which probes catch it; `strategy.py` + `improve.py` version the probe writer's instructions and adopt a version only on held-out evidence; `evidence_site/` renders static pages from committed JSON and serves a Challenge endpoint that reruns one probe on both engines.

**Tech Stack:** Python 3.11, pymongo, existing `archaeologist` package, stdlib `http.server`, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-08-hackathon-polish-design.md`

## Global Constraints

- No new third-party dependencies; `requirements.txt` unchanged.
- Settings are read through `archaeologist.config.env()`, never `os.environ` directly.
- Every number shown anywhere is computed from a committed JSON file under `out/`, `probes/` or `strategy/`.
- Held-out definition is option (a): the recorded workload (profiler entries and shape strings) for held-out shapes never enters any model prompt. Wording to reuse verbatim: "the model saw the code; it never saw how these operations ran in production."
- Adoption rule lives only in `archaeologist/improve.py:decide` and is never edited after seeing a candidate's result.
- A mutant may be uncaught by current probes but must be catchable by a rule-following probe.
- Mutants 1–6 are `train`, 7–10 are `heldout`; held-out mutant ids and descriptions never enter a model prompt.
- The mutation test runs behind `--with-mutation-test`; the default demo run is unchanged.
- Tests that need Docker engines are marked `@pytest.mark.integration` and skip when MongoDB is unreachable within 1 s.
- Run tests with `.venv/bin/python -m pytest`.

## Review Focus

1. The held-out file exists but the query log changed, so a sealed shape id no longer exists: `heldout.load_or_create` must raise with a message naming the stale ids, not silently split again. (Task 1 test `test_stale_heldout_file_raises`.)
2. A `$lookup` inside a `$facet` or `$unionWith` sub-pipeline: `lookup_drops_unmatched` must recurse into nested pipelines or the mutant is weaker than declared. (Task 2 test `test_lookup_mutation_recurses_into_facet`.)
3. A Challenge rerun while the pipeline is running: both would use `ca_tgt_<id>` database names and clobber each other. `diff_probe` gets a `namespaces` parameter and the Challenge path uses `("chs", "cht")`. (Task 5 test `test_challenge_uses_own_namespaces`.)
4. A mutant whose `feature_ids` have no valid probe at all: the mutation test must report `caught: false, no_probe_for_features: true`, not divide by zero or crash. (Task 3 test `test_mutant_without_probes_is_reported_uncaught`.)
5. First `improve.iterate` with no reference measurement for the active strategy: it must record `v000` as `reference` first and never compare a candidate against missing numbers. (Task 4 test `test_first_iteration_records_reference`.)

---

### Task 1: Sealed held-out slice and prompt guard

**Files:**
- Create: `archaeologist/heldout.py`
- Modify: `archaeologist/meta_client.py` (add `LEAK_GUARD`, `PromptBlocked`, check at top of `structured`)
- Modify: `archaeologist/ledger.py:147-158` (`_sample_operations` filters held-out entries), `archaeologist/ledger.py:314` (`run` loads held-out and installs the guard)
- Modify: `archaeologist/replay.py:106` (`replay_reads` takes `heldout_ids`, adds `heldout` per shape and `unseen_only` rollup)
- Modify: `archaeologist/diff.py:80` (`run` passes held-out ids)
- Test: `tests/test_heldout.py`

**Interfaces:**
- Produces: `heldout.split(summary: dict, entries: list[dict], seed: int = 20261108, fraction: float = 0.3) -> dict` returning `{"seed", "fraction", "population_shape_ids", "train_shape_ids", "heldout_shape_ids", "created"}`; `heldout.load_or_create(profile: str) -> dict` (path `OUT_DIR/<profile>/heldout.json`); `heldout.is_heldout(entry: dict, doc: dict) -> bool`; `heldout.guard_for(doc: dict, entries: list[dict], extra_forbidden: list[str] = ()) -> Callable[[str], str | None]`; `heldout.install_guard(doc, entries, extra_forbidden=())` sets `meta_client.LEAK_GUARD`; exceptions `HeldOutMismatch`, `StaleHeldOut`; `replay.unseen_rollup(shapes: list[dict]) -> dict` with keys `shapes, replayed, equal, different, errors`.
- Produces in `meta_client`: module attribute `LEAK_GUARD: Callable[[str], str | None] | None = None`; `class PromptBlocked(Exception)`; `structured()` calls the guard on `system + user` before anything else, records `{"status": "blocked_leak", "hit": hit[:80]}` and raises `PromptBlocked`.

Population rule: shapes whose entries `replay.partition(entries)["reads"]` contains; shape id via `replay._shape_id(shape_of(entry))`. Draw `ceil(fraction * len(population))` ids in `random.Random(seed)` shuffled order, skipping any id whose removal would leave a feature with zero train shapes. Text matching in the guard squashes whitespace on both sides (`"".join(text.split())`) and searches for each held-out shape string and each held-out entry's `json.dumps(clean_command(entry), sort_keys=True, default=str)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_heldout.py
def test_split_is_deterministic(summary, entries):
    assert heldout.split(summary, entries) == heldout.split(summary, entries)

def test_split_holds_out_thirty_percent_of_replayable_reads(summary, entries):
    doc = heldout.split(summary, entries)
    assert len(doc["heldout_shape_ids"]) == math.ceil(0.3 * len(doc["population_shape_ids"]))
    assert set(doc["heldout_shape_ids"]).isdisjoint(doc["train_shape_ids"])

def test_every_feature_keeps_a_train_shape(summary, entries):
    doc = heldout.split(summary, entries)
    for feature in {f for s in summary["shapes"] for f in s["features"]}:
        assert any(feature in s["features"] for s in summary["shapes"] if s["id"] in doc["train_shape_ids"])

def test_changed_seed_raises(tmp_path, monkeypatch): ...  # existing file seed 1 vs requested 20261108 -> HeldOutMismatch

def test_stale_heldout_file_raises(tmp_path, monkeypatch): ...  # file lists shape id not in current summary -> StaleHeldOut naming it

def test_guard_fires_on_indented_json(doc, entries):
    guard = heldout.guard_for(doc, entries)
    text = json.dumps([{"command": clean_command(e)} for e in entries if heldout.is_heldout(e, doc)][:1], indent=1, default=str)
    assert guard(text) is not None

def test_guard_ignores_train_entries(doc, entries): ...

def test_structured_raises_before_network(monkeypatch):
    meta_client.LEAK_GUARD = lambda text: "shape S1"
    with pytest.raises(meta_client.PromptBlocked):
        meta_client.structured(purpose="t", system="", user="x", schema={}, schema_name="t")

def test_unseen_rollup_counts_only_heldout_shapes():
    shapes = [{"heldout": True, "replayed": 4, "equal": 3, "different": 1, "target_errors": 0}, {"heldout": False, "replayed": 9, "equal": 9, "different": 0, "target_errors": 0}]
    assert replay.unseen_rollup(shapes) == {"shapes": 1, "replayed": 4, "equal": 3, "different": 1, "errors": 0}
```
Fixtures `summary`/`entries` load `evidence/shoplog_querylog_summary.json` and `evidence/query_log.jsonl` via `extract_querylog.load_log`.

- [ ] **Step 2: Run tests, expect ImportError / AttributeError**

Run: `.venv/bin/python -m pytest tests/test_heldout.py -v`

- [ ] **Step 3: Implement `archaeologist/heldout.py` with the signatures above**

- [ ] **Step 4: Add `LEAK_GUARD`, `PromptBlocked` and the pre-flight check to `meta_client.structured`**

- [ ] **Step 5: Thread held-out through `ledger._sample_operations(entries, doc)`, `ledger.run` (load + `install_guard` before any Muse call), `replay.replay_reads(..., heldout_ids=None)` and `diff.run`**

`replay_reads` adds `"heldout": shape_id in heldout_ids` to each shape dict and `"unseen_only": unseen_rollup(shapes)` beside `"overall"`.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass; `tests/test_differ.py` unchanged.

- [ ] **Step 7: Commit**

```bash
git add archaeologist/heldout.py archaeologist/meta_client.py archaeologist/ledger.py archaeologist/replay.py archaeologist/diff.py tests/test_heldout.py
git commit -m "Seal a held-out slice of the recorded workload and block it from every Muse prompt"
```

---

### Task 2: Mutation proxy

**Files:**
- Create: `archaeologist/mutants.py`, `mutants/catalog.json`
- Modify: `archaeologist/probe_runner.py:222` (`diff_probe(source, target, probe, namespaces=("src", "tgt"))`)
- Test: `tests/test_mutants.py`

**Interfaces:**
- Produces: `mutants.load_catalog() -> list[dict]` (each `{"id", "description", "feature_ids", "split", "kind"}`); `mutants.mutate_command(mutation: str | None, command: dict) -> dict` (pure; returns a deep copy); `mutants.mutate_reply(mutation: str | None, command: dict, reply: dict) -> dict` (pure); `class MutantClient(inner: MongoClient, mutation: str | None)` with `drop_database`, `__getitem__`, `get_database`, `start_session`, `__getattr__` delegation; `MutantDatabase.with_options(**kw)`, `.command(command, session=None, **kw)`, `.__getitem__`; `MutantCollection.watch(...)`; `MutantSession.start_transaction/commit_transaction/abort_transaction/end_session`, property `in_transaction`.
- Consumes: nothing new.

Catalog ids and kinds, in order: `abort_commits` (session), `txn_writes_visible` (session: write commands sent without the session), `collation_ignored` (request), `validator_dropped` (request), `unique_not_enforced` (request), `ttl_never_expires` (request), `change_streams_unsupported` (watch and `$changeStream` aggregate raise `OperationFailure("not supported", code=115)`), `lookup_drops_unmatched` (request: after every `{"$lookup": {"as": X}}` insert `{"$match": {X: {"$ne": []}}}`, recursing into `$facet`, `$lookup.pipeline`, `$unionWith.pipeline`), `facet_single_bucket` (reply: in `cursor.firstBatch` docs of a `$facet` aggregate keep the first key's list, set others to `[]`), `textscore_sort_dropped` (request: remove sort keys whose value is `{"$meta": "textScore"}` from `find.sort` and every `$sort` stage; drop an emptied sort). `feature_ids`: 1–2 → F4; 3 → F6; 4 → F8; 5 → F7; 6 → F7; 7 → F5; 8 → F1; 9 → F1; 10 → F9. Split: 1–6 `train`, 7–10 `heldout`.

- [ ] **Step 1: Write failing unit tests (pure functions and fakes)**

```python
def test_catalog_has_ten_entries_six_train_four_heldout(): ...
def test_collation_stripped_from_create_indexes_and_find(): ...
def test_validator_dropped_from_create_and_collmod(): ...
def test_unique_and_ttl_stripped_from_index_specs(): ...
def test_lookup_mutation_inserts_match_after_lookup():
    out = mutants.mutate_command("lookup_drops_unmatched", {"aggregate": "o", "pipeline": [{"$lookup": {"from": "c", "as": "cust", "pipeline": []}}, {"$unwind": "$cust"}], "cursor": {}})
    assert out["pipeline"][1] == {"$match": {"cust": {"$ne": []}}}
def test_lookup_mutation_recurses_into_facet(): ...  # $facet: {a: [{$lookup ...}]} gets the $match inside the sub-pipeline
def test_textscore_sort_dropped_removes_meta_sort_and_empties(): ...
def test_facet_single_bucket_keeps_first_key_only(): ...
def test_no_mutation_is_identity():
    for cmd in SAMPLE_COMMANDS:
        assert mutants.mutate_command(None, cmd) == cmd
def test_abort_commits_calls_inner_commit(fake_session): ...
def test_txn_writes_visible_drops_session_on_writes(fake_db): ...
def test_change_stream_watch_raises_115(fake_collection): ...
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/bin/python -m pytest tests/test_mutants.py -v`

- [ ] **Step 3: Implement `archaeologist/mutants.py` and `mutants/catalog.json`**

- [ ] **Step 4: Add `namespaces` parameter to `probe_runner.diff_probe` and pass through to both `run_probe` calls**

- [ ] **Step 5: Integration test, skipped without MongoDB**

```python
@pytest.mark.integration
def test_zero_mutation_proxy_matches_mongodb_on_handwritten_probes():
    source = source_client(timeout_ms=5000); _require(source)
    target = mutants.MutantClient(source_client(timeout_ms=5000), None)
    for probe in json.load(open("tests/fixtures/handwritten_probes.json")):
        assert diff_probe(source, target, probe, namespaces=("mt0s", "mt0t"))["verdict"] in {"equal", "probe_invalid"}

@pytest.mark.integration
def test_abort_commits_is_caught_by_txn_abort_fixture(): ...  # T.txn_abort verdict == "result_diff"
```
`_require` pings with `serverSelectionTimeoutMS=1000` and `pytest.skip`s on failure. Register the marker in `tests/conftest.py` via `pytest_configure`.

- [ ] **Step 6: Run unit tests; run integration tests if Docker is up**

Run: `.venv/bin/python -m pytest tests/test_mutants.py -v` and `docker compose up -d --wait && .venv/bin/python -m pytest -m integration -v`

- [ ] **Step 7: Commit**

```bash
git add archaeologist/mutants.py mutants/catalog.json archaeologist/probe_runner.py tests/test_mutants.py tests/conftest.py
git commit -m "Mutation proxy: ten named incompatibilities injected in front of MongoDB"
```

---

### Task 3: Mutation test runner and pipeline flag

**Files:**
- Create: `archaeologist/mutation_test.py`
- Modify: `archaeologist/pipeline.py:140` (flag `--with-mutation-test`, step `4g`), `archaeologist/memo.py:479` (`write` gains `mutation=None`, `heldout=None`; new `_verification_section`)
- Test: `tests/test_mutation_test.py`

**Interfaces:**
- Produces: `mutation_test.run(profile: str, probes: list[dict], repeats: int = 3, mutation_ids: list[str] | None = None) -> dict` writing `OUT_DIR/<profile>/mutation_test.json`; `mutation_test.summarize(catalog: list[dict], per_repeat: list[dict]) -> dict` (pure). `per_repeat[i]` is `{"zero": [probe_result...], "<mutant_id>": [probe_result...]}` where each probe result has `probe_id, feature_id, verdict`.
- Output document: `{"profile", "run_id", "at", "repeats", "probe_count", "strategy_version", "mutants": [{"id", "split", "feature_ids", "description", "caught", "flaky", "no_probe_for_features", "caught_each_repeat", "caught_by"}], "zero_mutation": {"false_flips_each_repeat", "false_flip_probes", "invalid_each_repeat"}, "summary": {"planted", "caught", "flaky", "train": {"planted", "caught"}, "heldout": {"planted", "caught"}, "false_flips_max", "invalid_probes_max"}}`.

Rules: a probe catches a mutant in one repeat iff `probe["feature_id"] in mutant["feature_ids"]` and `verdict not in {"equal", "probe_invalid"}`; `caught` = caught in every repeat; `flaky` = caught in some but not all; `false_flips` per repeat = zero-mutation probes with verdict not in `{"equal", "probe_invalid"}`; `invalid` = `probe_invalid` count. Mutants run sequentially; probes within a mutant in parallel with `PROBE_WORKERS` from `diff.py`. `strategy_version` = `probes[0].get("strategy_version", "v000")`.

- [ ] **Step 1: Write failing tests for `summarize`**

```python
def test_caught_requires_every_repeat(): ...        # [True, False] -> caught False, flaky True
def test_false_flips_exclude_probe_invalid(): ...
def test_mutant_without_probes_is_reported_uncaught():
    doc = mutation_test.summarize([{"id": "x", "split": "heldout", "feature_ids": ["F99"], "description": ""}], [{"zero": [], "x": []}])
    assert doc["mutants"][0] == {..., "caught": False, "flaky": False, "no_probe_for_features": True, "caught_each_repeat": [False], "caught_by": []}
def test_summary_splits_train_and_heldout(): ...
```

- [ ] **Step 2: Run, expect failure**

Run: `.venv/bin/python -m pytest tests/test_mutation_test.py -v`

- [ ] **Step 3: Implement `summarize` and `run`; `run` uses `MutantClient(source_client(), mutation)` as target and `diff_probe(..., namespaces=("mts", "mtt"))`**

- [ ] **Step 4: Wire `--with-mutation-test` into `pipeline.main` after step 4f, printing `planted/caught/false_flips_max`; pass `mutation` and `heldout` docs to `memo.write`**

`_verification_section` renders, under a heading "How we checked the checks": held-out sentence (Global Constraints wording) with unseen-only numbers from `runs["replay"]["reads"]["unseen_only"]`, and a table of mutants (id, split, caught, caught_by) plus `false_flips_max` and `repeats`. When `mutation` is None the section says the test was not run.

- [ ] **Step 5: Run tests; run the mutation test once for real if Docker is up**

Run: `.venv/bin/python -m pytest -q`; then `.venv/bin/python -m archaeologist.mutation_test --profile shoplog --repeats 1`
Expected: `out/shoplog/mutation_test.json` written; `abort_commits` and `validator_dropped` caught by existing probes.

- [ ] **Step 6: Commit**

```bash
git add archaeologist/mutation_test.py archaeologist/pipeline.py archaeologist/memo.py tests/test_mutation_test.py out/shoplog/mutation_test.json
git commit -m "Planted-gap mutation test with repeats, and a 'how we checked the checks' memo section"
```

---

### Task 4: Strategy versions and the improve loop

**Files:**
- Create: `archaeologist/strategy.py`, `archaeologist/improve.py`, `strategy/v000.json`, `strategy/roster.json`
- Modify: `archaeologist/probes.py:139` (`generate_for_item(item, source, strategy=None)`), `archaeologist/probes.py:217` (`generate(profile, ledger, strategy=None, only_features=None, base_probes=None, out_path=None)`), `archaeologist/diff.py:80` (append `history.jsonl`)
- Test: `tests/test_strategy.py`, `tests/test_improve.py`

**Interfaces:**
- `strategy.py`: `STRATEGY_DIR = ROOT / "strategy"`; `load(version: str) -> dict`; `active() -> dict` (reads `roster.json["active"]`); `next_version() -> str` (`v%03d`); `save(strategy: dict) -> Path`; `record(entry: dict) -> None` (appends to `roster.json["history"]`, sets `active` when `entry["decision"] == "adopted"`); `render_system(base: str, strategy: dict) -> str` (base + `"\n\n" + system_prompt_additions` when non-empty); `hint_for(strategy, feature_id) -> str`; `count_for(strategy, item) -> int` (override else `probes.probe_count_for`).
- Strategy JSON: `{"version", "parent", "created", "system_prompt_additions": "", "per_feature_hints": {}, "probe_count_overrides": {}, "required_checks": []}`. `v000` has empty additions, `parent: null`.
- Roster entry: `{"version", "parent", "decision": "adopted"|"rolled_back"|"reference", "train_catch": int, "heldout_catch": int, "false_flips_max": int, "invalid_probes": int, "probes": int, "generated_features": [..], "ferretdb_run_id": str|null, "at", "reason"}`.
- `probes.generate(...)`: when `only_features` is given, regenerate those features only and carry `base_probes` for the rest; every probe gets `probe_hash` (sha1 of `json.dumps(steps, sort_keys=True)`[:8]) and `strategy_version`; writes to `out_path or cache_path(profile)`.
- `improve.decide(candidate: dict, active: dict) -> tuple[str, str]` (pure): returns `("adopted", reason)` iff `candidate["heldout_catch"] > active["heldout_catch"]` or (`==` and `candidate["train_catch"] > active["train_catch"]`), and `candidate["false_flips_max"] <= active["false_flips_max"]`, and `candidate["invalid_probes"] <= active["invalid_probes"] + 1`; else `("rolled_back", reason)` naming the failed condition.
- `improve.iterate(profile: str, repeats: int = 2) -> dict` implementing spec §4.2 steps 1–6; `improve.reference(profile, repeats) -> dict` measures the active strategy and records a `reference` entry if the roster has none for it.
- `diff.run` appends to `OUT_DIR/<profile>/history.jsonl`: `{"run_id", "at", "strategy_version", "verdicts": {probe_id: verdict}}`.
- Muse call in `iterate`: `meta_client.structured(purpose="strategy_edit", system=STRATEGY_SYSTEM, user=..., schema=STRATEGY_SCHEMA, schema_name="strategy")`; the user prompt contains only train mutants' `id`/`description`/`feature_ids`, escaped status, titles and behaviours of current probes for those features, and oracle-gate failure messages. `install_guard` is called with `extra_forbidden` = held-out mutant ids and descriptions.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_strategy.py
def test_v000_renders_base_prompt_unchanged(): ...
def test_additions_appended_and_hint_and_count_override(): ...
def test_record_adopted_updates_active(tmp_roster): ...
def test_record_rolled_back_keeps_active(tmp_roster): ...

# tests/test_improve.py
def test_decide_adopts_on_heldout_gain(): assert improve.decide(c(h=3,t=4,ff=0,inv=0), a(h=2,t=4,ff=0,inv=0))[0] == "adopted"
def test_decide_adopts_on_tie_with_train_gain(): ...
def test_decide_rejects_more_false_flips(): ...
def test_decide_rejects_two_more_invalid_probes(): ...
def test_decide_rejects_no_gain(): ...
def test_first_iteration_records_reference(monkeypatch, tmp_path): ...  # roster empty -> history[0]["decision"] == "reference", version "v000"
def test_targeted_regeneration_keeps_other_probes(monkeypatch): ...      # fake writer; only F1 regenerated; F4 probes identical objects
def test_heldout_mutants_never_in_strategy_prompt(monkeypatch): ...     # capture the user prompt; assert no heldout id/description substring
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Implement `strategy.py`, `strategy/v000.json`, `strategy/roster.json` (`{"active": "v000", "history": []}`)**

- [ ] **Step 4: Extend `probes.generate_for_item` and `probes.generate` per the interfaces; add `history.jsonl` append to `diff.run`**

- [ ] **Step 5: Implement `improve.py` (`reference`, `iterate`, `decide`, CLI `python -m archaeologist.improve iterate --profile shoplog --repeats 2`)**

On adopt: write candidate probes to `probes/<profile>.json`, call `diff.run(profile, probes, probe_origin="muse")`, store its `run_id` in the roster entry. On roll back: leave `probes/<profile>.json` untouched; keep `strategy/vNNN.json` and `probes/<profile>.candidate.json` for the record.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest -q`

- [ ] **Step 7: Commit**

```bash
git add archaeologist/strategy.py archaeologist/improve.py archaeologist/probes.py archaeologist/diff.py strategy/ tests/test_strategy.py tests/test_improve.py
git commit -m "Versioned probe strategy with a held-out adoption rule and a committed roster"
```

---

### Task 5: Evidence site (static build and Challenge server)

**Files:**
- Create: `archaeologist/evidence_site/__init__.py`, `build.py`, `serve.py`, `pages.py`, `static.css`
- Test: `tests/test_evidence_site.py`

**Interfaces:**
- `build.load_context(profile: str) -> dict` reading `out/<profile>/{runs,ledger,heldout,mutation_test}.json`, `out/<profile>/history.jsonl`, `probes/<profile>.json`, `strategy/roster.json`, `strategy/v*.json`, `mutants/catalog.json`; missing files become `None`/empty.
- `pages.shape_colour(shape: dict, features: dict, items: list[dict]) -> str` returning `"red"` if (`shape["different"]` and not `shape.get("ranking_only")`) or `shape["target_errors"]` or any `features[f]["verdict"]` for `f in shape["features"]` is in `{"result_diff", "error", "unsupported", "timeout"}`; else `"yellow"` if any item for those features has `status == "INFERRED"` or `features[f]["probes"]` is empty; else `"green"`. `features` is `runs["features"]`, `items` is `ledger["items"]`.
- `pages.render_overview(ctx)`, `render_querylog(ctx)`, `render_finding(ctx, probe_id)`, `render_planted(ctx)`, `render_strategy(ctx)` → HTML strings; every tile in the overview shows the relative path of the file it came from. The query log page expands a row in place to show that shape's replay numbers (p50/p95 both engines, equal/different/errors, held-out badge). The strategy page renders the roster table and, below it, a unified diff (`difflib.unified_diff` of the pretty-printed JSON) between each version and its parent.
- `build.render_all(profile: str, live: bool) -> dict[str, str]` mapping `index.html`, `querylog.html`, `planted.html`, `strategy.html`, `findings/<probe_id>.html`, `static.css`; `build.write(profile, out_dir: Path, live: bool = False)`. With `live=False` the Challenge button is replaced by the text `python -m archaeologist.probe_runner --probe <probe_id>` (add that CLI to `probe_runner.__main__`).
- `serve.Challenges` with `start(probe_id: str, runner: Callable[[str], dict]) -> str` (job id; runs `runner` in a thread) and `status(job_id) -> dict` (`{"status": "running"|"done"|"error", "result"}`); default runner loads the probe and calls `diff_probe(source_client(), target_client(), probe, namespaces=("chs", "cht"))`.
- `serve.main()`: `python -m archaeologist.evidence_site.serve --profile shoplog --port 8765` serving `render_all(profile, live=True)` from memory, `POST /api/challenge/<probe_id>`, `GET /api/challenge/<job_id>`.

Finding page content: title, behaviour, steps table (op, command_ejson, expect_ejson), per-step MongoDB vs target captured values with `detail.difference.path` highlighted, verdict, status history rows from `history.jsonl` (run_id, strategy_version, verdict), mutants whose `caught_by` includes this probe, Challenge button that POSTs and polls every second, replacing the verdict and step table on `done`.

- [ ] **Step 1: Write failing tests**

```python
def test_shape_colour_red_on_probe_result_diff(): ...
def test_shape_colour_yellow_on_inferred_or_no_probe(): ...
def test_shape_colour_green_otherwise(): ...
def test_render_all_has_one_finding_per_probe(ctx_from_out):   # skip if out/shoplog/runs.json missing
    pages = build.render_all("shoplog", live=False)
    assert sum(1 for k in pages if k.startswith("findings/")) == len(ctx["runs"]["probes"])
def test_static_build_shows_cli_instead_of_button(): assert "python -m archaeologist.probe_runner --probe" in page and "<button" not in page
def test_overview_tiles_cite_files(): assert "out/shoplog/mutation_test.json" in pages["index.html"]
def test_challenge_uses_own_namespaces(monkeypatch):
    seen = {}; monkeypatch.setattr(serve, "diff_probe", lambda s, t, p, namespaces: seen.update(ns=namespaces) or {"verdict": "equal"})
    job = serve.Challenges().start("T.txn_abort", serve.default_runner); ...; assert seen["ns"] == ("chs", "cht")
def test_challenge_status_transitions(): ...  # running -> done with result
```

- [ ] **Step 2: Run, expect failures**

- [ ] **Step 3: Implement `pages.py` (colour rule and five renderers), `build.py`, `static.css`**

Plain HTML, light theme, one CSS file, no JavaScript except the Challenge poller on finding pages.

- [ ] **Step 4: Implement `serve.py` on `http.server.ThreadingHTTPServer`; add `--probe` CLI to `probe_runner.__main__`**

- [ ] **Step 5: Run tests; build the static site; open the server and press Challenge on one finding**

Run: `.venv/bin/python -m pytest -q && .venv/bin/python -m archaeologist.evidence_site.build --profile shoplog --out site/ && .venv/bin/python -m archaeologist.evidence_site.serve --profile shoplog --port 8765`
Expected: `site/index.html` exists; in a browser `http://localhost:8765/findings/F4.<id>.html` Challenge returns a verdict within 30 s.

- [ ] **Step 6: Commit (do not commit `site/`; add it to `.gitignore`)**

```bash
git add archaeologist/evidence_site tests/test_evidence_site.py archaeologist/probe_runner.py .gitignore
git commit -m "Evidence site: coloured query log, per-finding pages with status history, live Challenge rerun"
```

---

### Task 6: Full run, README results, and the roster artifact

**Files:**
- Modify: `README.md` ("What it found" gains a results table with a Source column; new section "How we checked the checks"; held-out wording verbatim), `docs/DEMO_GUIDE.md` (3-minute loop opens on the query log page)
- Commit: `out/shoplog/*.json`, `out/shoplog/memo.md`, `out/shoplog/history.jsonl`, `strategy/`, `probes/shoplog.json`

- [ ] **Step 1: Seal and measure the baseline**

Run: `docker compose up -d --wait && .venv/bin/python -m archaeologist.improve reference --profile shoplog --repeats 3`
Expected: `strategy/roster.json` has one `reference` entry for `v000` with `heldout_catch`, `train_catch`, `false_flips_max == 0`.

- [ ] **Step 2: Run up to three improvement rounds**

Run: `for i in 1 2 3; do .venv/bin/python -m archaeologist.improve iterate --profile shoplog --repeats 2; done`
Expected: each round appends a roster entry with `adopted` or `rolled_back` and a reason. Whatever the outcome, it is kept.

- [ ] **Step 3: Regenerate every surface from the active probe set**

Run: `./run_demo.sh --profile shoplog --with-self-check --with-mutation-test && ./run_demo.sh --profile lite`
Expected: memo has the "How we checked the checks" section; `out/shoplog/mutation_test.json` and `history.jsonl` updated; control app still `GO`.

- [ ] **Step 4: Update README and DEMO_GUIDE**

Results table rows: probes valid/invalid (`probes/shoplog.json`), planted caught N/10 with false flips and repeats (`out/shoplog/mutation_test.json`), unseen-only replay (`out/shoplog/runs.json`), strategy versions adopted/rolled back (`strategy/roster.json`), cutover clean/drill (existing files). Each row's Source column is the file path. Add the held-out sentence verbatim and a one-line honest note if no strategy version was adopted.

- [ ] **Step 5: Run the suite one last time and commit**

Run: `.venv/bin/python -m pytest -q`
```bash
git add README.md docs/DEMO_GUIDE.md out/shoplog out/lite strategy probes/shoplog.json
git commit -m "Baseline, improvement rounds and regenerated surfaces; README results table with sources"
```
