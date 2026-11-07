"""Paths and connections shared across the pipeline. Keys stay in the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "evidence"
PROBES_DIR = ROOT / "probes"
OUT_DIR = ROOT / "out"
LOGS_DIR = ROOT / "logs"

load_dotenv(ROOT / ".env")

SOURCE_LABEL = "MongoDB"
TARGET_LABEL = "FerretDB"


def env(name: str, default: str | None = None) -> str | None:
    """Reads a setting, treating an empty value the same as an unset one."""
    value = os.environ.get(name, "").strip()
    return value or default


def source_client(timeout_ms: int = 15000) -> MongoClient:
    return _client(env("SOURCE_URI", "mongodb://127.0.0.1:27017/?directConnection=true"), timeout_ms)


def target_client(timeout_ms: int = 15000) -> MongoClient:
    uri = env("TARGET_URI")
    if not uri:
        raise RuntimeError("TARGET_URI is not set; copy .env.example to .env")
    return _client(uri, timeout_ms)


def _client(uri: str, timeout_ms: int) -> MongoClient:
    return MongoClient(
        uri,
        serverSelectionTimeoutMS=8000,
        socketTimeoutMS=timeout_ms,
        retryWrites=False,
        uuidRepresentation="standard",
        tz_aware=True,
    )


def server_info(client: MongoClient) -> dict:
    """Engine identity recorded with every run, so results stay attributable."""
    info = client.admin.command("buildInfo")
    return {
        "reported_version": info.get("version"),
        "ferretdb_version": (info.get("ferretdb") or {}).get("version"),
        "git": info.get("gitVersion"),
    }
