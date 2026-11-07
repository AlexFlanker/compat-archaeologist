"""Creates the agent's identity seed in .env once. The seed is a secret and is never printed."""

from __future__ import annotations

import secrets

from archaeologist.config import ROOT, env


def main() -> int:
    if env("AGENT_SEED"):
        print("AGENT_SEED already set; nothing to do.")
        return 0
    path = ROOT / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    lines = [line for line in lines if not line.startswith("AGENT_SEED=")]
    lines.append(f"AGENT_SEED={secrets.token_hex(32)}")
    path.write_text("\n".join(lines) + "\n")
    print("AGENT_SEED written to .env (git-ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
