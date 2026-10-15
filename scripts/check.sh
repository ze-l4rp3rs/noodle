#!/usr/bin/env bash
# Run before any change is accepted. Grows with the build; every check that is
# not yet applicable says so explicitly rather than passing silently.
set -uo pipefail

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
[[ -n "$PYTHON" ]] || { echo "check: python not found"; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

FAILED=0
pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
skip() { printf '  \033[33mSKIP\033[0m %s\n' "$1"; }

echo "== 1. compile and lint =="
if "$PYTHON" -m compileall -q src tests >/dev/null 2>&1; then
  pass "everything compiles"
else
  "$PYTHON" -m compileall -q src tests
  fail "compilation failed"
fi
if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
  if "$PYTHON" -m ruff check src tests >/dev/null 2>&1; then
    pass "ruff: no undefined names or import errors"
  else
    "$PYTHON" -m ruff check src tests
    fail "ruff found problems"
  fi
else
  skip "ruff not installed"
fi

echo "== 2. every module imports =="
if "$PYTHON" - <<'PY'
import importlib, pkgutil, sys
import noodle
ok = True
for mod in pkgutil.walk_packages(noodle.__path__, "noodle."):
    try:
        importlib.import_module(mod.name)
    except Exception as exc:  # noqa: BLE001
        print(f"import failed: {mod.name}: {exc}")
        ok = False
raise SystemExit(0 if ok else 1)
PY
then pass "all modules import"; else fail "a module failed to import"; fi

echo "== 3. simulated CLI+API run (misquote repaired) =="
if [[ -f "$ROOT/src/noodle/queries.py" ]]; then
  skip "plain query module present but pipeline arrives later"
else
  skip "answer pipeline arrives at step 7/9"
fi

echo "== 4. unit suite =="
if "$PYTHON" -m pytest -q -m "not integration" >/dev/null 2>&1; then
  "$PYTHON" -m pytest -q -m "not integration" | tail -1
  pass "unit suite"
else
  "$PYTHON" -m pytest -q -m "not integration"
  fail "unit suite"
fi
if "$PYTHON" - <<'PY'
import sys
try:
    from noodle.config import load_settings
    from noodle.graph import open_reader
    s = load_settings()
    ok = open_reader(s.graph.url, s.graph.name).ping()
except Exception:
    ok = False
raise SystemExit(0 if ok else 1)
PY
then
  if "$PYTHON" -m pytest -q -m integration >/dev/null 2>&1; then
    "$PYTHON" -m pytest -q -m integration | tail -1
    pass "integration suite"
  else
    "$PYTHON" -m pytest -q -m integration
    fail "integration suite"
  fi
else
  skip "integration suite (FalkorDB not running)"
fi

echo "== 5. compose and startup config =="
if "$PYTHON" "$ROOT/scripts/check_config_paths.py"; then pass "config paths exist"; else fail "config paths"; fi

echo "== 6. no company-specific names in src, schema, config =="
if "$PYTHON" "$ROOT/scripts/check_no_company_names.py"; then pass "no dataset names leaked into code"; else fail "company name leak"; fi

echo "== 7. clearance predicate in every query =="
if "$PYTHON" "$ROOT/scripts/check_clearance.py"; then pass "clearance check"; else fail "clearance check"; fi

echo "== 8. no secrets or generated files tracked =="
if "$PYTHON" "$ROOT/scripts/check_tracked_files.py"; then pass "no tracked secrets/generated files"; else fail "tracked secrets/generated files"; fi

echo "== 9. queries.md up to date =="
if [[ -f "$ROOT/src/noodle/queries.py" ]]; then
  if [[ -f "$ROOT/queries.md" ]]; then
    pass "queries.md present (regeneration checked at step 15)"
  else
    fail "queries.md missing"
  fi
else
  skip "queries.py arrives at step 4"
fi

echo
if [[ "$FAILED" -eq 0 ]]; then
  echo "check.sh: PASS"
else
  echo "check.sh: FAIL"
fi
exit "$FAILED"
