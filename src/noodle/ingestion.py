"""Ingestion: the only write path (04-ingestion.md).

Plan first, then write. ``build_plan`` turns a dataset into every node and
relationship without touching the graph, validating endpoints as it goes.
``ingest`` diffs the plan against what the graph already holds (by fingerprint)
and writes only what is new or changed, then advances ``meta:sync`` only when
something actually changed (or ``touch`` is set).

Nothing here hard-codes a label, relationship type or property name: all of them
come from ``schema``. The Cypher is the same two batched statements for every
label and every relationship type, so removing FalkorDB removes ingestion.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

from . import dataset as dataset_module
from . import schema
from .graph import GraphClient, GraphError, IngestWriter, open_writer

BATCH_SIZE = 200
META_ID = "meta:sync"

# The one place each statement is spelled out, so tests can match on them.
NODE_READ_CYPHER = (
    "MATCH (n:{label}) RETURN n.id AS id, n.fingerprint AS fingerprint, n.current AS current"
)
REL_READ_CYPHER = (
    "MATCH (a)-[r]->(b) RETURN type(r) AS type, a.id AS src, b.id AS dst, r.origin AS origin"
)
NODE_WRITE_CYPHER = "UNWIND $batch AS row MERGE (n:{label} {{id: row.id}}) SET n = row.props"
REL_WRITE_CYPHER = (
    "UNWIND $batch AS row "
    "MATCH (a:{from_label} {{id: row.src}}), (b:{to_label} {{id: row.dst}}) "
    "MERGE (a)-[r:{type}]->(b) SET r = row.props"
)
META_CYPHER = (
    "MERGE (m:Meta {id: $id}) "
    "SET m.last_synced_at = $last_synced_at, m.dataset_version = $version, "
    "m.record_counts = $counts"
)
NODE_DELETE_CYPHER = "MATCH (n:{label} {{id: $id}}) DELETE n"
REL_DELETE_CYPHER = "MATCH (a)-[r:{type}]->(b) WHERE a.id = $src AND b.id = $dst DELETE r"
FLAG_WRITE_CYPHER = "MATCH (n:{label} {{id: $id}}) SET n.current = $current"
CLEAR_CYPHER = "MATCH (n) DETACH DELETE n"

# Keys that are authoring commentary, never part of a record's identity (02).
_AUTHORING_KEYS = frozenset({"notes", "authoring_notes"})

# Relationship types that make their target no longer current.
_SUPERSEDING_TYPES = ("SUPERSEDES", "REVERSES", "OVERRIDES")


class IngestionError(Exception):
    """A dataset could not be turned into a graph. Carries every problem."""

    def __init__(self, problems):
        self.problems: list[str] = list(problems)
        head = "; ".join(self.problems[:5])
        more = "" if len(self.problems) <= 5 else f" (+{len(self.problems) - 5} more)"
        super().__init__(f"{len(self.problems)} ingestion problem(s): {head}{more}")


# --------------------------------------------------------------------------- #
# Fingerprints and small helpers
# --------------------------------------------------------------------------- #


def record_fingerprint(record: Mapping[str, Any]) -> str:
    """Stable hash of a source record, authoring commentary excluded (02)."""

    clean = {
        key: value
        for key, value in record.items()
        if key not in _AUTHORING_KEYS and not str(key).startswith("_")
    }
    blob = json.dumps(clean, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_label(node_id: str) -> Optional[str]:
    section = schema.ID_PREFIX_TO_SECTION.get(node_id.split(":", 1)[0])
    return schema.ENTITY_LABELS.get(section) if section else None


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PlannedNode:
    label: str
    id: str
    props: Mapping[str, Any]
    section: str


@dataclass(frozen=True)
class PlannedRelationship:
    type: str
    src: str
    dst: str
    from_label: str
    to_label: str
    props: Mapping[str, Any]


@dataclass(frozen=True)
class Plan:
    nodes: tuple[PlannedNode, ...]
    relationships: tuple[PlannedRelationship, ...]
    notes: tuple[str, ...]
    counts: Mapping[str, int]

    def node_by_id(self) -> dict[str, PlannedNode]:
        return {node.id: node for node in self.nodes}


def _node_props(label: str, record: Mapping[str, Any], written_at: str) -> dict[str, Any]:
    props: dict[str, Any] = {"id": record["id"]}
    for prop in schema.NODE_PROPERTIES[label]:
        if prop.name == "id" or prop.name not in record:
            continue
        value = record[prop.name]
        if value is None or value == "" or value == [] or value == {}:
            continue
        if prop.kind == schema.KIND_JSON and not isinstance(value, str):
            value = json.dumps(value, sort_keys=True, ensure_ascii=True)
        props[prop.name] = value
    props["clearance"] = record.get("clearance", schema.CLEARANCE_DEFAULT)
    if record.get("access_owner_id"):
        props["access_owner_id"] = record["access_owner_id"]
    props["fingerprint"] = record_fingerprint(record)
    props["written_at"] = written_at
    return props


def _orient(record_id: str, target_id: str, reverse: bool) -> tuple[str, str]:
    if reverse:
        return target_id, record_id
    return record_id, target_id


def normalize_dataset(dataset: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize dataset to include sections and relationships, handling aliases and flat formats."""
    if "sections" in dataset and isinstance(dataset["sections"], Mapping):
        sections = dict(dataset["sections"])
    else:
        sections = {}
        for key, value in dataset.items():
            if key in ("metadata", "relationships") or str(key).startswith("_"):
                continue
            if isinstance(value, list):
                sections[key] = value

    if "slack_messages" in sections and "chat_messages" not in sections:
        sections["chat_messages"] = sections["slack_messages"]

    return {
        "metadata": dict(dataset.get("metadata") or {}),
        "sections": sections,
        "relationships": dict(dataset.get("relationships") or {}),
    }


def build_plan(
    dataset: Mapping[str, Any],
    *,
    written_at: Optional[str] = None,
    known_ids: Optional[Mapping[str, str]] = None,
) -> Plan:
    """Turn a dataset into nodes and relationships, validating as it goes.

    Unknown relationship lists are reported in ``notes`` and skipped (04). Every
    endpoint must exist with the label the relationship type expects, or the run
    fails naming the entry. ``known_ids`` maps ids already in the graph to their
    section, so a partial live update may reference records it does not restate.
    """

    written_at = written_at or _now_iso()
    dataset = normalize_dataset(dataset)
    sections: Mapping[str, Any] = dataset["sections"]
    known_sections: dict[str, str] = dict(known_ids or {})

    node_section: dict[str, str] = {}
    nodes: list[PlannedNode] = []
    props_by_id: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for section, records in sections.items():
        if section not in schema.ENTITY_LABELS:
            continue
        label = schema.ENTITY_LABELS[section]
        counts[section] = len(records)
        for record in records:
            rid = record["id"]
            node_section[rid] = section
            props = _node_props(label, record, written_at)
            props_by_id[rid] = props
            nodes.append(PlannedNode(label=label, id=rid, props=props, section=section))

    rels: dict[tuple[str, str, str], dict[str, Any]] = {}
    notes: list[str] = []

    def add_rel(rel_type: str, src: str, dst: str, props: Mapping[str, Any]) -> None:
        key = (rel_type, src, dst)
        if key in rels:
            merged = rels[key]
            for name, value in props.items():
                if value is None:
                    continue
                if name == "evidence" and merged.get("evidence"):
                    merged["evidence"] = list(dict.fromkeys([*merged["evidence"], *value]))
                elif not merged.get(name):
                    merged[name] = value
        else:
            rels[key] = {name: value for name, value in props.items() if value is not None}

    # ---- reference fields -> edges ------------------------------------- #
    for ref in schema.REFERENCE_FIELDS:
        if ref.section not in sections or ref.relationship is None:
            continue
        if ref.relationship not in schema.RELATIONSHIP_TYPES:
            continue
        for record in sections[ref.section]:
            rid = record["id"]
            value = record.get(ref.field)
            if value is None:
                # check aliases: e.g. owner_id, author_id, lead_id, approver_id, approver
                for alias in (f"{ref.field}_id", ref.field.rstrip("s") + "_id", ref.field.rstrip("s")):
                    if alias in record and record[alias] is not None:
                        value = record[alias]
                        break
            if value is None:
                continue
            if ref.evidence:
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    src, dst = _orient(rid, entry["id"], ref.reverse)
                    add_rel(
                        ref.relationship,
                        src,
                        dst,
                        {"origin": rid, "evidence": entry.get("evidence")},
                    )
                    for evidence_id in entry.get("evidence", []) or []:
                        add_rel(
                            "SOURCED_FROM",
                            rid,
                            evidence_id,
                            {"origin": rid, "evidence": [evidence_id]},
                        )
            elif ref.many:
                for target in value:
                    src, dst = _orient(rid, target, ref.reverse)
                    add_rel(ref.relationship, src, dst, {"origin": rid})
            else:
                src, dst = _orient(rid, value, ref.reverse)
                add_rel(ref.relationship, src, dst, {"origin": rid})

    # ---- typed relationship lists -------------------------------------- #
    for rel_type, entries in (dataset.get("relationships") or {}).items():
        if rel_type not in schema.RELATIONSHIP_LIST_TYPES:
            notes.append(f"skipped unknown relationship list {rel_type!r}")
            continue
        for entry in entries:
            props: dict[str, Any] = {"origin": entry.get("origin")}
            if entry.get("evidence"):
                props["evidence"] = list(entry["evidence"])
            add_rel(rel_type, entry["src"], entry["dst"], props)

    # ---- provenance ---------------------------------------------------- #
    # Change -> the record whose history it is.
    for record in sections.get("history", []):
        entity_section = node_section.get(record.get("entity_id"))
        if entity_section is None:
            continue
        if schema.ENTITY_LABELS[entity_section] in (schema.LABEL_PROJECT, schema.LABEL_TICKET):
            add_rel("CHANGES", record["id"], record["entity_id"], {"origin": record["id"]})

    # SUPERSEDED_BY: the reverse of a Decision SUPERSEDES, only when the forward
    # edge the other way is not already recorded (02).
    for rel_type, src, dst in list(rels):
        if rel_type != "SUPERSEDES":
            continue
        if _id_label(src) != schema.LABEL_DECISION or _id_label(dst) != schema.LABEL_DECISION:
            continue
        if ("SUPERSEDES", dst, src) in rels:
            continue
        add_rel("SUPERSEDED_BY", dst, src, {"origin": rels[(rel_type, src, dst)].get("origin")})

    # Names in chat text -> MENTIONS (provenance).
    person_names: dict[str, str] = {}
    for record in sections.get("people", []):
        name = record.get("name")
        if name:
            person_names[name] = record["id"]
    for record in sections.get("chat_messages", []):
        text = record.get("text") or ""
        if not text:
            continue
        for name, person_id in person_names.items():
            if name in text:
                add_rel("MENTIONS", record["id"], person_id, {"origin": record["id"]})

    # ---- derived current flags ----------------------------------------- #
    _apply_current_flags(sections, rels, props_by_id)

    # ---- validate endpoints and labels --------------------------------- #
    problems: list[str] = []
    planned_rels: list[PlannedRelationship] = []
    for (rel_type, src, dst), props in rels.items():
        rel_def = schema.RELATIONSHIP_BY_TYPE[rel_type]
        src_section = node_section.get(src) or known_sections.get(src)
        dst_section = node_section.get(dst) or known_sections.get(dst)
        if src_section is None:
            problems.append(f"{rel_type}({src} -> {dst}): source {src!r} does not exist")
            continue
        if dst_section is None:
            problems.append(f"{rel_type}({src} -> {dst}): target {dst!r} does not exist")
            continue
        src_label = schema.ENTITY_LABELS[src_section]
        dst_label = schema.ENTITY_LABELS[dst_section]
        if src_label not in rel_def.from_labels:
            problems.append(
                f"{rel_type}({src} -> {dst}): source is a {src_label}, "
                f"expected one of {rel_def.from_labels}"
            )
        if dst_label not in rel_def.to_labels:
            problems.append(
                f"{rel_type}({src} -> {dst}): target is a {dst_label}, "
                f"expected one of {rel_def.to_labels}"
            )
        planned_rels.append(
            PlannedRelationship(
                type=rel_type,
                src=src,
                dst=dst,
                from_label=src_label,
                to_label=dst_label,
                props=props,
            )
        )

    if problems:
        raise IngestionError(problems)

    return Plan(
        nodes=tuple(nodes),
        relationships=tuple(planned_rels),
        notes=tuple(notes),
        counts=counts,
    )


def _apply_current_flags(
    sections: Mapping[str, Any],
    rels: Mapping[tuple[str, str, str], Mapping[str, Any]],
    props_by_id: dict[str, dict[str, Any]],
) -> None:
    # Decisions: superseded/reversed, or status says superseded.
    superseded_targets = {
        dst
        for (rel_type, _src, dst) in rels
        if rel_type in _SUPERSEDING_TYPES and _id_label(dst) == schema.LABEL_DECISION
    }
    for record in sections.get("decisions", []):
        rid = record["id"]
        props_by_id[rid]["current"] = not (
            record.get("status") == "superseded" or rid in superseded_targets
        )

    # Documents: superseded, or a later version in the same family.
    doc_superseded = {
        dst for (rel_type, _src, dst) in rels if rel_type == "SUPERSEDES"
    }
    family_max: dict[str, int] = {}
    for record in sections.get("documents", []):
        family = record.get("family")
        if family:
            family_max[family] = max(family_max.get(family, 0), int(record.get("version", 1)))
    for record in sections.get("documents", []):
        rid = record["id"]
        family = record.get("family")
        version = int(record.get("version", 1))
        props_by_id[rid]["current"] = (
            rid not in doc_superseded and version >= family_max.get(family, version)
        )

    # Changes: the latest change to that field of that entity.
    latest: dict[tuple[Any, Any], tuple[int, int, str]] = {}
    for record in sections.get("history", []):
        key = (record.get("entity_id"), record.get("field"))
        rank = (int(record.get("version", 0)), int(record.get("seq", 0)))
        best = latest.get(key)
        if best is None or rank > (best[0], best[1]):
            latest[key] = (rank[0], rank[1], record["id"])
    for record in sections.get("history", []):
        key = (record.get("entity_id"), record.get("field"))
        props_by_id[record["id"]]["current"] = latest.get(key, (0, 0, None))[2] == record["id"]


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NodeState:
    fingerprint: Optional[str]
    current: Optional[bool]
    label: Optional[str] = None


@dataclass(frozen=True)
class IngestReport:
    added: Mapping[str, int]
    changed: Mapping[str, int]
    unchanged: Mapping[str, int]
    relationships_written: int
    relationships_removed: int
    flags_flipped: int
    records_removed: int
    synced: bool
    last_synced_at: Optional[str]
    notes: tuple[str, ...]

    @property
    def total_added(self) -> int:
        return sum(self.added.values())

    @property
    def total_changed(self) -> int:
        return sum(self.changed.values())

    @property
    def total_unchanged(self) -> int:
        return sum(self.unchanged.values())

    @property
    def wrote_anything(self) -> bool:
        return bool(self.total_added or self.total_changed or self.relationships_written)


def read_node_state(
    reader: GraphClient, labels: Iterable[str] = schema.LABELS
) -> dict[str, NodeState]:
    """Read id -> (fingerprint, current) for every existing node, per label."""

    state: dict[str, NodeState] = {}
    for label in labels:
        if label == schema.LABEL_META:
            continue
        try:
            result = reader.ro_query(NODE_READ_CYPHER.format(label=label))
        except GraphError as exc:
            if "empty key" in str(exc).lower():
                return {}
            raise
        for row in result.rows:
            state[row["id"]] = NodeState(
                fingerprint=row.get("fingerprint"),
                current=row.get("current"),
                label=label,
            )
    return state


def read_relationships(reader: GraphClient) -> set[tuple[str, str, str]]:
    try:
        result = reader.ro_query(REL_READ_CYPHER)
    except GraphError as exc:
        if "empty key" in str(exc).lower():
            return set()
        raise
    return {
        (row["type"], row["src"], row["dst"])
        for row in result.rows
        if row.get("origin") is not None
    }


def read_relationship_origins(reader: GraphClient) -> dict[tuple[str, str, str], str]:
    """Id -> origin for every relationship the graph holds (origin not null)."""

    try:
        result = reader.ro_query(REL_READ_CYPHER)
    except GraphError as exc:
        if "empty key" in str(exc).lower():
            return {}
        raise
    origins: dict[tuple[str, str, str], str] = {}
    for row in result.rows:
        if row.get("origin") is not None:
            origins[(row["type"], row["src"], row["dst"])] = row["origin"]
    return origins


def _run_batch(writer: IngestWriter, cypher: str, batch: list[dict], describe: str) -> None:
    if not batch:
        return
    try:
        writer.write_query(cypher, {"batch": batch})
        return
    except GraphError:
        # Retry entry by entry so the failing entry is named (04).
        for row in batch:
            try:
                writer.write_query(cypher, {"batch": [row]})
            except GraphError as inner:
                raise IngestionError([f"{describe}: {inner}"]) from inner


def write_nodes(writer: IngestWriter, nodes: Sequence[PlannedNode]) -> None:
    by_label: dict[str, list[dict]] = {}
    for node in nodes:
        by_label.setdefault(node.label, []).append({"id": node.id, "props": dict(node.props)})
    for label, rows in by_label.items():
        cypher = NODE_WRITE_CYPHER.format(label=label)
        for start in range(0, len(rows), BATCH_SIZE):
            _run_batch(writer, cypher, rows[start : start + BATCH_SIZE], f"node {label}")


def write_relationships(writer: IngestWriter, rels: Sequence[PlannedRelationship]) -> None:
    by_shape: dict[tuple[str, str, str], list[dict]] = {}
    for rel in rels:
        by_shape.setdefault((rel.type, rel.from_label, rel.to_label), []).append(
            {"src": rel.src, "dst": rel.dst, "props": dict(rel.props)}
        )
    for (rel_type, from_label, to_label), rows in by_shape.items():
        cypher = REL_WRITE_CYPHER.format(type=rel_type, from_label=from_label, to_label=to_label)
        for start in range(0, len(rows), BATCH_SIZE):
            _run_batch(
                writer, cypher, rows[start : start + BATCH_SIZE], f"relationship {rel_type}"
            )


def write_meta(
    writer: IngestWriter,
    *,
    last_synced_at: str,
    version: Optional[str],
    counts: Mapping[str, int],
) -> None:
    writer.write_query(
        META_CYPHER,
        {
            "id": META_ID,
            "last_synced_at": last_synced_at,
            "version": version,
            "counts": json.dumps(dict(counts), sort_keys=True),
        },
    )


def delete_nodes(writer: IngestWriter, by_label: Mapping[str, Sequence[str]]) -> int:
    removed = 0
    for label, ids in by_label.items():
        cypher = NODE_DELETE_CYPHER.format(label=label)
        for node_id in ids:
            writer.write_query(cypher, {"id": node_id})
            removed += 1
    return removed


def delete_relationships(
    writer: IngestWriter, rels: Sequence[tuple[str, str, str]]
) -> int:
    removed = 0
    for rel_type, src, dst in rels:
        writer.write_query(
            REL_DELETE_CYPHER.format(type=rel_type), {"src": src, "dst": dst}
        )
        removed += 1
    return removed


def write_flag(writer: IngestWriter, label: str, node_id: str, current: bool) -> None:
    writer.write_query(
        FLAG_WRITE_CYPHER.format(label=label), {"id": node_id, "current": current}
    )


def _dataset_sync_time(dataset: Mapping[str, Any]) -> Optional[str]:
    value = (dataset.get("metadata") or {}).get("last_synced_at")
    return value if isinstance(value, str) and value else None


def _dataset_version(dataset: Mapping[str, Any]) -> Optional[str]:
    value = (dataset.get("metadata") or {}).get("dataset_version")
    return value if isinstance(value, str) and value else None


def ingest(
    dataset: Mapping[str, Any],
    *,
    writer: IngestWriter,
    reader: GraphClient,
    touch: bool = False,
    written_at: Optional[str] = None,
    validate: bool = True,
    partial: bool = False,
    prune: bool = False,
    rebuild: bool = False,
    confirm_rebuild: bool = False,
    **validate_flags: Any,
) -> IngestReport:
    """Diff a dataset against the graph and write only what changed (04).

    Incremental by default: added and changed records are written, a relationship
    whose origin record is restated without implying it is removed (the old owner
    after a reassignment), stale Change records go, and only records whose
    ``current`` flag flips are rewritten.

    ``partial`` marks a live update: metadata/count/trap checks are relaxed
    (unless overridden) and references may point at records already in the graph;
    nothing outside the update is removed. ``prune`` also removes dataset records
    missing from the input (documents are never pruned). ``rebuild`` empties the
    graph and reloads; it requires ``confirm_rebuild``. The sync time written to
    ``meta:sync`` comes from the dataset (``metadata.last_synced_at``) so the demo
    snapshot is reproducible; it falls back to the clock for a dataset that
    carries no sync time.
    """

    if rebuild and not confirm_rebuild:
        raise IngestionError(["a full rebuild requires confirm_rebuild"])

    effective_rebuild = rebuild or confirm_rebuild

    if effective_rebuild:
        existing: dict[str, NodeState] = {}
    else:
        existing = read_node_state(reader)

    label_to_section = {label: section for section, label in schema.ENTITY_LABELS.items()}
    known_sections = {
        node_id: label_to_section[state.label]
        for node_id, state in existing.items()
        if state.label in label_to_section
    }
    known_for_plan = known_sections if partial else {}

    if validate:
        flags = dict(validate_flags)
        if partial:
            flags.setdefault("require_metadata", False)
            flags.setdefault("require_counts", False)
            flags.setdefault("require_traps", False)
        dataset_module.validate_dataset(dataset, known_ids=known_for_plan, **flags)

    plan = build_plan(dataset, written_at=written_at, known_ids=known_for_plan)
    input_ids = {node.id for node in plan.nodes}
    planned_rels = {(rel.type, rel.src, rel.dst) for rel in plan.relationships}

    if effective_rebuild:
        writer.write_query(CLEAR_CYPHER)
        existing = {}

    added: dict[str, int] = {}
    changed: dict[str, int] = {}
    unchanged: dict[str, int] = {}
    flags_flipped = 0
    to_write: list[PlannedNode] = []
    written_ids: set[str] = set()
    for node in plan.nodes:
        previous = existing.get(node.id)
        if previous is None:
            added[node.section] = added.get(node.section, 0) + 1
            to_write.append(node)
            written_ids.add(node.id)
        elif previous.fingerprint != node.props.get("fingerprint"):
            changed[node.section] = changed.get(node.section, 0) + 1
            to_write.append(node)
            written_ids.add(node.id)
        else:
            unchanged[node.section] = unchanged.get(node.section, 0) + 1
        new_current = node.props.get("current")
        if (
            previous is not None
            and new_current is not None
            and previous.current is not None
            and bool(previous.current) != bool(new_current)
        ):
            flags_flipped += 1
            if node.id not in written_ids:
                to_write.append(node)
                written_ids.add(node.id)

    # A supersession may target a record this update does not restate (a partial
    # live update): flip that existing record's flag and rewrite only it.
    for rel in plan.relationships:
        if rel.type not in _SUPERSEDING_TYPES or rel.dst in input_ids:
            continue
        previous = existing.get(rel.dst)
        if previous is None or previous.label is None or not previous.current:
            continue
        write_flag(writer, previous.label, rel.dst, False)
        flags_flipped += 1

    write_nodes(writer, to_write)

    existing_rels = read_relationships(reader)
    rels_to_write = [
        rel for rel in plan.relationships if (rel.type, rel.src, rel.dst) not in existing_rels
    ]
    write_relationships(writer, rels_to_write)

    # ---- removal (incremental only) ------------------------------------ #
    removed_rels: list[tuple[str, str, str]] = []
    removed_nodes: dict[str, list[str]] = {}
    if not partial and not effective_rebuild:
        for key, origin in read_relationship_origins(reader).items():
            if origin in input_ids and key not in planned_rels:
                removed_rels.append(key)
        for node_id, state in existing.items():
            if node_id in input_ids or state.label is None:
                continue
            if state.label == schema.LABEL_CHANGE:
                removed_nodes.setdefault(state.label, []).append(node_id)
            elif prune and state.label not in (schema.LABEL_DOCUMENT, schema.LABEL_META):
                removed_nodes.setdefault(state.label, []).append(node_id)

    deleted_nodes = delete_nodes(writer, removed_nodes)
    deleted_rels = delete_relationships(writer, removed_rels)

    changed_any = (
        bool(to_write)
        or bool(rels_to_write)
        or bool(deleted_nodes)
        or bool(deleted_rels)
        or flags_flipped > 0
    )
    synced = False
    last_synced_at = _dataset_sync_time(dataset)
    if changed_any or touch:
        write_meta(
            writer,
            last_synced_at=last_synced_at or _now_iso(),
            version=_dataset_version(dataset),
            counts=plan.counts,
        )
        synced = True

    return IngestReport(
        added=added,
        changed=changed,
        unchanged=unchanged,
        relationships_written=len(rels_to_write),
        relationships_removed=deleted_rels,
        flags_flipped=flags_flipped,
        records_removed=deleted_nodes,
        synced=synced,
        last_synced_at=last_synced_at,
        notes=plan.notes,
    )


def ingest_file(
    path: Any,
    *,
    writer: IngestWriter,
    reader: GraphClient,
    touch: bool = False,
    **validate_flags: Any,
) -> IngestReport:
    dataset = dataset_module.load_dataset(path)
    return ingest(dataset, writer=writer, reader=reader, touch=touch, **validate_flags)


def open_dataset_writer(url: str, graph_name: str, graph: Any = None) -> IngestWriter:
    """Open the ingestion-only write path. Used by ``POST /ingest`` (04)."""

    return open_writer(url, graph_name, graph=graph)


def sha256_file(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


MANIFEST_NAME = ".ingest_manifest.json"
MANIFEST_VERSION = 1
_IGNORED_PREFIXES = (".", "#", ".~lock")
_IGNORED_SUFFIXES = (".manifest.json", ".pyc", ".tmp", ".bak", ".swp", "~")


def _is_ignored(name: str) -> bool:
    return any(name.startswith(p) for p in _IGNORED_PREFIXES) or any(
        name.endswith(s) for s in _IGNORED_SUFFIXES
    )


def load_manifest(path: Union[str, Path]) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {"version": MANIFEST_VERSION, "files": {}}
    with open(p, encoding="utf-8") as file:
        data = json.load(file)
    data.setdefault("version", MANIFEST_VERSION)
    data.setdefault("files", {})
    return data


def save_manifest(path: Union[str, Path], manifest: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2, ensure_ascii=False, sort_keys=True)
        file.write("\n")


def load_source_path(source: Union[str, Path]) -> dict[str, Any]:
    """Load dataset from a single JSON/document file, or a folder of source files."""
    path = Path(source)
    if not path.exists():
        raise IngestionError([f"source not found: {source}"])

    if path.is_file():
        if path.suffix.lower() == ".json":
            data = dataset_module.load_dataset(path)
            return normalize_dataset(data)

        from .extractor import extract_bytes

        with open(path, "rb") as handle:
            raw = handle.read()
        text, meta = extract_bytes(raw, filename=path.name)
        doc = {
            "id": path.name,
            "title": meta.get("title") or path.name,
            "content": text,
            "location": str(path),
            "format": meta.get("format"),
            "clearance": schema.CLEARANCE_DEFAULT,
        }
        return normalize_dataset({"documents": [doc]})

    combined_sections: dict[str, list[dict[str, Any]]] = {}
    combined_relationships: dict[str, list[dict[str, Any]]] = {}
    metadata: dict[str, Any] = {}

    for root, _, names in os.walk(path):
        for name in sorted(names):
            if _is_ignored(name):
                continue
            file_path = Path(root) / name
            rel_key = str(file_path.relative_to(path)).replace("\\", "/")

            if file_path.suffix.lower() == ".json":
                try:
                    data = dataset_module.load_dataset(file_path)
                    normalized = normalize_dataset(data)
                    if normalized.get("metadata") and not metadata:
                        metadata = normalized["metadata"]
                    for sec, recs in normalized["sections"].items():
                        combined_sections.setdefault(sec, []).extend(recs)
                    for rel, entries in normalized["relationships"].items():
                        combined_relationships.setdefault(rel, []).extend(entries)
                    continue
                except Exception:
                    pass

            try:
                from .extractor import extract_bytes

                with open(file_path, "rb") as handle:
                    raw = handle.read()
                text, meta = extract_bytes(raw, filename=name)
                doc = {
                    "id": rel_key,
                    "title": meta.get("title") or name,
                    "content": text,
                    "location": rel_key,
                    "format": meta.get("format"),
                    "clearance": schema.CLEARANCE_DEFAULT,
                }
                combined_sections.setdefault("documents", []).append(doc)
            except Exception:
                continue

    return {
        "metadata": metadata,
        "sections": combined_sections,
        "relationships": combined_relationships,
    }


def ingest_source(
    source: Union[str, Path],
    *,
    writer: IngestWriter,
    reader: GraphClient,
    touch: bool = False,
    validate: bool = False,
    **validate_flags: Any,
) -> IngestReport:
    """Ingest a file or folder of source files (JSON, PDF, DOCX, XML, MD, text)."""
    dataset = load_source_path(source)
    return ingest(
        dataset,
        writer=writer,
        reader=reader,
        touch=touch,
        validate=validate,
        **validate_flags,
    )


__all__ = [
    "IngestionError",
    "PlannedNode",
    "PlannedRelationship",
    "Plan",
    "NodeState",
    "IngestReport",
    "NODE_READ_CYPHER",
    "REL_READ_CYPHER",
    "NODE_WRITE_CYPHER",
    "REL_WRITE_CYPHER",
    "META_CYPHER",
    "build_plan",
    "record_fingerprint",
    "read_node_state",
    "read_relationships",
    "read_relationship_origins",
    "write_nodes",
    "write_relationships",
    "write_meta",
    "open_dataset_writer",
    "ingest",
    "ingest_file",
    "ingest_source",
    "load_source_path",
    "normalize_dataset",
    "sha256_file",
    "load_manifest",
    "save_manifest",
]

