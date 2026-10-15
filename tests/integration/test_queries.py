"""Step 4 done-when against a live FalkorDB.

Loads the synthetic dataset into one throwaway graph for the whole module, then
runs every query at clearance 0 and clearance 3 and checks the RBAC behaviour
including RT2, RT3 and RT9. Run with ``pytest -m integration``.
"""

from __future__ import annotations

import uuid

import pytest
from falkordb import FalkorDB

from noodle import dataset as ds
from noodle import ingestion as ing
from noodle import queries as Q
from noodle import schema
from noodle.config import load_settings
from noodle.graph import open_reader, open_writer, parse_graph_url

pytestmark = pytest.mark.integration

DATASET = ds.DEFAULT_DATASET_PATH

PUBLIC = Q.UserCtx("person:unknown", clearance=0)
INTERNAL = Q.UserCtx("person:ada-wren", clearance=1)
RESTRICTED = Q.UserCtx("person:victor-hale", clearance=3)

# Every query function that must carry the predicate (hidden twins invert it),
# with the sample arguments used to exercise it.
FILTERED_QUERIES = (
    (Q.q1_search, ("auth",)),
    (Q.q2_owners, ("project:auth-migration",)),
    (Q.q3_experts, ("ticket:PLAT-882",)),
    (Q.q4_decision_trace, ("DEC-101",)),
    (Q.q4_decision_chain, ("decision:managed-queue",)),
    (Q.q5_currency, ("decision:vendor-choice",)),
    (Q.q6_timeline, ("ticket:PLAT-882",)),
    (Q.q7_conflicts, ()),
    (Q.q8_stale_documents, ()),
    (Q.q9_connection, ("person:iris-lund", "person:ken-zhao")),
    (Q.q10_team_impact, ("team:platform",)),
    (Q.q11_blast_radius, ("decision:self-host-queue",)),
)


@pytest.fixture(scope="module")
def graph():
    settings = load_settings()
    info = parse_graph_url(settings.graph.url)
    driver = FalkorDB(
        host=info.host,
        port=info.port,
        username=info.username,
        password=info.password,
        ssl=info.ssl,
    )
    try:
        if not driver.execute_command("PING"):
            pytest.skip("FalkorDB not reachable")
    except Exception:  # noqa: BLE001
        pytest.skip("FalkorDB not reachable")

    name = f"noodle_it_q_{uuid.uuid4().hex[:8]}"
    reader = open_reader(settings.graph.url, name)
    writer = open_writer(settings.graph.url, name)
    schema.ensure_schema(writer, dimension=settings.graph.embedding_dimension)
    data = ds.load_dataset(DATASET)
    ing.ingest(data, writer=writer, reader=reader)
    try:
        yield name, reader
    finally:
        reader.close()
        writer.close()
        try:
            driver.execute_command("GRAPH.DELETE", name)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass
        driver.close()


@pytest.mark.parametrize("fn,args", FILTERED_QUERIES, ids=lambda v: getattr(v, "__name__", v))
def test_query_runs_at_clearance_zero_and_three(graph, fn, args):
    _, reader = graph
    zero = fn(reader, PUBLIC, *args)
    three = fn(reader, RESTRICTED, *args)
    assert isinstance(zero, Q.QueryResult) and isinstance(three, Q.QueryResult)
    assert "clearance <= $clr" in zero.cypher
    assert "clearance <= $clr" in three.cypher


def test_no_query_leaks_a_parameterised_value(graph):
    _, reader = graph
    res = Q.q2_owners(reader, INTERNAL, "project:auth-migration")
    # The statement is returned, but never interpolated values.
    assert "$id" in res.cypher and "project:auth-migration" not in res.cypher


def test_user_context_is_redacted_from_params(graph):
    _, reader = graph
    res = Q.q2_owners(reader, INTERNAL, "project:auth-migration")
    assert "clr" not in res.params
    assert "user" not in res.params and "user_id" not in res.params


# --------------------------------------------------------------------------- #
# Q1 search and RT3
# --------------------------------------------------------------------------- #


def test_q1_search_finds_a_document(graph):
    _, reader = graph
    res = Q.q1_search(reader, INTERNAL, "auth")
    ids = {row["id"] for row in res.rows}
    assert "doc:rfc-031-auth-migration" in ids


def test_rt3_search_hidden_twin_counts_the_restricted_document(graph):
    _, reader = graph
    res = Q.q1_search(reader, INTERNAL, "incident")
    assert all(row["id"] != "doc:incident-postmortem" for row in res.rows)
    assert res.hidden.count == 1
    assert res.hidden.access_owners == ("person:victor-hale",)


def test_hidden_twin_carries_only_a_count_and_owners(graph):
    _, reader = graph
    res = Q.q1_search(reader, INTERNAL, "incident")
    assert set(res.hidden.access_owners) <= {"person:victor-hale"}
    assert not hasattr(res.hidden, "title")


# --------------------------------------------------------------------------- #
# Q2 owners, Q3 experts
# --------------------------------------------------------------------------- #


def test_q2_owners_names_the_owner(graph):
    _, reader = graph
    rows = Q.q2_owners(reader, INTERNAL, "project:auth-migration").rows
    owners = {(r["person_id"], r["rel"]) for r in rows}
    assert ("person:priya-nair", "OWNS") in owners


def test_q2_soft_dead_end_at_clearance_zero(graph):
    _, reader = graph
    res = Q.q2_owners(reader, PUBLIC, "project:auth-migration")
    assert res.rows == []
    assert res.hidden.hidden


def test_q3_experts_returns_people_with_evidence(graph):
    _, reader = graph
    res = Q.q3_experts(reader, INTERNAL, "ticket:PLAT-882")
    assert res.rows
    assert any(r["person_id"] == "person:priya-nair" for r in res.rows)
    assert Q.score_experts(res.rows)[0]["person_id"] == "person:priya-nair"


# --------------------------------------------------------------------------- #
# Q4 decision trace and RT2
# --------------------------------------------------------------------------- #


def test_q4_trace_has_approvers_and_sources(graph):
    _, reader = graph
    res = Q.q4_decision_trace(reader, INTERNAL, "DEC-101")
    row = res.rows[0]
    assert row["id"] == "decision:managed-queue"
    assert any(s["id"] == "doc:queue-adr" for s in row["sources"])


def test_rt2_hidden_source_is_excluded_and_counted(graph):
    _, reader = graph
    res = Q.q4_decision_trace(reader, INTERNAL, "DEC-101")
    source_ids = {s["id"] for s in res.rows[0]["sources"]}
    assert "doc:incident-postmortem" not in source_ids
    assert res.hidden.count == 1


def test_rt2_hidden_source_appears_at_clearance_three(graph):
    _, reader = graph
    res = Q.q4_decision_trace(reader, RESTRICTED, "DEC-101")
    source_ids = {s["id"] for s in res.rows[0]["sources"]}
    assert "doc:incident-postmortem" in source_ids


def test_q4_chain_has_every_hop_visible(graph):
    _, reader = graph
    chain = Q.q4_decision_chain(reader, RESTRICTED, "decision:managed-queue")
    assert any({n for n in row["nodes"]} >= {"decision:managed-queue"} for row in chain.rows)


# --------------------------------------------------------------------------- #
# Q5 currency and RT8
# --------------------------------------------------------------------------- #


def test_q5_hidden_superseder_at_internal_clearance(graph):
    _, reader = graph
    res = Q.q5_currency(reader, INTERNAL, "decision:vendor-choice")
    row = res.rows[0]
    assert row["is_current"] is False
    assert row["head_id"] is None
    assert res.hidden.count == 1


def test_rt8_superseder_visible_at_clearance_three(graph):
    _, reader = graph
    row = Q.q5_currency(reader, RESTRICTED, "decision:vendor-choice").rows[0]
    assert row["head_id"] == "decision:vendor-choice-override"


# --------------------------------------------------------------------------- #
# Q6 timeline, Q7 conflicts, Q8 stale
# --------------------------------------------------------------------------- #


def test_q6_timeline_shows_the_reassignment(graph):
    _, reader = graph
    rows = Q.q6_timeline(reader, INTERNAL, "ticket:PLAT-882").rows
    assignee = [r for r in rows if r["field"] == "assignee"]
    assert assignee and assignee[-1]["new_value"] == "person:priya-nair"


def test_q6_value_on_a_date(graph):
    _, reader = graph
    rows = Q.q6_timeline(
        reader, INTERNAL, "project:auth-migration", on="2026-04-01"
    ).rows
    assert rows  # an as-of answer is returned


def test_q7_finds_the_flagged_conflict(graph):
    _, reader = graph
    rows = Q.q7_conflicts(reader, INTERNAL).rows
    match = [r for r in rows if r["project_id"] == "project:search-rebuild"]
    assert match and match[0]["flagged"] is True
    assert {match[0]["a_id"], match[0]["b_id"]} == {"decision:search-elastic", "decision:search-postgres"}


def test_q8_finds_the_stale_document(graph):
    _, reader = graph
    rows = Q.q8_stale_documents(reader, INTERNAL).rows
    stale = {r["doc_id"] for r in rows}
    assert "doc:platform-strategy" in stale


# --------------------------------------------------------------------------- #
# Q9 connection and RT9
# --------------------------------------------------------------------------- #


def test_q9_finds_a_visible_connection(graph):
    _, reader = graph
    res = Q.q9_connection(reader, INTERNAL, "person:iris-lund", "person:ken-zhao")
    assert res.rows  # a visible path exists and is returned
    assert res.hidden.count >= 1  # a restricted hop exists too


def test_rt9_no_visible_path_but_a_hidden_one(graph):
    _, reader = graph
    res = Q.q9_connection(reader, INTERNAL, "person:wes-novak", "decision:freeze-hiring")
    assert res.rows == []
    assert res.hidden.count >= 1


# --------------------------------------------------------------------------- #
# Q10 team impact, Q11 blast radius, Q12 freshness
# --------------------------------------------------------------------------- #


def test_q10_team_impact(graph):
    _, reader = graph
    rows = Q.q10_team_impact(reader, INTERNAL, "team:platform").rows
    auth = [r for r in rows if r["project_id"] == "project:auth-migration"]
    assert auth and auth[0]["team_staff"] == 2 and auth[0]["total"] == 4


def test_q11_blast_radius(graph):
    _, reader = graph
    res = Q.q11_blast_radius(reader, INTERNAL, "decision:self-host-queue")
    assert res.rows
    assert res.rows[0]["affected"]


def test_q12_freshness_matches_the_dataset(graph):
    _, reader = graph
    data = ds.load_dataset(DATASET)
    row = Q.q12_freshness(reader, INTERNAL).rows[0]
    assert row["last_synced_at"] == data["metadata"]["last_synced_at"]
    assert row["version"] == data["metadata"]["dataset_version"]
