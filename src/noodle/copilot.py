"""The copilot surface: graph context for a node and heavy-mode drafts (07, 12).

``context`` gathers what an operator needs about one selected node -- owners,
decisions, prior documents, dependencies and stale warnings -- by calling the
query library, so every row is already clearance-filtered (A1) and no hidden id
can appear. A hidden twin still raises the fixed soft-dead-end notice through the
shared builder, exactly like ``/ask`` (06).

``draft_question`` turns a ``/copilot/draft`` request into a heavy-mode question;
the endpoint runs the normal pipeline, so a draft is cited or its unsupported
sentences are flagged and removed (05), and ``draft_followup`` only ever proposes
an action -- nothing is sent (E6).

No company, person or record name appears in this module.
"""

from __future__ import annotations

from typing import Any, Optional

from .graph import GraphClient
from .queries import (
    HiddenInfo,
    QueryResult,
    UserCtx,
    merge_hidden,
    q2_owners,
    q4_decision_chain,
    q4_decision_trace,
    q5_currency,
    q8_stale_documents,
    q10_team_impact,
    q11_blast_radius,
    resolve_access_owner_names,
    role_label,
)
from .response import access_notices


def _kind(entity_id: str) -> str:
    return entity_id.split(":", 1)[0] if ":" in entity_id else ""


def _currency(currency_res: QueryResult) -> tuple[str, Optional[str]]:
    """The same currency rule ``trace_decision`` uses (05, 06)."""

    head = currency_res.rows[0] if currency_res.rows else {}
    if head.get("is_current"):
        return "current", None
    if head.get("head_id"):
        return "superseded", f"superseded by {head.get('head_title')} on {head.get('head_date')}"
    if currency_res.hidden.count:
        return "cannot_confirm", None
    return "superseded", "no current decision found in the supersession chain"


def _decision_view(row: dict[str, Any], currency_res: QueryResult) -> dict[str, Any]:
    currency, note = _currency(currency_res)
    return {
        "id": row.get("id"),
        "key": row.get("key"),
        "title": row.get("title"),
        "date": row.get("date"),
        "status": row.get("status"),
        "current": bool(row.get("current")),
        "currency": currency,
        "note": note,
        "description": row.get("description"),
        "rationale": row.get("rationale"),
        "impact": row.get("impact"),
        "approvers": [a.get("id") for a in (row.get("approvers") or []) if a],
        "dissent": [d.get("who", {}).get("id") for d in (row.get("dissent") or []) if d.get("who")],
    }


def _source_view(src: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": src.get("id"),
        "kind": src.get("kind"),
        "label": src.get("label"),
        "date": src.get("date"),
        "url": src.get("url"),
        "status": "current" if src.get("current") else "superseded",
        "excerpt": src.get("text"),
    }


def _affected_view(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row.get("id"), "name": row.get("name"), "kind": row.get("kind")}


def _team_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_id": row.get("project_id"),
        "project": row.get("project"),
        "team_staff": row.get("team_staff"),
        "total": row.get("total"),
        "downstream": list(row.get("downstream") or []),
    }


def _stale_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": row.get("doc_id"),
        "title": row.get("title"),
        "url": row.get("url"),
        "superseded_decisions": list(row.get("superseded_decisions") or []),
    }


def context(client: GraphClient, user: UserCtx, entity_id: str) -> dict[str, Any]:
    """What is known about one node: decisions, owners, documents, risks (07).

    The shape is stable for every node kind: the sections that do not apply are
    empty lists, never missing keys, so a UI can render the panel uniformly.
    Owners come from Q2, a decision's trace and currency from Q4/Q4b/Q5, a team's
    risk from Q10, a decision's blast radius from Q11, and stale documents from
    Q8 (narrowed to the selected node when the node is a decision or a document).
    """

    kind = _kind(entity_id)
    hidden = HiddenInfo()

    owners_res = q2_owners(client, user, entity_id)
    hidden = merge_hidden(hidden, owners_res.hidden)
    owners = [
        {
            "person_id": row.get("person_id"),
            "name": row.get("name"),
            "rel": row.get("rel"),
            "role": role_label(row.get("rel") or ""),
            "entity": row.get("entity"),
        }
        for row in owners_res.rows
    ]

    decisions: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    dependencies: list[dict[str, Any]] = []

    if kind == "decision":
        trace = q4_decision_trace(client, user, entity_id)
        hidden = merge_hidden(hidden, trace.hidden)
        if trace.rows:
            row = trace.rows[0]
            decision_id = str(row.get("id"))
            currency_res = q5_currency(client, user, decision_id)
            chain = q4_decision_chain(client, user, decision_id)
            hidden = merge_hidden(hidden, currency_res.hidden, chain.hidden)
            decisions.append(_decision_view(row, currency_res))
            documents = [_source_view(src) for src in (row.get("sources") or []) if src and src.get("id")]
        blast = q11_blast_radius(client, user, entity_id)
        hidden = merge_hidden(hidden, blast.hidden)
        dependencies = [_affected_view(row) for row in blast.rows]
    elif kind == "team":
        impact = q10_team_impact(client, user, entity_id)
        hidden = merge_hidden(hidden, impact.hidden)
        dependencies = [_team_view(row) for row in impact.rows]

    stale_res = q8_stale_documents(client, user)
    hidden = merge_hidden(hidden, stale_res.hidden)
    stale_warnings: list[dict[str, Any]] = []
    for row in stale_res.rows:
        superseded = list(row.get("superseded_decisions") or [])
        if kind == "decision" and entity_id not in superseded:
            continue
        if kind == "doc" and row.get("doc_id") != entity_id:
            continue
        stale_warnings.append(_stale_view(row))

    names = resolve_access_owner_names(client, hidden)
    return {
        "entity_id": entity_id,
        "kind": kind,
        "owners": owners,
        "decisions": decisions,
        "documents": documents,
        "dependencies": dependencies,
        "stale_warnings": stale_warnings,
        "hidden": hidden.hidden,
        "access_notices": access_notices(hidden, names),
    }


def draft_question(entity_id: str, instruction: str, draft_text: Optional[str] = None) -> str:
    """Compose the heavy-mode question for a ``/copilot/draft`` request (07).

    The endpoint adds no state and sends nothing: the question runs through the
    normal pipeline, so citations, quote checks and hidden-data checks all apply.
    """

    parts = [
        f"Draft a follow-up about {entity_id}.",
        f"Instruction: {instruction}",
    ]
    if draft_text:
        parts.append("Improve this existing draft, keeping every fact that is already supported:")
        parts.append(draft_text)
    parts.append(
        "Use the records for every fact and cite it; list anything the records do not "
        "support rather than asserting it. Do not send anything."
    )
    return "\n\n".join(parts)


__all__ = ["context", "draft_question"]
