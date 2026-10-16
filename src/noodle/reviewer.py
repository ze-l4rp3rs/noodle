"""The reviewer: a separate model call that audits a draft (05 section 3).

The reviewer is the pipeline's reflector. It has no tools, sees the same sources
as the generator plus the tool-call trace, and receives the draft with numbered
sentences. It returns a structured verdict; the text around it is tolerated and
one retry is allowed when the reply is unusable. If the reviewer asks for a
tool, the draft is marked ``not checked`` (the caller sees ``None``).

Strictness lives here: a factual sentence the reviewer does not explicitly mark
supported counts as unconfirmed, so a rubber-stamp or an omitted line can never
keep an unsupported claim in the answer.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from .loop import ToolCallRecord
from .prompts import REVIEWER_INSTRUCTION, REVIEWER_RETRY
from .providers import Message, Provider
from .sources import SourceRegistry, source_block
from .verification import Problem, Sentence, number_sentences

DEFAULT_REVIEW_MAX_TOKENS = 1000

UNJUDGED_REASON = "the reviewer did not confirm this statement"
UNSUPPORTED_REASON = "the reviewer found this statement unsupported"


# --------------------------------------------------------------------------- #
# The verdict
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SentenceVerdict:
    """The reviewer's decision on one numbered statement."""

    number: int
    supported: bool
    reason: str = ""


@dataclass(frozen=True)
class Review:
    """A parsed verdict. ``sentences`` is keyed by the statement's number."""

    sentences: dict[int, SentenceVerdict] = field(default_factory=dict)
    unanswered: list[str] = field(default_factory=list)
    false_premises: list[str] = field(default_factory=list)
    raw: str = ""

    def verdict(self, number: int) -> Optional[SentenceVerdict]:
        return self.sentences.get(number)

    def unsupported_numbers(self) -> list[int]:
        return sorted(number for number, verdict in self.sentences.items() if not verdict.supported)


# --------------------------------------------------------------------------- #
# Parsing: tolerate text around the JSON, judge nothing as unusable
# --------------------------------------------------------------------------- #


def _candidate_objects(text: str) -> Iterable[str]:
    """Yield each balanced ``{...}`` object in ``text``, strings respected."""

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : index + 1]
                    break
        start = text.find("{", start + 1)


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1"):
            return True
        if low in ("false", "no", "0"):
            return False
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _to_review(data: Any) -> Optional[Review]:
    if not isinstance(data, dict):
        return None
    raw_sentences = data.get("sentences")
    if not isinstance(raw_sentences, list):
        return None
    sentences: dict[int, SentenceVerdict] = {}
    for item in raw_sentences:
        if not isinstance(item, dict):
            continue
        number = _as_int(item.get("n"))
        supported = _as_bool(item.get("supported"))
        if number is None or supported is None:
            continue
        sentences[number] = SentenceVerdict(number, supported, str(item.get("reason") or "").strip())
    if not sentences:
        return None  # a verdict that judges nothing is unusable, not an approval
    return Review(
        sentences=sentences,
        unanswered=_string_list(data.get("unanswered")),
        false_premises=_string_list(data.get("false_premises")),
    )


def parse_verdict(text: str) -> Optional[Review]:
    """Parse a verdict from ``text``, tolerating prose around the JSON object."""

    for candidate in _candidate_objects(str(text)):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        review = _to_review(data)
        if review is not None:
            review = Review(review.sentences, review.unanswered, review.false_premises, raw=candidate)
            return review
    return None


# --------------------------------------------------------------------------- #
# The reviewer call
# --------------------------------------------------------------------------- #


def _render_tools(records: Sequence[ToolCallRecord]) -> str:
    if not records:
        return "(no tools were called)"
    lines: list[str] = []
    for record in records:
        detail = f"{record.tool}({json.dumps(record.args, default=str, sort_keys=True)}) -> {record.status}"
        if record.error:
            detail += f": {record.error}"
        elif record.sources:
            detail += "; sources: " + ", ".join(record.sources)
        lines.append(f"- {detail}")
    return "\n".join(lines)


def build_review_request(
    sources_block: str, tool_records: Sequence[ToolCallRecord], sentences: Sequence[Sentence]
) -> str:
    """The reviewer's user message: sources, tool trace and numbered draft."""

    return (
        "SOURCES\n"
        f"{sources_block or '(no sources were returned)'}\n\n"
        "TOOL CALLS\n"
        f"{_render_tools(tool_records)}\n\n"
        "DRAFT (numbered)\n"
        f"{number_sentences(sentences)}\n\n"
        "Return the JSON verdict now."
    )


def review(
    provider: Provider,
    *,
    registry: SourceRegistry,
    tool_records: Sequence[ToolCallRecord] = (),
    sentences: Sequence[Sentence] = (),
    instruction: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = DEFAULT_REVIEW_MAX_TOKENS,
    temperature: float = 0.0,
) -> Optional[Review]:
    """Ask the reviewer to audit the draft.

    Returns ``None`` when the reviewer tried to call a tool (the draft is marked
    ``not checked``) or when two replies in a row were unusable. One retry is
    made before giving up.
    """

    system = REVIEWER_INSTRUCTION if instruction is None else instruction
    request = build_review_request(source_block(registry), list(tool_records), list(sentences))
    messages = [Message(role="user", text=request)]

    completion = provider.complete(
        messages, tools=None, system=system, model=model, max_tokens=max_tokens, temperature=temperature
    )
    if completion.tool_calls:
        return None
    verdict = parse_verdict(completion.text)
    if verdict is not None:
        return verdict

    messages.append(Message(role="assistant", text=completion.text))
    messages.append(Message(role="user", text=REVIEWER_RETRY))
    completion = provider.complete(
        messages, tools=None, system=system, model=model, max_tokens=max_tokens, temperature=temperature
    )
    if completion.tool_calls:
        return None
    return parse_verdict(completion.text)


# --------------------------------------------------------------------------- #
# Turning a verdict into problems
# --------------------------------------------------------------------------- #


def reviewer_problems(sentences: Iterable[Sentence], review: Review) -> list[Problem]:
    """One :class:`Problem` per factual sentence the reviewer did not confirm.

    A statement judged ``supported`` is skipped. A statement judged unsupported
    carries the reviewer's reason; a statement the reviewer did not judge is
    unconfirmed (05 section 3, strictness).
    """

    problems: list[Problem] = []
    for sentence in sentences:
        if sentence.kind != "fact" or not sentence.text.strip():
            continue
        verdict = review.verdict(sentence.number)
        if verdict is None:
            problems.append(
                Problem(sentence.number, sentence.text, "reviewer_unjudged", UNJUDGED_REASON)
            )
        elif not verdict.supported:
            problems.append(
                Problem(
                    sentence.number,
                    sentence.text,
                    "reviewer_unsupported",
                    verdict.reason or UNSUPPORTED_REASON,
                )
            )
    return problems


__all__ = [
    "DEFAULT_REVIEW_MAX_TOKENS",
    "UNJUDGED_REASON",
    "UNSUPPORTED_REASON",
    "SentenceVerdict",
    "Review",
    "parse_verdict",
    "build_review_request",
    "review",
    "reviewer_problems",
]
