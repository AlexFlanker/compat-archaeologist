"""Executes declarative probes against an engine and classifies the outcome.

Probes are data, not code. A model can only ask for the operations listed in STEP_OPS, so
nothing it writes ever runs as Python on this machine.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor

from bson import json_util
from bson.codec_options import CodecOptions
from pymongo import MongoClient
from pymongo.errors import ExecutionTimeout, NetworkTimeout, OperationFailure, PyMongoError

from archaeologist import normalize

STEP_OPS = {"command", "txn_start", "txn_commit", "txn_abort", "watch_open", "watch_next", "watch_close", "wait_ttl"}
TTL_WAIT_LIMIT_S = 80

# Codes meaning "this engine doesn't do that" rather than "that failed": CommandNotFound,
# CommandNotSupported, InvalidPipelineOperator, NotImplemented, CommandFailed-style feature
# gaps, and unrecognized pipeline stage.
UNSUPPORTED_CODES = {59, 115, 168, 238, 303, 40324}
UNSUPPORTED_MESSAGE = re.compile(r"not implemented|not supported|unsupported|unrecognized|unknown (operator|top level|option)", re.IGNORECASE)

VERDICT_ORDER = ["unsupported", "error", "result_diff", "timeout", "ranking_diff", "probe_invalid", "equal"]


def probe_database_name(probe_id: str, namespace: str) -> str:
    return f"ca_{namespace}_" + re.sub(r"[^A-Za-z0-9]", "_", probe_id)[:40]


def _parse(text: str):
    return json_util.loads(text)


def _is_unsupported(code, message: str) -> bool:
    return code in UNSUPPORTED_CODES or bool(UNSUPPORTED_MESSAGE.search(message or ""))


def run_probe(client: MongoClient, probe: dict, namespace: str = "probe") -> dict:
    """Runs every step in order. Stops at the first unexpected failure, as an application would."""
    name = probe_database_name(probe["probe_id"], namespace)
    client.drop_database(name)
    db = client[name].with_options(codec_options=CodecOptions(tz_aware=True, uuid_representation=4))
    session = None
    stream = None
    steps_out: list[dict] = []
    outcome = "completed"
    started = time.perf_counter()

    try:
        for index, step in enumerate(probe["steps"]):
            op = step["op"]
            label = step.get("label") or f"step{index + 1}"
            record: dict = {"label": label, "op": op}
            step_started = time.perf_counter()
            try:
                if op == "txn_start":
                    session = client.start_session()
                    session.start_transaction()
                    captured = None
                elif op == "txn_commit":
                    session.commit_transaction()
                    captured = None
                elif op == "txn_abort":
                    session.abort_transaction()
                    captured = None
                elif op == "watch_open":
                    pipeline = _parse(step["pipeline_ejson"]) if step.get("pipeline_ejson") else []
                    stream = db[step["collection"]].watch(
                        pipeline,
                        full_document=step.get("full_document") or None,
                        max_await_time_ms=200,
                    )
                    captured = None
                elif op == "watch_next":
                    captured = _read_events(stream, step.get("max_events") or 1, step.get("timeout_ms") or 3000)
                elif op == "watch_close":
                    stream.close()
                    stream = None
                    captured = None
                elif op == "wait_ttl":
                    time.sleep(min(step.get("seconds") or 70, TTL_WAIT_LIMIT_S))
                    captured = None
                elif op == "command":
                    command = _parse(step["command_ejson"])
                    use_session = session if step.get("in_txn") else None
                    reply = db.command(command, session=use_session)
                    captured = normalize.reduce_result(reply)
                    write_errors = captured.get("writeErrors") if isinstance(captured, dict) else None
                    if write_errors:
                        raise OperationFailure("write error", code=write_errors[0].get("code"))
                else:
                    raise ValueError(f"unknown step op {op}")

                record["ms"] = round((time.perf_counter() - step_started) * 1000, 2)
                if step.get("expect_error"):
                    record.update(status="unexpected_success", captured={"no_error": True}, capture=True)
                else:
                    record["status"] = "ok"
                    if step.get("capture"):
                        record.update(captured=normalize.comparable(captured, bool(step.get("unordered"))), capture=True)
                        record["types"] = normalize.type_signature(captured)
            except (ExecutionTimeout, NetworkTimeout) as exc:
                record.update(status="timeout", message=str(exc)[:300])
                steps_out.append(record)
                outcome = "timeout"
                break
            except (PyMongoError, ValueError, TypeError, AttributeError) as exc:
                code = getattr(exc, "code", None)
                message = str(exc)[:400]
                record["ms"] = round((time.perf_counter() - step_started) * 1000, 2)
                if step.get("expect_error"):
                    record.update(status="ok", captured={"error_code": code}, capture=True, message=message)
                else:
                    record.update(status="error", code=code, message=message)
                    steps_out.append(record)
                    outcome = "failed"
                    break
            steps_out.append(record)
    finally:
        if stream is not None:
            try:
                stream.close()
            except PyMongoError:
                pass
        if session is not None:
            try:
                if session.in_transaction:
                    session.abort_transaction()
            except PyMongoError:
                pass
            session.end_session()
        try:
            client.drop_database(name)
        except PyMongoError:
            pass

    return {"outcome": outcome, "steps": steps_out, "seconds": round(time.perf_counter() - started, 3)}


def _read_events(stream, max_events: int, timeout_ms: int) -> list[dict]:
    events: list[dict] = []
    deadline = time.monotonic() + timeout_ms / 1000
    while len(events) < max_events and time.monotonic() < deadline:
        event = stream.try_next()
        if event is not None:
            events.append(normalize.strip_change_event(event))
    return events


def source_gate(probe: dict, run: dict) -> str | None:
    """MongoDB is the oracle. Returns why a probe can't be trusted, or None if it can.

    A probe must run cleanly on the source, capture something, and predict what it captures.
    That stops a broken probe being blamed on the target, and stops a vacuous one passing as "equal".
    """
    if run["outcome"] != "completed":
        failed = run["steps"][-1]
        return f"step '{failed['label']}' failed on MongoDB: code={failed.get('code')} {failed.get('message', '')}"
    captures = 0
    for step, record in zip(probe["steps"], run["steps"]):
        if record.get("status") == "unexpected_success":
            return f"step '{record['label']}' was expected to fail on MongoDB but succeeded"
        if not record.get("capture"):
            continue
        captures += 1
        expected_text = step.get("expect_ejson")
        if step.get("expect_error"):
            wanted = step.get("expect_error_code")
            if wanted and record["captured"].get("error_code") != wanted:
                return f"step '{record['label']}' expected error {wanted}, MongoDB gave {record['captured']}"
            continue
        if not expected_text:
            return f"step '{record['label']}' captures a result but states no expected value"
        expected = normalize.comparable(_parse(expected_text), bool(step.get("unordered")))
        difference = normalize.first_difference(expected, record["captured"])
        if difference:
            return f"step '{record['label']}' expected a different result on MongoDB: {difference}"
    if captures == 0:
        return "probe captures nothing, so it can't show a difference"
    return None


def classify(source_run: dict, target_run: dict) -> dict:
    """Compares a gated source run with a target run."""
    if target_run["outcome"] == "timeout":
        return {"verdict": "timeout", "at_step": target_run["steps"][-1]["label"]}
    if target_run["outcome"] == "failed":
        failed = target_run["steps"][-1]
        verdict = "unsupported" if _is_unsupported(failed.get("code"), failed.get("message", "")) else "error"
        return {"verdict": verdict, "at_step": failed["label"], "code": failed.get("code"), "message": failed.get("message")}

    source_by_label = {record["label"]: record for record in source_run["steps"]}
    numeric_type_drift = []
    for record in target_run["steps"]:
        counterpart = source_by_label.get(record["label"])
        if not counterpart or not (record.get("capture") or counterpart.get("capture")):
            continue
        difference = normalize.first_difference(counterpart.get("captured"), record.get("captured"))
        if difference:
            return {
                "verdict": "result_diff",
                "silent": True,
                "at_step": record["label"],
                "difference": difference,
            }
        if counterpart.get("types") != record.get("types"):
            numeric_type_drift.append(record["label"])
    outcome = {"verdict": "equal"}
    if numeric_type_drift:
        outcome["numeric_type_drift"] = numeric_type_drift
    return outcome


def diff_probe(source: MongoClient, target: MongoClient, probe: dict) -> dict:
    """Runs one probe on both engines at once, which keeps TTL waits from adding up."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        source_future = pool.submit(run_probe, source, probe, "src")
        target_future = pool.submit(run_probe, target, probe, "tgt")
        source_run, target_run = source_future.result(), target_future.result()

    result = {
        "probe_id": probe["probe_id"],
        "feature_id": probe["feature_id"],
        "title": probe.get("title"),
        "behavior": probe.get("behavior"),
        "source": source_run,
        "target": target_run,
    }
    gate_failure = source_gate(probe, source_run)
    if gate_failure:
        result.update(verdict="probe_invalid", detail={"reason": gate_failure})
        return result
    classified = classify(source_run, target_run)
    result.update(verdict=classified.pop("verdict"), detail=classified)
    return result


def worst_verdict(verdicts: list[str]) -> str:
    for verdict in VERDICT_ORDER:
        if verdict in verdicts:
            return verdict
    return "no_probe"
