"""The single source of truth for Noodle's graph model.

Everything that knows a label, a relationship type, a property name, an index,
a reference field or a clearance rule reads it from here: ingestion, the query
library, the dataset checks, the generated docs and the tests. Nothing is
hard-coded elsewhere.

No company, person or record name appears in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .graph import GraphError, IngestWriter

# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #

LABEL_PERSON = "Person"
LABEL_TEAM = "Team"
LABEL_PROJECT = "Project"
LABEL_TICKET = "Ticket"
LABEL_DOCUMENT = "Document"
LABEL_DECISION = "Decision"
LABEL_CHAT_MESSAGE = "ChatMessage"
LABEL_CHANGE = "Change"
LABEL_CHUNK = "Chunk"
LABEL_META = "Meta"

LABELS: tuple[str, ...] = (
    LABEL_PERSON,
    LABEL_TEAM,
    LABEL_PROJECT,
    LABEL_TICKET,
    LABEL_DOCUMENT,
    LABEL_DECISION,
    LABEL_CHAT_MESSAGE,
    LABEL_CHANGE,
    LABEL_CHUNK,
    LABEL_META,
)

# Dataset section -> node label. The Meta node is written by ingestion, never
# read from a dataset section.
ENTITY_LABELS: dict[str, str] = {
    "people": LABEL_PERSON,
    "teams": LABEL_TEAM,
    "projects": LABEL_PROJECT,
    "tickets": LABEL_TICKET,
    "documents": LABEL_DOCUMENT,
    "decisions": LABEL_DECISION,
    "chat_messages": LABEL_CHAT_MESSAGE,
    "history": LABEL_CHANGE,
    "chunks": LABEL_CHUNK,
}

# Node id prefix -> dataset section. Ids are ``type:slug`` and stable (02).
ID_PREFIX_TO_SECTION: dict[str, str] = {
    "person": "people",
    "team": "teams",
    "project": "projects",
    "ticket": "tickets",
    "doc": "documents",
    "decision": "decisions",
    "chat": "chat_messages",
    "change": "history",
    "chunk": "chunks",
    "meta": "meta",
}

# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #

KIND_STR = "str"
KIND_INT = "int"
KIND_BOOL = "bool"
KIND_LIST = "list"
KIND_JSON = "json"
KIND_VECF32 = "vecf32"

KINDS: tuple[str, ...] = (KIND_STR, KIND_INT, KIND_BOOL, KIND_LIST, KIND_JSON, KIND_VECF32)


@dataclass(frozen=True)
class Property:
    name: str
    kind: str = KIND_STR
    required: bool = False


PROP_ID = Property("id", KIND_STR, required=True)

# Every node also carries these (02, "Every node also carries").
COMMON_NODE_PROPERTIES: tuple[Property, ...] = (
    Property("fingerprint", KIND_STR),
    Property("written_at", KIND_STR),
    Property("clearance", KIND_INT),
    Property("access_owner_id", KIND_STR),
)

NODE_PROPERTIES: dict[str, tuple[Property, ...]] = {
    LABEL_PERSON: (
        PROP_ID,
        Property("name"),
        Property("email"),
        Property("role"),
        Property("department"),
        Property("start_date"),
        Property("manager"),
        Property("team"),
    ),
    LABEL_TEAM: (PROP_ID, Property("name")),
    LABEL_PROJECT: (
        PROP_ID,
        Property("name"),
        Property("department"),
        Property("status"),
        Property("priority"),
        Property("scope"),
        Property("budget", KIND_INT),
        Property("target_end"),
    ),
    LABEL_TICKET: (
        PROP_ID,
        Property("key"),
        Property("title"),
        Property("status"),
        Property("priority"),
        Property("created"),
        Property("resolved"),
        Property("due"),
        Property("description"),
        Property("comments", KIND_LIST),
    ),
    LABEL_DOCUMENT: (
        PROP_ID,
        Property("title"),
        Property("family"),
        Property("version", KIND_INT),
        Property("created"),
        Property("status"),
        Property("content"),
        Property("key_facts", KIND_LIST),
        Property("current", KIND_BOOL),
        Property("url"),
        Property("tags", KIND_LIST),
        Property("embedding", KIND_VECF32),
    ),
    LABEL_DECISION: (
        PROP_ID,
        Property("key"),
        Property("title"),
        Property("date"),
        Property("status"),
        Property("description"),
        Property("rationale"),
        Property("impact"),
        Property("details", KIND_JSON),
        Property("current", KIND_BOOL),
    ),
    LABEL_CHAT_MESSAGE: (
        PROP_ID,
        Property("channel"),
        Property("author_name"),
        Property("ts"),
        Property("text"),
        Property("thread"),
        Property("reactions", KIND_LIST),
    ),
    LABEL_CHANGE: (
        PROP_ID,
        Property("entity_id"),
        Property("entity_type"),
        Property("field"),
        Property("old_value"),
        Property("new_value"),
        Property("date"),
        Property("reason"),
        Property("version", KIND_INT),
        Property("seq", KIND_INT),
        Property("current", KIND_BOOL),
    ),
    LABEL_CHUNK: (
        PROP_ID,
        Property("document_id"),
        Property("text"),
        Property("offset", KIND_INT),
        Property("embedding", KIND_VECF32),
    ),
    LABEL_META: (
        Property("id", KIND_STR, required=True),
        Property("last_synced_at"),
        Property("dataset_version"),
        Property("record_counts", KIND_JSON),
    ),
}

# --------------------------------------------------------------------------- #
# Relationships
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Relationship:
    type: str
    from_labels: tuple[str, ...]
    to_labels: tuple[str, ...]
    source: str


RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("OWNS", (LABEL_PERSON,), (LABEL_PROJECT,), "project owner"),
    Relationship("SPONSORS", (LABEL_PERSON,), (LABEL_PROJECT,), "project sponsor"),
    Relationship("LEADS", (LABEL_PERSON,), (LABEL_TEAM,), "team lead"),
    Relationship("MEMBER_OF", (LABEL_PERSON,), (LABEL_TEAM,), "team members"),
    Relationship("APPROVED", (LABEL_PERSON,), (LABEL_DECISION,), "approver"),
    Relationship("DISSENTS", (LABEL_PERSON,), (LABEL_DECISION,), "dissent (carries evidence)"),
    Relationship(
        "AUTHORED_BY", (LABEL_DOCUMENT,), (LABEL_PERSON,), "document author"
    ),
    Relationship("ASSIGNED_TO", (LABEL_TICKET,), (LABEL_PERSON,), "ticket assignee"),
    Relationship("REPORTED_BY", (LABEL_TICKET,), (LABEL_PERSON,), "ticket reporter"),
    Relationship("POSTED", (LABEL_PERSON,), (LABEL_CHAT_MESSAGE,), "chat author"),
    Relationship(
        "MENTIONS",
        (LABEL_CHAT_MESSAGE,),
        (LABEL_PROJECT, LABEL_TICKET, LABEL_PERSON),
        "lists, names in text",
    ),
    Relationship("BELONGS_TO", (LABEL_TICKET,), (LABEL_PROJECT,), "ticket list"),
    Relationship("BLOCKS", (LABEL_TICKET,), (LABEL_TICKET,), "ticket list"),
    Relationship("SPLIT_FROM", (LABEL_TICKET,), (LABEL_TICKET,), "ticket list"),
    Relationship("RELATED_TO", (LABEL_TICKET,), (LABEL_TICKET,), "ticket list"),
    Relationship("DEPENDS_ON", (LABEL_PROJECT,), (LABEL_PROJECT,), "project list"),
    Relationship(
        "SUPERSEDES",
        (LABEL_DECISION, LABEL_DOCUMENT),
        (LABEL_DECISION, LABEL_DOCUMENT),
        "lists, document supersedes field",
    ),
    Relationship(
        "SUPERSEDED_BY", (LABEL_DECISION,), (LABEL_DECISION,), "derived, when no SUPERSEDES"
    ),
    Relationship("OVERRIDES", (LABEL_DECISION,), (LABEL_DECISION,), "fields, lists"),
    Relationship("REVERSES", (LABEL_DECISION,), (LABEL_DECISION,), "fields, lists"),
    Relationship("CONTRADICTS", (LABEL_DECISION,), (LABEL_DECISION,), "fields, lists"),
    Relationship("AFFECTS", (LABEL_DECISION,), (LABEL_PROJECT,), "decision list"),
    Relationship(
        "DOCUMENTED_IN",
        (LABEL_DECISION,),
        (LABEL_DOCUMENT, LABEL_CHAT_MESSAGE),
        "decision list",
    ),
    Relationship(
        "ANNOUNCED_IN",
        (LABEL_DECISION,),
        (LABEL_DOCUMENT, LABEL_CHAT_MESSAGE),
        "decision list",
    ),
    Relationship("TRIGGERED", (LABEL_DOCUMENT,), (LABEL_DECISION,), "document list"),
    Relationship("MADE_CHANGE", (LABEL_PERSON,), (LABEL_CHANGE,), "who made the change"),
    Relationship(
        "CHANGES", (LABEL_CHANGE,), (LABEL_PROJECT, LABEL_TICKET), "the record's history"
    ),
    Relationship(
        "SOURCED_FROM",
        (LABEL_CHANGE, LABEL_DECISION),
        (LABEL_DOCUMENT, LABEL_CHAT_MESSAGE, LABEL_DECISION),
        "evidence lists",
    ),
    Relationship("PART_OF", (LABEL_CHUNK,), (LABEL_DOCUMENT,), "chunking"),
)

RELATIONSHIP_TYPES: frozenset[str] = frozenset(r.type for r in RELATIONSHIPS)
RELATIONSHIP_BY_TYPE: dict[str, Relationship] = {r.type: r for r in RELATIONSHIPS}

# Relationship types that may legitimately appear as typed lists in the dataset.
RELATIONSHIP_LIST_TYPES: frozenset[str] = RELATIONSHIP_TYPES

# --------------------------------------------------------------------------- #
# Reference fields (a record field that produces an edge)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RefField:
    section: str
    field: str
    target_section: str
    relationship: Optional[str] = None
    many: bool = False
    reverse: bool = False
    evidence: bool = False


REFERENCE_FIELDS: tuple[RefField, ...] = (
    RefField("people", "manager", "people"),
    RefField("people", "team", "teams", relationship="MEMBER_OF"),
    RefField("teams", "lead", "people", relationship="LEADS", reverse=True),
    RefField("teams", "members", "people", relationship="MEMBER_OF", many=True, reverse=True),
    RefField("projects", "owner", "people", relationship="OWNS", reverse=True),
    RefField("projects", "sponsor", "people", relationship="SPONSORS", reverse=True),
    RefField("tickets", "assignee", "people", relationship="ASSIGNED_TO"),
    RefField("tickets", "reporter", "people", relationship="REPORTED_BY"),
    RefField("tickets", "project", "projects", relationship="BELONGS_TO"),
    RefField("documents", "author", "people", relationship="AUTHORED_BY"),
    RefField(
        "decisions", "approvers", "people", relationship="APPROVED", many=True, reverse=True
    ),
    RefField(
        "decisions",
        "dissent",
        "people",
        relationship="DISSENTS",
        many=True,
        reverse=True,
        evidence=True,
    ),
    RefField(
        "chat_messages", "author", "people", relationship="POSTED", reverse=True
    ),
    RefField("history", "made_by", "people", relationship="MADE_CHANGE", reverse=True),
)

REFERENCE_FIELD_INDEX: dict[tuple[str, str], RefField] = {
    (r.section, r.field): r for r in REFERENCE_FIELDS
}

# --------------------------------------------------------------------------- #
# Clearance rules (06)
# --------------------------------------------------------------------------- #

CLEARANCE_MIN = 0
CLEARANCE_MAX = 3
CLEARANCE_DEFAULT = 1
ACCESS_OWNER_REQUIRED_AT = 2
CLEARANCE_PARAM = "clr"

CLEARANCE_LABELS: dict[int, str] = {
    0: "public",
    1: "internal",
    2: "confidential",
    3: "restricted",
}


def is_valid_clearance(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and CLEARANCE_MIN <= value <= CLEARANCE_MAX
    )


def requires_access_owner(value: int) -> bool:
    return value >= ACCESS_OWNER_REQUIRED_AT


def visibility_clause(var: str) -> str:
    """The Cypher visibility predicate for one variable (A1)."""

    return f"{var}.clearance <= ${CLEARANCE_PARAM}"


# --------------------------------------------------------------------------- #
# Indexes / DDL (02, syntax verified against FalkorDB 4.2.1 on day 1)
# --------------------------------------------------------------------------- #

DEFAULT_EMBEDDING_DIMENSION = 1536

_ID_INDEXED_LABELS: tuple[str, ...] = (
    LABEL_PERSON,
    LABEL_TEAM,
    LABEL_PROJECT,
    LABEL_TICKET,
    LABEL_DOCUMENT,
    LABEL_DECISION,
    LABEL_CHAT_MESSAGE,
    LABEL_CHANGE,
)


@dataclass(frozen=True)
class IndexSpec:
    kind: str  # "range" | "fulltext" | "vector"
    label: str
    fields: tuple[str, ...]

    def statement(self, dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> str:
        if self.kind == "range":
            field = self.fields[0]
            return f"CREATE INDEX FOR (n:{self.label}) ON (n.{field})"
        if self.kind == "fulltext":
            fields = ", ".join(f"'{f}'" for f in self.fields)
            return f"CALL db.idx.fulltext.createNodeIndex('{self.label}', {fields})"
        if self.kind == "vector":
            field = self.fields[0]
            return (
                f"CREATE VECTOR INDEX FOR (n:{self.label}) ON (n.{field}) "
                f"OPTIONS {{dimension: {dimension}, similarityFunction: 'cosine'}}"
            )
        raise ValueError(f"unknown index kind: {self.kind!r}")


INDEX_SPECS: tuple[IndexSpec, ...] = tuple(
    IndexSpec("range", label, ("id",)) for label in _ID_INDEXED_LABELS
) + (
    IndexSpec("range", LABEL_DECISION, ("current",)),
    IndexSpec("range", LABEL_DOCUMENT, ("current",)),
    IndexSpec("fulltext", LABEL_DOCUMENT, ("title", "content")),
    IndexSpec("fulltext", LABEL_CHAT_MESSAGE, ("text",)),
    IndexSpec("fulltext", LABEL_DECISION, ("title", "description", "rationale")),
    IndexSpec("vector", LABEL_DOCUMENT, ("embedding",)),
)

# Statements that produce no index (allow-listed DDL that is not a query). Kept
# for check.sh item 7.
DDL_ALLOWLIST = ("ensure_schema", "meta")


@dataclass(frozen=True)
class SchemaReport:
    dimension: int
    created: tuple[str, ...]

    @property
    def created_count(self) -> int:
        return len(self.created)


def ddl_statements(dimension: int = DEFAULT_EMBEDDING_DIMENSION) -> tuple[str, ...]:
    return tuple(spec.statement(dimension) for spec in INDEX_SPECS)


def ensure_schema(
    writer: IngestWriter, dimension: int = DEFAULT_EMBEDDING_DIMENSION
) -> SchemaReport:
    """Create every index and fulltext/vector index if missing.

    Idempotent: re-running creates nothing and raises nothing. FalkorDB reports
    "already indexed" on a repeat instead of succeeding, so that specific error
    is treated as success; every other error propagates.
    """

    created: list[str] = []
    for statement in ddl_statements(dimension):
        try:
            writer.write_query(statement)
        except GraphError as exc:
            if "already indexed" in str(exc).lower():
                continue
            raise
        created.append(statement)
    return SchemaReport(dimension=dimension, created=tuple(created))


# --------------------------------------------------------------------------- #
# Schema description (drives generator instructions and check.sh)
# --------------------------------------------------------------------------- #


def all_property_names(label: str) -> tuple[str, ...]:
    names = [p.name for p in NODE_PROPERTIES.get(label, ())]
    for prop in COMMON_NODE_PROPERTIES:
        if prop.name not in names:
            names.append(prop.name)
    return tuple(names)


def describe_schema() -> str:
    """A human- and model-readable description generated from this module."""

    lines: list[str] = ["Nodes:"]
    for label in LABELS:
        props = ", ".join(p.name for p in NODE_PROPERTIES[label])
        lines.append(f"  {label}({props})")
    lines.append("")
    lines.append("Relationships:")
    for rel in RELATIONSHIPS:
        left = "|".join(rel.from_labels)
        right = "|".join(rel.to_labels)
        lines.append(f"  {rel.type}: {left} -> {right} ({rel.source})")
    lines.append("")
    lines.append("Common node properties: " + ", ".join(p.name for p in COMMON_NODE_PROPERTIES))
    lines.append(
        "Clearance: "
        + ", ".join(f"{k} {v}" for k, v in sorted(CLEARANCE_LABELS.items()))
        + f"; a node is visible to a user iff {visibility_clause('n')}; "
        + f"clearance >= {ACCESS_OWNER_REQUIRED_AT} requires an access_owner_id."
    )
    lines.append("Indexes:")
    for spec in INDEX_SPECS:
        lines.append(f"  {spec.kind} {spec.label}({', '.join(spec.fields)})")
    return "\n".join(lines)


def iter_property_kinds() -> Iterable[tuple[str, str, str]]:
    for label, props in NODE_PROPERTIES.items():
        for prop in props:
            yield label, prop.name, prop.kind


__all__ = [
    "LABELS",
    "ENTITY_LABELS",
    "ID_PREFIX_TO_SECTION",
    "NODE_PROPERTIES",
    "COMMON_NODE_PROPERTIES",
    "RELATIONSHIPS",
    "RELATIONSHIP_TYPES",
    "RELATIONSHIP_LIST_TYPES",
    "REFERENCE_FIELDS",
    "INDEX_SPECS",
    "ensure_schema",
    "describe_schema",
    "visibility_clause",
]
