#!/usr/bin/env python
"""Write queries.md from the query library.

The query file is the single source of truth (03): this regenerates the
submission artifact from ``noodle.queries.render_markdown()`` so the two cannot
drift. Run with ``PYTHONPATH=src python scripts/gen_queries_md.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from noodle import queries  # noqa: E402

OUT = ROOT / "queries.md"


def main() -> int:
    text = queries.render_markdown()
    if not text.endswith("\n"):
        text += "\n"
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
