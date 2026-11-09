"""Probe provenance: was each probe written after the seal through the guard, or checked by hand against its prompt?"""

from datetime import datetime, timedelta, timezone

import pytest

from archaeologist import meta_client, probes

HELD = {"created": "2026-11-09T08:30:31+00:00"}
STRATEGY = {"version": "v000", "system_prompt_additions": "", "per_feature_hints": {}, "probe_count_overrides": {}, "required_checks": []}


@pytest.fixture(autouse=True)
def _reset_guard():
    yield
    meta_client.LEAK_GUARD = None


def _p(**stamps):
    return {"probe_id": "F1.a", "feature_id": "F1", **stamps}


def test_provenance_counts_post_seal_checked_and_unchecked():
    batch = [
        _p(generated_at="2026-11-09T09:00:00+00:00", prompt_guarded=True),
        _p(generated_at="2026-11-09T08:30:31+00:00", prompt_guarded=True),  # the same second as the seal counts
        _p(generated_at="2026-11-09T09:00:00+00:00", prompt_guarded=False),  # written after the seal, but unguarded
        _p(generated_at="2026-11-07T22:00:00+00:00", prompt_guarded=True),  # guarded, but before the seal
        _p(pre_seal_check={"ledger_commit": "6b69027", "heldout_shapes_in_prompt": 0}),
        _p(pre_seal_check={"ledger_commit": "6b69027", "heldout_shapes_in_prompt": 2}),
        _p(pre_seal_check={"heldout_shapes_in_prompt": 0}),  # no commit named, so nothing can be re-checked
        _p(),
    ]
    assert probes.provenance(batch, HELD) == {"total": 8, "post_seal": 2, "checked_pre_seal": 1, "unchecked": 5}


def test_provenance_of_an_empty_set_has_nothing_unchecked():
    assert probes.provenance([], HELD) == {"total": 0, "post_seal": 0, "checked_pre_seal": 0, "unchecked": 0}


def test_provenance_without_a_seal_date_counts_no_probe_as_post_seal():
    batch = [_p(generated_at="2026-11-09T09:00:00+00:00", prompt_guarded=True)]
    assert probes.provenance(batch, {})["unchecked"] == 1


def _generate_one(monkeypatch) -> dict:
    raw = {"probes": [{"slug": "a", "title": "t", "behavior": "b", "steps": [{"op": "command", "label": "x", "command_ejson": "{}"}]}]}
    monkeypatch.setattr(meta_client, "structured", lambda **k: raw)
    monkeypatch.setattr(meta_client, "model_name", lambda: "m")
    monkeypatch.setattr(probes, "_validate_on_source", lambda probe, source: None)
    (probe,) = probes.generate_for_item({"id": "F4", "feature": "f"}, object(), STRATEGY)
    return probe


def test_generated_probe_is_stamped_with_time_and_guard(monkeypatch):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(meta_client, "LEAK_GUARD", lambda text: None)
    probe = _generate_one(monkeypatch)
    stamped = datetime.fromisoformat(probe["generated_at"])
    assert probe["prompt_guarded"] is True
    assert before <= stamped <= datetime.now(timezone.utc) + timedelta(seconds=1)
    assert probe["generated_at"].endswith("+00:00") and "." not in probe["generated_at"]


def test_generated_probe_without_guard_says_so(monkeypatch):
    monkeypatch.setattr(meta_client, "LEAK_GUARD", None)
    assert _generate_one(monkeypatch)["prompt_guarded"] is False


def test_guard_installed_accessor(monkeypatch):
    monkeypatch.setattr(meta_client, "LEAK_GUARD", None)
    assert meta_client.guard_installed() is False
    monkeypatch.setattr(meta_client, "LEAK_GUARD", lambda text: None)
    assert meta_client.guard_installed() is True
