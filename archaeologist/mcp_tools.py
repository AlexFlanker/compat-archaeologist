"""The agent's database toolset: the official MongoDB MCP Server, called over stdio.

The server runs read-only. Connection strings and Atlas API credentials pass to it through its
environment and are never logged. Every tool call is recorded with its name, arguments' keys
and timing in logs/mcp_calls.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from archaeologist.config import LOGS_DIR, env

SERVER_PACKAGE = "mongodb-mcp-server@3.2.2"


class MCPUnavailable(Exception):
    pass


def _record(entry: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / "mcp_calls.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def _server(connection_string: str | None, with_atlas_api: bool) -> StdioServerParameters:
    environment = {key: value for key, value in os.environ.items() if key in {"PATH", "HOME", "TMPDIR", "USER"}}
    environment["MDB_MCP_READ_ONLY"] = "true"
    environment["MDB_MCP_TELEMETRY"] = "disabled"
    if connection_string:
        environment["MDB_MCP_CONNECTION_STRING"] = connection_string
    if with_atlas_api:
        client_id, secret = env("MDB_MCP_API_CLIENT_ID"), env("MDB_MCP_API_CLIENT_SECRET")
        if not (client_id and secret):
            raise MCPUnavailable("MDB_MCP_API_CLIENT_ID / MDB_MCP_API_CLIENT_SECRET are not set")
        environment["MDB_MCP_API_CLIENT_ID"] = client_id
        environment["MDB_MCP_API_CLIENT_SECRET"] = secret
    return StdioServerParameters(command="npx", args=["-y", SERVER_PACKAGE, "--readOnly"], env=environment)


@asynccontextmanager
async def _session(connection_string: str | None, with_atlas_api: bool = False):
    async with stdio_client(_server(connection_string, with_atlas_api)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _text_of(result) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content if getattr(block, "text", None))


async def _call_many(connection_string: str | None, calls: list[tuple[str, dict]], with_atlas_api: bool, label: str) -> list[dict]:
    outputs = []
    async with _session(connection_string, with_atlas_api) as session:
        available = {tool.name: tool for tool in (await session.list_tools()).tools}
        for name, arguments in calls:
            started = time.perf_counter()
            entry = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "server": SERVER_PACKAGE,
                "engine": label,
                "tool": name,
                "argument_keys": sorted(arguments),
            }
            if name not in available:
                _record({**entry, "status": "tool_not_available"})
                outputs.append({"tool": name, "ok": False, "error": "tool not available"})
                continue
            result = await session.call_tool(name, arguments)
            entry["latency_s"] = round(time.perf_counter() - started, 2)
            ok = not getattr(result, "isError", False)
            _record({**entry, "status": "ok" if ok else "tool_error"})
            print(f"  [mcp] {name} on {label} · {entry['latency_s']}s · {'ok' if ok else 'error'}", flush=True)
            outputs.append({"tool": name, "ok": ok, "text": _text_of(result)})
    return outputs


def call_tools(connection_string: str | None, calls: list[tuple[str, dict]], *, label: str, with_atlas_api: bool = False) -> list[dict]:
    try:
        return asyncio.run(asyncio.wait_for(_call_many(connection_string, calls, with_atlas_api, label), timeout=180))
    except MCPUnavailable:
        raise
    except Exception as exc:  # The server is an external process; any failure means the toolset is unavailable.
        raise MCPUnavailable(f"MCP server failed: {type(exc).__name__}") from exc


def list_tool_schemas(connection_string: str | None, with_atlas_api: bool = False) -> dict:
    async def _list():
        async with _session(connection_string, with_atlas_api) as session:
            return {tool.name: tool.inputSchema for tool in (await session.list_tools()).tools}

    return asyncio.run(asyncio.wait_for(_list(), timeout=180))
