#!/usr/bin/env python3
"""Check 7: every Cypher text in queries.py contains the clearance predicate, or
is in the explicit allow-list (ensure_schema DDL, the meta/freshness query).

Arrives with the query library (step 4); until queries.py exists this is a skip.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / "src" / "noodle" / "queries.py"
ALLOWLISTED_FUNCTIONS = {"q12_freshness", "ensure_schema"}
PREDICATE = "clearance <= $clr"


def main() -> int:
    if not QUERIES.is_file():
        print("check_clearance: queries.py not present yet (step 4); skipping")
        return 0

    tree = ast.parse(QUERIES.read_text(encoding="utf-8"))
    problems: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            texts = [
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            has_query = any(
                any(kw in t.upper() for kw in ("MATCH ", "CALL ", "CREATE ", "RETURN ", "MERGE "))
                for t in texts
            )
            if not has_query:
                return
            if node.name in ALLOWLISTED_FUNCTIONS:
                return
            joined = "\n".join(texts)
            if PREDICATE not in joined:
                problems.append(f"{QUERIES.name}:{node.lineno} {node.name} lacks '{PREDICATE}'")

    Visitor().visit(tree)
    if problems:
        for problem in problems:
            print(f"check_clearance: {problem}", file=sys.stderr)
        return 1
    print("check_clearance: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
