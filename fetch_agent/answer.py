"""Answers a free question from the evidence pack only. Every citation is checked; an uncited answer is never sent."""

from __future__ import annotations

import json

from archaeologist import evidence_pack, meta_client

ANSWER_TIMEOUT_S = 60

ANSWER_PROMPT = """You answer questions about one migration assessment produced by Compat Archaeologist.
You may use ONLY the facts below. Each fact starts with its id in brackets.

Rules:
- Every claim must come from a fact; put the ids you used in "citations" (ids only, without brackets).
- If the facts don't cover the question, set "answerable" to false and say briefly what the assessment does cover. Never guess numbers.
- Values labeled inferred or Muse estimates are estimates; say so when you use them.
- Answer in the language of the question. Be concise: at most 120 words, plain text, no markdown tables.
- The engine names are MongoDB (source), FerretDB (observed by probes and replay) and AWS DocumentDB (inferred from documentation only).

FACTS
"""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "answerable": {"type": "boolean"},
    },
    "required": ["answer", "citations", "answerable"],
    "additionalProperties": False,
}

NOT_COVERED = ("The evidence from this assessment doesn't cover that, so I won't guess. I can answer questions about the verdict, "
               "each gap (F1 to F11), the probes, the cost model and its assumptions, the cutover rehearsal and the hidden business rules.")


def answer(question: str, profile: str, history: list[dict] | None = None, focus: str | None = None) -> dict:
    """Returns {text, citations, answerable, dropped, via}."""
    pack = evidence_pack.build(profile)
    user = {"question": question, "earlier_turns": history or [], "gap_in_view": focus}
    try:
        result = meta_client.structured(
            purpose="agent_answer", system=ANSWER_PROMPT + evidence_pack.render(pack),
            user=json.dumps(user, ensure_ascii=False), schema=ANSWER_SCHEMA, schema_name="answer",
            max_tokens=4000, timeout_s=ANSWER_TIMEOUT_S, attempts=2,
        )
    except meta_client.MetaUnavailable as exc:
        return {"text": f"I can't reach the language model right now ({exc}), so I can only show the cards. Try \"what breaks\" or \"show the reversal\".",
                "citations": [], "answerable": False, "dropped": [], "via": "unavailable"}
    kept, dropped = evidence_pack.check_citations(pack, result.get("citations") or [])
    if result.get("answerable") and not kept:
        return {"text": NOT_COVERED, "citations": [], "answerable": False, "dropped": dropped, "via": "rejected_uncited"}
    facts = {fact["id"]: fact["text"] for fact in pack["facts"]}
    return {"text": result["answer"].strip(), "citations": [{"id": item, "text": facts[item]} for item in kept],
            "answerable": bool(result.get("answerable")), "dropped": dropped, "via": "muse"}
