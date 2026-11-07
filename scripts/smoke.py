"""Confirms both engines answer, and reports what each one says it is."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from archaeologist.config import server_info, source_client, target_client  # noqa: E402


def main() -> int:
    failed = False
    for label, make_client in (("source", source_client), ("target", target_client)):
        try:
            client = make_client()
            client.admin.command("ping")
            print(f"{label}: ok {server_info(client)}")
        except Exception as exc:  # A smoke test reports every failure rather than stopping at one.
            failed = True
            print(f"{label}: FAILED {type(exc).__name__}: {str(exc)[:200]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
