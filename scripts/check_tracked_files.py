#!/usr/bin/env python3
"""Check 8: no secrets and no generated files are tracked by git.

Generated: run logs, caches, byte-code, virtualenvs, local env files. Secrets:
.env, key material, or a tracked file that looks like it holds a private key.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_SUBSTRINGS = (
    "__pycache__/",
    ".pyc",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
)
FORBIDDEN_EXACT = {".env", "noodle.yaml.bak"}
# Built from parts so this file does not match its own secrets scan.
_SECRET_HEADER = b"-----BEGIN " + b"PRIVATE KEY-----"
_SECRET_RSA = b"-----BEGIN RSA " + b"PRIVATE KEY-----"
SECRET_MARKERS = (_SECRET_HEADER, _SECRET_RSA)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


def main() -> int:
    probe = _git("rev-parse", "--is-inside-work-tree")
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        print("check_tracked_files: not a git work tree; skipping")
        return 0

    files = [line for line in _git("ls-files").stdout.splitlines() if line.strip()]
    problems: list[str] = []

    for name in files:
        if name in FORBIDDEN_EXACT or any(part in name for part in FORBIDDEN_SUBSTRINGS):
            problems.append(f"tracked forbidden file: {name}")
            continue
        if name.startswith("runs/") and name != "runs/.gitkeep":
            problems.append(f"tracked run artifact: {name}")
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        if path.suffix in {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".sh", ".env", ""}:
            data = path.read_bytes()
            for marker in SECRET_MARKERS:
                if marker in data:
                    problems.append(f"tracked file looks like a private key: {name}")

    if problems:
        for problem in problems:
            print(f"check_tracked_files: {problem}", file=sys.stderr)
        return 1
    print("check_tracked_files: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
