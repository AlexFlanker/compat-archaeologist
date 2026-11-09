"""A proxy in front of MongoDB that injects exactly one named incompatibility.

Wrapping the real oracle means the planted gap is known, so we can measure which probes catch
which gap. With no mutation the proxy is a transparent pass-through.
Request mutations are pure and generic, so they apply equally to commands sent through replay.
"""

from __future__ import annotations

import copy
import json

from pymongo.errors import OperationFailure

from archaeologist.config import ROOT

CATALOG_PATH = ROOT / "mutants" / "catalog.json"
WRITE_COMMANDS = {"insert", "update", "delete", "findAndModify"}
TEXT_SCORE = {"$meta": "textScore"}


def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text())


# ---- request mutations (each edits a private copy in place) ----

def _index_specs(command: dict) -> list[dict]:
    return [spec for spec in command.get("indexes", []) if isinstance(spec, dict)] if "createIndexes" in command else []


def _collation_ignored(command: dict) -> None:
    command.pop("collation", None)
    for spec in _index_specs(command):
        spec.pop("collation", None)
    for key in ("updates", "deletes"):
        for entry in command.get(key, []) if isinstance(command.get(key), list) else []:
            if isinstance(entry, dict):
                entry.pop("collation", None)


def _validator_dropped(command: dict) -> None:
    if "create" in command or "collMod" in command:
        for key in ("validator", "validationLevel", "validationAction"):
            command.pop(key, None)


def _unique_not_enforced(command: dict) -> None:
    for spec in _index_specs(command):
        spec.pop("unique", None)


def _ttl_never_expires(command: dict) -> None:
    for spec in _index_specs(command):
        spec.pop("expireAfterSeconds", None)


def _nested_pipelines(stage: dict):
    """Yields (container, key) for every sub-pipeline a stage holds."""
    for name, body in stage.items():
        if name == "$facet" and isinstance(body, dict):
            for key in body:
                yield body, key
        elif name in ("$lookup", "$unionWith") and isinstance(body, dict) and isinstance(body.get("pipeline"), list):
            yield body, "pipeline"


def _lookup_drops_unmatched_pipeline(pipeline: list) -> list:
    out = []
    for stage in pipeline:
        if not isinstance(stage, dict):
            out.append(stage)
            continue
        for container, key in _nested_pipelines(stage):
            if isinstance(container[key], list):
                container[key] = _lookup_drops_unmatched_pipeline(container[key])
        out.append(stage)
        lookup = stage.get("$lookup")
        if isinstance(lookup, dict) and lookup.get("as"):
            out.append({"$match": {lookup["as"]: {"$ne": []}}})
    return out


def _lookup_drops_unmatched(command: dict) -> None:
    if isinstance(command.get("pipeline"), list):
        command["pipeline"] = _lookup_drops_unmatched_pipeline(command["pipeline"])


def _strip_text_score(sort):
    if not isinstance(sort, dict):
        return sort
    return {key: value for key, value in sort.items() if value != TEXT_SCORE}


def _textscore_pipeline(pipeline: list) -> list:
    out = []
    for stage in pipeline:
        if not isinstance(stage, dict):
            out.append(stage)
            continue
        for container, key in _nested_pipelines(stage):
            if isinstance(container[key], list):
                container[key] = _textscore_pipeline(container[key])
        if "$sort" in stage:
            stage["$sort"] = _strip_text_score(stage["$sort"])
            if not stage["$sort"]:
                continue
        out.append(stage)
    return out


def _textscore_sort_dropped(command: dict) -> None:
    if "sort" in command:
        command["sort"] = _strip_text_score(command["sort"])
        if not command["sort"]:
            del command["sort"]
    if isinstance(command.get("pipeline"), list):
        command["pipeline"] = _textscore_pipeline(command["pipeline"])


REQUEST_MUTATIONS = {
    "collation_ignored": _collation_ignored,
    "validator_dropped": _validator_dropped,
    "unique_not_enforced": _unique_not_enforced,
    "ttl_never_expires": _ttl_never_expires,
    "lookup_drops_unmatched": _lookup_drops_unmatched,
    "textscore_sort_dropped": _textscore_sort_dropped,
}


def mutate_command(mutation: str | None, command: dict) -> dict:
    """Returns a deep copy of the command with the mutation applied; the input is never touched."""
    out = copy.deepcopy(command)
    edit = REQUEST_MUTATIONS.get(mutation)
    if edit:
        edit(out)
    return out


def _has_stage(command: dict, stage_name: str) -> bool:
    pipeline = command.get("pipeline")
    return isinstance(pipeline, list) and any(isinstance(stage, dict) and stage_name in stage for stage in pipeline)


def mutate_reply(mutation: str | None, command: dict, reply: dict) -> dict:
    out = copy.deepcopy(reply)
    if mutation == "facet_single_bucket" and "aggregate" in command and _has_stage(command, "$facet"):
        for doc in (out.get("cursor") or {}).get("firstBatch", []):
            for position, key in enumerate(list(doc)):
                if position:
                    doc[key] = []
    return out


# ---- proxies ----

def _not_supported() -> OperationFailure:
    return OperationFailure("not supported", code=115)


class MutantSession:
    def __init__(self, inner, mutation: str | None):
        self.inner = inner
        self.mutation = mutation

    def start_transaction(self, *args, **kwargs):
        return self.inner.start_transaction(*args, **kwargs)

    def commit_transaction(self):
        return self.inner.commit_transaction()

    def abort_transaction(self):
        if self.mutation == "abort_commits":
            return self.inner.commit_transaction()
        return self.inner.abort_transaction()

    def end_session(self):
        return self.inner.end_session()

    @property
    def in_transaction(self):
        return self.inner.in_transaction

    def __getattr__(self, name):
        return getattr(self.inner, name)


class MutantCollection:
    def __init__(self, inner, mutation: str | None):
        self.inner = inner
        self.mutation = mutation

    def watch(self, *args, **kwargs):
        if self.mutation == "change_streams_unsupported":
            raise _not_supported()
        return self.inner.watch(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class MutantDatabase:
    def __init__(self, inner, mutation: str | None):
        self.inner = inner
        self.mutation = mutation

    def with_options(self, **kwargs):
        return MutantDatabase(self.inner.with_options(**kwargs), self.mutation)

    def __getitem__(self, name):
        return MutantCollection(self.inner[name], self.mutation)

    def command(self, command, session=None, **kwargs):
        if self.mutation == "change_streams_unsupported" and "aggregate" in command and _has_stage(command, "$changeStream"):
            raise _not_supported()
        if isinstance(session, MutantSession):
            drops_session = self.mutation == "txn_writes_visible" and next(iter(command), None) in WRITE_COMMANDS
            session = None if drops_session else session.inner
        sent = mutate_command(self.mutation, command)
        return mutate_reply(self.mutation, sent, self.inner.command(sent, session=session, **kwargs))

    def __getattr__(self, name):
        return getattr(self.inner, name)


class MutantClient:
    def __init__(self, inner, mutation: str | None):
        self.inner = inner
        self.mutation = mutation

    def drop_database(self, name):
        return self.inner.drop_database(name)

    def __getitem__(self, name):
        return MutantDatabase(self.inner[name], self.mutation)

    def get_database(self, name, **kwargs):
        return MutantDatabase(self.inner.get_database(name, **kwargs), self.mutation)

    def start_session(self, *args, **kwargs):
        return MutantSession(self.inner.start_session(*args, **kwargs), self.mutation)

    def __getattr__(self, name):
        return getattr(self.inner, name)
