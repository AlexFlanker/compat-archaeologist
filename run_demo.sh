#!/usr/bin/env bash
# One command, end to end. Usage: ./run_demo.sh [--profile shoplog|lite] [--refresh-probes] [--with-self-check]
set -euo pipefail

cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Add your keys to enable Muse, SubQ and Atlas."
fi
if [[ ! -x .venv/bin/python ]]; then
  uv venv --python 3.11 .venv -q
  uv pip install -q --python .venv/bin/python -r requirements.txt
fi

docker compose up -d --wait >/dev/null
exec .venv/bin/python -m archaeologist.pipeline "$@"
