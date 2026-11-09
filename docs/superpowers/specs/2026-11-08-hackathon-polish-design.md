# Design: three hard capabilities for the next hackathon

Date: 2026-11-08. Author: Tianjie Sun with Claude. Status: builder check done; awaiting team approval.

Language note: the codebase and its docs are English, so this document is too. The decision conversation happened in Chinese.

## 1. Intent and why

Compat Archaeologist did not place at the AIE Code Summit 2026 Hackathon. The gap analysis ([docs/HACKATHON_WINNERS_GAP_ANALYSIS.md](../../HACKATHON_WINNERS_GAP_ANALYSIS.md)) found that the verification engineering was competitive but three hard capabilities were missing, and every winner had them:

1. A way to prove the verifier itself works: a sealed held-out set and planted defects it must catch, with run-to-run noise reported.
2. A loop in which the system gets measurably better across runs, with adopt/rollback decisions logged in the repo.
3. A surface that exposes the evidence process to a judge in three minutes, not just a memo.

The next hackathon's sponsors are not known. So nothing here depends on a particular sponsor. The three capabilities must survive swapping Meta/SubQ for other models, and MongoDB/FerretDB stays the domain because it is built and works.

## 2. Goals, non-goals, anti-goals

Goals
- A judge can open one page, see per-finding evidence (what was predicted, what each engine answered, how the status changed over runs), press "Challenge" and watch a probe rerun on both engines live.
- The results table can truthfully say: "N planted incompatibilities, N/N caught, 0 false flips on the unmutated engine, over 3 repeated runs" and "held-out query shapes never shown to the model: X/Y equal, Z differ".
- A committed `strategy/roster.json` shows at least one probe-strategy version adopted or rolled back on held-out evidence, with the rule that decided it visible in code.
- Everything in `out/` stays regenerable by one command, as today.

Non-goals
- No change to the TCO model, the memo writer, or the decision rule in `archaeologist/tco.py`.
- No rewrite of the ASI:One cards; they may read new numbers (held-out, planted, roster) but their structure stays.
- No hosted deployment; the evidence UI runs locally and can export static HTML.
- No support for targets other than FerretDB and MongoDB-behind-a-mutation-proxy.

Anti-goals (what would make this a failure even if it works)
- A metrics dashboard as the main feature. The event rules ban that explicitly. The UI is an evidence browser with a live action; the pipeline and memo remain the product.
- Numbers that only live in screenshots. Every number on the UI and in the README must be computed from a committed JSON file.
- A self-improvement result that is tuned by hand after seeing the output. The adoption rule is code, runs before anyone looks, and a rejected version is reported as rejected.
- Held-out leakage. If a held-out shape appears in any model prompt, the whole "unseen" claim is void.

## 3. Constraints

- Python 3.11, pymongo, existing modules; no new web framework. Standard library `http.server` for the local UI server.
- Both engines run in Docker as today (`docker-compose.yml`); the mutation proxy is in-process Python and adds no containers.
- Meta Muse is the probe writer today via `archaeologist/meta_client.py`; the strategy loop must call the writer through that module only, so swapping the model is a one-file change.
- Probe runs are slow when they include `wait_ttl` (up to 72 s). Evaluation of a strategy version against all mutants may take minutes; this runs offline and its results are committed. The live demo never waits on it.
- Keep secrets hygiene: nothing new reads `.env` directly; use `archaeologist/config.env`.

## 4. The three parts

### 4.1 Sealed held-out slice and planted-gap mutation test

**Held-out split of query shapes.**
- Source of truth: `evidence/<profile>_querylog_summary.json["shapes"]` (29 shapes for ShopLog, each with `id`, `features`, `path`, `count`).
- A new module `archaeologist/heldout.py` splits shape ids deterministically (my call after the builder check): the population is the **replayable read shapes** only (the 14 ShopLog shapes that `replay.partition` routes to `replay_reads`; write shapes have no per-shape replay output today), seed `20261108`, 30% held-out rounded up, with the guarantee that **every feature keeps at least one train shape** (a feature with a single shape is never held out). Output `out/<profile>/heldout.json`: `{seed, population_shape_ids, train_shape_ids, heldout_shape_ids, created}`. The file is created once and never regenerated unless deleted; the pipeline refuses to run with a changed seed while the file exists. The memo reports unseen-only results as "N recorded operations across K shapes", since K will be about 4. Per-shape write replay is an optional extension left to the builder.
- **What "held-out" claims (decided by the team: option a).** Muse also reads the application source (`archaeologist/extract_code.py`), so the logic behind a held-out shape can reach the probe writer in prose. Held-out therefore means: the *recorded workload* for those shapes (the profiler entries and their shape strings) never enters any model prompt. The README, memo and UI say it in these words: "the model saw the code; it never saw how these operations ran in production. Unseen-only results are behavioural equivalence on recorded operations the model never read." Stripping code findings per feature (option b) was rejected because it would weaken the code-evidence feature; dropping the claim (option c) was rejected because the workload-level claim is true and useful.
- Enforcement, not convention: `archaeologist/ledger.py:_sample_operations` and `archaeologist/probes.py:_user_prompt` must filter out any query-log entry whose `shape_of(entry)` is held-out. `meta_client.structured` gets a guard: before sending, it checks the rendered prompt against every held-out shape's `shape` string and the literal commands of held-out entries; a hit raises `HeldOutLeak` and is logged to `logs/meta_calls.jsonl` with status `blocked_leak`. A unit test proves the guard fires.
- Reporting: `archaeologist/replay.replay_reads` already reports per shape. Add `heldout: true/false` per shape and an `unseen_only` rollup `{shapes, replayed, equal, different, errors}` next to `overall`. The memo and the UI show "unseen-only" beside the overall numbers.

**Mutation proxy ("mutant target").**
- New module `archaeologist/mutants.py`. A `MutantClient` wraps a real `MongoClient` to MongoDB itself and injects exactly one named incompatibility. Because the base engine is MongoDB, any difference the probes find is attributable to the mutation, and the zero-mutation proxy must be indistinguishable from MongoDB (that is the false-flip check).
- The proxy must support everything `probe_runner.run_probe` and `replay` touch: `drop_database`, `__getitem__` returning a database proxy with `with_options`, `command`, `__getitem__` returning a collection proxy with `watch`, `start_session` returning a session proxy with `start_transaction`, `commit_transaction`, `abort_transaction`, `in_transaction`, `end_session`. Anything else delegates via `__getattr__`.
- Mutations are declared in `mutants/catalog.json`, each with `id`, `description`, `feature_ids` it should be caught by, and a `kind` the proxy implements. Initial catalog (my call; builder may add):
  1. `abort_commits`: `abort_transaction` performs `commit_transaction` instead (the FerretDB finding, reproduced on purpose).
  2. `txn_writes_visible`: writes made with a session are executed without the session, so they are visible before commit.
  3. `collation_ignored`: strips `collation` from `createIndexes`, `find`, `aggregate`, `count`, `distinct` commands.
  4. `validator_dropped`: strips `validator`/`validationAction` from `create` and `collMod`.
  5. `unique_not_enforced`: strips `unique: true` from `createIndexes`.
  6. `ttl_never_expires`: strips `expireAfterSeconds` from `createIndexes`.
  7. `change_streams_unsupported`: `watch` raises `OperationFailure(code=115)`.
  8. `lookup_drops_unmatched` (request side): after every `$lookup` stage in an `aggregate` pipeline, inserts `{"$match": {<as>: {"$ne": []}}}`, so parent documents with no join matches disappear. Catchable by any probe whose seed data has an unmatched parent; the current F1 probes may not have one, which is exactly the kind of gap the strategy loop should close.
  9. `facet_single_bucket`: in `aggregate` replies with `$facet`, keeps only the first facet key's results and empties the others.
  10. `textscore_sort_dropped` (request side): removes any sort key whose value is `{"$meta": "textScore"}` from `find`/`aggregate`, so relevance ranking collapses to natural order. Reply-side alternatives were rejected at the builder check because rule-following probes never project textScore, which would have made the mutant uncatchable by construction.
- Rule for the catalog (my call): a mutant may be uncaught by the *current* probes, but it must be catchable by some probe that follows the writer's rules. An uncatchable mutant is a bug in the catalog, not a result.
- Split: mutants 1–6 are the **train** set (the strategy loop may read their descriptions and escaped results); 7–10 are **held-out mutants** (only the judge sees their results; their ids and descriptions never enter a model prompt).
- Runner: `archaeologist/mutation_test.py run --profile shoplog --repeats 3` executes the current probe set against each mutant and against the zero-mutation proxy, `repeats` times. For each mutant: `caught` = at least one probe whose `feature_ids` intersect the mutant's and whose verdict is not `equal` and not `probe_invalid`; `caught_by` lists them. For the zero-mutation proxy: `false_flips` = probes with verdict other than `equal` (expected 0). Also records `probe_invalid` count (oracle gate failures) per run. Output `out/<profile>/mutation_test.json` with per-repeat detail and a summary `{planted, caught, caught_each_repeat, false_flips_each_repeat, noise_note}`.
- Replay is not run against mutants (writes against the fixture are slow and the mutations target probe-visible behaviour). Builder may extend.

### 4.2 Self-improving probe strategy with adopt/rollback

- A **strategy** is the part of probe generation that a model may change: a JSON file `strategy/vNNN.json` with `{version, parent, created, system_prompt_additions: str, per_feature_hints: {F1: str, ...}, probe_count_overrides: {F10: 2, ...}, required_checks: [str]}`. `probes.generate_for_item` renders the system prompt as today plus `system_prompt_additions`, appends the feature's hint to the user prompt, and uses the count override when present. `v000` is the current behaviour (empty additions).
- `strategy/roster.json`: `{active: "v000", history: [{version, parent, decision: "adopted"|"rolled_back"|"reference", train: {...}, heldout: {...}, false_flips, invalid_probes, probes, at, reason}]}`. Committed to git after every evaluation, including rejections.
- `archaeologist/improve.py iterate --profile shoplog` does one round:
  1. Load the active strategy and the latest `mutation_test.json`. Collect **escaped train mutants** (not caught) and features whose probes were all `probe_invalid`.
  2. Ask the writer (Muse via `meta_client.structured`) for a strategy edit: input = current strategy, escaped train mutants' `description` and `feature_ids`, the probes that exist for those features (titles and behaviours, not held-out entries), and the oracle-gate failure messages. Output schema = the strategy JSON. Budget: one call, one retry on invalid JSON. Held-out mutant ids and held-out shapes are never in this prompt (guarded by the same `HeldOutLeak` check).
  3. Write `strategy/vNNN.json` as a candidate and regenerate probes **only for the features the edit targets** (the union of `per_feature_hints` keys and the escaped train mutants' `feature_ids`); every other feature keeps its active probes unchanged. The result is `probes/<profile>.candidate.json` (MongoDB oracle gate applies as today). My call after the builder check: regenerating everything would re-roll unrelated probes, change their ids and confound the strategy's effect with generation noise. Each probe gets a `probe_hash` (sha1 of its steps, first 8 hex) alongside the model-chosen `probe_id`; `history.jsonl` and the Finding page key on `probe_id` and show when a feature's probes were replaced by a strategy version. Generation noise is acknowledged as a limitation in the roster entry (`generated_features: [...]`), not measured.
  4. Evaluate: run `mutation_test` with the candidate probes (all 10 mutants + zero-mutation, `repeats` = 2 by default). Compute `train_catch`, `heldout_catch`, `false_flips_max`, `invalid_probes`.
  5. Adoption rule, in code and only here: adopt iff `heldout_catch > active.heldout_catch`, or (`heldout_catch == active.heldout_catch` and `train_catch > active.train_catch`), and `false_flips_max <= active.false_flips_max`, and `invalid_probes <= active.invalid_probes + 1`. Otherwise `rolled_back`: the candidate file stays in `strategy/` for the record, `probes/<profile>.json` is untouched, roster gets the entry with `reason`.
  6. On adopt: candidate probes replace `probes/<profile>.json`, roster `active` updates, and `iterate` immediately runs `diff.run` against FerretDB once (about two minutes), which rewrites `out/<profile>/runs.json` and appends to `history.jsonl` with the new strategy version. It does not rebuild the memo, TCO or cards; after the loop finishes, `./run_demo.sh` is run once so every committed surface reflects the same probe set. The roster entry records the FerretDB run id.
- The baseline entry: the first run of `improve.py` records `v000` as `reference` with its measured numbers, so the roster always has a comparison point.
- Repeat `iterate` up to 3 times for the demo artifact. If nothing is adopted, the README says so, as Airguard did.

### 4.3 Process-exposing evidence UI

- New package `archaeologist/evidence_site/`: `build.py` renders static HTML from `out/<profile>/*.json`, `probes/<profile>.json`, `strategy/roster.json`, `out/<profile>/history.jsonl`; `serve.py` serves the rendered pages plus a JSON endpoint `POST /api/challenge/<probe_id>` that runs `probe_runner.diff_probe` on the real source and target and returns the result; `GET /api/challenge/<job_id>` for polling. Standard library only (`http.server`, `json`, `threading`). Templates are Python f-strings or `string.Template`; one shared CSS file; no build step.
- Pages:
  1. **Overview**: headline tiles computed from files: probes valid/invalid, features by verdict, unseen-only replay result, planted caught N/N with repeats and false flips, active strategy version and roster length. Each tile links to the file that produced it (path shown). This page is deliberately small; it is not the main feature.
  2. **Query log**: one row per shape, coloured by evidence strength: green = replay equal and all probes for its features equal; red = any probe `result_diff`/`error`/`unsupported` or replay differences; yellow = feature INFERRED or no probe covers it; grey badge "held-out" on sealed shapes. Click opens the shape's replay detail (p50/p95 both engines, plans if present).
  3. **Finding** (one per probe): title and behaviour; the steps as a readable table (op, command, expected); MongoDB's answer vs the target's answer per step with the first difference highlighted; verdict; the status history from `history.jsonl` (verdict per run with run id, strategy version, timestamp); which mutants this probe caught; a **Challenge** button that calls the API and streams the new result into the page without reload.
  4. **Planted gaps**: the catalog with train/held-out labels, caught-by lists, per-repeat results.
  5. **Strategy**: the roster as a table (version, decision, train/held-out catch, false flips, reason) and a diff view between any two strategy files.
- Static export: `python -m archaeologist.evidence_site.build --profile shoplog --out site/` writes the same pages with the Challenge button disabled and replaced by the exact CLI command to rerun that probe. This is what can be hosted on GitHub Pages.
- `history.jsonl` is appended by `diff.run` (one line per run: run id, timestamp, strategy version, `{probe_id: verdict}`), so the status history exists without the UI.

## 5. What this changes for a judge

Fifteen seconds in: the query log page, coloured. One click: a finding with both engines' answers side by side. One more: Challenge, and the two engines answer again live. Then the results table with "10 planted, N caught, 0 false flips, 3 runs" and "held-out unseen-only X/Y". Then the roster. The memo and the ASI:One cards are unchanged and still the deliverable.

## 6. Order of work and size

Project with three parts, built in dependency order: 4.1 (held-out + mutants) → 4.2 (strategy loop) → 4.3 (UI). Each part lands with tests and is usable alone. Estimated: 4.1 one day, 4.2 one day, 4.3 one to two days.

## 7. Decided by the team

- Keep the MongoDB migration domain; no new sponsor-specific work.
- Mutations are injected by an in-process proxy in front of MongoDB, not by patching FerretDB.
- No web framework; local stdlib server with static export.
- Order 4.1 → 4.2 → 4.3.
- Everything numeric is committed JSON; the roster is committed even when it records rejections.

## 8. Left to the builder

- Exact HTML/CSS; keep it readable at a laptop projector, light theme fine.
- How the proxy intercepts `watch` and sessions in pymongo (subclass vs composition); whichever passes the existing probe runner unchanged.
- Whether `mutation_test` runs probes for all features against every mutant or skips probes whose features cannot interact with the mutation. Default: run all, so catch attribution is honest.
- Test design: unit tests for the proxy (each mutation changes exactly the intended command), the held-out guard, the adoption rule, the split determinism; an integration test that runs the zero-mutation proxy through `diff_probe` with the handwritten probe fixtures and expects all `equal`.
- `config.env` in section 3 means the `env()` function in `archaeologist/config.py`.
- Per-mutant `run_probe` namespaces so mutants can run in parallel without `ca_tgt_*` database-name collisions.
- "Caught" means caught in every repeat; a mutant caught in some repeats only is reported as flaky, not caught.
- The strategy version is stored in the probes file so `diff.run` can write it to `history.jsonl`.
- The mutation test runs behind a pipeline flag (like `--with-self-check`); it is not part of the default demo run.
- Timing budget for `improve.py` (parallelism, skipping `wait_ttl` probes during candidate evaluation with a flag that is recorded in the roster entry).
