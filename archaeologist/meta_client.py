"""Calls the Meta Model API and leaves a visible, secret-free record of every request."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone

from openai import APIStatusError, APITimeoutError, OpenAI, RateLimitError

from archaeologist.config import LOGS_DIR, env

BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
MAX_ATTEMPTS = 4


class MetaUnavailable(Exception):
    """The Meta API can't be used right now: no key, no billing, or repeated failures."""


def model_name() -> str:
    return env("META_MODEL", DEFAULT_MODEL)


def _api_key() -> str | None:
    # MODEL_API_KEY is the name Meta's own docs use; META_API_KEY is this project's name.
    return env("META_API_KEY") or env("MODEL_API_KEY")


def is_configured() -> bool:
    return bool(_api_key())


def _record(entry: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    with (LOGS_DIR / "meta_calls.jsonl").open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    if entry["status"] == "ok":
        print(
            f"  [meta] {entry['model']} · {entry['purpose']} · {entry['latency_s']}s · "
            f"{entry['input_tokens']}→{entry['output_tokens']} tok",
            flush=True,
        )
    else:
        print(f"  [meta] {entry['model']} · {entry['purpose']} · {entry['status']}", flush=True)


def structured(
    *,
    purpose: str,
    system: str,
    user: str,
    schema: dict,
    schema_name: str,
    max_tokens: int = 16000,
) -> dict:
    """Returns the parsed JSON object for one structured completion."""
    key = _api_key()
    if not key:
        raise MetaUnavailable("META_API_KEY (or MODEL_API_KEY) is not set")

    client = OpenAI(base_url=BASE_URL, api_key=key, timeout=180, max_retries=0)
    model = model_name()
    prompt_hash = hashlib.sha256((system + user).encode()).hexdigest()[:16]
    last_error = "unknown"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.perf_counter()
        base_entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "purpose": purpose,
            "model": model,
            "prompt_sha256_16": prompt_hash,
            "prompt_chars": len(system) + len(user),
            "attempt": attempt,
        }
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "schema": schema},
                },
                max_completion_tokens=max_tokens,
            )
        except (RateLimitError, APITimeoutError) as exc:
            last_error = type(exc).__name__
        except APIStatusError as exc:
            last_error = f"http_{exc.status_code}"
            if exc.status_code < 500:
                _record({**base_entry, "status": last_error, "latency_s": round(time.perf_counter() - started, 2)})
                raise MetaUnavailable(f"Meta API returned {exc.status_code}") from exc
        else:
            usage = response.usage
            content = response.choices[0].message.content or ""
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                last_error = "invalid_json"
            else:
                _record(
                    {
                        **base_entry,
                        "status": "ok",
                        "latency_s": round(time.perf_counter() - started, 2),
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                    }
                )
                return parsed
        _record({**base_entry, "status": last_error, "latency_s": round(time.perf_counter() - started, 2)})
        time.sleep(2**attempt)

    raise MetaUnavailable(f"Meta API failed after {MAX_ATTEMPTS} attempts: {last_error}")
