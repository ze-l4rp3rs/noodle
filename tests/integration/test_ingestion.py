"""Step 3 done-when against a live FalkorDB.

Ingests the synthetic dataset into a throwaway graph, twice. Run with
``pytest -m integration``.
"""

from __future__ import annotations

import uuid

import pytest
from falkordb import FalkorDB

from noodle import dataset as ds
from noodle import ingestion as ing
from noodle import schema
from noodle.config import load_settings
from noodle.graph import open_reader, open_writer, parse_graph_url

pytestmark = pytest.mark.integration

DATASET = ds.DEFAULT_DATASET_PATH


@pytest.fixture
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

    name = f"noodle_it_{uuid.uuid4().hex[:8]}"
    reader = open_reader(settings.graph.url, name)
    writer = open_writer(settings.graph.url, name)
    schema.ensure_schema(writer, dimension=settings.graph.embedding_dimension)
    try:
        yield name, reader, writer
    finally:
        reader.close()
        writer.close()
        try:
            driver.execute_command("GRAPH.DELETE", name)
        except Exception:  # noqa: BLE001 - cleanup is best effort
            pass
        driver.close()


def test_ingest_then_second_run_is_a_no_op(graph):
    name, reader, writer = graph
    dataset = ds.load_dataset(DATASET)
    plan = ing.build_plan(dataset)

    first = ing.ingest(dataset, writer=writer, reader=reader)
    assert first.total_added == len(plan.nodes)
    assert first.relationships_written == len(plan.relationships)
    assert first.synced is True

    total_nodes = reader.ro_query("MATCH (n) RETURN count(n) AS n").rows[0]["n"]
    assert total_nodes == len(plan.nodes) + 1  # + the meta:sync node

    second = ing.ingest(dataset, writer=writer, reader=reader)
    assert second.total_added == 0
    assert second.total_changed == 0
    assert second.total_unchanged == len(plan.nodes)
    assert second.relationships_written == 0
    assert second.synced is False


def test_everything_written_is_described_by_the_schema(graph):
    _, reader, writer = graph
    dataset = ds.load_dataset(DATASET)
    ing.ingest(dataset, writer=writer, reader=reader)

    for label in schema.LABELS:
        result = reader.ro_query(
            f"MATCH (n:{label}) UNWIND keys(n) AS k RETURN DISTINCT k AS k"
        )
        allowed = set(schema.all_property_names(label))
        for row in result.rows:
            assert row["k"] in allowed, f"{label}.{row['k']} is not in the schema"

    rels = reader.ro_query("MATCH ()-[r]->() RETURN DISTINCT type(r) AS t")
    for row in rels.rows:
        assert row["t"] in schema.RELATIONSHIP_TYPES


def test_meta_sync_node_holds_the_snapshot_time(graph):
    _, reader, writer = graph
    dataset = ds.load_dataset(DATASET)
    ing.ingest(dataset, writer=writer, reader=reader)

    result = reader.ro_query(
        "MATCH (m:Meta {id: 'meta:sync'}) RETURN m.last_synced_at AS t, "
        "m.dataset_version AS v"
    )
    row = result.rows[0]
    assert row["t"] == dataset["metadata"]["last_synced_at"]
    assert row["v"] == dataset["metadata"]["dataset_version"]
