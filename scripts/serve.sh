#!/usr/bin/env bash
# Serve the HTTP API locally (07, step 15): scripts/serve.sh
#
# Builds the runtime from noodle.yaml and runs the FastAPI app with uvicorn. The
# host and port come from the api section of the config. Requires the `api`
# extra (fastapi, uvicorn) and at least one provider key in the environment.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '%s\n' "[serve] $*"; }
die() { printf '%s\n' "[serve] ERROR: $*" >&2; exit 1; }

# Load .env without breaking values that contain spaces. Never printed.
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    key="${line%%=*}"; value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"; key="${key%"${key##*[![:space:]]}"}"
    [[ -z "$key" ]] && continue
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then value="${value:1:${#value}-2}"; fi
    export "$key=$value"
  done < "$ROOT/.env"
fi

PYTHON=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -n "$PYTHON" ]] || die "Python 3.11+ not found"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

"$PYTHON" - <<'PY' || die "the 'api' extra is not installed (pip install -e '.[api]')"
import uvicorn  # noqa: F401
PY

HOST="$("$PYTHON" - <<'PY'
from noodle.config import load_settings
print(load_settings().api.host)
PY
)"
PORT="$("$PYTHON" - <<'PY'
from noodle.config import load_settings
print(load_settings().api.port)
PY
)"

log "listening on http://${HOST}:${PORT} (docs at /docs)"
exec "$PYTHON" -m uvicorn --factory "noodle.server:create_server" --host "$HOST" --port "$PORT"
