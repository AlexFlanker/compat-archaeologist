"""Plain HTML renderers. Every number on a page is read from a file under out/, probes/ or strategy/."""

from __future__ import annotations

import difflib
import json
from html import escape

BAD_VERDICTS = {"result_diff", "error", "unsupported", "timeout"}
NAV = [("index.html", "Overview"), ("querylog.html", "Query log"), ("planted.html", "Planted gaps"), ("strategy.html", "Strategy")]

POLLER = """<script>
(function () {
  var btn = document.getElementById('challenge'), out = document.getElementById('challenge-status');
  var esc = function (s) { return String(s).replace(/[&<>]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]; }); };
  var cell = function (r) { return '<td>' + esc(r.status || '') + '<pre>' + esc(r.captured === undefined ? '' : JSON.stringify(r.captured, null, 1)) + '</pre></td>'; };
  function show(result) {
    var v = document.getElementById('verdict');
    v.textContent = result.verdict; v.className = 'v-' + result.verdict;
    var d = result.detail || {}, diff = d.difference, at = d.at_step, rows = '';
    document.getElementById('difference').innerHTML = diff
      ? 'First difference at step <code>' + esc(at) + '</code>, path <mark>' + esc(diff.path) + '</mark>: source <code>' + esc(JSON.stringify(diff.source)) + '</code>, target <code>' + esc(JSON.stringify(diff.target)) + '</code>'
      : esc(d.message || d.reason || '');
    (result.source.steps || []).forEach(function (s, i) {
      var t = (result.target.steps || [])[i] || {};
      rows += '<tr' + (s.label === at ? ' class="diff"' : '') + '><td>' + esc(s.label) + '</td>' + cell(s) + cell(t) + '</tr>';
    });
    document.getElementById('compare-body').innerHTML = rows;
    out.textContent = 'Rerun finished' + (diff ? ' (difference at ' + diff.path + ')' : '');
  }
  function fail(message) { btn.disabled = false; out.textContent = 'Rerun failed: ' + message; }
  function ok(r) { if (!r.ok) { throw new Error('server answered ' + r.status); } return r.json(); }
  function poll(job) {
    fetch('/api/challenge/' + job).then(ok).then(function (s) {
      if (s.status === 'running') { setTimeout(function () { poll(job); }, 1000); return; }
      if (s.status === 'done') { btn.disabled = false; show(s.result); } else { fail(s.error || s.status); }
    }).catch(function (e) { fail(e.message); });
  }
  btn.addEventListener('click', function () {
    btn.disabled = true; out.textContent = 'Running on both engines...';
    fetch('/api/challenge/' + btn.dataset.probe, {method: 'POST'}).then(ok).then(function (j) { poll(j.job_id); }).catch(function (e) { fail(e.message); });
  });
})();
</script>"""


def shape_colour(shape: dict, features: dict, items: list[dict]) -> str:
    feature_ids = shape.get("features") or []
    if (shape.get("different") and not shape.get("ranking_only")) or shape.get("target_errors"):
        return "red"
    if any((features.get(f) or {}).get("verdict") in BAD_VERDICTS for f in feature_ids):
        return "red"
    if any(item.get("status") == "INFERRED" and item.get("feature") in feature_ids for item in items):
        return "yellow"
    if any(not (features.get(f) or {}).get("probes") for f in feature_ids):
        return "yellow"
    return "green"


def _e(value) -> str:
    return escape("" if value is None else str(value))


def _json(value) -> str:
    return json.dumps(value, indent=1, sort_keys=True, default=str)


def _page(title: str, body: str, depth: int = 0) -> str:
    prefix = "../" * depth
    nav = " | ".join(f'<a href="{prefix}{href}">{label}</a>' for href, label in NAV)
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
        f'<link rel="stylesheet" href="{prefix}static.css"></head><body><nav>{nav}</nav><main>{body}</main></body></html>'
    )


def _missing(path: str) -> str:
    return f'<p class="muted">Not available: <code>{_e(path)}</code> has not been written yet.</p>'


def _tile(title: str, value: str, source: str, note: str = "") -> str:
    return f'<div class="tile"><h3>{_e(title)}</h3><div class="big">{value}</div><p>{_e(note)}</p><p class="src">{_e(source)}</p></div>'


def render_overview(ctx: dict) -> str:
    profile, runs, mutation, roster = ctx["profile"], ctx["runs"], ctx["mutation"], ctx["roster"]
    tiles = []
    if runs:
        probes = runs["probes"]
        verdicts = [p["verdict"] for p in probes]
        equal = verdicts.count("equal")
        tiles.append(_tile("Probes equal on both engines", f"{equal} of {len(probes)}", f"out/{profile}/runs.json", f"run {runs.get('run_id', '')}"))
        reads = (runs.get("replay") or {}).get("reads") or {}
        overall = reads.get("overall") or {}
        if overall:
            tiles.append(_tile("Recorded reads replayed", _e(overall.get("replayed", "")), f"out/{profile}/runs.json", f"{overall.get('equal', 0)} equal, {overall.get('different', 0)} different, {overall.get('errors', 0)} target errors"))
    if mutation:
        s = mutation["summary"]
        tiles.append(_tile("Planted gaps caught", f"{s['caught']} of {s['planted']}", f"out/{profile}/mutation_test.json", f"train {s['train']['caught']}/{s['train']['planted']}, held-out {s['heldout']['caught']}/{s['heldout']['planted']}, false flips (max) {s['false_flips_max']}"))
    if roster:
        tiles.append(_tile("Active strategy", _e(roster.get("active")), "strategy/roster.json", f"{len(roster.get('history', []))} recorded decisions"))
    if ctx.get("heldout"):
        h = ctx["heldout"]
        tiles.append(_tile("Held-out read shapes", f"{len(h.get('heldout_shape_ids', []))} of {len(h.get('population_shape_ids', []))}", f"out/{profile}/heldout.json", f"sealed with seed {h.get('seed')}"))
    if ctx.get("history"):
        tiles.append(_tile("Recorded runs", str(len(ctx["history"])), f"out/{profile}/history.jsonl"))
    body = f"<h1>Compat Archaeologist: {_e(profile)}</h1><p>Each tile names the file it was read from. Pages: findings are listed below.</p>"
    body += '<div class="tiles">' + "".join(tiles) + "</div>" if tiles else _missing(f"out/{profile}/runs.json")
    if runs:
        rows = "".join(
            f'<tr><td><a href="findings/{_e(p["probe_id"])}.html">{_e(p["probe_id"])}</a></td><td>{_e(p["feature_id"])}</td><td class="v-{_e(p["verdict"])}">{_e(p["verdict"])}</td></tr>'
            for p in runs["probes"]
        )
        body += f"<h2>Findings</h2><table><tr><th>Probe</th><th>Feature</th><th>Verdict</th></tr>{rows}</table>"
    return _page("Overview", body)


def render_querylog(ctx: dict) -> str:
    profile, runs = ctx["profile"], ctx["runs"]
    body = "<h1>Query log</h1><p>Each row is a recorded read shape replayed on both engines. Red: a difference or target error, or a probe disagreement. Yellow: inferred or unprobed feature. Green: otherwise. Click a row to expand.</p>"
    shapes = (((runs or {}).get("replay") or {}).get("reads") or {}).get("shapes") or []
    if not shapes:
        return _page("Query log", body + _missing(f"out/{profile}/runs.json"))
    features, items = runs.get("features") or {}, (ctx["ledger"] or {}).get("items", [])
    out = []
    for s in shapes:
        colour = shape_colour(s, features, items)
        held = ' <span class="badge">held-out</span>' if s.get("heldout") else ""
        summary = f'<summary><span class="dot {colour}"></span>{_e(s["shape_id"])} {_e(s.get("collection"))} / {_e(s.get("path"))} / {_e(", ".join(s.get("features") or []))}{held}</summary>'
        detail = (
            f'<table><tr><th></th><th>p50 ms</th><th>p95 ms</th></tr>'
            f'<tr><td>source</td><td>{_e(s.get("source_p50_ms"))}</td><td>{_e(s.get("source_p95_ms"))}</td></tr>'
            f'<tr><td>target</td><td>{_e(s.get("target_p50_ms"))}</td><td>{_e(s.get("target_p95_ms"))}</td></tr></table>'
            f'<p>replayed {_e(s.get("replayed"))} of {_e(s.get("in_log"))} in log: {_e(s.get("equal"))} equal, {_e(s.get("different"))} different, {_e(s.get("target_errors"))} target errors'
            f'{", ranking only" if s.get("ranking_only") else ""}</p>'
        )
        out.append(f'<details class="shape {colour}">{summary}{detail}</details>')
    return _page("Query log", body + "".join(out) + f'<p class="src">out/{_e(profile)}/runs.json, out/{_e(profile)}/ledger.json</p>')


def _step_rows(source: dict, target: dict, detail: dict) -> str:
    at = detail.get("at_step")
    target_steps = target.get("steps", [])
    rows = []
    for i, step in enumerate(source.get("steps", [])):
        other = target_steps[i] if i < len(target_steps) else {}
        cls = ' class="diff"' if step.get("label") == at else ""
        cells = "".join(f'<td>{_e(r.get("status"))}<pre>{_e(_json(r["captured"])) if "captured" in r else ""}</pre></td>' for r in (step, other))
        rows.append(f'<tr{cls}><td>{_e(step.get("label"))}</td>{cells}</tr>')
    return "".join(rows)


def render_finding(ctx: dict, probe_id: str, live: bool = False) -> str:
    profile = ctx["profile"]
    run = next(p for p in ctx["runs"]["probes"] if p["probe_id"] == probe_id)
    spec = next((p for p in ctx.get("probes") or [] if p["probe_id"] == probe_id), {})
    detail = run.get("detail") or {}
    body = f'<h1>{_e(run.get("title") or probe_id)}</h1><p class="muted">{_e(probe_id)} (feature {_e(run["feature_id"])})</p><p>{_e(run.get("behavior"))}</p>'
    body += f'<h2>Verdict: <span id="verdict" class="v-{_e(run["verdict"])}">{_e(run["verdict"])}</span></h2>'
    d = detail.get("difference")
    if d:
        text = f'First difference at step <code>{_e(detail.get("at_step"))}</code>, path <mark>{_e(d.get("path"))}</mark>: source <code>{_e(_json(d.get("source")))}</code>, target <code>{_e(_json(d.get("target")))}</code>'
    else:
        text = _e(detail.get("message") or detail.get("reason"))
    body += f'<p id="difference">{text}</p>'
    if live:
        body += f'<p><button id="challenge" data-probe="{_e(probe_id)}">Challenge: rerun on both engines</button> <span id="challenge-status"></span></p>'
    else:
        body += f"<p>Rerun this probe: <code>python -m archaeologist.probe_runner --probe {_e(probe_id)}</code></p>"
    steps = "".join(
        f'<tr><td>{_e(s.get("label"))}</td><td>{_e(s.get("op"))}</td><td><pre>{_e(s.get("command_ejson"))}</pre></td><td><pre>{_e(s.get("expect_ejson"))}</pre></td></tr>'
        for s in spec.get("steps", [])
    )
    body += f"<h2>Steps</h2><table><tr><th>Label</th><th>Op</th><th>command_ejson</th><th>expect_ejson</th></tr>{steps}</table>" if steps else _missing(f"probes/{profile}.json")
    body += f'<h2>Captured values</h2><table id="compare"><tr><th>Step</th><th>MongoDB</th><th>Target</th></tr><tbody id="compare-body">{_step_rows(run["source"], run["target"], detail)}</tbody></table>'
    rows = [
        f'<tr><td>{_e(h.get("run_id"))}</td><td>{_e(h.get("strategy_version"))}</td><td class="v-{_e(h["verdicts"][probe_id])}">{_e(h["verdicts"][probe_id])}</td></tr>'
        for h in ctx.get("history") or []
        if probe_id in (h.get("verdicts") or {})
    ]
    body += "<h2>Status history</h2>"
    if rows:
        body += f"<table><tr><th>Run</th><th>Strategy</th><th>Verdict</th></tr>{''.join(rows)}</table>"
    else:
        body += f'<p>No earlier runs recorded.</p><table><tr><th>Run</th><th>Strategy</th><th>Verdict</th></tr><tr><td>{_e(ctx["runs"].get("run_id"))}</td><td>current</td><td>{_e(run["verdict"])}</td></tr></table>'
    caught = [m for m in (ctx["mutation"] or {}).get("mutants", []) if probe_id in (m.get("caught_by") or [])]
    if caught:
        body += "<h2>Planted gaps this probe catches</h2><ul>" + "".join(f'<li><code>{_e(m["id"])}</code>: {_e(m["description"])}</li>' for m in caught) + "</ul>"
    if live:
        body += POLLER
    return _page(probe_id, body, depth=1)


def render_planted(ctx: dict) -> str:
    profile, mutation = ctx["profile"], ctx["mutation"]
    body = "<h1>Planted gaps</h1><p>Known incompatibilities injected in front of MongoDB, and whether the probes noticed.</p>"
    if not mutation:
        return _page("Planted gaps", body + _missing(f"out/{profile}/mutation_test.json"))
    s, zero = mutation["summary"], mutation.get("zero_mutation") or {}
    body += f'<p>{s["caught"]} of {s["planted"]} caught ({s["flaky"]} flaky); {mutation.get("repeats")} repeats, {mutation.get("probe_count")} probes, strategy {_e(mutation.get("strategy_version"))}. With nothing planted: {_e(s.get("false_flips_max"))} false flips (max), {_e(s.get("invalid_probes_max"))} invalid probes (max).</p>'
    catalog = {m["id"]: m for m in ctx.get("catalog") or []}
    rows = []
    for m in mutation["mutants"]:
        state = "missed" if not m["caught"] else "flaky" if m["flaky"] else "caught"
        who = ", ".join(f'<a href="findings/{_e(p)}.html">{_e(p)}</a>' for p in m.get("caught_by") or []) or ("no probe for these features" if m.get("no_probe_for_features") else "")
        rows.append(f'<tr><td><code>{_e(m["id"])}</code></td><td>{_e(m["split"])}</td><td>{_e(", ".join(m["feature_ids"]))}</td><td>{_e(m["description"] or catalog.get(m["id"], {}).get("description"))}</td><td class="m-{state}">{state}</td><td>{who}</td></tr>')
    body += f'<table><tr><th>Gap</th><th>Split</th><th>Features</th><th>What was planted</th><th>Result</th><th>Caught by</th></tr>{"".join(rows)}</table>'
    return _page("Planted gaps", body + f'<p class="src">out/{_e(profile)}/mutation_test.json, mutants/catalog.json</p>')


def _pretty_lines(doc: dict) -> list[str]:
    return json.dumps(doc, indent=2, sort_keys=True).splitlines()


def render_strategy(ctx: dict) -> str:
    roster, versions = ctx["roster"], ctx.get("strategy_versions") or {}
    body = "<h1>Probe strategy</h1>"
    if not roster:
        return _page("Strategy", body + _missing("strategy/roster.json"))
    body += f'<p>Active version: <b>{_e(roster.get("active"))}</b></p>'
    cols = ["version", "parent", "decision", "train_catch", "heldout_catch", "false_flips_max", "invalid_probes", "probes", "generated_features", "ferretdb_run_id", "at", "reason"]
    history = roster.get("history") or []
    rows = "".join("<tr>" + "".join(f"<td>{_e(h.get(c))}</td>" for c in cols) + "</tr>" for h in history)
    body += f"<table><tr>{''.join(f'<th>{c}</th>' for c in cols)}</tr>{rows}</table>" if rows else "<p>No strategy changes recorded yet.</p>"
    body += "<h2>Changes from parent</h2>"
    for h in history:
        child, parent = versions.get(h.get("version")), versions.get(h.get("parent"))
        if child is None:
            continue
        diff = "\n".join(difflib.unified_diff(_pretty_lines(parent or {}), _pretty_lines(child), f'{h.get("parent")}.json', f'{h.get("version")}.json', lineterm="", n=2))
        body += f"<h3>{_e(h.get('version'))} (decision: {_e(h.get('decision'))})</h3><pre class=\"udiff\">{_e(diff or 'no differences')}</pre>"
    return _page("Strategy", body + '<p class="src">strategy/roster.json, strategy/v*.json</p>')
