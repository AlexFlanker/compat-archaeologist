"""Compat Archaeologist as an ASI:One agent: Agent Chat Protocol, Agentverse mailbox, Interactive Cards.

Asking it to assess a migration runs the real pipeline (evidence, ledger, probes, differential run,
cost model) and answers with cards. Run: .venv/bin/python -m fetch_agent.agent
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)

from archaeologist.config import OUT_DIR, ROOT, env
from fetch_agent import cards

PIPELINE_TIMEOUT_S = 420
AGENT_README = Path(__file__).resolve().parent / "AGENT_README.md"

seed = env("AGENT_SEED")
if not seed:
    raise SystemExit("AGENT_SEED is not set. Run: .venv/bin/python -m fetch_agent.setup_seed")

agent = Agent(
    name="compat-archaeologist",
    seed=seed,
    port=8001,
    mailbox=True,
    publish_agent_details=True,
    readme_path=str(AGENT_README),
    description="Before you migrate off MongoDB: reconstructs how an app really uses MongoDB and proves, with probes and replay, whether a compatible engine behaves the same. Answers with a cost-backed decision.",
)
protocol = Protocol(spec=chat_protocol_spec)
running: set[str] = set()


def _read(profile: str, name: str):
    path = OUT_DIR / profile / name
    return json.loads(path.read_text()) if path.exists() else None


def _message(text: str, card: dict | None = None) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if card:
        content.append(MetadataContent(type="metadata", metadata=card))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


def _intent(text: str) -> dict:
    """Card clicks arrive as JSON; typed requests arrive as prose."""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            selection = json.loads(stripped)
            if isinstance(selection, dict):
                return selection
        except json.JSONDecodeError:
            pass
    lowered = stripped.lower()
    profile = "lite" if any(word in lowered for word in ("lite", "control", "simple", "crud")) else "shoplog"
    for view in ("gaps", "ledger", "latency"):
        if view in lowered or (view == "gaps" and "break" in lowered):
            return {"action": "view", "view": view, "profile": profile}
    if any(word in lowered for word in ("cached", "last result", "decision", "summary")) and "assess" not in lowered:
        return {"action": "decision", "profile": profile}
    return {"action": "assess", "profile": profile}


async def _run_pipeline(profile: str) -> tuple[bool, str]:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "archaeologist.pipeline", "--profile", profile,
        cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=PIPELINE_TIMEOUT_S)
    except asyncio.TimeoutError:
        process.kill()
        return False, "timed out"
    return process.returncode == 0, output.decode(errors="replace")[-600:]


async def _send_decision(ctx: Context, sender: str, profile: str) -> None:
    tco, ledger = _read(profile, "tco.json"), _read(profile, "ledger.json")
    if not (tco and ledger):
        await ctx.send(sender, _message(f"No assessment exists yet for {profile}. Ask me to assess it."))
        return
    narration, card = cards.decision_card(profile, tco, ledger, _read(profile, "scoreboard.json"), _read(profile, "cutover_clean.json"))
    await ctx.send(sender, _message(narration, card))


async def _send_view(ctx: Context, sender: str, profile: str, view: str) -> None:
    if view == "gaps":
        narration, card = cards.gaps_card(profile, _read(profile, "tco.json"))
    elif view == "ledger":
        narration, card = cards.ledger_card(profile, _read(profile, "ledger.json"))
    else:
        narration, card = cards.latency_card(profile, _read(profile, "runs.json"), _read(profile, "explain.json"))
    await ctx.send(sender, _message(narration, card))


@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = "".join(part.text for part in msg.content if isinstance(part, TextContent))
    intent = _intent(text)
    profile = intent.get("profile", "shoplog") if intent.get("profile") in cards.APP_NAME else "shoplog"
    ctx.logger.info(f"request from {sender[:16]}…: {intent}")

    if intent.get("action") == "view":
        await _send_view(ctx, sender, profile, intent.get("view", "gaps"))
        return
    if intent.get("action") == "decision":
        await _send_decision(ctx, sender, profile)
        return

    if profile in running:
        await ctx.send(sender, _message(f"An assessment of {cards.APP_NAME[profile]} is already running. I'll send the result when it's done."))
        return
    running.add(profile)
    try:
        await ctx.send(
            sender,
            _message(
                f"Assessing {cards.APP_NAME[profile]}. I'm reading its data, query log and code, rebuilding its MongoDB usage spec, "
                "then running behavioral probes and a workload replay on MongoDB and FerretDB side by side. This takes about two minutes."
            ),
        )
        ok, tail = await _run_pipeline(profile)
        if not ok:
            await ctx.send(sender, _message(f"The assessment failed: {tail[-300:]}"))
            return
        await _send_decision(ctx, sender, profile)
    finally:
        running.discard(profile)


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    return None


agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
