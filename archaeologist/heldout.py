"""Seals a slice of the recorded workload so no Muse prompt can contain it.

The held-out shapes are replayable reads picked once (seeded) and stored in out/<profile>/heldout.json.
Muse sees the code and the training shapes; the held-out shapes are only ever replayed, so a clean
replay on them is evidence about operations the model never saw.
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timezone
from typing import Callable

from archaeologist import meta_client
from archaeologist.config import EVIDENCE_DIR, OUT_DIR
from archaeologist.extract_querylog import clean_command, load_log, query_log_path, shape_of
from archaeologist.replay import _shape_id, partition

DEFAULT_SEED = 20261108
DEFAULT_FRACTION = 0.3


class HeldOutMismatch(Exception):
    """The stored held-out file was made with a different seed or fraction than requested."""


class StaleHeldOut(Exception):
    """The stored held-out file names shapes that are not in the current workload."""


def _squash(text: str) -> str:
    return "".join(text.split())


def _population_ids(entries: list[dict]) -> list[str]:
    return sorted({_shape_id(shape_of(entry)) for entry in partition(entries)["reads"]})


def split(summary: dict, entries: list[dict], seed: int = DEFAULT_SEED, fraction: float = DEFAULT_FRACTION) -> dict:
    population = _population_ids(entries)
    features = {shape["id"]: set(shape["features"]) for shape in summary["shapes"]}
    wanted = math.ceil(fraction * len(population))

    shuffled = list(population)
    random.Random(seed).shuffle(shuffled)
    held: list[str] = []
    for shape_id in shuffled:
        if len(held) >= wanted:
            break
        remaining = [other for other in features if other != shape_id and other not in held]
        covered = all(any(feature in features[other] for other in remaining) for feature in features.get(shape_id, ()))
        if covered:
            held.append(shape_id)

    return {
        "seed": seed,
        "fraction": fraction,
        "population_shape_ids": population,
        "train_shape_ids": sorted(set(features) - set(held)),
        "heldout_shape_ids": sorted(held),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def load_or_create(profile: str) -> dict:
    path = OUT_DIR / profile / "heldout.json"
    summary = json.loads((EVIDENCE_DIR / f"{profile}_querylog_summary.json").read_text())
    entries = load_log(query_log_path(profile))
    if path.exists():
        doc = json.loads(path.read_text())
        if doc.get("seed") != DEFAULT_SEED or doc.get("fraction") != DEFAULT_FRACTION:
            raise HeldOutMismatch(
                f"{path} was made with seed={doc.get('seed')} fraction={doc.get('fraction')}, "
                f"not seed={DEFAULT_SEED} fraction={DEFAULT_FRACTION}; delete it to re-split"
            )
        missing = sorted(set(doc["population_shape_ids"]) - set(_population_ids(entries)))
        if missing:
            raise StaleHeldOut(f"{path} names shapes that are not in the current workload: {', '.join(missing)}")
        return doc
    doc = split(summary, entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    return doc


def is_heldout(entry: dict, doc: dict) -> bool:
    return _shape_id(shape_of(entry)) in set(doc["heldout_shape_ids"])


def guard_for(doc: dict, entries: list[dict], extra_forbidden: list[str] = ()) -> Callable[[str], str | None]:
    """Returns a checker: given prompt text, the first held-out string it contains, else None."""
    held = set(doc["heldout_shape_ids"])
    needles: list[tuple[str, str]] = []
    for entry in entries:
        shape = shape_of(entry)
        shape_id = _shape_id(shape)
        if shape_id not in held:
            continue
        needles.append((f"shape {shape_id}", _squash(shape)))
        for sort_keys in (True, False):  # prompts may serialise a command with or without sorted keys
            command = json.dumps(clean_command(entry), sort_keys=sort_keys, default=str)
            needles.append((f"command of shape {shape_id}", _squash(command)))
    needles.extend((f"forbidden string {text[:40]}", _squash(text)) for text in extra_forbidden if text.strip())
    unique = [(label, needle) for label, needle in dict.fromkeys(needles) if needle]

    def check(text: str) -> str | None:
        squashed = _squash(text)
        for label, needle in unique:
            if needle in squashed:
                return label
        return None

    return check


def install_guard(doc: dict, entries: list[dict], extra_forbidden: list[str] = ()) -> None:
    meta_client.LEAK_GUARD = guard_for(doc, entries, extra_forbidden)
