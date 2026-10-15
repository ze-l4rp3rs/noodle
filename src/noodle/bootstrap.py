"""Bring-up entrypoint used by ``scripts/up.sh``.

Connects to FalkorDB, runs the idempotent ``ensure_schema()``, then ingests the
synthetic dataset. Ingestion is incremental, so running this again reports every
record unchanged and writes nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import load_settings
from .dataset import DEFAULT_DATASET_PATH, DatasetError, load_dataset
from .graph import GraphClient, IngestWriter, open_reader, open_writer
from .ingestion import IngestionError, ingest
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

    dataset_path = Path(DEFAULT_DATASET_PATH)
    if not dataset_path.exists():
        print(f"[bootstrap] dataset not found at {dataset_path}; nothing ingested")
    else:
        try:
            dataset = load_dataset(dataset_path)
            result = ingest(dataset, writer=writer, reader=reader)
        except (DatasetError, IngestionError) as exc:
            print(f"[bootstrap] ingestion failed: {exc}", file=sys.stderr)
            reader.close()
            writer.close()
            return 1
        print(
            f"[bootstrap] ingest: {result.total_added} added, "
            f"{result.total_changed} changed, {result.total_unchanged} unchanged, "
            f"{result.relationships_written} relationship(s) written, "
            f"sync {'advanced' if result.synced else 'unchanged'}"
        )

    count = _node_count(reader)
    if count == 0:
        print("[bootstrap] graph is empty; run again after ingestion if this is unexpected")
    else:
        print(f"[bootstrap] graph holds {count} node(s)")

    reader.close()
    writer.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
