"""Saves ledger, runs and memo to MongoDB Atlas. Atlas stores results only; it's never benchmarked."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from archaeologist.config import env


def save(profile: str, run_id: str, ledger: dict, runs: dict, tco: dict, memo: str) -> dict:
    uri = env("MONGODB_URI_ATLAS")
    if not uri:
        return {"saved": False, "reason": "MONGODB_URI_ATLAS is not set"}
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        db = client["compat_archaeologist"]
        # Results contain field names like $lookup, so full payloads are stored as JSON text.
        db["assessments"].replace_one(
            {"_id": f"{profile}:{run_id}"},
            {
                "_id": f"{profile}:{run_id}",
                "profile": profile,
                "run_id": run_id,
                "saved_at": datetime.now(timezone.utc),
                "decisions": {name: info["decision"] for name, info in tco["targets"].items()},
                "net_base": {name: info["economics"]["net"]["base"] for name, info in tco["targets"].items()},
                "memo": memo,
                "ledger_json": json.dumps(ledger, default=str),
                "tco_json": json.dumps(tco, default=str),
                "runs_json": json.dumps(runs, default=str),
            },
            upsert=True,
        )
        return {"saved": True, "database": "compat_archaeologist", "collection": "assessments"}
    except PyMongoError as exc:
        # The driver's message can include host details, so only the error type is reported.
        return {"saved": False, "reason": f"Atlas write failed: {type(exc).__name__}"}
