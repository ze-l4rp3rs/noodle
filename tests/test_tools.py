"""Step 5 unit tests: tools over the recording graph client.

No database, no model. The recording client (09) records each query and its
params and answers with prepared rows, so these tests pin the contract:
parameterised statements, evidence conversion with stable refs, failures
raising, and no user or clearance argument ever taken from the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from noodle import queries as Q
from noodle import tools as T
from noodle.graph import GraphError
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent
USER = Q.UserCtx("person:test-user", clearance=1)

# Substrings that tell the main statements and the hidden twins apart.
Q1 = "doc.clearance <= $clr"
TWIN_SEARCH = "doc.clearance > $clr"
Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
Q3 = "MATCH (t)-[*0..1]-(e)"
Q4 = "WITH d, approvers, dissent"
TWIN_SOURCES = "src.clearance > $clr"
Q5 = "h.current = true"
TWIN_SUPERSEDER = "h.clearance > $clr"
Q6 = ")-[:CHANGES]->(x {id: $id})"
Q7 = "CONTRADICTS"
Q8 = ":DOCUMENTED_IN]->"
Q9 = "MATCH p = (a)-["
TWIN_CONNECTION = "n.clearance > $clr"
Q10 = "MEMBER_OF"
TWIN_TEAM = "tm.clearance > $clr"
Q11 = "algo.BFS"
Q12 = "meta:sync"
TWIN_ENTITY = "WITH CASE WHEN x.clearance > $clr"


def run(name: str, args: dict, client) -> T.ToolResult:
    return T.TOOLS[name].run(client, USER, args)


# --------------------------------------------------------------------------- #
# Registry and guardrails
# --------------------------------------------------------------------------- #


def test_the_eleven_tools_are_registered():
    assert set(T.TOOLS) == {
        "search_records",
        "find_owner",
        "find_experts",
        "trace_decision",
        "get_timeline",
        "find_conflicts",
        "connect_entities",
        "team_impact",
        "get_freshness",
        "draft_followup",
        "current_time",
    }


def test_every_tool_says_when_to_use_it():
    for tool in T.TOOLS.values():
        assert len(tool.description) > 20, tool.name
        assert tool.parameters.get("type") == "object", tool.name


@pytest.mark.parametrize("name", sorted(T.TOOLS))
def test_no_tool_takes_a_user_or_clearance_argument(name):
    spec = T.TOOLS[name].parameters
    banned = {"user", "clearance", "clr", "access_token"}
    assert not banned & set(spec.get("properties", {})), name
    assert not banned & set(spec.get("required", [])), name


def test_tools_never_build_cypher():
    src = (ROOT / "src" / "noodle" / "tools.py").read_text(encoding="utf-8")
    for token in ("MATCH ", "OPTIONAL MATCH", "MERGE ", "UNWIND ", "CREATE ", "CALL ", "RETURN "):
        assert token not in src, token


@pytest.mark.parametrize("args", [{}, {"ref": "x", "extra": 1}, {"ref": 5}])
def test_bad_arguments_fail_closed(args):
    client = RecordingGraphClient()
    with pytest.raises(ValueError):
        run("find_owner", args, client)
    assert client.calls == []


# --------------------------------------------------------------------------- #
# search_records
# --------------------------------------------------------------------------- #


def test_search_records_is_parameterised_and_converts_evidence():
    client = RecordingGraphClient(
        (Q1, graph_result([
            {"id": "doc:one", "title": "Doc One", "content": "Text about queues.", "family": "f",
             "version": 1, "status": "accepted", "current": True, "url": "https://example.com/1",
             "tags": ["queues"], "score": 2.0, "author_id": "person:a", "author": "A", "decision_ids": []},
            {"id": "doc:old", "title": "Doc Old", "content": "Superseded text.", "family": "f",
             "version": 1, "status": "accepted", "current": False, "url": None,
             "tags": [], "score": 1.0, "author_id": None, "author": None, "decision_ids": []},
        ])),
        (TWIN_SEARCH, hidden_result(0)),
    )
    res = run("search_records", {"query": "queue rollout"}, client)

    cypher, params = client.calls[0]
    assert "queue rollout" not in cypher
    assert params["text"] == "queue rollout" and params["clr"] == 1
    assert [e.kind for e in res.evidence] == ["doc", "doc"]
    first, second = res.evidence
    assert first.ref == "doc:one" and first.text == "Text about queues."
    assert first.status == "current" and first.url == "https://example.com/1"
    assert second.status == "superseded"
    assert res.trace[0].cypher == cypher


def test_hidden_counts_never_reach_the_model_payload():
    client = RecordingGraphClient((Q1, graph_result([])), (TWIN_SEARCH, hidden_result(3)))
    res = run("search_records", {"query": "anything"}, client)
    assert res.hidden.count == 3
    payload = res.to_payload()
    assert "hidden" not in payload and "count" not in payload and "access_owners" not in payload


# --------------------------------------------------------------------------- #
# find_owner
# --------------------------------------------------------------------------- #


def test_find_owner_returns_role_statements_with_combined_refs():
    client = RecordingGraphClient(
        (Q2, graph_result([
            {"person_id": "person:p", "name": "P Person", "rel": "OWNS", "entity": "project:x"},
            {"person_id": "person:q", "name": "Q Person", "rel": "ASSIGNED_TO", "entity": "ticket:y"},
        ])),
        (TWIN_ENTITY, hidden_result(0)),
    )
    res = run("find_owner", {"ref": "project:x"}, client)
    _, params = client.calls[0]
    assert params["id"] == "project:x"
    assert [e.kind for e in res.evidence] == ["role", "role"]
    owner = res.evidence[0]
    assert owner.ref == "role:OWNS:person:p:project:x"
    assert owner.text == "P Person is the owner of project:x"
    assert owner.label == "owner of project:x"
    assert res.evidence[1].text == "Q Person is the assignee of ticket:y"


def test_a_failing_query_raises_and_is_never_a_source():
    client = RecordingGraphClient((Q2, GraphError("boom")))
    with pytest.raises(GraphError):
        run("find_owner", {"ref": "project:x"}, client)


# --------------------------------------------------------------------------- #
# find_experts
# --------------------------------------------------------------------------- #


def test_find_experts_ranks_people_and_lists_the_items():
    client = RecordingGraphClient(
        (Q3, graph_result([
            {"person_id": "person:p1", "name": "P One", "rel": "OWNS", "via": "project:x", "on": "2026-09-01"},
            {"person_id": "person:p2", "name": "P Two", "rel": "POSTED", "via": "chat:c:1", "on": "2026-09-01"},
            {"person_id": "person:p2", "name": "P Two", "rel": "POSTED", "via": "chat:c:2", "on": "2026-08-01"},
        ])),
        (TWIN_ENTITY, hidden_result(0)),
    )
    res = run("find_experts", {"ref": "project:x"}, client)
    assert [e.ref for e in res.evidence] == ["person:p1", "person:p2"]  # ranked, not alphabetical
    top = res.evidence[0]
    assert top.kind == "person" and top.text.startswith("P One")
    assert "project:x" in top.text  # the item behind the score
    assert res.payload["ranking"][0] == {"person_id": "person:p1", "score": 3.0}


# --------------------------------------------------------------------------- #
# trace_decision
# --------------------------------------------------------------------------- #


def _decision_row(current=False):
    return {
        "id": "decision:d", "key": "DEC-1", "title": "Pick the thing", "date": "2026-01-01",
        "status": "accepted", "description": "We pick the thing.", "rationale": "It is cheaper.",
        "impact": "Teams migrate.", "details": None, "current": current,
        "approvers": [{"id": "person:ap", "name": "Approver One"}],
        "dissent": [{"who": {"id": "person:ds", "name": "Dissenter Two"}, "evidence": ["chat:e:1"]}],
        "sources": [
            {"id": "doc:s", "kind": "Document", "status": "accepted", "current": True,
             "label": "Doc S", "text": "The document text.", "date": "2026-01-02", "url": None},
            {"id": "chat:chan:2026-01-02T10:00:00Z", "kind": "ChatMessage", "status": None,
             "current": True, "label": "chan", "text": "First message.", "date": "2026-01-02", "url": None},
            {"id": "chat:chan:2026-01-02T11:00:00Z", "kind": "ChatMessage", "status": None,
             "current": True, "label": "chan", "text": "Second message.", "date": "2026-01-02", "url": None},
        ],
    }


def _trace_client(head, superseder_hidden=0, entity_hidden=0, current=False):
    return RecordingGraphClient(
        (Q4, graph_result([_decision_row(current=current)])),
        (TWIN_SOURCES, hidden_result(0)),
        (Q5, graph_result([head])),
        (TWIN_SUPERSEDER, hidden_result(superseder_hidden, ["person:v"] if superseder_hidden else [])),
        ("MATCH p = (d)-[", graph_result([{"nodes": ["decision:d", "decision:h"], "rels": ["SUPERSEDED_BY"], "length": 1}])),
        (TWIN_ENTITY, hidden_result(entity_hidden)),
        (TWIN_ENTITY, hidden_result(entity_hidden)),
    )


def test_trace_decision_names_a_visible_superseder():
    res = run("trace_decision", {"ref": "DEC-1"}, _trace_client({
        "id": "decision:d", "is_current": False,
        "head_id": "decision:h", "head_title": "Reverse the pick", "head_date": "2026-02-02",
    }))
    decision = res.evidence[0]
    assert decision.kind == "decision" and decision.ref == "decision:d"
    assert decision.status == "superseded"
    assert "Reverse the pick" in decision.note
    assert res.payload["currency"] == "superseded"
    assert res.payload["chain"][0]["nodes"] == ["decision:d", "decision:h"]


def test_trace_decision_cannot_confirm_when_the_superseder_is_hidden():
    res = run("trace_decision", {"ref": "decision:d"}, _trace_client(
        {"id": "decision:d", "is_current": False, "head_id": None, "head_title": None, "head_date": None},
        superseder_hidden=1,
    ))
    assert res.payload["currency"] == "cannot_confirm"
    assert res.hidden.count >= 1
    assert res.evidence[0].note is None  # never names what it cannot see


def test_trace_decision_gives_each_chat_message_its_own_ref():
    res = run("trace_decision", {"ref": "DEC-1"}, _trace_client({
        "id": "decision:d", "is_current": True, "head_id": None, "head_title": None, "head_date": None,
    }, current=True))
    chat_refs = [e.ref for e in res.evidence if e.kind == "chat"]
    assert len(chat_refs) == 2 and len(set(chat_refs)) == 2
    assert chat_refs[0] == "chat:chan:2026-01-02T10:00:00Z"
    texts = [e.text for e in res.evidence if e.kind == "chat"]
    assert texts == ["First message.", "Second message."]
    assert res.evidence[0].status == "current"  # currency read from the row


def test_trace_decision_reports_dissent_and_approvers():
    res = run("trace_decision", {"ref": "DEC-1"}, _trace_client({
        "id": "decision:d", "is_current": True, "head_id": None, "head_title": None, "head_date": None,
    }, current=True))
    roles = [e for e in res.evidence if e.kind == "role"]
    assert any("Approver One" in e.text and "approver" in e.text for e in roles)
    dissent = [e for e in roles if "dissent" in e.text]
    assert dissent and dissent[0].meta["evidence"] == ["chat:e:1"]


def test_trace_decision_with_no_visible_match():
    client = RecordingGraphClient((Q4, graph_result([])), (TWIN_SOURCES, hidden_result(0)),
                                 (TWIN_ENTITY, hidden_result(0)))
    res = run("trace_decision", {"ref": "DEC-404"}, client)
    assert res.evidence == []
    assert res.note


# --------------------------------------------------------------------------- #
# get_timeline
# --------------------------------------------------------------------------- #


def test_get_timeline_builds_the_change_sentence():
    client = RecordingGraphClient(
        (Q6, graph_result([{
            "id": "change:ticket:t-1:assignee:2", "field": "assignee",
            "old_value": "person:a", "new_value": "person:b", "date": "2026-09-01",
            "reason": "The team moved.", "version": 2, "seq": 2, "current": True,
            "by_id": "person:c", "by": "C Person",
        }])),
        (TWIN_ENTITY, hidden_result(0)),
    )
    res = run("get_timeline", {"ref": "ticket:t-1"}, client)
    change = res.evidence[0]
    assert change.kind == "change" and change.ref == "change:ticket:t-1:assignee:2"
    assert change.label == "ticket:t-1 assignee"
    assert "assignee changed from person:a to person:b on 2026-09-01 by C Person" in change.text
    assert "The team moved." in change.text
    assert change.status == "current"
    _, params = client.calls[0]
    assert params["field"] is None and params["on"] is None


# --------------------------------------------------------------------------- #
# find_conflicts
# --------------------------------------------------------------------------- #


def _conflict_client():
    def decision_row(id_, title, current):
        return {"id": id_, "key": id_.upper(), "title": title, "date": "2026-01-01",
                "status": "accepted", "description": f"About {title}.", "rationale": "R",
                "impact": "I", "details": None, "current": current,
                "approvers": [], "dissent": [], "sources": []}

    return RecordingGraphClient(
        (Q7, graph_result([{"project_id": "project:web", "project": "Web", "a_id": "decision:a1",
                            "a_title": "Use engine A", "b_id": "decision:b1", "b_title": "Use engine B",
                            "flagged": True}])),
        (Q8, graph_result([{"doc_id": "doc:stale", "title": "Old plan", "content": "Plan text.",
                            "url": None, "family": "plan", "version": 1, "created": "2026-01-05",
                            "current": True, "superseded_decisions": ["decision:a1"]}])),
        ("d.id = $ref OR d.key = $ref OR toLower(d.title) = toLower($ref)", graph_result([decision_row("decision:a1", "Use engine A", True)])),
        (TWIN_SOURCES, hidden_result(0)),
        ("d.id = $ref OR d.key = $ref OR toLower(d.title) = toLower($ref)", graph_result([decision_row("decision:b1", "Use engine B", True)])),
        (TWIN_SOURCES, hidden_result(0)),
    )


def test_find_conflicts_cites_both_decisions_and_the_stale_doc():
    res = run("find_conflicts", {}, _conflict_client())
    kinds = [(e.kind, e.ref) for e in res.evidence]
    assert ("decision", "decision:a1") in kinds and ("decision", "decision:b1") in kinds
    stale = [e for e in res.evidence if e.ref == "doc:stale"]
    assert stale and "superseded" in stale[0].note
    assert res.payload["conflicts"] == [{"project_id": "project:web", "project": "Web",
                                         "a_id": "decision:a1", "b_id": "decision:b1", "flagged": True}]
    assert res.payload["stale_documents"] == ["doc:stale"]


# --------------------------------------------------------------------------- #
# connect_entities
# --------------------------------------------------------------------------- #


def test_connect_entities_renders_the_path():
    client = RecordingGraphClient(
        (Q9, graph_result([{"nodes": ["person:u", "ticket:t", "person:v"],
                            "names": ["U One", "Ticket T", "V Two"],
                            "rels": ["REPORTED_BY", "ASSIGNED_TO"], "length": 2}])),
        (TWIN_CONNECTION, hidden_result(0)),
    )
    res = run("connect_entities", {"a": "person:u", "b": "person:v"}, client)
    connection = res.evidence[0]
    assert connection.kind == "connection"
    assert "U One -REPORTED_BY- Ticket T -ASSIGNED_TO- V Two" in connection.text
    assert res.payload["connected"] is True
    _, params = client.calls[0]
    assert params["a"] == "person:u" and params["b"] == "person:v"


def test_connect_entities_says_no_visible_connection_when_hidden():
    client = RecordingGraphClient((Q9, graph_result([])), (TWIN_CONNECTION, hidden_result(2)))
    res = run("connect_entities", {"a": "person:u", "b": "decision:w"}, client)
    assert res.evidence == []
    assert res.payload["connected"] is False
    assert res.note == "no visible connection"
    assert res.hidden.count == 2


# --------------------------------------------------------------------------- #
# team_impact
# --------------------------------------------------------------------------- #


def test_team_impact_for_a_team():
    client = RecordingGraphClient(
        (Q10, graph_result([{"project_id": "project:x", "project": "X", "total": 4,
                             "team_staff": 2, "downstream": ["project:y"]}])),
        (TWIN_TEAM, hidden_result(0)),
    )
    res = run("team_impact", {"team": "team:eng", "lost": 2}, client)
    project = res.evidence[0]
    assert project.kind == "project" and project.ref == "project:x"
    assert "2 of 4" in project.text
    _, params = client.calls[0]
    assert params["team"] == "team:eng" and params["lost"] == 2


def test_team_impact_for_a_decision():
    client = RecordingGraphClient(
        (Q11, graph_result([{"id": "project:x", "name": "X", "kind": "Project"},
                            {"id": "project:y", "name": "Y", "kind": "Project"}])),
        (TWIN_ENTITY, hidden_result(0)),
    )
    res = run("team_impact", {"decision": "decision:drop"}, client)
    assert [e.ref for e in res.evidence] == ["project:x", "project:y"]
    assert all(e.kind == "project" for e in res.evidence)
    assert "decision:drop" in res.evidence[0].text


def test_team_impact_needs_a_team_or_a_decision():
    client = RecordingGraphClient()
    with pytest.raises(ValueError):
        run("team_impact", {}, client)
    assert client.calls == []


# --------------------------------------------------------------------------- #
# freshness, draft, time
# --------------------------------------------------------------------------- #


def test_get_freshness_returns_the_as_of_time():
    client = RecordingGraphClient((Q12, graph_result([
        {"last_synced_at": "2026-10-15T00:05:00Z", "version": "0.1.0"}])))
    res = run("get_freshness", {}, client)
    assert res.evidence == []
    assert res.payload["as_of"] == "2026-10-15T00:05:00Z"
    assert res.payload["dataset_version"] == "0.1.0"


def test_current_time_is_iso_utc_and_queries_nothing():
    client = RecordingGraphClient()
    res = run("current_time", {}, client)
    assert client.calls == []
    parsed = datetime.fromisoformat(res.payload["now"])
    assert parsed.tzinfo is not None
    assert (datetime.now(timezone.utc) - parsed).total_seconds() < 5


def test_draft_followup_never_sends_and_queries_nothing():
    client = RecordingGraphClient()
    res = run("draft_followup", {"to": "person:p", "subject": "S", "body": "B"}, client)
    assert client.calls == []
    draft = res.payload["draft"]
    assert draft["to"] == "person:p" and draft["subject"] == "S" and draft["body"] == "B"
    assert draft["sent"] is False
    assert "nothing is sent" in (res.note or "").lower()


def test_draft_followup_rejects_empty_fields():
    with pytest.raises(ValueError):
        run("draft_followup", {"to": "person:p", "subject": "", "body": "B"}, RecordingGraphClient())


# --------------------------------------------------------------------------- #
# model payload
# --------------------------------------------------------------------------- #


def test_to_payload_carry_evidence_but_no_trace():
    client = RecordingGraphClient(
        (Q1, graph_result([{"id": "doc:one", "title": "Doc One", "content": "Text.", "family": "f",
                            "version": 1, "status": "accepted", "current": True, "url": None,
                            "tags": [], "score": 1.0, "author_id": None, "author": None,
                            "decision_ids": []}])),
        (TWIN_SEARCH, hidden_result(0)),
    )
    res = run("search_records", {"query": "queues"}, client)
    payload = res.to_payload()
    assert payload["tool"] == "search_records"
    assert payload["evidence"][0]["ref"] == "doc:one"
    assert "trace" not in payload and "cypher" not in payload
    assert "clr" not in str(payload)
