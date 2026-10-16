"""Tools: the model-facing operations over the query library.

Every Cypher statement lives in :mod:`noodle.queries`; a tool only shapes
arguments, converts rows into evidence items and collects traces. Contract
(03, 06): the user identity is supplied by the system and never appears in a
model-facing parameter list; a tool fails by raising, so an error can never
become a source; each evidence item is its own citable record, and ``ref`` is
always the record's own graph id. What the model sees
(:meth:`ToolResult.to_payload`) carries evidence only -- never hidden counts or
statements.

No company, person or record name appears in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

from .graph import GraphClient
from .queries import (
    HiddenInfo,
    QueryResult,
    UserCtx,
    hidden_twin_entity,
    merge_hidden,
    q1_search,
    q2_owners,
    q3_experts,
    q4_decision_chain,
    q4_decision_trace,
    q5_currency,
    q6_timeline,
    q7_conflicts,
    q8_stale_documents,
    q9_connection,
    q10_team_impact,
    q11_blast_radius,
    q12_freshness,
    role_label,
    score_experts,
)

# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Evidence:
    """One citable record (03). ``text`` is what quotes are checked against."""

    kind: str  # doc | chat | ticket | decision | change | project | person | role | connection
    label: str
    text: str
    ref: str
    date: Optional[str] = None
    url: Optional[str] = None
    status: str = "current"  # current | superseded, from the flag
    note: Optional[str] = None
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """What a tool produced: evidence for citing, traces for the UI graph, the
    merged hidden twin for the system's notice, and a note for the answer."""

    tool: str
    evidence: list[Evidence] = field(default_factory=list)
    trace: list[QueryResult] = field(default_factory=list)
    hidden: HiddenInfo = field(default_factory=HiddenInfo)
    note: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    reused: bool = False  # set by the loop when an identical call is repeated

    def to_payload(self) -> dict[str, Any]:
        """What the model is shown. Excludes hidden info and traces (A4)."""

        out: dict[str, Any] = {"tool": self.tool, "evidence": [asdict(e) for e in self.evidence]}
        if self.note:
            out["note"] = self.note
        out.update(self.payload)
        return out


_KIND_BY_LABEL = {
    "Document": "doc",
    "ChatMessage": "chat",
    "Ticket": "ticket",
    "Decision": "decision",
    "Change": "change",
    "Project": "project",
    "Person": "person",
    "Team": "team",
}


def _flag_status(current: Any) -> str:
    return "current" if current else "superseded"


def _doc_evidence(row: Mapping[str, Any], note: Optional[str] = None) -> Evidence:
    ref = row.get("id") or row.get("doc_id") or ""
    return Evidence(
        kind="doc",
        label=str(row.get("title") or ref),
        text=str(row.get("content") or row.get("title") or ""),
        ref=str(ref),
        date=row.get("created"),
        url=row.get("url"),
        status=_flag_status(row.get("current")),
        note=note,
        meta={
            "family": row.get("family"),
            "version": row.get("version"),
            "record_status": row.get("status"),
            "tags": list(row.get("tags") or []),
        },
    )


def _source_evidence(src: Mapping[str, Any]) -> Evidence:
    kind = _KIND_BY_LABEL.get(str(src.get("kind") or ""), "record")
    label = str(src.get("label") or src.get("id") or "")
    return Evidence(
        kind=kind,
        label=label,
        text=str(src.get("text") or label),
        ref=str(src.get("id") or ""),
        date=src.get("date"),
        url=src.get("url"),
        status=_flag_status(src.get("current")),
        meta={"record_status": src.get("status")},
    )


def _decision_evidence(row: Mapping[str, Any], note: Optional[str] = None) -> Evidence:
    key = row.get("key") or ""
    title = row.get("title") or row.get("id") or ""
    text = " ".join(
        part
        for part in (
            str(row.get("description") or ""),
            f"Rationale: {row['rationale']}." if row.get("rationale") else "",
            f"Impact: {row['impact']}." if row.get("impact") else "",
            f"Status: {row['status']}." if row.get("status") else "",
        )
        if part
    )
    return Evidence(
        kind="decision",
        label=f"{key} {title}".strip(),
        text=text,
        ref=str(row.get("id") or ""),
        date=row.get("date"),
        status=_flag_status(row.get("current")),
        note=note,
        meta={
            "approvers": [a.get("id") for a in (row.get("approvers") or []) if a],
            "dissent": [d.get("who", {}).get("id") for d in (row.get("dissent") or []) if d.get("who")],
            "sources": [s.get("id") for s in (row.get("sources") or []) if s],
        },
    )


def _role_evidence(person_id: str, name: str, rel: str, entity: str) -> Evidence:
    role = role_label(rel)
    return Evidence(
        kind="role",
        label=f"{role} of {entity}",
        text=f"{name} is the {role} of {entity}",
        ref=f"role:{rel}:{person_id}:{entity}",
        meta={"person": person_id, "entity": entity, "rel": rel},
    )


def _dissent_evidence(person_id: str, name: str, entity: str, evidence_ids: Any) -> Evidence:
    ids = [str(e) for e in (evidence_ids or [])]
    return Evidence(
        kind="role",
        label=f"dissent on {entity}",
        text=f"{name} dissents from {entity}; evidence: {', '.join(ids) if ids else 'on record'}",
        ref=f"role:DISSENTS:{person_id}:{entity}",
        meta={"person": person_id, "entity": entity, "evidence": ids},
    )


def _change_evidence(row: Mapping[str, Any]) -> Evidence:
    change_id = str(row.get("id") or "")
    parts = change_id[len("change:"):].rsplit(":", 2) if change_id.startswith("change:") else []
    entity = parts[0] if len(parts) == 3 else "the record"
    field_name = row.get("field") or (parts[1] if len(parts) == 3 else "field")

    def shown(value: Any) -> str:
        return str(value) if value is not None else "(unset)"

    text = (
        f"{field_name} changed from {shown(row.get('old_value'))} to {shown(row.get('new_value'))} "
        f"on {row.get('date') or 'an unknown date'} by {row.get('by') or 'an unknown person'}, "
        f"with the reason: {row.get('reason') or 'none recorded'}"
    )
    return Evidence(
        kind="change",
        label=f"{entity} {field_name}",
        text=text,
        date=row.get("date"),
        ref=change_id,
        status=_flag_status(row.get("current")),
        meta={"by_id": row.get("by_id"), "version": row.get("version"), "seq": row.get("seq")},
    )


def _expert_evidence(entry: Mapping[str, Any], topic: str) -> Evidence:
    items = list(entry.get("items") or [])
    listed = "; ".join(
        f"{item.get('role')} of {item.get('via')}" + (f" ({item.get('date')})" if item.get("date") else "")
        for item in items
    )
    return Evidence(
        kind="person",
        label=str(entry.get("name") or entry.get("person_id") or ""),
        text=f"{entry.get('name')}: {listed or 'no visible items'} (expertise score {entry.get('score')})",
        ref=str(entry.get("person_id") or ""),
        meta={"score": entry.get("score"), "items": items, "topic": topic},
    )


def _connection_evidence(a: str, b: str, index: int, row: Mapping[str, Any]) -> Evidence:
    names = [str(n) for n in (row.get("names") or row.get("nodes") or [])]
    rels = list(row.get("rels") or [])
    parts: list[str] = [names[0] if names else a]
    for i, rel in enumerate(rels[: max(0, len(names) - 1)]):
        parts.append(f"-{rel}-")
        parts.append(names[i + 1] if i + 1 < len(names) else "?")
    text = " ".join(parts)
    return Evidence(
        kind="connection",
        label=f"{parts[0]} to {parts[-1]}",
        text=text,
        ref=f"connection:{a}|{b}|{index}",
        meta={"nodes": row.get("nodes"), "rels": rels, "length": row.get("length")},
    )


# --------------------------------------------------------------------------- #
# Tool functions. Each takes the client and the system-supplied user only;
# everything else arrives validated from the registry.
# --------------------------------------------------------------------------- #


def _t_search_records(client: GraphClient, user: UserCtx, *, query: str, limit: int = 10) -> ToolResult:
    res = q1_search(client, user, query, limit=limit)
    return ToolResult("search_records", [_doc_evidence(row) for row in res.rows], [res], res.hidden)


def _t_find_owner(client: GraphClient, user: UserCtx, *, ref: str) -> ToolResult:
    res = q2_owners(client, user, ref)
    evidence = [
        _role_evidence(row["person_id"], row.get("name") or row["person_id"], row["rel"], row["entity"])
        for row in res.rows
    ]
    return ToolResult("find_owner", evidence, [res], res.hidden)


def _t_find_experts(client: GraphClient, user: UserCtx, *, ref: str) -> ToolResult:
    res = q3_experts(client, user, ref)
    latest = max((row.get("on") for row in res.rows if row.get("on")), default=None)
    ranked = score_experts(res.rows, topic_latest=latest)
    evidence = [_expert_evidence(entry, ref) for entry in ranked]
    payload = {"ranking": [{"person_id": e["person_id"], "score": e["score"]} for e in ranked]}
    return ToolResult("find_experts", evidence, [res], res.hidden, payload=payload)


def _t_trace_decision(client: GraphClient, user: UserCtx, *, ref: str) -> ToolResult:
    trace = q4_decision_trace(client, user, ref)
    hidden = merge_hidden(trace.hidden, hidden_twin_entity(client, user, ref))
    if not trace.rows:
        return ToolResult("trace_decision", [], [trace], hidden, note="no matching visible decision")

    row = trace.rows[0]
    decision_id = str(row["id"])
    currency_res = q5_currency(client, user, decision_id)
    chain = q4_decision_chain(client, user, decision_id)
    hidden = merge_hidden(hidden, currency_res.hidden, chain.hidden)

    head = currency_res.rows[0] if currency_res.rows else {}
    note: Optional[str] = None
    if head.get("is_current"):
        currency = "current"
    elif head.get("head_id"):
        currency = "superseded"
        note = f"superseded by {head.get('head_title')} on {head.get('head_date')}"
    elif currency_res.hidden.count:
        currency = "cannot_confirm"  # a hidden record supersedes it (06)
    else:
        currency = "superseded"
        note = "no current decision found in the supersession chain"

    evidence = [_decision_evidence(row, note=note)]
    for approver in row.get("approvers") or []:
        if approver and approver.get("id"):
            evidence.append(
                _role_evidence(approver["id"], approver.get("name") or approver["id"], "APPROVED", decision_id)
            )
    for dissenter in row.get("dissent") or []:
        who = (dissenter or {}).get("who") or {}
        if who.get("id"):
            evidence.append(
                _dissent_evidence(who["id"], who.get("name") or who["id"], decision_id, dissenter.get("evidence"))
            )
    for src in row.get("sources") or []:
        if src and src.get("id"):
            evidence.append(_source_evidence(src))

    payload = {"id": decision_id, "currency": currency, "chain": chain.rows}
    return ToolResult("trace_decision", evidence, [trace, currency_res, chain], hidden, payload=payload)


def _t_get_timeline(
    client: GraphClient, user: UserCtx, *, ref: str, field: Optional[str] = None, on: Optional[str] = None
) -> ToolResult:
    res = q6_timeline(client, user, ref, field=field, on=on)
    evidence = [_change_evidence(row) for row in res.rows]
    note = None
    payload: dict[str, Any] = {}
    if on:
        note = f"value of {res.rows[0]['field']} as of {on}: {res.rows[0]['new_value']}" if res.rows else None
        if res.rows:
            payload["value_on"] = res.rows[0]["new_value"]
    return ToolResult("get_timeline", evidence, [res], res.hidden, note=note, payload=payload)


def _t_find_conflicts(client: GraphClient, user: UserCtx) -> ToolResult:
    conflicts_res = q7_conflicts(client, user)
    stale_res = q8_stale_documents(client, user)
    trace = [conflicts_res, stale_res]
    evidence: list[Evidence] = []
    seen: set[str] = set()
    conflicts = []
    for row in conflicts_res.rows:
        conflicts.append(
            {
                "project_id": row["project_id"],
                "project": row["project"],
                "a_id": row["a_id"],
                "b_id": row["b_id"],
                "flagged": bool(row["flagged"]),
            }
        )
        for side_id in (row["a_id"], row["b_id"]):
            if side_id in seen:
                continue
            seen.add(side_id)
            side = q4_decision_trace(client, user, side_id)
            trace.append(side)
            if side.rows:
                evidence.append(_decision_evidence(side.rows[0]))
    stale_ids = []
    for row in stale_res.rows:
        stale_ids.append(row["doc_id"])
        superseded = row.get("superseded_decisions") or []
        evidence.append(
            _doc_evidence(row, note="rests on superseded decision(s): " + ", ".join(superseded))
        )
    payload = {"conflicts": conflicts, "stale_documents": stale_ids}
    hidden = merge_hidden(*[t.hidden for t in trace])
    return ToolResult("find_conflicts", evidence, trace, hidden, payload=payload)


def _t_connect_entities(client: GraphClient, user: UserCtx, *, a: str, b: str, limit: int = 5) -> ToolResult:
    res = q9_connection(client, user, a, b, limit=limit)
    evidence = [_connection_evidence(a, b, index, row) for index, row in enumerate(res.rows)]
    note = None
    if not res.rows:
        note = "no visible connection" if res.hidden.count else "no connection found"
    payload = {"connected": bool(res.rows), "paths": res.rows}
    return ToolResult("connect_entities", evidence, [res], res.hidden, note=note, payload=payload)


def _t_team_impact(
    client: GraphClient, user: UserCtx, *, team: Optional[str] = None, decision: Optional[str] = None,
    lost: int = 2, depth: int = 3,
) -> ToolResult:
    if not team and not decision:
        raise ValueError("team_impact needs a team id or a decision id")
    evidence: list[Evidence] = []
    trace: list[QueryResult] = []
    hidden = HiddenInfo()
    payload: dict[str, Any] = {}
    if team:
        res = q10_team_impact(client, user, team, lost=lost)
        trace.append(res)
        hidden = merge_hidden(hidden, res.hidden)
        for row in res.rows:
            downstream = row.get("downstream") or []
            text = (
                f"{row['team_staff']} of {row['total']} assignees on open tickets of {row['project']} "
                f"belong to the team; downstream dependents: {', '.join(downstream) or 'none'}"
            )
            evidence.append(
                Evidence(
                    kind="project", label=row["project"], text=text, ref=row["project_id"],
                    meta={"total": row["total"], "team_staff": row["team_staff"], "downstream": downstream},
                )
            )
        payload["team_risk"] = res.rows
    if decision:
        res = q11_blast_radius(client, user, decision, depth=depth)
        trace.append(res)
        hidden = merge_hidden(hidden, res.hidden)
        for row in res.rows:
            kind = _KIND_BY_LABEL.get(str(row.get("kind") or ""), "record")
            name = row.get("name") or row["id"]
            evidence.append(
                Evidence(
                    kind=kind, label=str(name), text=f"{name} is affected by {decision}",
                    ref=row["id"], meta={"kind": row.get("kind")},
                )
            )
        payload["blast_radius"] = res.rows
    return ToolResult("team_impact", evidence, trace, hidden, payload=payload)


def _t_get_freshness(client: GraphClient, user: UserCtx) -> ToolResult:
    res = q12_freshness(client, user)
    row = res.rows[0] if res.rows else {}
    note = f"data current as of {row.get('last_synced_at')}" if row else None
    payload = {"as_of": row.get("last_synced_at"), "dataset_version": row.get("version")}
    return ToolResult("get_freshness", [], [res], HiddenInfo(), note=note, payload=payload)


def _t_draft_followup(client: GraphClient, user: UserCtx, *, to: str, subject: str, body: str) -> ToolResult:
    for name, value in (("to", to), ("subject", subject), ("body", body)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"draft_followup: {name} must be a non-empty string")
    draft = {"to": to, "subject": subject, "body": body, "status": "draft", "sent": False}
    note = "Draft only; nothing is sent until a person confirms."
    return ToolResult("draft_followup", [], [], HiddenInfo(), note=note, payload={"draft": draft})


def _t_current_time(client: GraphClient, user: UserCtx) -> ToolResult:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return ToolResult("current_time", [], [], HiddenInfo(), payload={"now": now})


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tool:
    """A named operation with its model-facing contract. The parameters schema
    never includes the user identity or clearance (A6): those are supplied by
    the system."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., ToolResult]

    def run(self, client: GraphClient, user: UserCtx, args: Optional[Mapping[str, Any]]) -> ToolResult:
        return self.fn(client, user, **_validate(self.name, self.parameters, args or {}))


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def _validate(name: str, spec: Mapping[str, Any], args: Mapping[str, Any]) -> dict[str, Any]:
    """Closed-world argument checking: unknown keys, missing keys and wrong
    types are rejected before anything runs."""

    if not isinstance(args, Mapping):
        raise ValueError(f"{name}: arguments must be an object")
    properties = spec.get("properties") or {}
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ValueError(f"{name}: unknown argument(s): {', '.join(unknown)}")
    missing = [key for key in spec.get("required") or [] if key not in args]
    if missing:
        raise ValueError(f"{name}: missing required argument(s): {', '.join(missing)}")
    clean: dict[str, Any] = {}
    for key, value in args.items():
        expected = properties[key].get("type", "string")
        if expected in _TYPES:
            ok = isinstance(value, _TYPES[expected]) and not (expected == "integer" and isinstance(value, bool))
            if not ok:
                raise ValueError(f"{name}: {key} must be {expected}")
        enum = properties[key].get("enum")
        if enum and value not in enum:
            raise ValueError(f"{name}: {key} must be one of {enum}")
        clean[key] = value
    return clean


_TOOL_TABLE = (
    Tool(
        "search_records",
        "Search documents by topic. Use for open questions and material about a subject when no specific "
        "record is known. For recorded responsibility use find_owner instead; for why something was decided "
        "use trace_decision.",
        {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["query"]},
        _t_search_records,
    ),
    Tool(
        "find_owner",
        "Who is formally responsible for a record right now: owner, sponsor, lead, assignee, reporter, author "
        "or approver. Use for 'who owns / is responsible for X'. For informal expertise use find_experts.",
        {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
        _t_find_owner,
    ),
    Tool(
        "find_experts",
        "Who knows about a topic, ranked, with the records behind each score. Use for 'who should I ask about "
        "X'. For formal recorded responsibility use find_owner.",
        {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
        _t_find_experts,
    ),
    Tool(
        "trace_decision",
        "Why a decision was made and whether it still holds: description, rationale, approvers, dissent with "
        "evidence, sources and the supersession chain. Use for 'why was X decided' and 'is X still current'.",
        {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
        _t_trace_decision,
    ),
    Tool(
        "get_timeline",
        "History of a record as recorded changes, or the value one field held on a past date. Use for 'what "
        "changed' and 'what was X on date D'.",
        {"type": "object",
         "properties": {"ref": {"type": "string"}, "field": {"type": "string"}, "on": {"type": "string"}},
         "required": ["ref"]},
        _t_get_timeline,
    ),
    Tool(
        "find_conflicts",
        "Current decisions that contradict each other on one project, and current documents resting on "
        "superseded decisions. Use for 'conflict', 'contradiction' and 'out of date'.",
        {"type": "object", "properties": {}},
        _t_find_conflicts,
    ),
    Tool(
        "connect_entities",
        "How two records are related, as visible paths through the graph. Use for 'how are A and B connected'.",
        {"type": "object",
         "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["a", "b"]},
        _t_connect_entities,
    ),
    Tool(
        "team_impact",
        "What is at risk. Give team for the projects at risk if the team loses people; give decision for the "
        "blast radius of a decision. Use for 'what is at risk if...'.",
        {"type": "object",
         "properties": {"team": {"type": "string"}, "decision": {"type": "string"},
                        "lost": {"type": "integer"}, "depth": {"type": "integer"}},
         "required": []},
        _t_team_impact,
    ),
    Tool(
        "get_freshness",
        "The as-of time of the company records. Use for any question about today, now, latest or recent "
        "events before answering.",
        {"type": "object", "properties": {}},
        _t_get_freshness,
    ),
    Tool(
        "draft_followup",
        "Draft a message or ticket for a named person. Nothing is sent; a person confirms. Use when the user "
        "asks to draft or prepare communication.",
        {"type": "object",
         "properties": {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "subject", "body"]},
        _t_draft_followup,
    ),
    Tool(
        "current_time",
        "The current time of day. Use when the user asks for the time. For data freshness use get_freshness.",
        {"type": "object", "properties": {}},
        _t_current_time,
    ),
)

TOOLS: dict[str, Tool] = {tool.name: tool for tool in _TOOL_TABLE}


def tool_specs(names: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
    """The tool contracts shown to the model (never the user identity).

    ``names`` restricts the set to those tools, in order; ``None`` is every
    tool.
    """

    if names is None:
        chosen = list(_TOOL_TABLE)
    else:
        chosen = [TOOLS[name] for name in names]
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for tool in chosen
    ]


__all__ = ["Evidence", "ToolResult", "Tool", "TOOLS", "tool_specs"]
