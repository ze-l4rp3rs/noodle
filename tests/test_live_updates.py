"""Step 13: incremental and live updates (04, 07, 09).

Done-when: editing one record changes exactly one node; superseding a decision
flips the old flag and the answer changes. Also covers origin-based relationship
removal, stale Change removal, prune, confirmed rebuild, partial live updates and
the token-protected ``POST /ingest`` endpoint. No database, no model.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noodle import dataset as ds
from noodle import ingestion as ing
from noodle import schema
from noodle.api import create_app
from noodle.config import load_settings
from noodle.graph import GraphClient, IngestWriter
from noodle.providers import ScriptedProvider
from noodle.service import build_runtime
from tests.fakes import FakeGraph, FakeIngestGraph

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / ds.DEFAULT_DATASET_PATH


@pytest.fixture()
def real() -> dict:
    return ds.load_dataset(DATASET_PATH)


@pytest.fixture()
def store() -> FakeIngestGraph:
    return FakeIngestGraph()


def _pair(store: FakeIngestGraph):
    reader = GraphClient("x:1", "g", graph=store)
    writer = IngestWriter("x:1", "g", graph=store)
    return reader, writer


def _new_decision(decision_id: str, key: str) -> dict:
    return {
        "id": decision_id,
        "key": key,
        "title": "A live decision",
        "date": "2026-10-01",
        "status": "accepted",
        "description": "Made during a live update.",
        "rationale": "Because.",
        "impact": "Small.",
        "approvers": ["person:ada-wren"],
    }


# --------------------------------------------------------------------------- #
# Incremental diffing
# --------------------------------------------------------------------------- #


def test_editing_one_record_changes_exactly_one_node(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    before = {node_id: node["fingerprint"] for node_id, node in store.nodes.items()}

    edited = copy.deepcopy(real)
    edited["sections"]["projects"][0]["status"] = "blocked"

    report = ing.ingest(edited, writer=writer, reader=reader)

    assert report.total_added == 0
    assert report.total_changed == 1
    assert report.changed.get("projects") == 1
    diffs = [
        node_id
        for node_id, node in store.nodes.items()
        if before.get(node_id) != node["fingerprint"]
    ]
    assert len(diffs) == 1


def test_loading_twice_reports_unchanged_and_writes_nothing(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    node_writes, rel_writes, meta_writes = (
        store.node_writes,
        store.rel_writes,
        store.meta_writes,
    )

    report = ing.ingest(real, writer=writer, reader=reader)

    assert report.total_added == 0
    assert report.total_changed == 0
    assert report.total_unchanged == len(store.nodes)
    assert report.relationships_written == 0
    assert store.node_writes == node_writes
    assert store.rel_writes == rel_writes
    assert store.meta_writes == meta_writes


def test_reassignment_removes_the_old_ownership_edge(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)

    project = real["sections"]["projects"][0]
    old_owner = project["owner"]
    new_owner = next(p["id"] for p in real["sections"]["people"] if p["id"] != old_owner)

    edited = copy.deepcopy(real)
    edited["sections"]["projects"][0]["owner"] = new_owner

    report = ing.ingest(edited, writer=writer, reader=reader)

    assert report.relationships_removed >= 1
    assert ("OWNS", new_owner, project["id"]) in store.rels
    assert ("OWNS", old_owner, project["id"]) not in store.rels


def test_removing_a_history_entry_drops_its_change_record(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    change_count = sum(1 for node in store.nodes.values() if node.get("_label") == schema.LABEL_CHANGE)

    edited = copy.deepcopy(real)
    edited["sections"]["history"].pop()
    edited["metadata"]["record_counts"]["history"] -= 1

    report = ing.ingest(edited, writer=writer, reader=reader)

    assert report.records_removed == 1
    remaining = sum(
        1 for node in store.nodes.values() if node.get("_label") == schema.LABEL_CHANGE
    )
    assert remaining == change_count - 1


def test_prune_removes_missing_records_but_keeps_documents(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    documents_before = {
        node_id for node_id, node in store.nodes.items() if node.get("_label") == schema.LABEL_DOCUMENT
    }

    edited = copy.deepcopy(real)
    ticket = edited["sections"]["tickets"].pop()
    # a pruned ticket takes its history with it, or the dataset is inconsistent
    edited["sections"]["history"] = [
        h for h in edited["sections"]["history"] if h.get("entity_id") != ticket["id"]
    ]
    edited["metadata"]["record_counts"]["tickets"] -= 1
    edited["metadata"]["record_counts"]["history"] = len(edited["sections"]["history"])

    report = ing.ingest(edited, writer=writer, reader=reader, prune=True)

    assert report.records_removed >= 1
    assert ticket["id"] not in store.nodes
    documents_after = {
        node_id for node_id, node in store.nodes.items() if node.get("_label") == schema.LABEL_DOCUMENT
    }
    assert documents_before <= documents_after


# --------------------------------------------------------------------------- #
# Rebuild
# --------------------------------------------------------------------------- #


def test_rebuild_requires_confirmation(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    with pytest.raises(ing.IngestionError):
        ing.ingest(real, writer=writer, reader=reader, rebuild=True)


def test_confirmed_rebuild_empties_and_reloads(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    report = ing.ingest(
        real, writer=writer, reader=reader, rebuild=True, confirm_rebuild=True
    )
    assert report.total_added == len(store.nodes)
    assert report.total_unchanged == 0


# --------------------------------------------------------------------------- #
# Partial (live) updates
# --------------------------------------------------------------------------- #


def test_partial_update_may_reference_existing_records(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)

    partial = {"sections": {"decisions": [_new_decision("decision:test-partial", "DEC-9001")]}}
    report = ing.ingest(partial, writer=writer, reader=reader, partial=True)

    assert report.total_added == 1
    assert "decision:test-partial" in store.nodes
    assert any(
        rel_type == "APPROVED" and dst == "decision:test-partial"
        for (rel_type, _src, dst) in store.rels
    )


def test_superseding_partial_update_flips_the_old_flag(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    target = "decision:self-host-queue"
    assert store.nodes[target]["current"] is True

    partial = {
        "sections": {"decisions": [_new_decision("decision:test-sup", "DEC-9002")]},
        "relationships": {
            "SUPERSEDES": [
                {"src": "decision:test-sup", "dst": target, "origin": "decision:test-sup"}
            ]
        },
    }
    report = ing.ingest(partial, writer=writer, reader=reader, partial=True)

    assert report.flags_flipped >= 1
    assert store.nodes[target]["current"] is False


def test_superseding_a_decision_changes_the_answer(real, store):
    """The whole done-when: a supersession flips the flag the answer relies on."""

    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    target = "decision:self-host-queue"
    before = ing.read_node_state(reader)[target].current
    assert before is True

    partial = {
        "sections": {"decisions": [_new_decision("decision:test-sup", "DEC-9002")]},
        "relationships": {
            "SUPERSEDES": [
                {"src": "decision:test-sup", "dst": target, "origin": "decision:test-sup"}
            ]
        },
    }
    ing.ingest(partial, writer=writer, reader=reader, partial=True)

    after = ing.read_node_state(reader)[target].current
    assert after is False
    assert before != after


# --------------------------------------------------------------------------- #
# POST /ingest
# --------------------------------------------------------------------------- #


def _api(store: FakeIngestGraph, *, up: bool = True) -> TestClient:
    graph = store if up else FakeGraph(raise_on="ro_query")
    client = GraphClient("x:1", "g", graph=graph)
    writer = IngestWriter("x:1", "g", graph=store)
    runtime = build_runtime(
        load_settings(),
        provider=ScriptedProvider([]),
        reviewer_provider=ScriptedProvider([]),
        client=client,
    )
    runtime.ingest_writer = writer
    return TestClient(create_app(runtime))


PARTIAL = {"sections": {"decisions": [_new_decision("decision:api-1", "DEC-9003")]}}


def test_ingest_endpoint_is_hidden_without_a_server_token(store, monkeypatch):
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    client = _api(store)
    response = client.post("/ingest", json=PARTIAL)
    assert response.status_code == 401
    assert "decision:api-1" not in store.nodes


def test_ingest_endpoint_rejects_a_wrong_token(store, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "s3cret")
    client = _api(store)
    response = client.post("/ingest", json=PARTIAL, headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_ingest_endpoint_rejects_an_invalid_dataset(store, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "s3cret")
    client = _api(store)
    response = client.post(
        "/ingest",
        json={"sections": {"decisions": [{"id": "decision:bad", "approvers": ["person:nobody"]}]}},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert response.status_code == 422
    assert response.json()["problems"]


def test_ingest_endpoint_rejects_an_oversized_body(store, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "s3cret")
    client = _api(store)
    big = "x" * (2 * 1024 * 1024)
    response = client.post(
        "/ingest",
        content=big,
        headers={"Authorization": "Bearer s3cret", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_ingest_endpoint_is_503_when_the_graph_is_down(store, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "s3cret")
    client = _api(store, up=False)
    response = client.post("/ingest", json=PARTIAL, headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 503


def test_ingest_endpoint_returns_the_change_report(real, store, monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "s3cret")
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)

    client = _api(store)
    response = client.post("/ingest", json=PARTIAL, headers={"Authorization": "Bearer s3cret"})

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == {"decisions": 1}
    assert body["synced"] is True
    assert "decision:api-1" in store.nodes
