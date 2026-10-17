#!/usr/bin/env python
"""Run the golden-question evaluation (09).

Two modes:

* ``--offline`` confirms every fact a golden question relies on against the
  dataset -- no model, no graph. This is the check the build must pass.
* the default answers every question through the shared service and scores the
  responses, then runs the vector-only baseline over the same questions and
  prints both side by side. It needs real provider keys and a loaded graph.

Scores are printed, never written to disk (09). Run with
``PYTHONPATH=src python scripts/run_eval.py --offline``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from noodle import auth  # noqa: E402
from noodle import dataset as dataset_mod
from noodle import evaluation as ev
from noodle.service import build_runtime  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_eval", description="Score the golden questions.")
    parser.add_argument("--golden", default=str(ROOT / ev.GOLDEN_DIR), help="golden question dir")
    parser.add_argument("--dataset", default=str(ROOT / dataset_mod.DEFAULT_DATASET_PATH))
    parser.add_argument("--provider", default=None, help="provider name (default: auto-select)")
    parser.add_argument("--mode", default=None, choices=["fast", "heavy"], help="force a mode")
    parser.add_argument("--only", default=None, help="comma-separated question ids")
    parser.add_argument("--threshold", type=float, default=0.8, help="minimum graph-agent score")
    parser.add_argument("--offline", action="store_true", help="fact check only; no model or graph")
    return parser


def _select(questions, only: str | None):
    if not only:
        return questions
    wanted = {part.strip() for part in only.split(",") if part.strip()}
    return [q for q in questions if q.id in wanted]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data = dataset_mod.load_dataset(args.dataset)
    questions = _select(ev.load_golden_questions(args.golden), args.only)
    if not questions:
        print("run_eval: no questions selected", file=sys.stderr)
        return 2

    facts = ev.offline_check(questions, data)
    print(ev.render_fact_report(facts))
    if not all(outcome.ok for outcome in facts):
        print("run_eval: the offline fact check failed; fix the golden questions", file=sys.stderr)
        return 1
    if args.offline:
        return 0

    runtime = build_runtime(provider_name=args.provider)
    if not runtime.graph_up():
        print("run_eval: FalkorDB is unreachable; cannot run the graph agent", file=sys.stderr)
        return 2

    def resolve(person_id: str):
        return auth.resolve_user(runtime.client, person_id, graph_up=True)

    agent = ev.run_questions(
        runtime, questions, user_resolver=resolve, mode=args.mode, context_dir=ROOT / ev.CONTEXT_DIR
    )
    baseline = ev.run_baseline(data, questions)
    print()
    print(ev.render_comparison(agent, baseline, threshold=args.threshold))
    return 0 if agent.score >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
