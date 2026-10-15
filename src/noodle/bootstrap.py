"""Bring-up entrypoint used by ``scripts/up.sh``.

Connects to FalkorDB, runs the idempotent ``ensure_schema()`` and reports what
it found. Dataset loading arrives with ingestion (step 3); until then this only
tells the operator the graph state.
"""

from __future__ import annotations

import sys

from .config import load_settings
from .graph import GraphClient, IngestWriter, open_reader, open_writer
from .schema import ensure_schema


def _node_count(reader: GraphClient) -> int:
    result = reader.ro_query("MATCH (n) RETURN count(n) AS n")
    if not result.rows:
        return 0
    return int(result.rows[0].get("n", 0))


def run() -> int:
    settings = load_settings()
    reader = open_reader(settings.graph.url, settings.graph.name)
    if not reader.ping():
        print(f"[bootstrap] FalkorDB at {settings.graph.url} is not reachable", file=sys.stderr)
        return 1

    writer: IngestWriter = open_writer(settings.graph.url, settings.graph.name)
    report = ensure_schema(writer, dimension=settings.graph.embedding_dimension)
    print(
        f"[bootstrap] schema ready (created {report.created_count} index(es), "
        f"embedding dimension {report.dimension})"
    )

    count = _node_count(reader)
    if count == 0:
        print(
            "[bootstrap] graph is empty; dataset loading arrives with ingestion (step 3)"
        )
    else:
        print(f"[bootstrap] graph holds {count} node(s)")

    reader.close()
    writer.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
