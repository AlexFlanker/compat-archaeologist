"""The evidence pack: every fact the agent may cite, with a stable id, small enough for one prompt."""

from __future__ import annotations

import pytest

from archaeologist import evidence_pack
from archaeologist.config import OUT_DIR


@pytest.fixture(scope="module")
def pack():
    if not (OUT_DIR / "shoplog" / "tco.json").exists():
        pytest.skip("run the pipeline first")
    return evidence_pack.build("shoplog")


def test_ids_are_unique_and_cover_every_kind_of_evidence(pack):
    ids = [fact["id"] for fact in pack["facts"]]
    assert len(ids) == len(set(ids))
    for expected in ("decision:FerretDB", "gap:FerretDB:F4", "gap:AWS DocumentDB:F9", "probe:F4.txn-abort-rollback",
                     "step:0", "step:1", "ledger:F6", "rule:R2", "subq:FerretDB", "assumption:people.engineer_cost_per_hour"):
        assert expected in ids, expected
    assert any(item.startswith("check:") for item in ids)
    assert any(item.startswith("question:") for item in ids)
    assert any(item.startswith("behavior:") for item in ids)


def test_pack_fits_one_prompt(pack):
    text = evidence_pack.render(pack)
    assert len(text) < evidence_pack.MAX_CHARS
    assert "[gap:FerretDB:F4]" in text


def test_facts_carry_the_numbers(pack):
    facts = {fact["id"]: fact["text"] for fact in pack["facts"]}
    assert "10" in facts["probe:F4.txn-abort-rollback"] and "8" in facts["probe:F4.txn-abort-rollback"]
    assert "-$26,700" in facts["step:2"]
    assert "120" in facts["assumption:people.engineer_cost_per_hour"]


def test_invented_citations_are_dropped(pack):
    kept, dropped = evidence_pack.check_citations(pack, ["gap:FerretDB:F4", "gap:FerretDB:F42", "probe:made-up", "step:1"])
    assert kept == ["gap:FerretDB:F4", "step:1"]
    assert dropped == ["gap:FerretDB:F42", "probe:made-up"]


def test_a_missing_prefix_is_resolved_only_when_unambiguous(pack):
    kept, dropped = evidence_pack.check_citations(pack, ["people.engineer_cost_per_hour", "F11"])
    assert kept == ["assumption:people.engineer_cost_per_hour"]
    assert dropped == ["F11"]  # gap:FerretDB:F11, gap:AWS DocumentDB:F11 and ledger:F11 all end with it
