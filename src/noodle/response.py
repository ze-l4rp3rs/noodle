"""The API response: one shape, one builder, shared by the HTTP API and the CLI.

A run (:class:`~noodle.pipeline.AnswerRun`) is turned into the 07 response object
here, and nothing else renders one. Because the CLI and ``/ask`` both call this,
the same run always produces the same response (07, step 9).

What this module adds on top of the run:

* ``access_notices`` -- the fixed soft dead-end text (06) the *system*, never the
  model, appends when RBAC filtered material out. It carries a contact and never
  a hidden title, id, count or type;
* ``graph_trace`` -- one entry per Cypher statement the tools ran, with the exact
  statement, its redacted params and the node/edge ids it touched (07, A6). Hidden
  node ids never appear;
* ``freshness`` -- the dataset's as-of time, read from the run's own freshness
  trace, plus the ``live_intent`` flag the router sets (step 10);
* ``proposed_actions`` -- drafts the model prepared, never sent (07);
* the SSE event stream (07), reconstructed from the finished run so the UI can
  build against the real contract.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

from .pipeline import AnswerRun

ACCESS_NOTICE_TEXT = (
    "There is relevant material above your access level. "
    "To request access, contact {contact}."
)
# 06: an empty or unresolvable owner falls back to this.
ACCESS_FALLBACK_CONTACT = "your administrator"

# 06: the fixed as-of line the system appends for a live-intent question (D2).
AS_OF_TEXT = "My data is current as of {as_of}. I can't see updates after that."

FRESHNESS_CYPHER_MARKER = "meta:sync"


def _access_notices(run: AnswerRun) -> list[dict[str, str]]:
    """The fixed soft dead-end notice (06), appended by the system, not the model.

    ``contact`` is the access owner's person id, the stable handle step 11 later
    resolves to a display name; it is never a hidden node's title, id or type.
    """

    if not run.hidden.hidden:
        return []
    owner = run.hidden.access_owners[0] if run.hidden.access_owners else ""
    contact = owner or ACCESS_FALLBACK_CONTACT
    return [{"text": ACCESS_NOTICE_TEXT.format(contact=contact), "contact": owner}]


def _graph_trace(run: AnswerRun) -> list[dict[str, Any]]:
    """One entry per Cypher statement executed, with the exact statement (07, A6)."""

    entries: list[dict[str, Any]] = []
    for call in run.tool_calls:
        for query in call.queries:
            entries.append(
                {
                    "tool": call.tool,
                    "cypher": query["cypher"],
                    "params": query["params"],
                    "node_ids": query["node_ids"],
                    "edge_ids": query["edge_ids"],
                    "rows": query["rows"],
                    "duration_ms": round(call.duration_ms, 3),
                    "reused": call.reused,
                }
            )
    return entries


def _proposed_actions(run: AnswerRun) -> list[dict[str, Any]]:
    """Drafts the model prepared. Nothing is sent; a person confirms (07)."""

    actions: list[dict[str, Any]] = []
    for call in run.tool_calls:
        if call.tool != "draft_followup" or call.status != "ok":
            continue
        actions.append(
            {
                "kind": "message",
                "recipient": str(call.args.get("to") or ""),
                "subject": str(call.args.get("subject") or ""),
                "body": str(call.args.get("body") or ""),
                "sources": list(call.sources),
                "requires_confirmation": True,
            }
        )
    return actions


def _as_of(run: AnswerRun) -> str:
    """The dataset's as-of time, read from the run's freshness trace if it ran."""

    for trace in run.traces:
        if FRESHNESS_CYPHER_MARKER in trace.cypher and trace.rows:
            value = trace.rows[0].get("last_synced_at")
            if value:
                return str(value)
    return ""


def build_response(
    run: AnswerRun,
    *,
    mode: str,
    generator_model: Optional[str] = None,
    reviewer_model: Optional[str] = None,
) -> dict[str, Any]:
    """The 07 response object for one completed run.

    When the question was live-intent, the fixed as-of line is appended to the
    answer by the system, not the model (05 section 5, 06, D2).
    """

    as_of = run.as_of or _as_of(run)
    answer = run.answer
    if run.live_intent and as_of:
        answer = f"{answer}\n\n{AS_OF_TEXT.format(as_of=as_of)}"

    return {
        "answer": answer,
        "references": [reference.to_dict() for reference in run.references],
        "status": run.status,
        "unverified": [dict(item) for item in run.unverified],
        "missing": list(run.missing),
        "premise": list(run.premise),
        "proposed_actions": _proposed_actions(run),
        "access_notices": _access_notices(run),
        "freshness": {"as_of": as_of, "live_intent": bool(run.live_intent)},
        "graph_trace": _graph_trace(run),
        "metadata": {
            "rounds": run.rounds,
            "approved": run.approved,
            "processingTimeMs": round(run.elapsed_ms, 3),
            "mode": mode,
            "generator_model": generator_model or "",
            "reviewer_model": reviewer_model or "",
        },
    }


def sse_event(name: str, data: Any) -> str:
    """One Server-Sent Event frame (07 streaming)."""

    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def stream_events(response: dict[str, Any], run: AnswerRun, *, mode: str) -> Iterator[str]:
    """The structured event stream for one completed run (07).

    The events are reconstructed from the finished run so the UI can build
    against the contract now; incremental streaming arrives with the router
    (step 10). Hidden content is never emitted.
    """

    yield sse_event("router", {"mode": mode})
    for call in run.tool_calls:
        yield sse_event("tool_call", {"tool": call.tool, "args": dict(call.args)})
        for query in call.queries:
            yield sse_event(
                "tool_result",
                {
                    "tool": call.tool,
                    "rows": query["rows"],
                    "cypher": query["cypher"],
                    "node_ids": query["node_ids"],
                    "edge_ids": query["edge_ids"],
                },
            )
    rounds = response["metadata"]["rounds"]
    yield sse_event("draft", {"round": rounds})
    yield sse_event("review", {"round": rounds, "problems": len(response["unverified"])})
    yield sse_event("answer", response)


__all__ = [
    "ACCESS_NOTICE_TEXT",
    "ACCESS_FALLBACK_CONTACT",
    "AS_OF_TEXT",
    "build_response",
    "sse_event",
    "stream_events",
]
