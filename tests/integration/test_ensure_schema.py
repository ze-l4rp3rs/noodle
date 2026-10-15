"""Step 1 done-when: FalkorDB healthy and ``ensure_schema()`` is idempotent.

Needs a live FalkorDB (Docker). Run with ``pytest -m integration``.
"""

from __future__ import annotations

import pytest

from noodle.config import load_settings
from noodle.graph import open_reader, open_writer
from noodle.schema import INDEX_SPECS, ensure_schema

pytestmark = pytest.mark.integration


@pytest.fixture
def graph_handles():
    settings = load_settings()
    reader = open_reader(settings.graph.url, settings.graph.name)
    if not reader.ping():
        pytest.skip("FalkorDB not reachable")
    writer = open_writer(settings.graph.url, settings.graph.name)
    yield reader, writer, settings
    reader.close()
    writer.close()


def test_ping_reports_up(graph_handles):
    reader, _, _ = graph_handles
    assert reader.ping() is True


def test_ensure_schema_creates_then_is_idempotent(graph_handles):
    _, writer, settings = graph_handles
    dimension = settings.graph.embedding_dimension

    ensure_schema(writer, dimension=dimension)
    second = ensure_schema(writer, dimension=dimension)

    # Second run must create nothing and raise nothing, whatever the first did.
    assert second.created_count == 0


def test_indexes_exist_after_ensure_schema(graph_handles):
    reader, writer, settings = graph_handles
    ensure_schema(writer, dimension=settings.graph.embedding_dimension)

    result = reader.ro_query("CALL db.indexes() YIELD label, properties, types RETURN label, properties, types")
    indexed = {(row["label"], prop) for row in result.rows for prop in row["properties"]}

    for spec in INDEX_SPECS:
        for field in spec.fields:
            assert (spec.label, field) in indexed, (spec.label, field)
