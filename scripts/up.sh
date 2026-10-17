#!/usr/bin/env bash
# Bring Noodle up locally: load secrets safely, check the toolchain and config,
# start FalkorDB and wait on its health check (no fixed sleeps), run the
# idempotent ensure_schema(), ingest the synthetic dataset, then serve the API so
# a fresh checkout reaches a working /ask (08 step 15).
#
#   scripts/up.sh              # bring everything up and serve in the foreground
#   scripts/up.sh --no-serve   # set up only (graph + schema + dataset loaded)
set -euo pipefail

SERVE=1
for arg in "$@"; do
  case "$arg" in
    --no-serve) SERVE=0 ;;
    -h|--help) printf 'usage: scripts/up.sh [--no-serve]\n'; exit 0 ;;
    *) printf '[up] ERROR: unknown argument %s\n' "$arg" >&2; exit 1 ;;
  esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

log() { printf '%s\n' "[up] $*"; }
die() { printf '%s\n' "[up] ERROR: $*" >&2; exit 1; }

# --- secrets -----------------------------------------------------------------
# Load .env without breaking values that contain spaces. Never printed.
load_env_file() {
  local file="$1" line key value
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    key="${line%%=*}"
    value="${line#*=}"
    # trim surrounding whitespace on the key
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ -z "$key" ]] && continue
    # strip one layer of matching quotes, keep inner spaces
    if [[ "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "$key=$value"
  done < "$file"
}

load_env_file "$ROOT/.env"

# --- python ------------------------------------------------------------------
PYTHON=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
fi
[[ -n "$PYTHON" ]] || die "Python 3.11+ not found"

PY_VER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PYTHON" - <<'PY' || die "Python 3.11+ required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
log "python $PY_VER ($PYTHON)"

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# --- docker ------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "docker not found"
docker info >/dev/null 2>&1 || die "docker daemon not reachable"

# --- config and provider keys ------------------------------------------------
"$PYTHON" - <<'PY' || die "configuration is invalid (see message above)"
import sys
from noodle.config import ConfigError, load_settings
try:
    settings = load_settings()
except ConfigError as exc:
    print(f"[up] {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"[up] config ok: {settings.source} (graph {settings.graph.name})")
PY

found_provider=0
for var in ANTHROPIC_API_KEY GOOGLE_API_KEY OPENAI_API_KEY GROQ_API_KEY; do
  if [[ -n "${!var:-}" ]]; then found_provider=1; fi
done
if [[ "$found_provider" -eq 0 ]]; then
  log "WARNING: no provider key found; /ask needs one (see .env.example)"
fi

# --- FalkorDB ----------------------------------------------------------------
log "starting FalkorDB"
docker compose up -d falkordb >/dev/null

log "waiting for FalkorDB health check"
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' noodle-falkordb 2>/dev/null || echo none)"
  case "$status" in
    healthy) log "FalkorDB healthy"; break ;;
    none) ;; # no healthcheck reported yet; keep waiting
  esac
  sleep 1
done
status="$(docker inspect --format '{{.State.Health.Status}}' noodle-falkordb 2>/dev/null || echo unknown)"
[[ "$status" == "healthy" ]] || die "FalkorDB did not become healthy (status: $status)"

# --- schema ------------------------------------------------------------------
"$PYTHON" -m noodle.bootstrap

# --- dataset -----------------------------------------------------------------
DATA="$ROOT/data/synthetic/dataset.json"
if [[ -f "$DATA" ]]; then
  log "ingesting $DATA"
  "$PYTHON" "$ROOT/scripts/ingest.py" "$DATA"
else
  log "WARNING: no dataset at data/synthetic/dataset.json; /ask will find nothing"
fi

if [[ "$SERVE" -eq 0 ]]; then
  cat <<'NEXT'

[up] Setup complete (graph + schema + dataset). Start the API with:
  scripts/serve.sh
NEXT
  exit 0
fi

cat <<'NEXT'

[up] Next steps in another terminal:
  - Run the checks:        scripts/check.sh
  - Run the demo:          scripts/demo.sh
  - Stop the graph:        scripts/db.sh down

[up] Serving the API now (Ctrl-C to stop)...
NEXT

exec "$ROOT/scripts/serve.sh"
