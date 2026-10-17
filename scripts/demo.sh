#!/usr/bin/env bash
# Run the demo scenarios against a live Noodle server (08 step 15).
#
#   scripts/demo.sh                     # http://localhost:8000
#   scripts/demo.sh --base-url URL
#
# Needs a running server (scripts/up.sh) with a provider key. Not part of check.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -n "$PYTHON" ]] || { echo "[demo] ERROR: Python 3.11+ not found" >&2; exit 1; }

exec "$PYTHON" "$ROOT/scripts/demo.py" "$@"
