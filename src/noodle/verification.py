"""Automatic checks on a draft: quote matching, repair, sentence checks.

This is 05 step 2, and none of it calls a model. The generator writes facts with
a citation -- ``[[source-id]] "the exact words it rests on"``. This module
repairs a broken citation or a quote that is not quite in its source, splits the
draft into numbered sentences, checks every sentence, removes the ones that
cannot be supported, and renders the survivor into the answer plus its reference
list.

Two guarantees the rest of the system relies on:

* a quote one letter off is repaired from the source with no model call;
* an unsupported sentence is removed and listed with a plain-language reason,
  and an answer with nothing left becomes the fixed message.

Hidden material fails the run (06): if a citation points at a ref the caller may
not see, :class:`HiddenDataError` is raised rather than quietly dropped.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Iterable, Mapping, Optional

from .sources import SourceRegistry

NO_ANSWER = "I couldn't find a verifiable answer in the company records."


def _flatten_bold(text: str) -> str:
    """Collapse runs of three or more asterisks so no line carries nested bold (07, G4)."""
    return re.sub(r"\*{3,}", "**", text)
MISSING_HEADING = "Not in company records"

DEFAULT_REPAIR_SIMILARITY = 0.9

# Not a source type in 07's list, so derived evidence maps onto one it knows.
_TYPE_BY_KIND = {"connection": "role", "team": "project"}
_KNOWN_TYPES = {"doc", "chat", "ticket", "decision", "change", "project", "person", "role"}

_CITATION_RE = re.compile(r'\[\[([0-9a-f]{8})\]\]\s*(?:"([^"]*)"|\u201c([^\u201d]*)\u201d)?')

# Words that betray the model's own reasoning rather than a record (05 step 2.3).
_REASONING_MARKERS = (
    "chain of thought",
    "as an ai",
    "as a language model",
    "i am an ai",
    "let me think",
    "my reasoning",
    "internal monologue",
    "<thinking>",
    "system prompt",
    "my instructions",
)

# A sentence that claims a same-day event fails unless a same-day source backs it.
_LIVE_MARKERS = ("today", "just now", "this morning", "right now", "this hour", "moments ago", "breaking")

_ABBREVIATIONS = (
    "e.g.", "i.e.", "etc.", "vs.", "no.", "mr.", "mrs.", "ms.", "dr.", "st.",
    "jr.", "sr.", "inc.", "ltd.", "co.", "fig.", "vol.", "approx.", "cf.",
)

_PLAIN_SOURCE_RE = re.compile(
    r"(?<![\w:/])(?:doc|chat|ticket|decision|change|project|person|team|role):[A-Za-z0-9._:-]+"
)


class HiddenDataError(RuntimeError):
    """A citation referenced material the caller may not see (06)."""


# --------------------------------------------------------------------------- #
# Normalisation and quote matching
# --------------------------------------------------------------------------- #

_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
    "\u00a0": " ",
})


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace; unify typographic quotes and dashes.

    Quote matching forgives letter case, whitespace, typographic quotes and
    dashes, and nothing else (05 step 2).
    """

    if not text:
        return ""
    folded = str(text).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", folded).strip().casefold()


def quote_in_source(quote: str, source_text: str) -> bool:
    """Is ``quote`` present in ``source_text`` (forgiving case and spacing)?

    An ellipsis (``...`` or ``…``) may skip words inside the quote; the
    remaining fragments must appear in order.
    """

    wanted = normalize(quote)
    haystack = normalize(source_text)
    if not wanted:
        return False
    fragments = [part.strip() for part in re.split(r"\.\.\.|\u2026", wanted) if part.strip()]
    if not fragments:
        return False
    cursor = 0
    for fragment in fragments:
        found = haystack.find(fragment, cursor)
        if found < 0:
            return False
        cursor = found + len(fragment)
    return True


def best_passage(quote: str, source_text: str, threshold: float = DEFAULT_REPAIR_SIMILARITY):
    """The most similar passage of about the quote's length in the source.

    Returns ``(passage, ratio)`` with the passage in the source's own words, or
    ``(None, ratio)`` when the best ratio is below ``threshold``.
    """

    wanted = normalize(quote)
    if not wanted:
        return None, 0.0
    words = str(source_text).split()
    wanted_words = wanted.split()
    if not words or not wanted_words:
        return None, 0.0

    length = len(wanted_words)
    widths = []
    for width in (length, length - 1, length + 1, length - 2, length + 2):
        if width >= 1 and width not in widths:
            widths.append(width)

    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(wanted)
    best_ratio = 0.0
    best_window: Optional[str] = None
    for width in widths:
        for start in range(len(words) - width + 1):
            window = " ".join(words[start:start + width])
            matcher.set_seq1(normalize(window))
            ratio = matcher.ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_window = window
    if best_window is not None and best_ratio >= threshold:
        return best_window, best_ratio
    return None, best_ratio


# --------------------------------------------------------------------------- #
# Repair
# --------------------------------------------------------------------------- #


def repair_citations(text: str) -> str:
    """Close a citation with a missing or doubled bracket."""

    text = re.sub(r"\[\[([0-9a-f]{8})\](?!\])", r"[[\1]]", text)  # [[id]  -> [[id]]
    text = re.sub(r"(?<!\[)\[([0-9a-f]{8})\]\]", r"[[\1]]", text)  # [id]]  -> [[id]]
    text = re.sub(r"(?<!\[)\[([0-9a-f]{8})\](?!\])", r"[[\1]]", text)  # [id] -> [[id]]
    return text


def repair_quotes(text: str, registry: SourceRegistry, threshold: float = DEFAULT_REPAIR_SIMILARITY) -> str:
    """Replace a quote that is not in its source with the best passage >= threshold."""

    def replace(match: re.Match) -> str:
        source_id = match.group(1)
        quote = match.group(2) if match.group(2) is not None else match.group(3)
        if quote is None:
            return match.group(0)
        source = registry.get(source_id)
        if source is None or quote_in_source(quote, source.text):
            return match.group(0)
        passage, _ = best_passage(quote, source.text, threshold)
        if passage is None:
            return match.group(0)
        return f'[[{source_id}]] "{passage}"'

    return _CITATION_RE.sub(replace, text)


# --------------------------------------------------------------------------- #
# Sentences
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Citation:
    source_id: str
    quote: Optional[str]
    span: tuple[int, int]


@dataclass(frozen=True)
class Sentence:
    number: int
    text: str
    citations: list[Citation] = field(default_factory=list)
    kind: str = "fact"  # fact | heading | gap
    line: int = 0


@dataclass(frozen=True)
class Problem:
    number: int
    sentence: str
    kind: str
    reason: str


def extract_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for match in _CITATION_RE.finditer(text):
        quote = match.group(2) if match.group(2) is not None else match.group(3)
        out.append(Citation(source_id=match.group(1), quote=quote, span=match.span()))
    return out


def _ends_sentence(text: str, index: int) -> bool:
    """Does the period at ``index`` end a sentence? (05 step 2)"""

    before = text[:index]
    after = text[index + 1:]
    if after and not after[0].isspace() and after[0] not in "\"\u2019\u201d)]}":
        return False  # a dot inside a token (a url, id or version), not a full stop
    if before and before[-1].isdigit() and after[:1].isdigit():
        return False  # version or decimal, e.g. 4.2
    if re.search(r"(?<![A-Za-z])[A-Za-z]$", before):
        return False  # single-letter initial, e.g. J. Smith, U.S.
    low = before.casefold()
    for abbreviation in _ABBREVIATIONS:
        stem = abbreviation.rstrip(".")
        if low.endswith(stem):
            cut = len(low) - len(stem)
            if cut == 0 or not low[cut - 1].isalpha():
                return False  # abbreviation, e.g. etc.
    return True


def split_sentences(text: str) -> list[str]:
    """Split into sentences; quotes, abbreviations, initials, versions and
    citation markers never end a sentence."""

    text = str(text).strip()
    out: list[str] = []
    if not text:
        return out
    n = len(text)
    start = 0
    i = 0
    in_quote = False
    while i < n:
        if text.startswith("[[", i):
            close = text.find("]]", i + 2)
            i = n if close < 0 else close + 2
            continue
        ch = text[i]
        if ch == '"':
            in_quote = not in_quote
        elif ch == "\u201c":
            in_quote = True
        elif ch == "\u201d":
            in_quote = False
        if ch in ".!?" and not in_quote and (ch != "." or _ends_sentence(text, i)):
            # A citation written after the fact (05 section 1) belongs to it:
            # if a marker follows this punctuation, keep it with the sentence.
            after = i + 1
            while after < n and text[after] == " ":
                after += 1
            attached = _CITATION_RE.match(text, after)
            if attached is not None:
                end = attached.end()
                piece = text[start:end].strip()
                if piece:
                    out.append(piece)
                start = end
                i = end
                continue
            piece = text[start:i + 1].strip()
            if piece:
                out.append(piece)
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def number_sentences(sentences: Iterable[Sentence]) -> str:
    """The numbered sentence list the reviewer is shown (05 section 3)."""

    return "\n".join(f"{sentence.number}. {sentence.text}" for sentence in sentences)


def split_draft(text: str, missing_heading: str = MISSING_HEADING) -> list[Sentence]:
    """Number the draft's sentences and label each heading, gap or fact."""

    sentences: list[Sentence] = []
    number = 0
    in_gap = False
    wanted = normalize(missing_heading)
    for line_no, raw in enumerate(str(text).split("\n"), start=1):
        stripped = raw.strip()
        if not stripped:
            continue
        probe = normalize(stripped.strip("#*").strip())
        is_gap_heading = bool(wanted) and probe == wanted
        is_heading = (
            stripped.startswith("#")
            or (stripped.startswith("**") and stripped.endswith("**"))
            or is_gap_heading
        )
        if is_heading:
            in_gap = is_gap_heading
            number += 1
            sentences.append(Sentence(number=number, text=stripped, kind="heading", line=line_no))
            continue
        kind = "gap" if in_gap else "fact"
        for piece in split_sentences(stripped):
            number += 1
            sentences.append(
                Sentence(number=number, text=piece, citations=extract_citations(piece), kind=kind, line=line_no)
            )
    return sentences


# --------------------------------------------------------------------------- #
# Sentence checks
# --------------------------------------------------------------------------- #


def _cited_urls(citations: Iterable[Citation], registry: SourceRegistry) -> set[str]:
    urls: set[str] = set()
    for citation in citations:
        source = registry.get(citation.source_id)
        if source and source.evidence.url:
            urls.add(str(source.evidence.url).rstrip(".,)"))
    return urls


def _has_fake_link(text: str, citations: Iterable[Citation], registry: SourceRegistry) -> bool:
    allowed = _cited_urls(citations, registry)
    for match in re.finditer(r"\]\((https?://[^)\s]+)\)", text):
        if match.group(1).rstrip(".,)") not in allowed:
            return True
    for match in re.finditer(r"(?<!\()(https?://[^\s)]+)", text):
        if match.group(1).rstrip(".,)") not in allowed:
            return True
    return False


def _has_plain_source(text: str) -> bool:
    return bool(_PLAIN_SOURCE_RE.search(text))


def _has_leaked_reasoning(text: str) -> bool:
    low = text.casefold()
    return any(marker in low for marker in _REASONING_MARKERS)


def _has_live_claim(text: str) -> bool:
    low = f" {normalize(text)} "
    return any(marker in low for marker in _LIVE_MARKERS)


def _record_precedes_sync(record_date: Optional[str], last_synced_at: Optional[str]) -> bool:
    """Is ``record_date`` the same calendar day as the sync, and no later than it?

    05 step 2.5: a same-day claim stands only when a record dated that day exists
    *and* precedes ``last_synced_at``. A date-only record counts as the start of
    its day; when both carry a time the ISO-8601 strings order, so they compare
    directly.
    """

    if not record_date or not last_synced_at:
        return False
    record, sync = str(record_date), str(last_synced_at)
    if record[:10] != sync[:10]:
        return False
    if len(record) > 10 and len(sync) > 10:
        return record <= sync
    return True


def check_draft(
    sentences: Iterable[Sentence],
    registry: SourceRegistry,
    *,
    forbidden_refs: Iterable[str] = (),
    last_synced_at: Optional[str] = None,
    quotes_required: bool = True,
) -> list[Problem]:
    """Run every mechanical sentence check (05 step 2.3 to 2.5).

    Raises :class:`HiddenDataError` if a citation names material the caller may
    not see. Returns one :class:`Problem` per failing sentence.
    """

    forbidden = set(forbidden_refs)
    problems: list[Problem] = []
    for sentence in sentences:
        if sentence.kind in ("heading", "gap") or not sentence.text.strip():
            continue
        citations = sentence.citations
        for citation in citations:
            source = registry.get(citation.source_id)
            if source is not None and source.ref in forbidden:
                raise HiddenDataError("a citation referenced material above the caller's access level")
        if not citations:
            problems.append(Problem(sentence.number, sentence.text, "no_citation", "no source is cited for this sentence"))
            continue
        cited_sources = []
        for citation in citations:
            source = registry.get(citation.source_id)
            if source is None:
                problems.append(
                    Problem(sentence.number, sentence.text, "unknown_source", f"source {citation.source_id} was not provided")
                )
                continue
            cited_sources.append(source)
            if not citation.quote:
                if quotes_required:
                    problems.append(
                        Problem(sentence.number, sentence.text, "quote_missing", f"citation {citation.source_id} has no quote")
                    )
                continue
            if not quote_in_source(citation.quote, source.text):
                problems.append(
                    Problem(
                        sentence.number,
                        sentence.text,
                        "quote_not_in_source",
                        f"the quote is not in source {citation.source_id}",
                    )
                )
        # Only the model's own prose is checked: a quoted source is verified
        # verbatim above and may itself contain a ``kind:id``.
        if _has_plain_source(_strip_quotes(sentence.text)):
            problems.append(
                Problem(sentence.number, sentence.text, "plain_source", "a source is written as plain text, not a citation")
            )
        if _has_fake_link(sentence.text, citations, registry):
            problems.append(Problem(sentence.number, sentence.text, "fake_link", "a link does not point at a provided source"))
        if _has_leaked_reasoning(sentence.text):
            problems.append(Problem(sentence.number, sentence.text, "leaked_reasoning", "the sentence contains reasoning text"))
        if last_synced_at and _has_live_claim(sentence.text):
            backed = any(
                _record_precedes_sync(source.evidence.date, last_synced_at)
                for source in cited_sources
            )
            if not backed:
                problems.append(
                    Problem(sentence.number, sentence.text, "live_claim", "the sentence claims a same-day event with no same-day source")
                )
    return problems


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Reference:
    """One entry of the response's ``references`` list (07)."""

    id: str
    number: int
    type: str
    title: str
    excerpt: str
    quotes: list[str]
    date: Optional[str]
    url: str
    node_id: str
    freshness: str
    freshness_note: str
    meta: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "number": self.number,
            "type": self.type,
            "title": self.title,
            "excerpt": self.excerpt,
            "quotes": list(self.quotes),
            "date": self.date or "",
            "url": self.url or "",
            "nodeId": self.node_id,
            "freshness": self.freshness,
            "freshness_note": self.freshness_note,
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class VerificationResult:
    text: str
    references: list[Reference]
    unverified: list[dict]
    missing: list[str]
    status: str  # verified | partly_verified | not_checked
    problems: list[Problem]


def _reference_type(kind: str) -> str:
    if kind in _KNOWN_TYPES:
        return kind
    return _TYPE_BY_KIND.get(kind, "record")


def _freshness(status: str) -> str:
    if status == "current":
        return "current"
    if status == "superseded":
        return "superseded"
    return "unknown"


def _strip_quotes(text: str) -> str:
    return _CITATION_RE.sub(lambda match: f"[[{match.group(1)}]]", text)


def _gap_text(text: str) -> str:
    return re.sub(r"^\s*[-*\u2022]+\s*", "", text).strip()


def _render(sentences: Iterable[Sentence], registry: SourceRegistry) -> tuple[str, list[Reference]]:
    order: list[str] = []
    quotes: dict[str, list[str]] = {}
    parts: list[str] = []
    for sentence in sentences:
        text = sentence.text
        if sentence.kind == "fact":
            for citation in sentence.citations:
                if not registry.has(citation.source_id):
                    continue
                if citation.source_id not in order:
                    order.append(citation.source_id)
                if citation.quote:
                    quotes.setdefault(citation.source_id, []).append(citation.quote)
            text = _strip_quotes(text)
        parts.append(text)

    answer = "\n\n".join(parts).strip()
    references: list[Reference] = []
    for position, source_id in enumerate(order, start=1):
        source = registry.get(source_id)
        assert source is not None
        evidence = source.evidence
        collected = quotes.get(source_id, [])
        references.append(
            Reference(
                id=source_id,
                number=position,
                type=_reference_type(evidence.kind),
                title=evidence.label or evidence.ref,
                excerpt=collected[0] if collected else evidence.text[:200],
                quotes=list(collected),
                date=evidence.date,
                url=evidence.url or "",
                node_id=evidence.ref,
                freshness=_freshness(evidence.status),
                freshness_note=evidence.note or "",
                meta=dict(evidence.meta) if evidence.meta else {},
            )
        )
    return answer, references


def verify(
    draft: str,
    registry: SourceRegistry,
    *,
    forbidden_refs: Iterable[str] = (),
    last_synced_at: Optional[str] = None,
    quotes_required: bool = True,
    missing_heading: str = MISSING_HEADING,
    threshold: float = DEFAULT_REPAIR_SIMILARITY,
    extra_problems: Iterable[Problem] = (),
) -> VerificationResult:
    """Repair, check and render a draft into the answer and its references.

    Unsupported sentences are removed and listed in ``unverified``; the gaps the
    answer states under the fixed heading become ``missing``. If nothing
    verifiable survives, the answer is the fixed message.

    ``extra_problems`` carries the reviewer's verdicts (05 section 3) as
    :class:`Problem`s keyed by sentence number; they remove the same sentences
    and join ``unverified``, so removal has one home.
    """

    repaired = repair_quotes(repair_citations(draft), registry, threshold)
    sentences = split_draft(repaired, missing_heading=missing_heading)
    problems = list(
        check_draft(
            sentences,
            registry,
            forbidden_refs=forbidden_refs,
            last_synced_at=last_synced_at,
            quotes_required=quotes_required,
        )
    )
    problems.extend(extra_problems)
    bad = {problem.number for problem in problems}
    kept = [sentence for sentence in sentences if sentence.number not in bad]

    unverified = [{"sentence": problem.sentence, "reason": problem.reason} for problem in problems]
    missing = [_gap_text(sentence.text) for sentence in kept if sentence.kind == "gap"]

    text, references = _render(kept, registry)
    text = _flatten_bold(text)
    if not any(sentence.kind == "fact" for sentence in kept):
        text, references = NO_ANSWER, []

    status = "verified" if not problems and not missing else "partly_verified"
    return VerificationResult(
        text=text,
        references=references,
        unverified=unverified,
        missing=missing,
        status=status,
        problems=problems,
    )


__all__ = [
    "NO_ANSWER",
    "MISSING_HEADING",
    "DEFAULT_REPAIR_SIMILARITY",
    "HiddenDataError",
    "Citation",
    "Sentence",
    "Problem",
    "Reference",
    "VerificationResult",
    "normalize",
    "quote_in_source",
    "best_passage",
    "repair_citations",
    "repair_quotes",
    "extract_citations",
    "split_sentences",
    "number_sentences",
    "split_draft",
    "check_draft",
    "verify",
]
