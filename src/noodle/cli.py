"""The command line: one question through the shared answer service (07).

The CLI and ``/ask`` render the same response from the same run, so this module
only parses arguments, builds the runtime and prints the response object as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional, Sequence

from .queries import UserCtx
from .service import GraphDownError, Runtime, answer, build_runtime


def cli_answer(
    runtime: Runtime,
    user: UserCtx,
    question: str,
    *,
    mode: str = "fast",
    context: Optional[str] = None,
) -> dict[str, Any]:
    """The CLI's answer: the same call ``/ask`` makes."""

    response, _ = answer(runtime, user, question, mode=mode, context=context)
    return response


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="noodle", description="Ask the company brain.")
    parser.add_argument("question", help="the question to answer")
    parser.add_argument("--user", default="person:local", help="the caller identity (dev only)")
    parser.add_argument("--clearance", type=int, default=None, help="override the caller's clearance")
    parser.add_argument("--mode", default="fast", choices=["fast", "heavy"])
    parser.add_argument("--context", default=None, help="extra context for the question")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.clearance is None:
        user = UserCtx(id=args.user)
    else:
        user = UserCtx(id=args.user, clearance=args.clearance)
    runtime = build_runtime()
    try:
        response = cli_answer(runtime, user, args.question, mode=args.mode, context=args.context)
    except GraphDownError as exc:
        sys.stderr.write(f"noodle: {exc}\n")
        return 2
    json.dump(response, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


__all__ = ["build_parser", "cli_answer", "main"]
