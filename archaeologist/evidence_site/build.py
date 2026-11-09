"""Loads the pipeline's evidence files and renders them as static pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from archaeologist import probes as probe_store
from archaeologist.config import OUT_DIR, ROOT
from archaeologist.evidence_site import pages

STATIC_CSS = Path(__file__).with_name("static.css")


def _json(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_context(profile: str) -> dict:
    out = OUT_DIR / profile
    versions = {path.stem: json.loads(path.read_text()) for path in sorted((ROOT / "strategy").glob("v*.json"))}
    held_out = _json(out / "heldout.json")
    probe_list = _json(ROOT / "probes" / f"{profile}.json") or []
    return {
        "profile": profile,
        "runs": _json(out / "runs.json"),
        "ledger": _json(out / "ledger.json"),
        "heldout": held_out,
        "provenance": probe_store.provenance(probe_list, held_out) if held_out else None,
        "mutation": _json(out / "mutation_test.json"),
        "history": _jsonl(out / "history.jsonl"),
        "probes": probe_list,
        "roster": _json(ROOT / "strategy" / "roster.json"),
        "strategy_versions": versions,
        "catalog": _json(ROOT / "mutants" / "catalog.json") or [],
    }


def render_all(profile: str, live: bool) -> dict[str, str]:
    ctx = load_context(profile)
    rendered = {
        "index.html": pages.render_overview(ctx),
        "querylog.html": pages.render_querylog(ctx),
        "planted.html": pages.render_planted(ctx),
        "strategy.html": pages.render_strategy(ctx),
        "static.css": STATIC_CSS.read_text(),
    }
    for probe in (ctx["runs"] or {}).get("probes", []):
        rendered[pages.finding_path(probe["probe_id"])] = pages.render_finding(ctx, probe["probe_id"], live=live)
    return rendered


def write(profile: str, out_dir: Path, live: bool = False) -> list[str]:
    rendered = render_all(profile, live)
    root = out_dir.resolve()
    for name, text in rendered.items():
        target = out_dir / name
        if not target.resolve().is_relative_to(root):
            raise ValueError(f"refusing to write {name}: it resolves outside {out_dir}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return sorted(rendered)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the static evidence site.")
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--out", default="site")
    args = parser.parse_args()
    names = write(args.profile, Path(args.out))
    print(f"wrote {len(names)} files to {args.out}/ (open {args.out}/index.html)")


if __name__ == "__main__":
    main()
