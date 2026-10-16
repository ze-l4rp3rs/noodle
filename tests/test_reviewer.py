"""Step 8 unit tests: the reviewer and its verdict (05 section 3, 09 matrix).

No database, no model: a scripted provider queues the reviewer's replies. Covers
the 09 reviewer rows -- verdict parsing with surrounding text, one retry, an
unjudged sentence counting as unconfirmed, a reviewer tool call marking the draft
``not checked``, and an inference sentence being rejected -- plus the R1 to R7
instruction and the strictness rules.
"""

from __future__ import annotations

import json

from noodle.loop import ToolCallRecord
from noodle.prompts import REVIEWER_INSTRUCTION
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.reviewer import (
    Review,
    SentenceVerdict,
    build_review_request,
    parse_verdict,
    review,
    reviewer_problems,
)
from noodle.sources import SourceRegistry
from noodle.verification import Sentence


def sent(number: int, text: str = "a statement", kind: str = "fact") -> Sentence:
    return Sentence(number=number, text=text, citations=[], kind=kind)


def verdict_text(sentences=None, unanswered=None, premises=None) -> str:
    return json.dumps(
        {
            "sentences": sentences if sentences is not None else [],
            "unanswered": unanswered or [],
            "false_premises": premises or [],
        }
    )


def approval(*numbers: int) -> Completion:
    return Completion(
        text=verdict_text([{"n": n, "supported": True, "reason": "stated"} for n in numbers])
    )


# --------------------------------------------------------------------------- #
# The instruction
# --------------------------------------------------------------------------- #


def test_reviewer_instruction_states_r1_to_r7_and_the_json_contract():
    for tag in ("R1", "R2", "R3", "R4", "R5", "R6", "R7"):
        assert tag in REVIEWER_INSTRUCTION
    for key in ('"sentences"', "supported", "unanswered", "false_premises"):
        assert key in REVIEWER_INSTRUCTION


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_verdict_parses_with_surrounding_text():
    text = (
        "Here is my verdict.\n"
        + verdict_text([{"n": 1, "supported": True, "reason": "ok"}], ["missing detail"])
        + "\nLet me know if you need more."
    )
    review_ = parse_verdict(text)
    assert review_ is not None
    assert review_.sentences[1].supported is True
    assert review_.unanswered == ["missing detail"]


def test_parse_tolerates_string_numbers_and_booleans():
    review_ = parse_verdict('{"sentences":[{"n":"2","supported":"false","reason":"inferred"}]}')
    assert review_ is not None
    assert review_.sentences[2].supported is False
    assert review_.sentences[2].reason == "inferred"


def test_parse_keeps_braces_inside_a_reason():
    review_ = parse_verdict(
        '{"sentences":[{"n":1,"supported":true,"reason":"quoted {like} this"}],"unanswered":[]}'
    )
    assert review_ is not None
    assert review_.sentences[1].reason == "quoted {like} this"


def test_parse_returns_none_for_unusable_text():
    assert parse_verdict("no verdict at all") is None
    assert parse_verdict('{"unanswered": []}') is None
    assert parse_verdict('{"sentences": []}') is None


def test_unsupported_numbers_are_reported():
    review_ = Review({1: SentenceVerdict(1, True), 2: SentenceVerdict(2, False, "x")})
    assert review_.unsupported_numbers() == [2]


# --------------------------------------------------------------------------- #
# The reviewer call: retry and the tool-call escape
# --------------------------------------------------------------------------- #


def test_an_unusable_verdict_is_retried_once():
    provider = ScriptedProvider([Completion(text="not a verdict"), approval(1)])
    review_ = review(provider, registry=SourceRegistry(), sentences=[sent(1)])
    assert review_ is not None and review_.sentences[1].supported
    assert len(provider.requests) == 2
    # the retry continued the same conversation
    roles = [message.role for message in provider.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_a_second_unusable_verdict_gives_up():
    provider = ScriptedProvider([Completion(text="nope"), Completion(text="still nope")])
    assert review(provider, registry=SourceRegistry(), sentences=[sent(1)]) is None
    assert len(provider.requests) == 2


def test_a_reviewer_tool_call_marks_the_draft_not_checked():
    provider = ScriptedProvider(
        [Completion(tool_calls=[ToolCall("c1", "search_records", {"query": "x"})])]
    )
    assert review(provider, registry=SourceRegistry(), sentences=[sent(1)]) is None
    assert len(provider.requests) == 1  # a tool call is not retried


def test_the_reviewer_is_offered_no_tools():
    provider = ScriptedProvider([approval(1)])
    review(provider, registry=SourceRegistry(), sentences=[sent(1)])
    assert provider.requests[0]["tools"] is None


def test_review_request_carries_sources_trace_and_numbered_draft():
    record = ToolCallRecord("find_owner", {"ref": "project:y"}, "ok", sources=["role:OWNS:person:x:project:y"])
    request = build_review_request(
        '[[09a121fe]] doc "a record" body', [record], [sent(1, "one"), sent(2, "two")]
    )
    assert "[[09a121fe]]" in request
    assert "find_owner" in request and "project:y" in request
    assert "1. one" in request and "2. two" in request


def test_review_request_notes_when_no_sources_were_returned():
    request = build_review_request("", [], [sent(1)])
    assert "no sources" in request


# --------------------------------------------------------------------------- #
# Strictness: a verdict becomes problems
# --------------------------------------------------------------------------- #


def test_an_unjudged_sentence_counts_as_unconfirmed():
    review_ = Review({1: SentenceVerdict(1, True, "ok")})
    problems = reviewer_problems([sent(1, "supported"), sent(2, "not judged")], review_)
    assert [problem.number for problem in problems] == [2]
    assert problems[0].kind == "reviewer_unjudged"


def test_a_rejected_sentence_carries_the_reviewers_reason():
    review_ = Review({1: SentenceVerdict(1, False, "in progress is not stated in any source")})
    problems = reviewer_problems([sent(1, "The project is in progress.")], review_)
    assert len(problems) == 1
    assert problems[0].kind == "reviewer_unsupported"
    assert "in progress" in problems[0].reason


def test_the_in_progress_inference_is_rejected():
    # 09: an inference sentence ("in progress" with no source stating it) is rejected.
    review_ = parse_verdict(
        verdict_text([{"n": 1, "supported": False, "reason": "no source states the status"}])
    )
    assert review_ is not None
    problems = reviewer_problems([sent(1, "The migration is in progress.")], review_)
    assert problems and problems[0].number == 1


def test_headings_and_gap_lines_are_not_judged():
    review_ = Review({})
    sentences = [sent(1, "Not in company records", kind="heading"), sent(2, "completion %", kind="gap")]
    assert reviewer_problems(sentences, review_) == []


def test_a_confirmed_sentence_is_not_a_problem():
    review_ = Review({1: SentenceVerdict(1, True, "stated")})
    assert reviewer_problems([sent(1, "supported")], review_) == []
