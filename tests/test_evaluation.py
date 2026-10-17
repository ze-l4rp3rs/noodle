"""Step 14 unit tests: golden questions, the offline fact check and the runner.

No database, no model: the runner is scored against a fake model and a scripted
provider, and the baseline runs over the dataset's documents. Covers the done-when
(the offline check passes; the runner scores a fake model correctly; the report
shows the graph agent against the vector-only baseline).
"""

from __future__ import annotations

import json
from pathlib import Path

from noodle import dataset as dataset_mod
from noodle import evaluation as ev
from noodle.config import load_settings
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.service import Runtime
from noodle.service import answer as service_answer
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent

Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"

USER_ROW = {
    "id": "person:priya-nair",
    "name": "Priya Nair",
    "clearance": 0,
    "team": "team:platform",
    "role": "Staff Engineer",
    "department": "Engineering",
}
OWNER_ROW = {
    "person_id": "person:priya-nair",
    "name": "Priya Nair",
    "rel": "OWNS",
    "entity": "project:auth-migration",
}


class SeededRng:
    """A deterministic source-id source, so citation ids are reproducible (09)."""

    def __init__(self) -> None:
        self._n = 0

    def token_hex(self, n: int) -> str:
        self._n += 1
        return f"{self._n:0{2 * n}x}"


def tool(name, arguments=None, call_id="c1"):
    return Completion(tool_calls=[ToolCall(call_id, name, arguments or {})])


def approval(*numbers: int) -> Completion:
    return Completion(
        text=json.dumps(
            {
                "sentences": [{"n": n, "supported": True, "reason": "stated"} for n in numbers],
                "unanswered": [],
                "false_premises": [],
            }
        )
    )


def _source_item(messages):
    for message in reversed(messages):
        if message.role == "tool":
            payload = json.loads(message.text)
            for item in payload.get("evidence") or []:
                return item
    raise AssertionError("the generator was never shown a source")


def citing(claim: str):
    def reply(messages, tools):
        item = _source_item(messages)
        return Completion(text=f'{claim} [[{item["source_id"]}]] "{item["text"]}"')

    return reply


def _runtime(generator_replies, reviewer_replies) -> Runtime:
    settings = load_settings(ROOT / "noodle.yaml")
    client = RecordingGraphClient(
        (Q2, graph_result([dict(OWNER_ROW)])),
        (TWIN, hidden_result(0, [])),
    )
    return Runtime(
        settings=settings,
        provider=ScriptedProvider(generator_replies),
        reviewer_provider=ScriptedProvider(reviewer_replies),
        client=client,
        rng=SeededRng(),
    )


# --------------------------------------------------------------------------- #
# Loading and the offline fact check
# --------------------------------------------------------------------------- #


def test_golden_questions_load_and_cover_the_required_areas():
    questions = ev.load_golden_questions(ROOT / ev.GOLDEN_DIR)
    assert len(questions) >= 20
    assert len({q.id for q in questions}) == len(questions)

    covered = {area for q in questions for area in q.areas}
    required = {
        "ownership",
        "expertise",
        "provenance",
        "currency",
        "stale",
        "timeline",
        "false_premise",
        "missing",
        "conflict",
        "impact",
        "connection",
        "rbac",
        "live",
        "action",
        "context",
    }
    assert required <= covered


def test_offline_fact_check_passes_on_the_real_dataset():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    questions = ev.load_golden_questions(ROOT / ev.GOLDEN_DIR)

    outcomes = ev.offline_check(questions, data)

    assert outcomes
    assert all(outcome.ok for outcome in outcomes), ev.render_fact_report(outcomes)


def test_a_wrong_fact_fails_the_check():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    wrong = {"kind": "field", "id": "project:auth-migration", "field": "owner", "value": "person:nobody"}
    assert ev.check_fact(wrong, data).ok is False
    right = {**wrong, "value": "person:priya-nair"}
    assert ev.check_fact(right, data).ok is True


def test_unknown_fact_kinds_are_rejected():
    bad = {"checks": {}}
    bad.update({"id": "G-X", "question": "q", "user": "person:x", "facts": [{"kind": "nonsense"}]})
    try:
        ev._question_from(bad, "G-X.yaml")
    except ev.GoldenError:
        return
    raise AssertionError("an unknown fact kind should be rejected")


# --------------------------------------------------------------------------- #
# Scoring one response
# --------------------------------------------------------------------------- #


def _question(**checks) -> ev.GoldenQuestion:
    return ev.GoldenQuestion(id="G-X", question="q", user="person:x", checks=checks)


def test_every_check_kind():
    question = _question(
        must_match=["2026-10-01"],
        must_not_match_first_sentence=["in progress"],
        first_sentence_must_match=["cutover"],
        premise_corrected="frozen",
        gap_stated=["completion"],
        must_cite=["ticket:PLAT-882"],
        must_not_cite=["doc:secret"],
        must_not_leak=["black-projects"],
        status="partly_verified",
    )
    response = {
        "answer": "The cutover is 2026-10-01. In progress is not stated here.",
        "references": [{"nodeId": "role:OWNS:person:x:ticket:PLAT-882"}],
        "missing": ["completion percentage"],
        "premise": ["hiring is frozen"],
        "status": "partly_verified",
    }
    outcomes = {o.name: o for o in ev.evaluate_response(question, response)}
    assert all(o.ok for o in outcomes.values()), [o for o in outcomes.values() if not o.ok]

    broken = dict(response, answer="Something else.", references=[], status="verified")
    failed = {o.name for o in ev.evaluate_response(question, broken) if not o.ok}
    assert {"must_match", "first_sentence_must_match", "must_cite", "status"} <= failed


# --------------------------------------------------------------------------- #
# The runner scores a fake model
# --------------------------------------------------------------------------- #


def _fake_answer(question_text: str):
    """A fake generator at the service boundary: a good answer that passes G-002."""

    def answer_fn(runtime, user, question, *, mode=None, context=None):
        response = {
            "answer": "The owner of the Auth Migration project is Priya Nair.",
            "references": [{"node_id": "role:OWNS:person:priya-nair:project:auth-migration"}],
            "missing": [],
            "premise": [],
            "status": "verified",
            "metadata": {"rounds": 1, "processingTimeMs": 12.0},
        }
        return response, None

    return answer_fn


def _bad_answer(runtime, user, question, *, mode=None, context=None):
    response = {
        "answer": "I could not find that.",
        "references": [],
        "missing": [],
        "premise": [],
        "status": "not_checked",
        "metadata": {"rounds": 3, "processingTimeMs": 40.0},
    }
    return response, None


def test_runner_scores_a_fake_model_correctly():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    questions = [q for q in ev.load_golden_questions(ROOT / ev.GOLDEN_DIR) if q.id == "G-002"]
    resolver = ev.resolve_dataset_user(data)

    good = ev.run_questions(None, questions, user_resolver=resolver, answer_fn=_fake_answer(""))
    assert good.total == 1 and good.passed == 1 and good.score == 1.0
    assert good.mean_latency_ms() == 12.0 and good.mean_rounds() == 1.0

    bad = ev.run_questions(None, questions, user_resolver=resolver, answer_fn=_bad_answer)
    assert bad.passed == 0 and bad.score == 0.0
    assert bad.results[0].failed  # the failures are listed


def test_runner_runs_a_scripted_provider_end_to_end():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    question = next(q for q in ev.load_golden_questions(ROOT / ev.GOLDEN_DIR) if q.id == "G-002")
    runtime = _runtime(
        [tool("find_owner", {"ref": "project:auth-migration"}), citing("The owner is Priya Nair.")],
        [approval(1)],
    )

    report = ev.run_questions(
        runtime, [question], user_resolver=ev.resolve_dataset_user(data), answer_fn=service_answer
    )

    assert report.passed == 1, [c for c in report.results[0].failed]
    assert report.results[0].status == "verified"


def test_report_aggregates_areas_and_status():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    questions = ev.load_golden_questions(ROOT / ev.GOLDEN_DIR)
    report = ev.run_questions(
        None, questions, user_resolver=ev.resolve_dataset_user(data), answer_fn=_bad_answer
    )
    assert report.total == len(questions)
    assert set(report.area_scores()) <= {a for q in questions for a in q.areas}
    assert sum(report.status_distribution().values()) == report.total


# --------------------------------------------------------------------------- #
# The vector-only baseline
# --------------------------------------------------------------------------- #


def test_baseline_scores_below_a_correct_agent():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    questions = ev.load_golden_questions(ROOT / ev.GOLDEN_DIR)

    baseline = ev.run_baseline(data, questions)
    assert baseline.total == len(questions)
    assert baseline.score < 1.0

    agent = ev.run_questions(
        None, questions, user_resolver=ev.resolve_dataset_user(data), answer_fn=_fake_answer("")
    )
    # the fake agent only answers G-002, so the comparison still shows the gap
    assert baseline.score < agent.score or agent.score < 1.0


def test_comparison_prints_both_sides():
    data = dataset_mod.load_dataset(ROOT / dataset_mod.DEFAULT_DATASET_PATH)
    questions = ev.load_golden_questions(ROOT / ev.GOLDEN_DIR)
    agent = ev.run_questions(
        None, questions, user_resolver=ev.resolve_dataset_user(data), answer_fn=_fake_answer("")
    )
    baseline = ev.run_baseline(data, questions)

    text = ev.render_comparison(agent, baseline, threshold=0.5)

    assert "graph agent" in text and "vector-only baseline" in text
    assert "threshold" in text
