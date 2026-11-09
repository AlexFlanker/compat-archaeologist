import json
from pathlib import Path

import pytest
from pymongo import MongoClient
from pymongo.errors import OperationFailure

from archaeologist import mutants
from archaeologist.config import env, source_client
from archaeologist.probe_runner import diff_probe

SAMPLE_COMMANDS = [
    {"find": "c", "filter": {}, "sort": {"s": {"$meta": "textScore"}}, "collation": {"locale": "en"}},
    {"createIndexes": "c", "indexes": [{"key": {"a": 1}, "name": "i", "unique": True, "expireAfterSeconds": 5, "collation": {"locale": "en"}}]},
    {"create": "c", "validator": {"$jsonSchema": {}}},
    {"aggregate": "o", "pipeline": [{"$lookup": {"from": "c", "as": "x", "pipeline": []}}, {"$facet": {"a": []}}], "cursor": {}},
]


def test_catalog_has_ten_entries_six_train_four_heldout():
    catalog = mutants.load_catalog()
    assert [m["id"] for m in catalog] == [
        "abort_commits", "txn_writes_visible", "collation_ignored", "validator_dropped", "unique_not_enforced",
        "ttl_never_expires", "change_streams_unsupported", "lookup_drops_unmatched", "facet_single_bucket", "textscore_sort_dropped",
    ]
    assert [m["split"] for m in catalog].count("train") == 6
    assert [m["split"] for m in catalog].count("heldout") == 4
    assert [m["feature_ids"] for m in catalog] == [["F4"], ["F4"], ["F6"], ["F8"], ["F7"], ["F7"], ["F5"], ["F1"], ["F1"], ["F9"]]
    assert all(m["kind"] in {"request", "reply", "session", "watch"} and m["description"] for m in catalog)


def test_collation_stripped_from_create_indexes_and_find():
    out = mutants.mutate_command("collation_ignored", SAMPLE_COMMANDS[1])
    assert "collation" not in out["indexes"][0] and out["indexes"][0]["unique"] is True
    out = mutants.mutate_command("collation_ignored", SAMPLE_COMMANDS[0])
    assert "collation" not in out and "sort" in out


def test_validator_dropped_from_create_and_collmod():
    assert "validator" not in mutants.mutate_command("validator_dropped", {"create": "c", "validator": {"a": 1}})
    assert "validator" not in mutants.mutate_command("validator_dropped", {"collMod": "c", "validator": {"a": 1}, "validationLevel": "strict"})


def test_unique_and_ttl_stripped_from_index_specs():
    cmd = SAMPLE_COMMANDS[1]
    out = mutants.mutate_command("unique_not_enforced", cmd)
    assert "unique" not in out["indexes"][0] and out["indexes"][0]["expireAfterSeconds"] == 5
    out = mutants.mutate_command("ttl_never_expires", cmd)
    assert "expireAfterSeconds" not in out["indexes"][0] and out["indexes"][0]["unique"] is True
    assert cmd["indexes"][0]["unique"] is True  # input untouched


def test_lookup_mutation_inserts_match_after_lookup():
    out = mutants.mutate_command("lookup_drops_unmatched", {"aggregate": "o", "pipeline": [{"$lookup": {"from": "c", "as": "cust", "pipeline": []}}, {"$unwind": "$cust"}], "cursor": {}})
    assert out["pipeline"][1] == {"$match": {"cust": {"$ne": []}}}
    assert out["pipeline"][2] == {"$unwind": "$cust"}


def test_lookup_mutation_recurses_into_facet_and_nested_pipelines():
    look = {"$lookup": {"from": "c", "as": "x", "localField": "a", "foreignField": "b"}}
    out = mutants.mutate_command("lookup_drops_unmatched", {"aggregate": "o", "pipeline": [{"$facet": {"a": [look]}}, {"$unionWith": {"coll": "z", "pipeline": [look]}}], "cursor": {}})
    assert out["pipeline"][0]["$facet"]["a"][1] == {"$match": {"x": {"$ne": []}}}
    assert out["pipeline"][1]["$unionWith"]["pipeline"][1] == {"$match": {"x": {"$ne": []}}}
    nested = mutants.mutate_command("lookup_drops_unmatched", {"aggregate": "o", "pipeline": [{"$lookup": {"from": "c", "as": "x", "pipeline": [look]}}], "cursor": {}})
    assert nested["pipeline"][0]["$lookup"]["pipeline"][1] == {"$match": {"x": {"$ne": []}}}
    assert nested["pipeline"][1] == {"$match": {"x": {"$ne": []}}}


def test_textscore_sort_dropped_removes_meta_sort_and_empties():
    ts = {"$meta": "textScore"}
    out = mutants.mutate_command("textscore_sort_dropped", {"find": "c", "sort": {"s": ts}})
    assert "sort" not in out
    out = mutants.mutate_command("textscore_sort_dropped", {"find": "c", "sort": {"s": ts, "n": 1}})
    assert out["sort"] == {"n": 1}
    out = mutants.mutate_command("textscore_sort_dropped", {"aggregate": "c", "pipeline": [{"$sort": {"s": ts}}, {"$facet": {"a": [{"$sort": {"s": ts, "k": -1}}]}}], "cursor": {}})
    assert out["pipeline"] == [{"$facet": {"a": [{"$sort": {"k": -1}}]}}]


def test_facet_single_bucket_keeps_first_key_only():
    cmd = {"aggregate": "o", "pipeline": [{"$facet": {"a": [], "b": []}}], "cursor": {}}
    reply = {"ok": 1.0, "cursor": {"firstBatch": [{"a": [1, 2], "b": [3]}], "id": 0}}
    out = mutants.mutate_reply("facet_single_bucket", cmd, reply)
    assert out["cursor"]["firstBatch"] == [{"a": [1, 2], "b": []}]
    assert reply["cursor"]["firstBatch"][0]["b"] == [3]
    assert mutants.mutate_reply("facet_single_bucket", {"find": "c"}, reply) == reply


def test_no_mutation_is_identity():
    for cmd in SAMPLE_COMMANDS:
        out = mutants.mutate_command(None, cmd)
        assert out == cmd and out is not cmd
        assert mutants.mutate_reply(None, cmd, {"ok": 1}) == {"ok": 1}
        for m in mutants.load_catalog():
            before = json.dumps(cmd, sort_keys=True)
            mutants.mutate_command(m["id"], cmd)
            assert json.dumps(cmd, sort_keys=True) == before


class FakeSession:
    in_transaction = False

    def __init__(self):
        self.calls = []

    def start_transaction(self):
        self.calls.append("start")

    def commit_transaction(self):
        self.calls.append("commit")

    def abort_transaction(self):
        self.calls.append("abort")

    def end_session(self):
        self.calls.append("end")


class FakeCollection:
    def watch(self, *a, **kw):
        return "stream"


class FakeDb:
    def __init__(self):
        self.commands = []

    def command(self, command, session=None, **kw):
        self.commands.append((command, session))
        return {"ok": 1.0}

    def __getitem__(self, name):
        return FakeCollection()

    def with_options(self, **kw):
        return self


class FakeClient:
    some_attr = "delegated"

    def __init__(self):
        self.db = FakeDb()
        self.session = FakeSession()
        self.dropped = []

    def drop_database(self, name):
        self.dropped.append(name)

    def __getitem__(self, name):
        return self.db

    def start_session(self):
        return self.session


def test_abort_commits_calls_inner_commit():
    client = FakeClient()
    session = mutants.MutantClient(client, "abort_commits").start_session()
    session.start_transaction()
    session.abort_transaction()
    session.end_session()
    assert client.session.calls == ["start", "commit", "end"]
    plain = FakeClient()
    mutants.MutantClient(plain, None).start_session().abort_transaction()
    assert plain.session.calls == ["abort"]


def test_txn_writes_visible_drops_session_on_writes():
    client = FakeClient()
    proxy = mutants.MutantClient(client, "txn_writes_visible")
    session = proxy.start_session()
    db = proxy["d"]
    db.command({"insert": "c", "documents": []}, session=session)
    db.command({"find": "c"}, session=session)
    assert [s for _, s in client.db.commands] == [None, client.session]
    kept = mutants.MutantClient(client, None)
    kept["d"].command({"insert": "c"}, session=session)
    assert client.db.commands[-1][1] is client.session


def test_change_stream_watch_raises_115():
    proxy = mutants.MutantClient(FakeClient(), "change_streams_unsupported")
    with pytest.raises(OperationFailure) as err:
        proxy["d"]["c"].watch([])
    assert err.value.code == 115
    with pytest.raises(OperationFailure):
        proxy["d"].command({"aggregate": "c", "pipeline": [{"$changeStream": {}}], "cursor": {}})
    assert mutants.MutantClient(FakeClient(), None)["d"]["c"].watch([]) == "stream"


def test_proxy_delegates_and_drops():
    client = FakeClient()
    proxy = mutants.MutantClient(client, None)
    assert proxy.some_attr == "delegated"
    proxy.drop_database("x")
    assert client.dropped == ["x"]
    assert isinstance(proxy["d"].with_options(codec_options=None), mutants.MutantDatabase)


def _require():
    client = MongoClient(env("SOURCE_URI", "mongodb://127.0.0.1:27017/?directConnection=true"), serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except Exception:
        pytest.skip("MongoDB not reachable")


def _probes():
    return json.loads((Path(__file__).parent / "fixtures" / "handwritten_probes.json").read_text())


@pytest.mark.integration
def test_zero_mutation_proxy_matches_mongodb_on_handwritten_probes():
    _require()
    source = source_client(timeout_ms=5000)
    target = mutants.MutantClient(source_client(timeout_ms=5000), None)
    for probe in _probes():
        verdict = diff_probe(source, target, probe, namespaces=("mt0s", "mt0t"))["verdict"]
        assert verdict in {"equal", "probe_invalid"}, (probe["probe_id"], verdict)


@pytest.mark.integration
def test_abort_commits_is_caught_by_txn_abort_fixture():
    _require()
    source = source_client(timeout_ms=5000)
    target = mutants.MutantClient(source_client(timeout_ms=5000), "abort_commits")
    probe = next(p for p in _probes() if p["probe_id"] == "T.txn_abort")
    assert diff_probe(source, target, probe, namespaces=("mt1s", "mt1t"))["verdict"] == "result_diff"
