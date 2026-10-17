"""Evaluation: golden questions, the offline fact check and the runner (09).

Three things live here, all company-agnostic:

* **Golden questions** -- one YAML file per question under ``eval/golden/``. Each
  names the asker, the question, the facts it relies on and the checks its answer
  must satisfy.
* **Offline fact check** -- every fact a question relies on is confirmed against
  the dataset *before* any model or graph is involved. It knows a small set of
  general fact kinds (a field value, the latest change, a value on a date, a
  dissent, a message, a relationship, a document dated after a decision, the
  absence of a figure, blocked items and owners), never a company or a name. When
  a fact fails, fix the question, not the data.
* **The runner** -- answers every golden question through the shared service and
  scores the response against its checks, plus a vector-only baseline over the
  same documents for the demo comparison. Needs real keys and a loaded graph; it
  is not part of ``check.sh``.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import yaml

from .queries import UserCtx

GOLDEN_DIR = Path("eval/golden")
CONTEXT_DIR = Path("eval/context")

# The general fact kinds the offline check understands. Each is a small, name-
# free predicate over the dataset, so golden questions stay portable.
FACT_KINDS = (
    "exists",
    "field",
    "clearance",
    "latest_change",
    "value_on",
    "dissent",
    "approver",
    "message",
    "relationship",
    "document_after",
    "absent",
    "blocked_have_owners",
)


class GoldenError(Exception):
    """A golden question file is malformed."""


@dataclass(frozen=True)
class GoldenQuestion:
    """One evaluated question (09)."""

    id: str
    question: str
    user: str
    areas: tuple[str, ...] = ()
    mode: Optional[str] = None
    context_file: Optional[str] = None
    facts: tuple[Mapping[str, Any], ...] = ()
    checks: Mapping[str, Any] = field(default_factory=dict)
    source: str = ""

    def check(self, name: str) -> Any:
        return self.checks.get(name)


@dataclass(frozen=True)
class FactOutcome:
    question: str
    kind: str
    detail: str
    ok: bool


@dataclass(frozen=True)
class CheckOutcome:
    question: str
    name: str
    detail: str
    ok: bool


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_golden_questions(directory: Path | str = GOLDEN_DIR) -> list[GoldenQuestion]:
    """Load and validate every golden question YAML file, sorted by id."""

    path = Path(directory)
    questions: list[GoldenQuestion] = []
    for file in sorted(path.glob("*.yaml")):
        with open(file, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
        questions.append(_question_from(raw, file.name))
    questions.sort(key=lambda q: q.id)
    return questions


def _question_from(raw: Mapping[str, Any], name: str) -> GoldenQuestion:
    if not isinstance(raw, Mapping):
        raise GoldenError(f"{name}: not a mapping")
    for required in ("id", "question", "user"):
        if not raw.get(required):
            raise GoldenError(f"{name}: missing {required!r}")
    checks = raw.get("checks") or {}
    for fact in raw.get("facts") or []:
        if not isinstance(fact, Mapping) or fact.get("kind") not in FACT_KINDS:
            raise GoldenError(f"{name}: unknown fact {fact!r}")
    return GoldenQuestion(
        id=str(raw["id"]),
        question=str(raw["question"]),
        user=str(raw["user"]),
        areas=tuple(raw.get("areas") or ()),
        mode=raw.get("mode") or None,
        context_file=raw.get("context_file") or None,
        facts=tuple(raw.get("facts") or ()),
        checks=dict(checks),
        source=name,
    )


# --------------------------------------------------------------------------- #
# Offline fact check
# --------------------------------------------------------------------------- #


def _by_id(dataset: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for records in (dataset.get("sections") or {}).values():
        for record in records or []:
            if isinstance(record, Mapping) and record.get("id"):
                index[str(record["id"])] = record
    return index


def _scalar_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _scalar_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _scalar_strings(item)
    elif value is not None:
        yield str(value)


def _relationships(dataset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    out: list[Mapping[str, Any]] = []
    for rel_type, entries in (dataset.get("relationships") or {}).items():
        for entry in entries or []:
            if isinstance(entry, Mapping):
                out.append({**entry, "type": rel_type})
    return out


def _latest_history(
    dataset: Mapping[str, Any], entity: str, field_name: Optional[str]
) -> Optional[Mapping[str, Any]]:
    rows = [
        row
        for row in (dataset.get("sections") or {}).get("history", []) or []
        if row.get("entity_id") == entity and (field_name is None or row.get("field") == field_name)
    ]
    if not rows:
        return None
    return max(rows, key=lambda row: (str(row.get("date") or ""), int(row.get("seq") or 0)))


def check_fact(fact: Mapping[str, Any], dataset: Mapping[str, Any]) -> FactOutcome:
    """Confirm one fact against the dataset and return a pass/fail outcome."""

    kind = str(fact.get("kind"))
    by_id = _by_id(dataset)
    ok = False
    detail = ""

    if kind == "exists":
        target = str(fact.get("id"))
        ok = target in by_id
        detail = f"{target} {'present' if ok else 'missing'}"
    elif kind == "field":
        record = by_id.get(str(fact.get("id")))
        actual = record.get(str(fact.get("field"))) if record else None
        want = fact.get("value")
        ok = actual == want
        detail = f"{fact.get('id')}.{fact.get('field')}={actual!r} (want {want!r})"
    elif kind == "clearance":
        record = by_id.get(str(fact.get("id")))
        actual = record.get("clearance", 0) if record else None
        ok = actual == fact.get("value")
        detail = f"{fact.get('id')} clearance={actual!r} (want {fact.get('value')!r})"
    elif kind == "latest_change":
        row = _latest_history(dataset, str(fact.get("entity")), fact.get("field"))
        actual = row.get("new_value") if row else None
        ok = actual == fact.get("new_value")
        detail = f"latest {fact.get('entity')}.{fact.get('field')}={actual!r}"
    elif kind == "value_on":
        entity = str(fact.get("entity"))
        field_name = fact.get("field")
        on = str(fact.get("as_of"))
        rows = sorted(
            (
                row
                for row in (dataset.get("sections") or {}).get("history", []) or []
                if row.get("entity_id") == entity
                and (field_name is None or row.get("field") == field_name)
            ),
            key=lambda r: (str(r.get("date") or ""), int(r.get("seq") or 0)),
        )
        before = [row for row in rows if str(row.get("date") or "") <= on]
        if before:
            actual = before[-1].get("new_value")
        elif rows:
            # the earliest change after the date carries the value it replaced
            actual = rows[0].get("old_value")
        else:
            actual = None
        ok = actual == fact.get("value")
        detail = f"{entity}.{field_name} on {on} = {actual!r}"
    elif kind == "dissent":
        record = by_id.get(str(fact.get("decision")))
        people = {entry.get("id") for entry in (record or {}).get("dissent", []) or []}
        ok = fact.get("person") in people
        detail = f"dissent on {fact.get('decision')} = {sorted(people)}"
    elif kind == "approver":
        record = by_id.get(str(fact.get("decision")))
        people = set((record or {}).get("approvers", []) or [])
        ok = fact.get("person") in people
        detail = f"approvers on {fact.get('decision')} = {sorted(people)}"
    elif kind == "message":
        record = by_id.get(str(fact.get("id")))
        contains = fact.get("contains")
        ok = record is not None and (
            contains is None or str(contains).casefold() in str(record.get("text") or "").casefold()
        )
        detail = f"message {fact.get('id')} {'found' if record else 'missing'}"
    elif kind == "relationship":
        ok = any(
            rel.get("type") == fact.get("type")
            and rel.get("src") == fact.get("src")
            and rel.get("dst") == fact.get("dst")
            for rel in _relationships(dataset)
        )
        detail = f"{fact.get('src')} -[{fact.get('type')}]-> {fact.get('dst')}"
    elif kind == "document_after":
        doc = by_id.get(str(fact.get("document")))
        decision = by_id.get(str(fact.get("decision")))
        ok = bool(
            doc and decision and str(doc.get("created") or "") > str(decision.get("date") or "")
        )
        detail = f"{fact.get('document')}.created > {fact.get('decision')}.date"
    elif kind == "absent":
        needle = str(fact.get("text")).casefold()
        found = any(
            needle in value.casefold()
            for record in by_id.values()
            for value in _scalar_strings(record)
        )
        ok = not found
        detail = f"no record states {fact.get('text')!r}"
    elif kind == "blocked_have_owners":
        blocked = [
            row
            for row in (dataset.get("sections") or {}).get("tickets", []) or []
            if row.get("status") == "blocked"
        ]
        ok = bool(blocked) and all(row.get("assignee") for row in blocked)
        detail = f"{len(blocked)} blocked tickets, all with an assignee" if ok else "a blocked ticket has no owner"

    return FactOutcome(question="", kind=kind, detail=detail, ok=ok)


def check_question_facts(
    question: GoldenQuestion, dataset: Mapping[str, Any]
) -> list[FactOutcome]:
    """Confirm every fact a question relies on. Never uses a model or the graph."""

    outcomes: list[FactOutcome] = []
    for fact in question.facts:
        outcome = check_fact(fact, dataset)
        outcomes.append(
            FactOutcome(question=question.id, kind=outcome.kind, detail=outcome.detail, ok=outcome.ok)
        )
    return outcomes


def offline_check(
    questions: Sequence[GoldenQuestion], dataset: Mapping[str, Any]
) -> list[FactOutcome]:
    """Run the offline fact check over every question."""

    return [outcome for q in questions for outcome in check_question_facts(q, dataset)]


# --------------------------------------------------------------------------- #
# Scoring a response
# --------------------------------------------------------------------------- #


def _first_sentence(answer: str) -> str:
    text = answer.strip()
    if not text:
        return ""
    match = re.search(r"(?<=[.!?])\s", text)
    return text[: match.start() + 1] if match else text


def _reference_ids(response: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for ref in response.get("references") or []:
        if isinstance(ref, Mapping):
            value = ref.get("nodeId") or ref.get("node_id") or ref.get("ref")
            if value:
                ids.add(str(value))
        elif isinstance(ref, str):
            ids.add(ref)
    return ids


def evaluate_response(
    question: GoldenQuestion, response: Mapping[str, Any]
) -> list[CheckOutcome]:
    """Score one response against a question's ``checks`` (09)."""

    answer = str(response.get("answer") or "")
    first = _first_sentence(answer)
    refs = _reference_ids(response)
    missing = " ".join(str(item) for item in response.get("missing") or [])
    premise = " ".join(str(item) for item in response.get("premise") or [])
    out: list[CheckOutcome] = []

    def add(name: str, ok: bool, detail: str) -> None:
        out.append(CheckOutcome(question=question.id, name=name, detail=detail, ok=ok))

    for text in question.check("must_match") or []:
        add("must_match", str(text) in answer, f"{text!r} in answer")
    for text in question.check("must_not_match_first_sentence") or []:
        add(
            "must_not_match_first_sentence",
            str(text) not in first,
            f"{text!r} absent from first sentence",
        )
    for text in question.check("first_sentence_must_match") or []:
        add("first_sentence_must_match", str(text) in first, f"{text!r} in first sentence")
    expected_premise = question.check("premise_corrected")
    if expected_premise:
        lowered = (answer + " " + premise).casefold()
        add("premise_corrected", str(expected_premise).casefold() in lowered, str(expected_premise))
    for text in question.check("gap_stated") or []:
        haystack = (missing + " " + answer).casefold()
        add("gap_stated", str(text).casefold() in haystack, f"{text!r} stated as a gap")
    for ref in question.check("must_cite") or []:
        add("must_cite", any(str(ref) in cited for cited in refs), f"cited {ref}")
    for ref in question.check("must_not_cite") or []:
        add("must_not_cite", not any(str(ref) in cited for cited in refs), f"not cited {ref}")
    for leak in question.check("must_not_leak") or []:
        haystack = (answer + " " + " ".join(sorted(refs))).casefold()
        add("must_not_leak", str(leak).casefold() not in haystack, f"{leak!r} not leaked")
    expected_status = question.check("status")
    if expected_status:
        actual = str(response.get("status") or "")
        add("status", actual == expected_status, f"status={actual!r} (want {expected_status!r})")
    return out


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #


@dataclass
class QuestionResult:
    question: GoldenQuestion
    checks: list[CheckOutcome]
    status: str = ""
    latency_ms: float = 0.0
    rounds: int = 0

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.ok for check in self.checks)

    @property
    def failed(self) -> list[CheckOutcome]:
        return [check for check in self.checks if not check.ok]


@dataclass
class Report:
    label: str
    results: list[QuestionResult]

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def score(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def area_scores(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for area in sorted({a for result in self.results for a in result.question.areas}):
            in_area = [r for r in self.results if area in r.question.areas]
            scores[area] = sum(1 for r in in_area if r.passed) / len(in_area) if in_area else 0.0
        return scores

    def status_distribution(self) -> dict[str, int]:
        distribution: dict[str, int] = {}
        for result in self.results:
            key = result.status or "unknown"
            distribution[key] = distribution.get(key, 0) + 1
        return distribution

    def mean_latency_ms(self) -> float:
        return sum(r.latency_ms for r in self.results) / self.total if self.total else 0.0

    def mean_rounds(self) -> float:
        return sum(r.rounds for r in self.results) / self.total if self.total else 0.0


def resolve_dataset_user(dataset: Mapping[str, Any]) -> Callable[[str], UserCtx]:
    """A user resolver that reads clearance and team from the dataset (offline).

    The online path resolves the same facts from the graph (:mod:`noodle.auth`);
    the runner takes a resolver so its user lookup is not tied to a database.
    """

    people = {p["id"]: p for p in (dataset.get("sections") or {}).get("people", []) or []}

    def resolve(person_id: str) -> UserCtx:
        record = people.get(person_id)
        if record is None:
            return UserCtx.anonymous()
        return UserCtx(
            id=person_id,
            clearance=int(record.get("clearance") or 0),
            team=record.get("team") or None,
        )

    return resolve


def run_questions(
    runtime: Any,
    questions: Sequence[GoldenQuestion],
    *,
    user_resolver: Callable[[str], UserCtx],
    mode: Optional[str] = None,
    answer_fn: Optional[Callable[..., tuple[dict[str, Any], Any]]] = None,
    context_dir: Path | str = CONTEXT_DIR,
) -> Report:
    """Answer every question through the shared service and score the responses.

    ``answer_fn`` defaults to :func:`noodle.service.answer`; tests inject a
    scripted one so the runner itself can be scored without a model or a graph.
    """

    if answer_fn is None:
        from .service import answer as answer_fn  # noqa: N806 - default injection

    results: list[QuestionResult] = []
    for question in questions:
        user = user_resolver(question.user)
        context = _load_context(question, context_dir)
        response, run = answer_fn(
            runtime, user, question.question, mode=question.mode or mode, context=context
        )
        checks = evaluate_response(question, response)
        metadata = response.get("metadata") or {}
        results.append(
            QuestionResult(
                question=question,
                checks=checks,
                status=str(response.get("status") or ""),
                latency_ms=float(metadata.get("processingTimeMs") or 0.0),
                rounds=int(metadata.get("rounds") or 0),
            )
        )
    return Report(label="graph agent", results=results)


def _load_context(question: GoldenQuestion, context_dir: Path | str) -> Optional[str]:
    if not question.context_file:
        return None
    path = Path(context_dir) / question.context_file
    if not path.is_file():
        raise GoldenError(f"{question.id}: context file {path} not found")
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Vector-only baseline (no graph, no flags, no RBAC)
# --------------------------------------------------------------------------- #


def load_document_corpus(dataset: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The baseline corpus: every document's id and text, and nothing else."""

    corpus: list[tuple[str, str]] = []
    for doc in (dataset.get("sections") or {}).get("documents", []) or []:
        text = " ".join(
            str(part)
            for part in (doc.get("title"), doc.get("content"), *(_scalar_strings(doc.get("key_facts"))))
            if part
        )
        corpus.append((str(doc.get("id")), text))
    return corpus


_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.casefold()))


def vector_only_response(question: str, corpus: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Answer a question from the nearest document by token overlap (baseline).

    No graph, no ``current`` flags, no RBAC: a document hidden from the asker is
    still returned, which is exactly the gap the demo shows.
    """

    query = _tokens(question)
    best_id, best_text, best_score = "", "", -1.0
    for doc_id, text in corpus:
        doc_tokens = _tokens(text)
        if not doc_tokens:
            continue
        score = len(query & doc_tokens) / len(query | doc_tokens)
        if score > best_score:
            best_id, best_text, best_score = doc_id, text, score
    return {
        "answer": best_text,
        "references": [{"node_id": best_id, "status": "current"}] if best_id else [],
        "missing": [],
        "premise": [],
        "status": "verified",
        "metadata": {"rounds": 1, "processingTimeMs": 0.0},
    }


def run_baseline(
    dataset: Mapping[str, Any], questions: Sequence[GoldenQuestion]
) -> Report:
    """Run the vector-only RAG baseline over the same questions (09)."""

    corpus = load_document_corpus(dataset)
    results: list[QuestionResult] = []
    for question in questions:
        response = vector_only_response(question.question, corpus)
        results.append(
            QuestionResult(
                question=question,
                checks=evaluate_response(question, response),
                status=str(response.get("status") or ""),
                latency_ms=0.0,
                rounds=1,
            )
        )
    return Report(label="vector-only baseline", results=results)


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_fact_report(outcomes: Sequence[FactOutcome]) -> str:
    lines = ["offline fact check:"]
    for outcome in outcomes:
        mark = "ok  " if outcome.ok else "FAIL"
        lines.append(f"  [{mark}] {outcome.question} {outcome.kind}: {outcome.detail}")
    failed = sum(1 for outcome in outcomes if not outcome.ok)
    lines.append(f"  {len(outcomes) - failed}/{len(outcomes)} facts confirmed")
    return "\n".join(lines)


def render_report(report: Report) -> str:
    lines = [f"{report.label}: {report.passed}/{report.total} passed ({report.score:.0%})"]
    for result in report.results:
        mark = "PASS" if result.passed else "FAIL"
        lines.append(f"  [{mark}] {result.question.id} ({result.status or 'n/a'})")
        for check in result.failed:
            lines.append(f"         - {check.name}: {check.detail}")
    for area, score in report.area_scores().items():
        lines.append(f"  area {area}: {score:.0%}")
    lines.append(f"  mean latency {report.mean_latency_ms():.1f} ms, mean rounds {report.mean_rounds():.2f}")
    lines.append("  status: " + ", ".join(f"{k}={v}" for k, v in sorted(report.status_distribution().items())))
    return "\n".join(lines)


def render_comparison(agent: Report, baseline: Report, *, threshold: float = 0.0) -> str:
    lines = [render_report(agent), "", render_report(baseline), ""]
    lines.append(f"graph agent {agent.score:.0%} vs vector-only baseline {baseline.score:.0%}")
    lines.append(f"threshold {threshold:.0%}: {'PASS' if agent.score >= threshold else 'FAIL'}")
    return "\n".join(lines)


__all__ = [
    "FACT_KINDS",
    "GOLDEN_DIR",
    "CONTEXT_DIR",
    "GoldenError",
    "GoldenQuestion",
    "FactOutcome",
    "CheckOutcome",
    "QuestionResult",
    "Report",
    "load_golden_questions",
    "check_fact",
    "check_question_facts",
    "offline_check",
    "evaluate_response",
    "resolve_dataset_user",
    "run_questions",
    "load_document_corpus",
    "vector_only_response",
    "run_baseline",
    "render_fact_report",
    "render_report",
    "render_comparison",
]
