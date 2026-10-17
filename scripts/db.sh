#!/usr/bin/env bash
# Manage the local FalkorDB: scripts/db.sh up | ingest [source] | status | down | reset
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${FALKORDB_PORT:-6379}"
BROWSER_PORT="${FALKORDB_BROWSER_PORT:-3000}"

PYTHON=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/../lore/.venv/bin/python" ]]; then
  PYTHON="$ROOT/../lore/.venv/bin/python"
elif command -v python3.11 >/dev/null 2>&1; then
  PYTHON="$(command -v python3.11)"
else
  PYTHON="$(command -v python3 || true)"
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

case "${1:-}" in
  up)
    echo "Starting FalkorDB..."
    docker compose up -d falkordb
    echo "Waiting for FalkorDB health check..."
    for _ in $(seq 1 60); do
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' noodle-falkordb 2>/dev/null || echo none)"
      if [[ "$status" == "healthy" ]]; then
        break
      fi
      sleep 1
    done
    echo "FalkorDB healthy."
    echo "Running schema DDL and bootstrap..."
    "$PYTHON" -m noodle.bootstrap
    echo
    echo "FalkorDB:    redis://localhost:${PORT}/0"
    echo "Browser UI:  http://localhost:${BROWSER_PORT}"
    ;;
  ingest)
    SOURCE="${2:-data/synthetic/dataset.json}"
    "$PYTHON" "$ROOT/scripts/ingest.py" "$SOURCE"
    ;;
  status)
    docker compose ps
    ;;
  down)
    docker compose down
    ;;
  reset)
    docker compose down --volumes
    ;;
  *)
    echo "usage: scripts/db.sh up | ingest [source] | status | down | reset" >&2
    exit 1
    ;;
esac
