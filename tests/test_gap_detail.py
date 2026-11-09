"""The gap detail card: built from real pipeline output, one per blocking gap, small enough for ASI:One."""

from __future__ import annotations

import json

import pytest

from archaeologist.config import OUT_DIR, ROOT
from fetch_agent import cards


def _bundle(profile: str) -> dict:
    tco = OUT_DIR / profile / "tco.json"
    if not tco.exists():
        pytest.skip(f"run the pipeline first: {tco} is missing")
    return cards.load_bundle(profile)


def _blocking(bundle: dict):
    for target, info in bundle["tco"]["targets"].items():
        for gap in info["gaps"]:
            if gap["blocking"]:
                yield target, gap


def test_every_blocking_gap_has_a_card_under_the_limit():
    bundle = _bundle("shoplog")
    gaps = list(_blocking(bundle))
    assert len(gaps) >= 6
    for target, gap in gaps:
        narration, card = cards.gap_detail_card("shoplog", bundle, target, gap["key"])
        payload = json.loads(card["card_payload"])
        assert payload["root"]["type"] == "section"
        assert gap["feature"] in narration
        assert len(card["card_payload"].encode()) <= cards.MAX_PAYLOAD_BYTES


def test_transaction_card_shows_both_engines_the_command_and_the_code():
    bundle = _bundle("shoplog")
    _, card = cards.gap_detail_card("shoplog", bundle, "FerretDB", "behavior:F4")
    text = card["card_payload"]
    assert "MongoDB: 10" in text and "FerretDB: 8" in text
    assert "commitTransaction" in text
    assert "shoplog/checkout.py:57" in text
    assert "Step 1 of the reversal" in text


def test_collation_card_links_the_failed_cutover_checks():
    bundle = _bundle("shoplog")
    _, card = cards.gap_detail_card("shoplog", bundle, "FerretDB", "behavior:F6")
    text = card["card_payload"]
    assert "email_ci_unique" in text and "FAIL" in text


def test_unknown_gap_falls_back_to_the_gap_list():
    bundle = _bundle("shoplog")
    narration, card = cards.gap_detail_card("shoplog", bundle, "FerretDB", "behavior:F99")
    assert "not a blocking gap" in narration
    assert "What breaks" in card["card_payload"]


def test_gap_list_offers_a_dig_in_button_per_gap():
    bundle = _bundle("shoplog")
    _, card = cards.gaps_card("shoplog", bundle["tco"])
    text = card["card_payload"]
    assert text.count("Dig into ") >= 6


def test_find_gap_by_feature_prefers_ferretdb():
    bundle = _bundle("shoplog")
    assert cards.find_gap(bundle["tco"], "F11") == ("FerretDB", "documented:F11")
    assert cards.find_gap(bundle["tco"], "F2") == ("AWS DocumentDB", "documented:F2")
    assert cards.find_gap(bundle["tco"], "F10") is None


def test_probe_definitions_are_where_the_card_expects_them():
    assert (ROOT / "probes" / "shoplog.json").exists()
