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
from fetch_agent import answer, cards, router

PIPELINE_TIMEOUT_S = 420
MAX_MEMORIES = 1000
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
memories: dict[str, router.Memory] = {}


def _read(profile: str, name: str):
    path = OUT_DIR / profile / name
    return json.loads(path.read_text()) if path.exists() else None


def _message(text: str, card: dict | None = None) -> ChatMessage:
    content = [TextContent(type="text", text=text)]
    if card:
        content.append(MetadataContent(type="metadata", metadata=card))
    return ChatMessage(timestamp=datetime.now(timezone.utc), msg_id=uuid4(), content=content)


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
    if view == "reversal":
        narration, card = cards.reversal_card(profile, _read(profile, "tco.json"))
    elif view == "gaps":
        narration, card = cards.gaps_card(profile, _read(profile, "tco.json"))
    elif view == "ledger":
        narration, card = cards.ledger_card(profile, _read(profile, "ledger.json"))
    else:
        narration, card = cards.latency_card(profile, _read(profile, "runs.json"), _read(profile, "explain.json"))
    await ctx.send(sender, _message(narration, card))


async def _send_gap(ctx: Context, sender: str, profile: str, decided: dict, memory: router.Memory) -> None:
    bundle = cards.load_bundle(profile)
    if not bundle["tco"]:
        await ctx.send(sender, _message(f"No assessment exists yet for {profile}. Ask me to assess it."))
        return
    target, key = decided.get("target"), decided.get("gap")
    if not key and decided.get("feature"):
        found = None
        if target:
            found = next(((name, gap["key"]) for name, gap in cards.blocking_gaps(bundle["tco"]) if name == target and gap["feature"] == decided["feature"]), None)
        found = found or cards.find_gap(bundle["tco"], decided["feature"])
        if found:
            target, key = found
    if not key:
        narration, card = cards.gaps_card(profile, bundle["tco"])
        await ctx.send(sender, _message(f"{decided.get('feature', 'That')} isn't a blocking gap in this assessment. {narration}", card))
        return
    narration, card = cards.gap_detail_card(profile, bundle, target, key)
    gap = next((gap for name, gap in cards.blocking_gaps(bundle["tco"]) if name == target and gap["key"] == key), None)
    if gap:
        memory.target, memory.gap, memory.feature = target, key, gap["feature"]
    await ctx.send(sender, _message(narration, card))


async def _send_answer(ctx: Context, sender: str, profile: str, question: str, memory: router.Memory) -> None:
    if not _read(profile, "tco.json"):
        await ctx.send(sender, _message(f"No assessment exists yet for {profile}. Ask me to assess it."))
        return
    focus = f"{memory.target} {memory.feature}" if memory.feature else None
    result = await asyncio.to_thread(answer.answer, question, profile, memory.history, focus)
    ctx.logger.info(f"answer via {result['via']}: {len(result['citations'])} cited, {len(result['dropped'])} dropped")
    narration, card = cards.answer_card(profile, result, _read(profile, "tco.json"))
    memory.remember_turn(question, result["text"])
    await ctx.send(sender, _message(narration, card))


@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(timestamp=datetime.now(timezone.utc), acknowledged_msg_id=msg.msg_id))
    text = "".join(part.text for part in msg.content if isinstance(part, TextContent))
    # ASI:One relays every user through one sender address, so memory is keyed by the conversation session too.
    key = f"{sender}:{ctx.session}"
    memory = memories.setdefault(key, router.Memory())
    if len(memories) > MAX_MEMORIES:
        memories.pop(next(iter(memories)))
    decided = await asyncio.to_thread(router.route, text, memory)
    profile = decided.get("profile") if decided.get("profile") in cards.APP_NAME else "shoplog"
    memory.profile = profile
    ctx.logger.info(f"request from {sender[:16]}… session {str(ctx.session)[:8]}: {decided}")
    action = decided["action"]

    if action in {"reversal", "gaps", "ledger", "latency"}:
        await _send_view(ctx, sender, profile, action)
        return
    if action == "gap_detail":
        await _send_gap(ctx, sender, profile, decided, memory)
        return
    if action == "ask":
        await _send_answer(ctx, sender, profile, decided.get("question") or text, memory)
        return
    if action == "help":
        narration, card = cards.help_card(profile)
        await ctx.send(sender, _message(narration, card))
        return
    if action == "decision":
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
