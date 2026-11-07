#!/usr/bin/env bash
# Fails if a secret is tracked or staged. Runs before every push.
# Prints only the variable name or file path, never a matched value.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
failed=0

# Rule: no .env file may ever be tracked, except the example.
tracked_env="$(git ls-files | grep -E '(^|/)\.env(\..+)?$' | grep -v '\.env\.example$' || true)"
if [[ -n "$tracked_env" ]]; then
  echo "FAIL: env file is tracked: $tracked_env"
  failed=1
fi

# Rule: the real values from .env must not appear in any tracked or staged file.
if [[ -f .env ]]; then
  for name in META_API_KEY SUBQ_API_KEY MONGODB_URI_ATLAS; do
    value="$(grep -E "^${name}=" .env | head -1 | cut -d= -f2- || true)"
    if [[ ${#value} -ge 12 ]] && git grep --cached -qF -- "$value"; then
      echo "FAIL: value of $name found in a staged or tracked file"
      failed=1
    fi
  done
fi

# Rule: nothing shaped like a credential, even from another source.
patterns=(
  'mongodb(\+srv)?://[^<[:space:]"'"'"']+:[^<[:space:]"'"'"']+@[^[:space:]"'"'"']*mongodb\.net'
  'LLM[|_][A-Za-z0-9_-]{8,}[|_][A-Za-z0-9_-]{8,}'
  'AKIA[0-9A-Z]{16}'
  'sk-[A-Za-z0-9_-]{24,}'
  'Bearer [A-Za-z0-9_-]{24,}'
)
for pattern in "${patterns[@]}"; do
  hits="$(git grep --cached -lE -- "$pattern" -- ':!scripts/check_secrets.sh' || true)"
  if [[ -n "$hits" ]]; then
    echo "FAIL: credential-shaped string in: $hits"
    failed=1
  fi
done

if [[ $failed -ne 0 ]]; then
  echo "Secret check failed."
  exit 1
fi
echo "Secret check passed."
