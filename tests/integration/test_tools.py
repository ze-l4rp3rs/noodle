"""Step 5 done-when against a live FalkorDB: every tool runs against the real
graph loaded with the synthetic dataset. Run with ``pytest -m integration``.
"""

from __future__ import annotations

import uuid

import pytest
from falkordb import FalkorDB

from noodle import dataset as ds
from noodle import ingestion as ing
from noodle import queries as Q
from noodle import schema
from noodle import tools as T
from noodle.config import load_settings
from noodle.graph import open_reader, open_writer, parse_graph_url

pytestmark = pytest.mark.integration

DATASET = ds.DEFAULT_DATASET_PATH

INTERNAL = Q.UserCtx("person:ada-wren", clearance=1)
PUBLIC = Q.UserCtx("person:unknown", clearance=0)
RESTRICTED = Q.UserCtx("person:victor-hale", clearance=3)


@pytest.fixture(scope="module")
def graph():
    settings = load_settings()
    info = parse_graph_url(settings.graph.url)
    driver = FalkorDB(
        host=info.host, port=info.port, username=info.username,
        password=info.password, ssl=info.ssl,
    )
    try:
        if not driver.execute_command("PING"):
            pytest.skip("FalkorDB not reachable")
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")

    name = f"noodle_it_t_{uuid.uuid4().hex[:8]}"
    reader = open_reader(settings.graph.url, name)
    writer = open_writer(settings.graph.url, name)
    schema.ensure_schema(writer, dimension=settings.graph.embedding_dimension)
    ing.ingest(ds.load_dataset(DATASET), writer=writer, reader=reader)
    try:
        yield reader
    finally:
        reader.close()
        writer.close()
        try:
            driver.execute_command("GRAPH.DELETE", name)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass
        driver.close()


def run(reader, tool, user, args):
    return T.TOOLS[tool].run(reader, user, args)


# --------------------------------------------------------------------------- #
# Each tool against the real graph
# --------------------------------------------------------------------------- #


def test_search_records_finds_the_document_and_cites_it(graph):
    res = run(graph, "search_records", INTERNAL, {"query": "auth migration"})
    refs = [e.ref for e in res.evidence]
    assert "doc:rfc-031-auth-migration" in refs
    top = [e for e in res.evidence if e.ref == "doc:rfc-031-auth-migration"][0]
    assert top.kind == "doc" and top.text and top.status in {"current", "superseded"}
    # parameterised: the user's words are in params, never in the statement
    for trace in res.trace:
        assert "auth migration" not in trace.cypher


def test_search_records_at_clearance_zero_leaks_nothing(graph):
    res = run(graph, "search_records", PUBLIC, {"query": "auth"})
    assert res.evidence == []
    assert res.hidden.count > 0


def test_find_owner_reports_the_owner_not_the_former_assignee(graph):
    res = run(graph, "find_owner", INTERNAL, {"ref": "project:auth-migration"})
    owners = [e for e in res.evidence if e.meta.get("rel") == "OWNS"]
    assert owners and owners[0].meta["person"] == "person:priya-nair"
    assert owners[0].ref == "role:OWNS:person:priya-nair:project:auth-migration"
    assert res.hidden.count >= 1  # the restricted sponsor exists, unseen


def test_find_owner_at_clearance_zero_is_empty_with_a_notice_seed(graph):
    res = run(graph, "find_owner", PUBLIC, {"ref": "project:auth-migration"})
    assert res.evidence == []
    assert res.hidden.count == 1  # the record itself is above clearance 0


def test_find_experts_ranks_with_items_behind_the_score(graph):
    res = run(graph, "find_experts", INTERNAL, {"ref": "ticket:PLAT-882"})
    assert res.evidence
    assert res.evidence[0].meta["items"]
    assert res.payload["ranking"][0]["person_id"] == res.evidence[0].ref


def test_trace_decision_walks_the_chain_at_internal(graph):
    res = run(graph, "trace_decision", INTERNAL, {"ref": "decision:managed-queue"})
    decision = res.evidence[0]
    assert decision.kind == "decision" and decision.status == "superseded"
    assert res.payload["currency"] == "superseded"
    assert res.payload["id"] == "decision:managed-queue"
    assert res.payload["chain"]
    refs = [e.ref for e in res.evidence]
    assert "doc:incident-postmortem" not in refs  # RT2: hidden source never cited
    assert res.hidden.count >= 1


def test_trace_decision_cannot_confirm_a_hidden_superseder(graph):
    res = run(graph, "trace_decision", INTERNAL, {"ref": "decision:vendor-choice"})
    assert res.payload["currency"] == "cannot_confirm"  # RT8 at tool level
    assert res.hidden.count >= 1
    assert all("override" not in (e.note or "") for e in res.evidence)


def test_trace_decision_names_the_head_at_restricted(graph):
    res = run(graph, "trace_decision", RESTRICTED, {"ref": "decision:vendor-choice"})
    assert res.payload["currency"] == "superseded"
    assert "decision:vendor-choice-override" in str(res.payload["chain"])


def test_get_timeline_shows_the_reassignment(graph):
    res = run(graph, "get_timeline", INTERNAL, {"ref": "ticket:PLAT-882"})
    assignee = [e for e in res.evidence if e.meta.get("seq") == 2]
    assert assignee and "person:noah-fenn" in assignee[0].text
    assert "person:priya-nair" in assignee[0].text
    assert assignee[0].ref.startswith("change:ticket:PLAT-882:assignee:")


def test_get_timeline_value_on_a_date(graph):
    res = run(graph, "get_timeline", INTERNAL, {
        "ref": "project:auth-migration", "on": "2026-09-30"})
    assert res.payload["value_on"] == "2026-10-01"
    assert "2026-10-01" in res.note


def test_find_conflicts_cites_both_sides_and_the_stale_doc(graph):
    res = run(graph, "find_conflicts", INTERNAL, {})
    pair = [c for c in res.payload["conflicts"] if c["project_id"] == "project:search-rebuild"]
    assert pair and pair[0]["flagged"] is True
    decision_refs = {e.ref for e in res.evidence if e.kind == "decision"}
    assert {"decision:search-elastic", "decision:search-postgres"} <= decision_refs
    assert "doc:platform-strategy" in res.payload["stale_documents"]


def test_connect_entities_renders_a_visible_path(graph):
    res = run(graph, "connect_entities", INTERNAL, {"a": "person:iris-lund", "b": "person:ken-zhao"})
    assert res.payload["connected"] is True
    assert res.evidence and res.evidence[0].kind == "connection"
    assert "-ASSIGNED_TO-" in res.evidence[0].text or "-REPORTED_BY-" in res.evidence[0].text


def test_connect_entities_reports_no_visible_connection_when_hidden(graph):
    res = run(graph, "connect_entities", INTERNAL,
              {"a": "person:wes-novak", "b": "decision:freeze-hiring"})
    assert res.payload["connected"] is False  # RT9 at tool level
    assert res.note == "no visible connection"
    assert res.hidden.count >= 1


def test_team_impact_for_a_team(graph):
    res = run(graph, "team_impact", INTERNAL, {"team": "team:platform"})
    auth = [e for e in res.evidence if e.ref == "project:auth-migration"]
    assert auth and "2 of 4" in auth[0].text


def test_team_impact_for_a_decision(graph):
    res = run(graph, "team_impact", INTERNAL, {"decision": "decision:self-host-queue"})
    assert [e.ref for e in res.evidence] == ["project:auth-migration"]
    assert res.evidence[0].kind == "project"


def test_get_freshness_matches_the_dataset(graph):
    data = ds.load_dataset(DATASET)
    res = run(graph, "get_freshness", INTERNAL, {})
    assert res.payload["as_of"] == data["metadata"]["last_synced_at"]
    assert res.payload["dataset_version"] == data["metadata"]["dataset_version"]


def test_every_tool_trace_carries_the_predicate_and_params(graph):
    res = run(graph, "trace_decision", INTERNAL, {"ref": "decision:managed-queue"})
    assert res.trace
    for trace in res.trace:
        assert trace.cypher
        assert trace.params != {} or "meta:sync" in trace.cypher
    model_payload = res.to_payload()
    assert "hidden" not in model_payload and "trace" not in model_payload
