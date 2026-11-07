from __future__ import annotations

import threading

from pymongo.database import Database

WATCHED_OPERATIONS = ["update", "replace"]


def run_sync(db: Database, stop: threading.Event, ready: threading.Event | None = None) -> int:
    processed = 0
    state = db.sync_state.find_one({"_id": "inventory"})
    resume_after = state["token"] if state else None
    pipeline = [{"$match": {"operationType": {"$in": WATCHED_OPERATIONS}}}]
    with db.inventory.watch(
        pipeline,
        full_document="updateLookup",
        resume_after=resume_after,
        max_await_time_ms=200,
    ) as stream:
        if ready:
            ready.set()
        while not stop.is_set():
            change = stream.try_next()
            if change is None:
                continue
            db.inventory_events.insert_one(
                {
                    "sku": change["documentKey"]["_id"],
                    "on_hand": change["fullDocument"]["on_hand"],
                    "at": change["clusterTime"],
                }
            )
            db.sync_state.replace_one({"_id": "inventory"}, {"_id": "inventory", "token": stream.resume_token}, upsert=True)
            processed += 1
    return processed
