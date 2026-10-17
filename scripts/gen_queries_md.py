#!/usr/bin/env python
"""Write queries.md from the query library.

The query file is the single source of truth (03): this regenerates the
submission artifact from ``noodle.queries.render_markdown()`` so the two cannot
drift. Run with ``PYTHONPATH=src python scripts/gen_queries_md.py``.

``--check`` regenerates in memory and compares against the committed file,
exiting non-zero when they differ. ``check.sh`` item 9 uses it so a stale
``queries.md`` fails the build instead of shipping (step 15).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from noodle import queries  # noqa: E402

OUT = ROOT / "queries.md"


def render() -> str:
    text = queries.render_markdown()
    if not text.endswith("\n"):
        text += "\n"
    return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate queries.md from the query library.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and fail if the committed file differs",
    )
    args = parser.parse_args(argv)

    text = render()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print("queries.md is out of date; run scripts/gen_queries_md.py", file=sys.stderr)
            return 1
        print(f"queries.md is up to date ({len(text.splitlines())} lines)")
        return 0

    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
