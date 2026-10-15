#!/usr/bin/env python3
"""Check 6: no dataset name appears in src/, the schema, the agent instructions
or the config. Dataset names belong in data/, tests and golden questions only.

Heuristic: a phrase from the dataset is "distinctive" if it has a space, a digit
or a hyphen, or is at least 10 characters. That keeps the check company-agnostic
while catching real leaks (names like "Ada Wren", keys like "PLAT-882", ids like
"auth-migration"). The list of phrases never enters the repo as code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "synthetic" / "dataset.json"
SCAN_SUFFIXES = {".py", ".yaml", ".yml", ".txt", ".md"}
SCAN_ROOTS = [ROOT / "src", ROOT / "noodle.yaml"]


def _distinctive(phrase: str) -> bool:
    phrase = phrase.strip()
    if len(phrase) < 4:
        return False
    if any(ch.isspace() for ch in phrase) or any(ch.isdigit() for ch in phrase):
        return True
    if "-" in phrase or "_" in phrase or "#" in phrase:
        return True
    return len(phrase) >= 10


def _collect_phrases(dataset: dict) -> set[str]:
    phrases: set[str] = set()

    def add(value):
        if isinstance(value, str):
            if _distinctive(value):
                phrases.add(value.strip())
        elif isinstance(value, list):
            for item in value:
                add(item)

    for key, records in (dataset.get("sections") or {}).items():
        for record in records or []:
            if not isinstance(record, dict):
                continue
            for field in ("name", "key", "title", "channel", "id"):
                add(record.get(field))
            rid = record.get("id")
            if isinstance(rid, str) and ":" in rid:
                add(rid.split(":", 1)[1])
    return phrases


def _iter_source_files():
    for target in SCAN_ROOTS:
        if target.is_file() and target.suffix in SCAN_SUFFIXES:
            yield target
        elif target.is_dir():
            for path in target.rglob("*"):
                if path.is_file() and path.suffix in SCAN_SUFFIXES:
                    yield path


def main() -> int:
    if not DATASET.is_file():
        print("check_no_company_names: dataset not present yet (step 2); skipping")
        return 0

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    phrases = _collect_phrases(dataset)
    if not phrases:
        print("check_no_company_names: no distinctive phrases found")
        return 0

    leaks: list[str] = []
    lowered = sorted(phrases, key=len, reverse=True)
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        for phrase in lowered:
            if phrase.lower() in text:
                leaks.append(f"{path.relative_to(ROOT)}: contains '{phrase}'")

    if leaks:
        for leak in leaks:
            print(f"check_no_company_names: {leak}", file=sys.stderr)
        return 1
    print(f"check_no_company_names: ok ({len(phrases)} phrases, no leaks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
