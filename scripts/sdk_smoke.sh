#!/usr/bin/env bash
# Wrapper for scripts/sdk_smoke.py: loads .env, ensures the openai SDK is
# installed in a project-local venv (scripts/.venv), then runs the Python
# smoke test. Used by tasks.md §5.1.
#
# Usage:
#   scripts/sdk_smoke.sh                                            # local
#   BASE_URL=https://qwen-embed.your-domain.com/v1 scripts/sdk_smoke.sh  # public
#
# The local venv avoids the PEP 668 "externally-managed-environment" error
# Homebrew Python raises against system-wide pip installs. First run creates
# it (~5s); subsequent runs reuse it.

set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  set -a
  . ./.env
  set +a
fi

VENV=scripts/.venv
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "creating venv at $VENV ..." >&2
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
fi
"$VENV/bin/pip" install --quiet --upgrade openai

exec "$VENV/bin/python" scripts/sdk_smoke.py "$@"
