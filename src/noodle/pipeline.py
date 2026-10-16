"""The answer pipeline: generate, check, review, fix (05 sections 1 to 5).

One run answers one question:

1. the generator runs its bounded tool loop (05 section 1, :mod:`noodle.loop`);
2. the automatic checks repair, split and inspect the draft (05 section 2,
   :mod:`noodle.verification`);
3. the reviewer audits it (05 section 3, :mod:`noodle.reviewer`);

and, while problems remain and the round budget is not spent, a fix request goes
back into the generator's own conversation -- tool exchanges included -- so the
model keeps its history (05 section 4).

The best draft wins, not the latest: drafts are scored by *reviewed or not*,
then *number of problem sentences*, then *unanswered plus false premises* (05
section 5). The winner is rendered with its unsupported sentences removed, its
gaps mirrored into ``missing`` and a status. The fixed notices (the soft
dead-end and the as-of line) and the API response are added by later steps.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence

from .config import DEFAULT_FIX_ROUNDS
from .graph import GraphClient
from .loop import DEFAULT_MAX_TOOL_ROUNDS, ToolCallRecord, ToolSet, redacted_query, run_tool_loop
from .prompts import FIX_INSTRUCTION, GENERATOR_INSTRUCTION
from .providers import Completion, Message, Provider
from .queries import HiddenInfo, QueryResult, UserCtx, merge_hidden, q12_freshness
from .reviewer import Review, review, reviewer_problems
from .sources import SourceRegistry
from .tools import TOOLS, Evidence
from .verification import (
    DEFAULT_REPAIR_SIMILARITY,
    MISSING_HEADING,
    Problem,
    Reference,
    Sentence,
    check_draft,
    number_sentences,
    repair_citations,
    repair_quotes,
    split_draft,
    verify,
)


@dataclass
class Draft:
    """One draft, its checks and the reviewer's verdict."""

    text: str
    sentences: list[Sentence]
    mechanical: list[Problem]
    reviewer: list[Problem]  # reviewer problems for sentences with no mechanical one
    review: Optional[Review]
    score: tuple[int, int, int]


@dataclass
class AnswerRun:
    """The outcome of one run, before the API response is built (07, step 9)."""

    answer: str
    references: list[Reference]
    status: str  # verified | partly_verified | not_checked
    unverified: list[dict]
    missing: list[str]
    premise: list[str]
    approved: bool
    rounds: int  # drafts generated and reviewed
    fix_rounds: int  # fix requests actually sent
    evidence: list[Evidence]
    hidden: HiddenInfo
    traces: list[QueryResult]
    tool_calls: list[ToolCallRecord]
    completions: list[Completion]
    messages: list[Message]
    registry: SourceRegistry
    forced: bool
    elapsed_ms: float
    live_intent: bool = False
    as_of: str = ""


def build_fix_request(
    sentences: Sequence[Sentence],
    problems: Sequence[Problem],
    unanswered: Sequence[str],
    false_premises: Sequence[str],
) -> str:
    """The fix request: only the problems, and an instruction to copy the rest."""

    numbered = number_sentences(sentences)
    problem_lines = "\n".join(f"- {problem.number}: {problem.reason}" for problem in problems) or "(none)"
    unanswered_lines = "\n".join(f"- {item}" for item in unanswered) or "(none)"
    premise_lines = "\n".join(f"- {item}" for item in false_premises) or "(none)"
    return FIX_INSTRUCTION.format(
        numbered=numbered,
        problems=problem_lines,
        unanswered=unanswered_lines,
        premises=premise_lines,
    )


def _reviewed_draft(
    draft_text: str,
    registry: SourceRegistry,
    *,
    reviewer_provider: Provider,
    tool_records: Sequence[ToolCallRecord],
    reviewer_system: Optional[str],
    reviewer_model: Optional[str],
    review_max_tokens: int,
    temperature: float,
    forbidden_refs: Sequence[str],
    last_synced_at: Optional[str],
    quotes_required: bool,
    missing_heading: str,
    threshold: float,
) -> Draft:
    """Run the automatic checks and the reviewer on one draft."""

    repaired = repair_quotes(repair_citations(draft_text), registry, threshold)
    sentences = split_draft(repaired, missing_heading=missing_heading)
    mechanical = check_draft(
        sentences,
        registry,
        forbidden_refs=forbidden_refs,
        last_synced_at=last_synced_at,
        quotes_required=quotes_required,
    )
    verdict = review(
        reviewer_provider,
        registry=registry,
        tool_records=tool_records,
        sentences=sentences,
        instruction=reviewer_system,
        model=reviewer_model,
        max_tokens=review_max_tokens,
        temperature=temperature,
    )
    if verdict is None:
        score = (1, len({problem.number for problem in mechanical}), 0)
        return Draft(draft_text, sentences, list(mechanical), [], None, score)

    mechanical_numbers = {problem.number for problem in mechanical}
    reviewer_only = [
        problem for problem in reviewer_problems(sentences, verdict) if problem.number not in mechanical_numbers
    ]
    problem_numbers = mechanical_numbers | {problem.number for problem in reviewer_only}
    score = (0, len(problem_numbers), len(verdict.unanswered) + len(verdict.false_premises))
    return Draft(draft_text, sentences, list(mechanical), reviewer_only, verdict, score)


def run_answer(
    provider: Provider,
    client: GraphClient,
    user: UserCtx,
    question: str,
    *,
    reviewer_provider: Optional[Provider] = None,
    tools: ToolSet = TOOLS,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    fix_rounds: int = DEFAULT_FIX_ROUNDS,
    system: Optional[str] = None,
    reviewer_system: Optional[str] = None,
    generator_model: Optional[str] = None,
    reviewer_model: Optional[str] = None,
    max_tokens: int = 2000,
    review_max_tokens: int = 1000,
    temperature: float = 0.0,
    last_synced_at: Optional[str] = None,
    forbidden_refs: Sequence[str] = (),
    missing_heading: str = MISSING_HEADING,
    threshold: float = DEFAULT_REPAIR_SIMILARITY,
    quotes_required: bool = True,
    registry: Optional[SourceRegistry] = None,
    live_intent: bool = False,
) -> AnswerRun:
    """Answer ``question`` for ``user``, reviewing and fixing until clean or out of rounds."""

    started = time.perf_counter()
    registry = registry if registry is not None else SourceRegistry()
    reviewer_provider = reviewer_provider if reviewer_provider is not None else provider
    generator_system = GENERATOR_INSTRUCTION if system is None else system
    reviewer_instruction = reviewer_system

    conversation: list[Message] = [Message(role="user", text=question)]
    drafts: list[Draft] = []
    evidence: list[Evidence] = []
    traces: list[QueryResult] = []
    tool_calls: list[ToolCallRecord] = []
    completions: list[Completion] = []
    hidden = HiddenInfo()
    forced = False
    fix_rounds_used = 0
    messages = list(conversation)

    # 06: a live-intent question forces the freshness guard, and the as-of time is
    # read from the graph, never the clock at answer time (D1). The system runs
    # the lookup itself, so the guard holds even if the model forgets to.
    as_of = ""
    if live_intent:
        if last_synced_at is None:
            started_fresh = time.perf_counter()
            freshness = q12_freshness(client, user)
            traces.append(freshness)
            tool_calls.append(
                ToolCallRecord(
                    "get_freshness",
                    {},
                    "ok",
                    rows=len(freshness.rows),
                    node_ids=list(freshness.node_ids),
                    edge_ids=list(freshness.edge_ids),
                    duration_ms=(time.perf_counter() - started_fresh) * 1000,
                    queries=[redacted_query(freshness)],
                )
            )
            if freshness.rows:
                value = freshness.rows[0].get("last_synced_at")
                last_synced_at = str(value) if value else None
        if last_synced_at:
            as_of = str(last_synced_at)

    for cycle in range(fix_rounds + 1):
        result = run_tool_loop(
            provider,
            client,
            user,
            conversation,
            tools=tools,
            max_tool_rounds=max_tool_rounds,
            system=generator_system,
            model=generator_model,
            max_tokens=max_tokens,
            temperature=temperature,
            registry=registry,
        )
        evidence.extend(result.evidence)
        traces.extend(result.traces)
        tool_calls.extend(result.tool_calls)
        completions.extend(result.completions)
        hidden = merge_hidden(hidden, result.hidden)
        forced = result.forced
        messages = list(result.messages)
        conversation = list(result.messages)

        draft = _reviewed_draft(
            result.text,
            registry,
            reviewer_provider=reviewer_provider,
            tool_records=result.tool_calls,
            reviewer_system=reviewer_instruction,
            reviewer_model=reviewer_model,
            review_max_tokens=review_max_tokens,
            temperature=temperature,
            forbidden_refs=forbidden_refs,
            last_synced_at=last_synced_at,
            quotes_required=quotes_required,
            missing_heading=missing_heading,
            threshold=threshold,
        )
        drafts.append(draft)

        if draft.review is None:
            break  # review unusable: stop early (05 section 3)
        clean = not draft.mechanical and not draft.reviewer and not draft.review.unanswered \
            and not draft.review.false_premises
        if clean or cycle == fix_rounds:
            break
        conversation.append(
            Message(
                role="user",
                text=build_fix_request(
                    draft.sentences, draft.mechanical + draft.reviewer,
                    draft.review.unanswered, draft.review.false_premises,
                ),
            )
        )
        fix_rounds_used += 1

    best = min(drafts, key=lambda item: item.score)
    verdict = verify(
        best.text,
        registry,
        forbidden_refs=forbidden_refs,
        last_synced_at=last_synced_at,
        quotes_required=quotes_required,
        missing_heading=missing_heading,
        threshold=threshold,
        extra_problems=best.reviewer,
    )

    missing = list(verdict.missing)
    premise: list[str] = []
    if best.review is not None:
        for part in best.review.unanswered:
            if part not in missing:
                missing.append(part)
        premise = list(best.review.false_premises)

    if best.review is None:
        status = "not_checked"
    elif verdict.status == "partly_verified" or premise or best.review.unanswered:
        status = "partly_verified"
    else:
        status = "verified"

    approved = (
        best.review is not None
        and not best.mechanical
        and not best.reviewer
        and not best.review.unanswered
        and not best.review.false_premises
    )

    return AnswerRun(
        answer=verdict.text,
        references=verdict.references,
        status=status,
        unverified=verdict.unverified,
        missing=missing,
        premise=premise,
        approved=approved,
        rounds=len(drafts),
        fix_rounds=fix_rounds_used,
        evidence=evidence,
        hidden=hidden,
        traces=traces,
        tool_calls=tool_calls,
        completions=completions,
        messages=messages,
        registry=registry,
        forced=forced,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        live_intent=live_intent,
        as_of=as_of,
    )


__all__ = ["Draft", "AnswerRun", "build_fix_request", "run_answer"]
