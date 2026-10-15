"""Step 3 done-when: ingest loads every record; a second run writes nothing and
reports all unchanged; everything written is described by the schema."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from noodle import dataset as ds
from noodle import ingestion as ing
from noodle import schema
from noodle.graph import GraphClient, IngestWriter
from tests.fakes import FakeIngestGraph

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


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def test_plan_has_one_node_per_record(real):
    plan = ing.build_plan(real)
    expected = sum(len(records) for records in real["sections"].values())
    assert len(plan.nodes) == expected
    assert len({n.id for n in plan.nodes}) == expected


def test_plan_uses_only_schema_labels_types_and_props(real):
    plan = ing.build_plan(real)
    for node in plan.nodes:
        assert node.label in schema.LABELS
        allowed = set(schema.all_property_names(node.label))
        assert set(node.props) <= allowed, node.id
    for rel in plan.relationships:
        assert rel.type in schema.RELATIONSHIP_TYPES
        assert rel.from_label in schema.RELATIONSHIP_BY_TYPE[rel.type].from_labels
        assert rel.to_label in schema.RELATIONSHIP_BY_TYPE[rel.type].to_labels


def test_plan_produces_relationships(real):
    plan = ing.build_plan(real)
    assert len(plan.relationships) > 22


def test_build_plan_rejects_dangling_reference(real):
    victim = real["sections"]["tickets"][0]
    victim["assignee"] = "person:nobody"
    with pytest.raises(ing.IngestionError) as excinfo:
        ing.build_plan(real)
    message = str(excinfo.value)
    assert "person:nobody" in message
    assert victim["id"] in message


def test_build_plan_rejects_wrong_endpoint_label(real):
    real["sections"]["projects"][0]["owner"] = "project:auth-migration"
    with pytest.raises(ing.IngestionError) as excinfo:
        ing.build_plan(real)
    assert "OWNS" in str(excinfo.value)


def test_unknown_relationship_list_is_noted_and_skipped(real):
    real["relationships"]["TOTALLY_MADE_UP"] = [
        {"src": "person:ada-wren", "dst": "project:auth-migration", "origin": "x"}
    ]
    plan = ing.build_plan(real)
    assert any("TOTALLY_MADE_UP" in note for note in plan.notes)


def test_everything_written_is_in_the_schema_description(real):
    plan = ing.build_plan(real)
    description = schema.describe_schema()
    for label in {node.label for node in plan.nodes}:
        assert label in description
    for rel_type in {rel.type for rel in plan.relationships}:
        assert rel_type in description


# --------------------------------------------------------------------------- #
# Derived current flags
# --------------------------------------------------------------------------- #


def _current(real) -> dict:
    plan = ing.build_plan(real)
    return {node.id: node.props.get("current") for node in plan.nodes}


def test_decision_current_flags(real):
    current = _current(real)
    assert current["decision:managed-queue"] is False
    assert current["decision:self-host-queue"] is True
    # Two conflicting current decisions must both stay current (trap 7).
    assert current["decision:search-elastic"] is True
    assert current["decision:search-postgres"] is True
    # A restricted decision supersedes a visible one (trap 11).
    assert current["decision:vendor-choice"] is False
    assert current["decision:vendor-choice-override"] is True
    assert current["decision:freeze-hiring"] is True


def test_document_current_flags(real):
    current = _current(real)
    assert current["doc:billing-rfc-v1"] is False
    assert current["doc:billing-rfc-v2"] is True
    assert current["doc:platform-strategy"] is True


def test_change_current_flags(real):
    current = _current(real)
    assert current["change:ticket:PLAT-882:assignee:2"] is True
    assert current["change:project:auth-migration:target_end_date:2"] is True


# --------------------------------------------------------------------------- #
# Provenance edges
# --------------------------------------------------------------------------- #


def test_provenance_edges_are_present(real):
    plan = ing.build_plan(real)
    keys = {(rel.type, rel.src, rel.dst) for rel in plan.relationships}
    assert ("CHANGES", "change:ticket:PLAT-882:assignee:2", "ticket:PLAT-882") in keys
    assert (
        "MADE_CHANGE",
        "person:ada-wren",
        "change:ticket:PLAT-882:assignee:2",
    ) in keys
    assert (
        "SUPERSEDED_BY",
        "decision:managed-queue",
        "decision:self-host-queue",
    ) in keys
    assert (
        "SOURCED_FROM",
        "decision:self-host-queue",
        "chat:#platform-leads:2026-09-12T10:05:00Z",
    ) in keys
    assert (
        "DISSENTS",
        "person:dana-okoro",
        "decision:self-host-queue",
    ) in keys
    # Name found in chat text.
    assert (
        "MENTIONS",
        "chat:#platform-leads:2026-09-12T09:00:00Z",
        "person:noah-fenn",
    ) in keys


def test_relationships_are_deduplicated(real):
    plan = ing.build_plan(real)
    keys = [(rel.type, rel.src, rel.dst) for rel in plan.relationships]
    assert len(keys) == len(set(keys))


# --------------------------------------------------------------------------- #
# The diff and write loop
# --------------------------------------------------------------------------- #


def test_first_ingest_writes_every_record(real, store):
    reader, writer = _pair(store)
    plan = ing.build_plan(real)
    report = ing.ingest(real, writer=writer, reader=reader)

    assert report.total_added == len(plan.nodes)
    assert report.total_changed == 0
    assert report.total_unchanged == 0
    assert report.relationships_written == len(plan.relationships)
    assert len(store.nodes) == len(plan.nodes)
    assert len(store.rels) == len(plan.relationships)
    assert report.synced is True


def test_meta_carries_the_dataset_sync_time(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    assert store.meta["last_synced_at"] == real["metadata"]["last_synced_at"]
    assert store.meta["version"] == real["metadata"]["dataset_version"]
    assert store.meta["id"] == ing.META_ID


def test_second_ingest_reports_unchanged_and_writes_nothing(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    node_writes = store.node_writes
    rel_writes = store.rel_writes
    meta_writes = store.meta_writes

    report = ing.ingest(real, writer=writer, reader=reader)

    assert report.total_added == 0
    assert report.total_changed == 0
    assert report.total_unchanged == len(store.nodes)
    assert report.relationships_written == 0
    assert report.synced is False
    assert store.node_writes == node_writes
    assert store.rel_writes == rel_writes
    assert store.meta_writes == meta_writes


def test_touch_advances_meta_without_changing_records(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)
    node_writes = store.node_writes
    report = ing.ingest(real, writer=writer, reader=reader, touch=True)
    assert report.synced is True
    assert report.total_unchanged == len(store.nodes)
    assert store.node_writes == node_writes


def test_changed_record_is_detected(real, store):
    reader, writer = _pair(store)
    ing.ingest(real, writer=writer, reader=reader)

    edited = copy.deepcopy(real)
    edited["sections"]["projects"][0]["status"] = "blocked"
    report = ing.ingest(edited, writer=writer, reader=reader)

    assert report.total_added == 0
    assert report.total_changed == 1
    assert report.changed.get("projects") == 1


def test_failed_batch_is_retried_entry_by_entry(real, store):
    reader, writer = _pair(store)
    store.fail_batch_over = 1
    report = ing.ingest(real, writer=writer, reader=reader)
    assert report.total_added == len(store.nodes)
    assert len(store.nodes) == sum(len(r) for r in real["sections"].values())


def test_failing_entry_is_named(real, store):
    reader, writer = _pair(store)
    store.fail_ids = {"person:ada-wren"}
    with pytest.raises(ing.IngestionError) as excinfo:
        ing.ingest(real, writer=writer, reader=reader)
    message = str(excinfo.value)
    assert "person:ada-wren" in message
    assert "Person" in message


def test_read_node_state_on_empty_graph_is_empty():
    reader = GraphClient("x:1", "g", graph=FakeIngestGraph(empty_key=True))
    assert ing.read_node_state(reader) == {}
    assert ing.read_relationships(reader) == set()


def test_fingerprint_ignores_authoring_notes():
    a = ing.record_fingerprint({"id": "x", "name": "n", "authoring_notes": "one"})
    b = ing.record_fingerprint({"id": "x", "name": "n", "authoring_notes": "two"})
    c = ing.record_fingerprint({"id": "x", "name": "different"})
    assert a == b
    assert a != c
