"""The differ has to be right before any of its results mean anything."""

from __future__ import annotations

from datetime import datetime, timezone

from bson import Int64

from archaeologist import normalize
from archaeologist.probe_runner import classify, source_gate


def _run(captured, types=None, outcome="completed", **extra):
    step = {"label": "read", "status": "ok", "capture": True, "captured": captured, "types": types, **extra}
    return {"outcome": outcome, "steps": [step]}


def test_identical_results_are_equal():
    assert classify(_run({"docs": [{"a": 1}]}), _run({"docs": [{"a": 1}]}))["verdict"] == "equal"


def test_tampered_value_is_a_result_diff():
    outcome = classify(_run({"docs": [{"a": 1}]}), _run({"docs": [{"a": 2}]}))
    assert outcome["verdict"] == "result_diff"
    assert outcome["difference"]["path"] == "$.docs[0].a"


def test_missing_field_is_a_result_diff():
    assert classify(_run({"docs": [{"a": 1, "b": 2}]}), _run({"docs": [{"a": 1}]}))["verdict"] == "result_diff"


def test_extra_document_is_a_result_diff():
    assert classify(_run({"docs": [{"a": 1}]}), _run({"docs": [{"a": 1}, {"a": 2}]}))["verdict"] == "result_diff"


def test_silently_accepted_write_is_a_result_diff():
    source = _run({"error_code": 121})
    target = {"outcome": "completed", "steps": [{"label": "read", "status": "unexpected_success", "capture": True, "captured": {"no_error": True}}]}
    assert classify(source, target)["verdict"] == "result_diff"


def test_not_implemented_error_is_unsupported():
    failed = {"outcome": "failed", "steps": [{"label": "index", "status": "error", "code": 115, "message": "not implemented yet"}]}
    assert classify(_run({}), failed)["verdict"] == "unsupported"


def test_other_error_stays_an_error():
    failed = {"outcome": "failed", "steps": [{"label": "x", "status": "error", "code": 11000, "message": "duplicate key"}]}
    assert classify(_run({}), failed)["verdict"] == "error"


def test_timeout_is_reported_as_timeout():
    failed = {"outcome": "timeout", "steps": [{"label": "slow", "status": "timeout"}]}
    assert classify(_run({}), failed)["verdict"] == "timeout"


def test_numeric_type_drift_is_flagged_but_equal():
    outcome = classify(_run({"docs": [{"a": 5}]}, types="int"), _run({"docs": [{"a": 5}]}, types="double"))
    assert outcome["verdict"] == "equal"
    assert outcome["numeric_type_drift"] == ["read"]


def test_canonical_merges_number_types_and_key_order():
    assert normalize.canonical({"b": 5.0, "a": Int64(3)}) == {"a": 3, "b": 5}


def test_canonical_keeps_dates_comparable_across_timezones():
    assert normalize.canonical(datetime(2026, 1, 1, tzinfo=timezone.utc)) == normalize.canonical(datetime(2026, 1, 1))


def test_order_matters_unless_declared_unordered():
    left = {"docs": [{"a": 1}, {"a": 2}]}
    right = {"docs": [{"a": 2}, {"a": 1}]}
    assert normalize.first_difference(normalize.comparable(left), normalize.comparable(right))
    assert not normalize.first_difference(normalize.comparable(left, True), normalize.comparable(right, True))


def test_server_bookkeeping_is_removed():
    reply = {"n": 1, "ok": 1.0, "$clusterTime": {"x": 1}, "operationTime": 5, "electionId": "abc"}
    assert normalize.reduce_result(reply) == {"n": 1}


def test_gate_rejects_probe_that_fails_on_source():
    probe = {"steps": [{"op": "command", "label": "read"}]}
    run = {"outcome": "failed", "steps": [{"label": "read", "status": "error", "code": 2, "message": "bad"}]}
    assert "failed on MongoDB" in source_gate(probe, run)


def test_gate_rejects_vacuous_probe():
    probe = {"steps": [{"op": "command", "label": "seed"}]}
    run = {"outcome": "completed", "steps": [{"label": "seed", "status": "ok"}]}
    assert "captures nothing" in source_gate(probe, run)


def test_gate_rejects_wrong_expectation():
    probe = {"steps": [{"op": "command", "label": "read", "capture": True, "expect_ejson": '{"docs": [{"a": 1}]}'}]}
    run = _run({"docs": [{"a": 2}]})
    assert "expected a different result" in source_gate(probe, run)


def test_gate_requires_stated_expectation():
    probe = {"steps": [{"op": "command", "label": "read", "capture": True}]}
    assert "no expected value" in source_gate(probe, _run({"docs": []}))


def test_gate_accepts_correct_probe():
    probe = {"steps": [{"op": "command", "label": "read", "capture": True, "expect_ejson": '{"docs": [{"a": 1}]}'}]}
    assert source_gate(probe, _run({"docs": [{"a": 1}]})) is None
