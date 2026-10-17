#!/usr/bin/env python3
"""Check 5: compose and startup configuration are valid and every local path
they mention exists. Company-agnostic.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:  # PyYAML is a project dependency; import lazily for a clean message.
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    print("check_config_paths: PyYAML not installed", file=sys.stderr)
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parent.parent


def _load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def main() -> int:
    problems: list[str] = []

    config = ROOT / "noodle.yaml"
    if not config.is_file():
        problems.append(f"missing {config.relative_to(ROOT)}")
    else:
        settings = _load_yaml(config)
        for section in ("graph", "api", "logs"):
            if section not in settings:
                problems.append(f"noodle.yaml missing section '{section}'")
        logs_folder = (settings.get("logs") or {}).get("folder")
        if logs_folder:
            folder = (ROOT / logs_folder).resolve()
            if not folder.exists():
                problems.append(f"logs folder does not exist: {logs_folder}")

    compose = ROOT / "docker-compose.yml"
    if not compose.is_file():
        problems.append("missing docker-compose.yml")
    else:
        spec = _load_yaml(compose)
        services = spec.get("services") or {}
        if "falkordb" not in services:
            problems.append("docker-compose.yml has no 'falkordb' service")
        else:
            fb = services["falkordb"] or {}
            if not fb.get("healthcheck"):
                problems.append("falkordb service has no healthcheck")
            if not fb.get("image"):
                problems.append("falkordb service has no image")

    for script in ("scripts/up.sh", "scripts/serve.sh", "scripts/demo.sh", "scripts/check.sh", ".env.example"):
        if not (ROOT / script).exists():
            problems.append(f"missing {script}")

    if problems:
        for problem in problems:
            print(f"check_config_paths: {problem}", file=sys.stderr)
        return 1
    print("check_config_paths: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
