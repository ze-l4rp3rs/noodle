"""Loading and validation of a dataset (04-ingestion.md).

The dataset is the only source ingestion reads. This module knows nothing about
the graph: it reads JSON, checks every reference resolves and every planted trap
is present, so a bad dataset fails before any write touches FalkorDB.

Nothing here is label-, section- or name-specific beyond what ``schema`` already
declares; the checks are driven by ``schema.ENTITY_LABELS``,
``schema.ID_PREFIX_TO_SECTION``, ``schema.NODE_PROPERTIES`` and
``schema.REFERENCE_FIELDS``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from . import schema

DEFAULT_DATASET_PATH = Path("data/synthetic/dataset.json")
DEFAULT_FIXTURE_PATH = Path("fixtures/hand_fixture.json")

# Every trap in 04. The dataset must carry all of them.
REQUIRED_TRAP_IDS: frozenset[str] = frozenset(
    {
        "decision-superseded-by-chat",
        "reassignment-owner-vs-expert",
        "stale-strategy-document",
        "dissenter-with-evidence",
        "nonexistent-figure",
        "false-premise",
        "two-conflicting-current-decisions",
        "hidden-depends-on",
        "restricted-doc-linked-to-visible-decision",
        "restricted-only-intermediate-hop",
        "restricted-superseder",
        "post-sync-records",
    }
)

# Section -> inclusive count range (04: "About 40 to 60 people, ...").
COUNT_RANGES: dict[str, tuple[int, int]] = {
    "people": (40, 60),
    "teams": (6, 8),
    "projects": (12, 15),
    "tickets": (80, 200),
}

# Sections that must hold at least one record even where no upper bound is set.
MIN_COUNTS: dict[str, int] = {
    "documents": 1,
    "decisions": 1,
    "chat_messages": 1,
    "history": 1,
}


class DatasetError(Exception):
    """Raised when a dataset fails validation. Carries every problem found."""

    def __init__(self, problems: list[str]):
        self.problems: list[str] = list(problems)
        head = "; ".join(self.problems[:5])
        more = "" if len(self.problems) <= 5 else f" (+{len(self.problems) - 5} more)"
        super().__init__(f"{len(self.problems)} dataset problem(s): {head}{more}")


@dataclass(frozen=True)
class ValidationReport:
    counts: Mapping[str, int]
    record_count: int
    relationship_count: int
    trap_ids: tuple[str, ...]

    @property
    def trap_count(self) -> int:
        return len(self.trap_ids)


def load_dataset(path: Union[str, Path]) -> dict:
    """Read a dataset JSON file into a plain dict."""

    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise DatasetError(["dataset root must be a JSON object"])
    return data


def _label_to_section() -> dict[str, str]:
    return {label: section for section, label in schema.ENTITY_LABELS.items()}


def _allowed_fields(section: str) -> set[str]:
    props = {p.name for p in schema.NODE_PROPERTIES[schema.ENTITY_LABELS[section]]}
    props |= {p.name for p in schema.COMMON_NODE_PROPERTIES}
    props |= {r.field for r in schema.REFERENCE_FIELDS if r.section == section}
    props.add("id")
    return props


def validate_dataset(
    dataset: Mapping[str, Any],
    *,
    require_metadata: bool = True,
    require_counts: bool = True,
    require_traps: bool = True,
    known_ids: Optional[Mapping[str, str]] = None,
) -> ValidationReport:
    """Validate a dataset; raise :class:`DatasetError` listing every problem.

    Reference resolution is always checked. The metadata, count and trap checks
    are what the full synthetic dataset must satisfy; the hand fixture relaxes
    them while still proving every reference resolves. ``known_ids`` maps ids
    already in the graph to their section, so a partial live update may reference
    records it does not restate.
    """

    problems: list[str] = []
    known: Mapping[str, str] = known_ids or {}

    sections = dataset.get("sections")
    if not isinstance(sections, Mapping):
        raise DatasetError(["dataset has no 'sections' object"])
    for name in sections:
        if name not in schema.ENTITY_LABELS:
            problems.append(f"unknown section {name!r}")

    # ---- index every record by id -------------------------------------- #
    by_id: dict[str, tuple[str, Mapping[str, Any]]] = {}
    counts: dict[str, int] = {}
    for section, records in sections.items():
        if section not in schema.ENTITY_LABELS:
            continue
        if not isinstance(records, list):
            problems.append(f"section {section!r} must be a list")
            continue
        counts[section] = len(records)
        for position, record in enumerate(records):
            if not isinstance(record, Mapping):
                problems.append(f"{section}[{position}] is not an object")
                continue
            rid = record.get("id")
            if not isinstance(rid, str) or not rid:
                problems.append(f"{section}[{position}] has no string id")
                continue
            if rid in by_id:
                problems.append(f"duplicate id {rid!r}")
                continue
            prefix = rid.split(":", 1)[0]
            expected = schema.ID_PREFIX_TO_SECTION.get(prefix)
            if expected is None:
                problems.append(f"{rid!r}: unknown id prefix {prefix!r}")
            elif expected != section:
                problems.append(f"{rid!r}: id belongs in section {expected!r}, found in {section!r}")
            by_id[rid] = (section, record)

    def section_of(ref: Any) -> Optional[str]:
        found = by_id.get(ref) if isinstance(ref, str) else None
        if found is not None:
            return found[0]
        return known.get(ref) if isinstance(ref, str) else None

    def resolve(ref: Any, target_section: str, where: str) -> None:
        if not isinstance(ref, str) or not ref:
            problems.append(f"{where}: reference is not a non-empty string")
            return
        found = by_id.get(ref)
        if found is not None:
            if found[0] != target_section:
                problems.append(
                    f"{where}: reference {ref!r} is in {found[0]!r}, expected {target_section!r}"
                )
            return
        known_section = known.get(ref)
        if known_section is None:
            problems.append(f"{where}: reference {ref!r} does not resolve")
        elif known_section != target_section:
            problems.append(
                f"{where}: reference {ref!r} is a known {known_section!r}, "
                f"expected {target_section!r}"
            )

    # ---- per-record property, clearance and reference checks ----------- #
    for section, records in sections.items():
        if section not in schema.ENTITY_LABELS:
            continue
        allowed = _allowed_fields(section)
        for record in records:
            if not isinstance(record, Mapping):
                continue
            rid = record.get("id", f"{section}[?]")
            for field_name in record:
                if field_name not in allowed:
                    problems.append(f"{rid}: unknown field {field_name!r}")
            clearance = record.get("clearance")
            if clearance is not None:
                if not schema.is_valid_clearance(clearance):
                    problems.append(
                        f"{rid}: clearance {clearance!r} is not in "
                        f"{schema.CLEARANCE_MIN}..{schema.CLEARANCE_MAX}"
                    )
                elif schema.requires_access_owner(clearance):
                    owner = record.get("access_owner_id")
                    if not owner:
                        problems.append(
                            f"{rid}: clearance {clearance} requires an access_owner_id"
                        )
                    else:
                        resolve(owner, "people", f"{rid}.access_owner_id")
            elif record.get("access_owner_id"):
                resolve(record["access_owner_id"], "people", f"{rid}.access_owner_id")

    # ---- reference fields ---------------------------------------------- #
    for ref in schema.REFERENCE_FIELDS:
        if ref.section not in sections:
            continue
        for record in sections[ref.section]:
            if not isinstance(record, Mapping):
                continue
            rid = record.get("id", ref.section)
            value = record.get(ref.field)
            if value is None:
                continue
            where = f"{rid}.{ref.field}"
            if ref.evidence:
                entries = value if isinstance(value, list) else [value]
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        problems.append(f"{where}: evidence entry is not an object")
                        continue
                    resolve(entry.get("id"), ref.target_section, where)
                    for ev in entry.get("evidence", []) or []:
                        if section_of(ev) is None:
                            problems.append(f"{where}: evidence {ev!r} does not resolve")
            elif ref.many:
                if not isinstance(value, list):
                    problems.append(f"{where}: expected a list")
                    continue
                for item in value:
                    resolve(item, ref.target_section, where)
            else:
                resolve(value, ref.target_section, where)

    # ---- history must point at a real entity --------------------------- #
    for record in sections.get("history", []):
        if not isinstance(record, Mapping):
            continue
        rid = record.get("id", "history[?]")
        entity_id = record.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            problems.append(f"{rid}.entity_id is not a non-empty string")
        elif section_of(entity_id) is None:
            problems.append(f"{rid}.entity_id {entity_id!r} does not resolve")
        entity_type = record.get("entity_type")
        if isinstance(entity_id, str) and isinstance(entity_type, str):
            if entity_id.split(":", 1)[0] != entity_type:
                problems.append(
                    f"{rid}: entity_type {entity_type!r} does not match entity_id {entity_id!r}"
                )

    # ---- typed relationship lists -------------------------------------- #
    relationships = dataset.get("relationships", {})
    relationship_count = 0
    if not isinstance(relationships, Mapping):
        problems.append("'relationships' must be an object")
        relationships = {}
    label_to_section = _label_to_section()
    for rel_type, entries in relationships.items():
        rel = schema.RELATIONSHIP_BY_TYPE.get(rel_type)
        if rel is None:
            problems.append(f"unknown relationship type {rel_type!r}")
            continue
        if not isinstance(entries, list):
            problems.append(f"relationship list {rel_type!r} must be a list")
            continue
        from_sections = {label_to_section[l] for l in rel.from_labels}
        to_sections = {label_to_section[l] for l in rel.to_labels}
        for entry in entries:
            if not isinstance(entry, Mapping):
                problems.append(f"{rel_type}: entry is not an object")
                continue
            src, dst = entry.get("src"), entry.get("dst")
            where = f"{rel_type}({src} -> {dst})"
            origin = entry.get("origin")
            if not isinstance(origin, str) or not origin:
                problems.append(f"{where}: missing origin")
            for ref, allowed_sections in ((src, from_sections), (dst, to_sections)):
                found_section = section_of(ref)
                if found_section is None:
                    problems.append(f"{where}: reference {ref!r} does not resolve")
                elif found_section not in allowed_sections:
                    problems.append(
                        f"{where}: {ref!r} is a {found_section!r} node, not allowed for {rel_type}"
                    )
            for ev in entry.get("evidence", []) or []:
                if section_of(ev) is None:
                    problems.append(f"{where}: evidence {ev!r} does not resolve")
            relationship_count += 1

    # ---- metadata ------------------------------------------------------- #
    if require_metadata:
        metadata = dataset.get("metadata")
        if not isinstance(metadata, Mapping):
            problems.append("dataset has no 'metadata' object")
        else:
            if metadata.get("synthetic") is not True:
                problems.append("metadata.synthetic must be true (synthetic data only)")
            if not isinstance(metadata.get("last_synced_at"), str) or not metadata.get("last_synced_at"):
                problems.append("metadata.last_synced_at must be a non-empty string")

    # ---- counts --------------------------------------------------------- #
    if require_counts:
        for section, (low, high) in COUNT_RANGES.items():
            actual = counts.get(section, 0)
            if not (low <= actual <= high):
                problems.append(
                    f"section {section!r}: {actual} records, expected {low}..{high}"
                )
        for section, minimum in MIN_COUNTS.items():
            if counts.get(section, 0) < minimum:
                problems.append(
                    f"section {section!r}: {counts.get(section, 0)} records, "
                    f"expected at least {minimum}"
                )
        metadata = dataset.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("record_counts"), Mapping):
            for section, actual in counts.items():
                declared = metadata["record_counts"].get(section)
                if declared != actual:
                    problems.append(
                        f"metadata.record_counts[{section!r}] is {declared!r}, actual is {actual}"
                    )

    # ---- traps ---------------------------------------------------------- #
    trap_ids: list[str] = []
    traps = dataset.get("traps", [])
    if not isinstance(traps, list):
        problems.append("'traps' must be a list")
        traps = []
    for trap in traps:
        if not isinstance(trap, Mapping) or not isinstance(trap.get("id"), str):
            problems.append("trap entry has no string id")
            continue
        trap_ids.append(trap["id"])
        for entity in trap.get("entities", []) or []:
            if entity not in by_id:
                problems.append(f"trap {trap['id']!r}: entity {entity!r} does not resolve")
    if require_traps:
        missing = REQUIRED_TRAP_IDS - set(trap_ids)
        for trap_id in sorted(missing):
            problems.append(f"required trap {trap_id!r} is missing")

    if problems:
        raise DatasetError(problems)

    return ValidationReport(
        counts=counts,
        record_count=sum(counts.values()),
        relationship_count=relationship_count,
        trap_ids=tuple(trap_ids),
    )


__all__ = [
    "DatasetError",
    "ValidationReport",
    "REQUIRED_TRAP_IDS",
    "COUNT_RANGES",
    "MIN_COUNTS",
    "DEFAULT_DATASET_PATH",
    "DEFAULT_FIXTURE_PATH",
    "load_dataset",
    "validate_dataset",
]
