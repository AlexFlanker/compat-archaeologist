"""Finds MongoDB dependencies in application code using Meta Muse, then checks every citation.

A model can cite lines that don't say what it claims, so each finding must point at a real
line containing the quoted token. A plain token scan runs alongside it: it measures what the
model missed, and it is the labeled fallback when the model can't be reached.
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from archaeologist import meta_client
from archaeologist.config import EVIDENCE_DIR, ROOT
from archaeologist.features import CODE_TOKENS, EXTRA_STAGE_TOKENS, FEATURES, extra_item_id

APP_DIR = ROOT / "shoplog"
LINE_TOLERANCE = 3
# Files that hold no MongoDB behavior of their own.
SKIP_FILES = {"__init__.py"}
PROFILE_FILES = {"lite": ["crud.py"]}

FINDINGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "business_rules"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["feature_id", "feature_name", "line", "token", "behavior", "confidence"],
                "properties": {
                    "feature_id": {"type": "string"},
                    "feature_name": {"type": "string"},
                    "line": {"type": "integer"},
                    "token": {"type": "string"},
                    "behavior": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "business_rules": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rule", "line", "token", "confidence"],
                "properties": {
                    "rule": {"type": "string"},
                    "line": {"type": "integer"},
                    "token": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You are a software archaeologist reading one file of a legacy application that uses MongoDB.
Report two things, using only what this file shows.

1. findings: each MongoDB-specific behavior the code depends on.
   Use feature_id from this list when it fits, otherwise use "OTHER":
{feature_list}
   Plain finds, inserts, updates and deletes by _id or simple filters are F10.
   Name the behavior the application relies on, not just the API call. For example:
   "a checkout must roll back the stock reservation if the order insert fails".

2. business_rules: implicit business rules that the code enforces, stated as a rule a product owner would recognize.
   Be specific and include the numbers. Do not report generic programming facts.

For every item, give the line number from the numbered listing and a token: a short string copied exactly
from that line. The citation is checked by a program, so a token that isn't on that line causes the item to be discarded.
confidence is your probability from 0 to 1 that the application really depends on this. If the file has nothing relevant, return empty arrays."""


def _feature_list() -> str:
    return "\n".join(f"   {key}: {value['name']}" for key, value in FEATURES.items())


def app_files(profile: str) -> list[Path]:
    allowed = PROFILE_FILES.get(profile)
    files = sorted(path for path in APP_DIR.glob("*.py") if path.name not in SKIP_FILES)
    return [path for path in files if allowed is None or path.name in allowed]


def _numbered(path: Path) -> str:
    return "\n".join(f"{number:4d}  {line}" for number, line in enumerate(path.read_text().splitlines(), start=1))


def verify_citation(lines: list[str], claimed_line: int, token: str) -> int | None:
    """Returns the line where the token really is, or None if the citation doesn't hold."""
    if not token:
        return None
    if 1 <= claimed_line <= len(lines) and token in lines[claimed_line - 1]:
        return claimed_line
    low = max(1, claimed_line - LINE_TOLERANCE)
    high = min(len(lines), claimed_line + LINE_TOLERANCE)
    for number in sorted(range(low, high + 1), key=lambda candidate: abs(candidate - claimed_line)):
        if token in lines[number - 1]:
            return number
    return None


def static_scan(files: list[Path]) -> list[dict]:
    hits = []
    tokens = [(feature, token) for feature, values in CODE_TOKENS.items() for token in values]
    tokens += [(extra_item_id(token), token) for token in EXTRA_STAGE_TOKENS]
    for path in files:
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            for feature, token in tokens:
                if token in line:
                    hits.append({"feature_id": feature, "file": str(path.relative_to(ROOT)), "line": number, "token": token})
    return hits


def _analyze_file(path: Path) -> dict:
    relative = str(path.relative_to(ROOT))
    response = meta_client.structured(
        purpose=f"code_evidence:{path.name}",
        system=SYSTEM_PROMPT.format(feature_list=_feature_list()),
        user=f"File: {relative}\n\n{_numbered(path)}",
        schema=FINDINGS_SCHEMA,
        schema_name="code_evidence",
    )
    lines = path.read_text().splitlines()
    findings, rules, dropped = [], [], []
    for finding in response.get("findings", []):
        verified = verify_citation(lines, finding["line"], finding["token"])
        if verified is None:
            dropped.append({"file": relative, **finding})
        else:
            findings.append({**finding, "file": relative, "line": verified})
    for rule in response.get("business_rules", []):
        verified = verify_citation(lines, rule["line"], rule["token"])
        if verified is None:
            dropped.append({"file": relative, **rule})
        else:
            rules.append({**rule, "file": relative, "line": verified})
    return {"findings": findings, "business_rules": rules, "dropped": dropped}


def run(profile: str, refresh: bool = True) -> dict:
    cache_path = EVIDENCE_DIR / f"{profile}_code_findings.json"
    files = app_files(profile)
    static = static_scan(files)

    if refresh and meta_client.is_configured():
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(_analyze_file, files))
        except meta_client.MetaUnavailable as exc:
            print(f"  [code] Muse unavailable ({exc}); using the last saved findings if any")
        else:
            document = {
                "profile": profile,
                "source": "muse",
                "model": meta_client.model_name(),
                "findings": [item for result in results for item in result["findings"]],
                "business_rules": [item for result in results for item in result["business_rules"]],
                "dropped_citations": [item for result in results for item in result["dropped"]],
                "static_scan": static,
            }
            cache_path.write_text(json.dumps(document, indent=2))
            return document

    if cache_path.exists():
        document = json.loads(cache_path.read_text())
        if document.get("source") == "muse":
            document["source"] = "muse_cached"
            return document

    return {
        "profile": profile,
        "source": "static_scan_only",
        "model": None,
        "findings": [
            {
                **hit,
                "feature_name": FEATURES.get(hit["feature_id"], {}).get("name", hit["token"]),
                "behavior": f"Code uses {hit['token']}",
                "confidence": None,
            }
            for hit in static
        ],
        "business_rules": [],
        "dropped_citations": [],
        "static_scan": static,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="shoplog")
    parser.add_argument("--no-refresh", action="store_true")
    arguments = parser.parse_args()
    result = run(arguments.profile, refresh=not arguments.no_refresh)
    print(f"source={result['source']} findings={len(result['findings'])} rules={len(result['business_rules'])} dropped={len(result['dropped_citations'])} static_hits={len(result['static_scan'])}")
