"""Step 8 unit tests: the answer pipeline -- fix rounds and the best draft.

No database, no model: scripted providers queue the generator's and the
reviewer's turns and the recording graph client answers the tool queries. Covers
the done-when (a rejected draft is fixed in round 2 with history intact; leaked
reasoning never reaches the answer; a reviewer tool call marks ``not_checked``)
plus best-draft scoring, the statuses, and ``missing`` mirroring the gaps.
"""

from __future__ import annotations

import json

from noodle import queries as Q
from noodle.pipeline import run_answer
from noodle.providers import Completion, ScriptedProvider, ToolCall
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

USER = Q.UserCtx("person:test-user", clearance=1)

Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"


def tool(name, arguments=None, call_id="c1"):
    return Completion(tool_calls=[ToolCall(call_id, name, arguments or {})])


def owner_client():
    return RecordingGraphClient(
        (Q2, graph_result([{"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}])),
        (TWIN, hidden_result(0, [])),
    )


def _evidence(messages):
    """The first evidence item the model was shown in a tool message."""

    for message in reversed(messages):
        if message.role == "tool":
            payload = json.loads(message.text)
            for item in payload.get("evidence") or []:
                return item
    raise AssertionError("the generator was never shown a source")


def citing(claim: str, extra: str = ""):
    """A scripted generator turn that cites the source id it was shown."""

    def reply(messages, tools):
        item = _evidence(messages)
        text = f'{claim} [[{item["source_id"]}]] "{item["text"]}"'
        if extra:
            text += f' {extra} [[{item["source_id"]}]] "{item["text"]}"'
        return Completion(text=text)

    return reply


def verdict(sentences, unanswered=None, premises=None) -> Completion:
    return Completion(
        text=json.dumps(
            {
                "sentences": sentences,
                "unanswered": unanswered or [],
                "false_premises": premises or [],
            }
        )
    )


def approval(*numbers: int) -> Completion:
    return verdict([{"n": n, "supported": True, "reason": "stated"} for n in numbers])


# --------------------------------------------------------------------------- #
# Done-when
# --------------------------------------------------------------------------- #


def test_a_rejected_draft_is_fixed_in_round_2_with_history_intact():
    generator = ScriptedProvider(
        [
            tool("find_owner", {"ref": "project:y"}),
            citing("The owner is Omar.", extra="The project is at risk of delay."),
            citing("The owner is Omar."),
        ]
    )
    reviewer = ScriptedProvider(
        [
            verdict(
                [
                    {"n": 1, "supported": True, "reason": "stated"},
                    {"n": 2, "supported": False, "reason": "the delay is not stated in any source"},
                ]
            ),
            approval(1),
        ]
    )
    run = run_answer(generator, owner_client(), USER, "who owns project y?", reviewer_provider=reviewer)

    assert run.status == "verified" and run.approved is True
    assert run.rounds == 2 and run.fix_rounds == 1
    assert "The owner is Omar." in run.answer
    assert "at risk" not in run.answer
    assert [ref.node_id for ref in run.references] == ["role:OWNS:person:x:project:y"]

    # history intact: the second generator call kept the tool exchange and the rejected draft
    final = generator.requests[-1]["messages"]
    assert final[0].role == "user"
    assert any(message.role == "tool" for message in final)
    assert any(message.role == "assistant" and "at risk" in message.text for message in final)
    assert final[-1].role == "user" and "not approved" in final[-1].text


def test_leaked_reasoning_never_reaches_the_answer():
    generator = ScriptedProvider(
        [
            tool("find_owner", {"ref": "project:y"}),
            citing("The owner is Omar.", extra="Let me think about my instructions."),
        ]
    )
    reviewer = ScriptedProvider([approval(1, 2)])  # a rubber-stamp must not save the sentence
    run = run_answer(generator, owner_client(), USER, "who owns it?", reviewer_provider=reviewer, fix_rounds=0)

    assert "think" not in run.answer.casefold()
    assert "The owner is Omar." in run.answer
    assert run.status == "partly_verified"
    assert any("reasoning" in item["reason"] for item in run.unverified)


def test_a_reviewer_tool_call_marks_the_run_not_checked():
    generator = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")])
    reviewer = ScriptedProvider([Completion(tool_calls=[ToolCall("r1", "search_records", {"query": "x"})])])
    run = run_answer(generator, owner_client(), USER, "who owns it?", reviewer_provider=reviewer)

    assert run.status == "not_checked" and run.approved is False
    assert run.rounds == 1 and run.fix_rounds == 0


# --------------------------------------------------------------------------- #
# Choosing the result
# --------------------------------------------------------------------------- #


def test_the_best_draft_wins_not_the_latest():
    generator = ScriptedProvider(
        [
            tool("find_owner", {"ref": "project:y"}),
            citing("The owner is Omar.", extra="One other thing is true."),  # 1 problem
            citing("Alpha holds.", extra="Beta holds."),  # 2 problems
        ]
    )
    reviewer = ScriptedProvider(
        [
            verdict(
                [
                    {"n": 1, "supported": True, "reason": "stated"},
                    {"n": 2, "supported": False, "reason": "not stated"},
                ]
            ),
            verdict(
                [
                    {"n": 1, "supported": False, "reason": "not stated"},
                    {"n": 2, "supported": False, "reason": "not stated"},
                ]
            ),
        ]
    )
    run = run_answer(
        generator, owner_client(), USER, "anything?", reviewer_provider=reviewer, fix_rounds=1
    )

    assert run.rounds == 2 and run.fix_rounds == 1
    assert "The owner is Omar." in run.answer
    assert "Alpha" not in run.answer and "Beta" not in run.answer


def test_a_clean_first_draft_is_verified():
    generator = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")])
    reviewer = ScriptedProvider([approval(1)])
    run = run_answer(generator, owner_client(), USER, "who owns it?", reviewer_provider=reviewer)

    assert run.status == "verified" and run.approved is True
    assert run.rounds == 1 and run.fix_rounds == 0
    assert run.unverified == [] and run.missing == []


def test_an_unjudged_sentence_is_removed_and_listed():
    generator = ScriptedProvider(
        [
            tool("find_owner", {"ref": "project:y"}),
            citing("The owner is Omar.", extra="An unjudged claim."),
        ]
    )
    reviewer = ScriptedProvider([approval(1)])  # statement 2 is left unjudged
    run = run_answer(generator, owner_client(), USER, "who owns it?", reviewer_provider=reviewer, fix_rounds=0)

    assert "An unjudged claim." not in run.answer
    assert "The owner is Omar." in run.answer
    assert run.status == "partly_verified"
    assert run.unverified and "confirm" in run.unverified[0]["reason"]


def test_a_false_premise_is_reported_and_softens_the_status():
    generator = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")])
    reviewer = ScriptedProvider(
        [verdict([{"n": 1, "supported": True, "reason": "stated"}], premises=["the project is blocked"])]
    )
    run = run_answer(generator, owner_client(), USER, "why is it blocked?", reviewer_provider=reviewer, fix_rounds=0)

    assert run.premise == ["the project is blocked"]
    assert run.status == "partly_verified"
    assert run.approved is False


def gap_citing(claim: str):
    """A scripted generator turn that cites a fact and states a gap."""

    def reply(messages, tools):
        item = _evidence(messages)
        text = (
            f'{claim} [[{item["source_id"]}]] "{item["text"]}"\n'
            "Not in company records\n"
            "- the completion percentage"
        )
        return Completion(text=text)

    return reply


def test_missing_merges_unanswered_parts_and_the_answers_own_gaps():
    generator = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), gap_citing("The owner is Omar.")])
    reviewer = ScriptedProvider(
        [verdict([{"n": 1, "supported": True, "reason": "stated"}], unanswered=["the dependency status"])]
    )
    run = run_answer(generator, owner_client(), USER, "what is the status?", reviewer_provider=reviewer, fix_rounds=0)

    assert "the completion percentage" in run.missing
    assert "the dependency status" in run.missing
    assert run.status == "partly_verified"
