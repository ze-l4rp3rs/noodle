#!/usr/bin/env python3
"""Reconcile FalkorDB with a dataset file or a folder of source files.

Source can be:
- a single .json dataset file (e.g. data/synthetic/dataset.json)
- a folder of source files (.json, .pdf, .docx, .xml, .md, .txt)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add src to sys.path so noodle can be imported without installation
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from noodle.config import load_settings
from noodle.graph import open_reader, open_writer
from noodle.ingestion import (
    IngestionError,
    build_plan,
    ingest_source,
    load_source_path,
    read_node_state,
    read_relationships,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest company records into FalkorDB.")
    parser.add_argument(
        "source",
        nargs="?",
        default="data/synthetic/dataset.json",
        help="source JSON dataset or folder of documents (default: data/synthetic/dataset.json)",
    )
    parser.add_argument(
        "--touch",
        action="store_true",
        help="advance meta:sync even if no data changed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to FalkorDB",
    )
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.is_absolute():
        source_path = ROOT / source_path

    if not source_path.exists():
        print(f"ERROR: source not found: {source_path}", file=sys.stderr)
        return 1

    settings = load_settings()
    try:
        reader = open_reader(settings.graph.url, settings.graph.name)
        if not reader.ping():
            print(f"ERROR: FalkorDB is not reachable at {settings.graph.url}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"ERROR: FalkorDB is not reachable at {settings.graph.url}: {exc}", file=sys.stderr)
        return 1

    try:
        dataset = load_source_path(source_path)
    except IngestionError as exc:
        print(f"ERROR: failed to load source: {exc}", file=sys.stderr)
        reader.close()
        return 1

    if args.dry_run:
        plan = build_plan(dataset)
        existing_nodes = read_node_state(reader)
        existing_rels = read_relationships(reader)

        added = sum(1 for n in plan.nodes if n.id not in existing_nodes)
        changed = sum(
            1
            for n in plan.nodes
            if n.id in existing_nodes
            and existing_nodes[n.id].fingerprint != n.props.get("fingerprint")
        )
        unchanged = sum(
            1
            for n in plan.nodes
            if n.id in existing_nodes
            and existing_nodes[n.id].fingerprint == n.props.get("fingerprint")
        )
        new_rels = sum(
            1 for r in plan.relationships if (r.type, r.src, r.dst) not in existing_rels
        )

        print(f"\n=== DRY RUN: Planned reconciliation for '{args.source}' ===")
        print(f"  • Nodes to add: {added}")
        print(f"  • Nodes to update: {changed}")
        print(f"  • Nodes unchanged: {unchanged}")
        print(f"  • Relationships to write: {new_rels}")
        if plan.notes:
            print("  Notes:")
            for note in plan.notes:
                print(f"    - {note}")
        print("\n✓ Dry run complete. No changes were written to the database.\n")
        reader.close()
        return 0

    writer = open_writer(settings.graph.url, settings.graph.name)
    try:
        report = ingest_source(
            source_path,
            writer=writer,
            reader=reader,
            touch=args.touch,
            validate=False,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: ingestion failed: {exc}", file=sys.stderr)
        reader.close()
        writer.close()
        return 1

    print(f"\n=== Ingested '{args.source}' ===")
    print(f"  • Added: {report.total_added} node(s)")
    print(f"  • Changed: {report.total_changed} node(s)")
    print(f"  • Unchanged: {report.total_unchanged} node(s)")
    print(f"  • Relationships written: {report.relationships_written}")
    print(f"  • Sync timestamp: {'advanced (' + str(report.last_synced_at) + ')' if report.synced else 'unchanged'}")
    if report.notes:
        print("  Notes:")
        for note in report.notes:
            print(f"    - {note}")
    print("\n✓ Ingestion complete.\n")

    reader.close()
    writer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
