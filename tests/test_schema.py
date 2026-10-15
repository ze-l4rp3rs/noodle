from __future__ import annotations

import pytest

from noodle import schema
from noodle.schema import (
    ACCESS_OWNER_REQUIRED_AT,
    CLEARANCE_MAX,
    CLEARANCE_MIN,
    COMMON_NODE_PROPERTIES,
    ENTITY_LABELS,
    INDEX_SPECS,
    LABELS,
    NODE_PROPERTIES,
    REFERENCE_FIELDS,
    RELATIONSHIP_TYPES,
    RELATIONSHIPS,
    ddl_statements,
    describe_schema,
    ensure_schema,
    is_valid_clearance,
    requires_access_owner,
    visibility_clause,
)
from tests.fakes import FakeWriter


def test_labels_are_unique_and_properties_present():
    assert len(LABELS) == len(set(LABELS))
    for label in LABELS:
        assert label in NODE_PROPERTIES
        assert any(p.name == "id" for p in NODE_PROPERTIES[label])


def test_every_entity_section_maps_to_a_label():
    for section, label in ENTITY_LABELS.items():
        assert label in LABELS, (section, label)


def test_relationship_types_unique_and_endpoints_valid():
    types = [r.type for r in RELATIONSHIPS]
    assert len(types) == len(set(types))
    for rel in RELATIONSHIPS:
        for label in rel.from_labels + rel.to_labels:
            assert label in LABELS, (rel.type, label)


def test_reference_fields_resolve_to_known_sections_and_types():
    for ref in REFERENCE_FIELDS:
        assert ref.section in ENTITY_LABELS, ref
        assert ref.target_section in ENTITY_LABELS, ref
        if ref.relationship is not None:
            assert ref.relationship in RELATIONSHIP_TYPES, ref


def test_index_specs_cover_ids_and_current_flags():
    labels_indexed = {spec.label for spec in INDEX_SPECS if spec.kind == "range"}
    for label in ("Person", "Team", "Project", "Ticket", "Document", "Decision", "ChatMessage", "Change"):
        assert label in labels_indexed
    assert any(s.kind == "fulltext" and s.label == "Document" for s in INDEX_SPECS)
    assert any(s.kind == "vector" and s.label == "Document" for s in INDEX_SPECS)


def test_ddl_statements_use_verified_syntax_and_dimension():
    statements = "\n".join(ddl_statements(1536))
    assert "CREATE INDEX FOR (n:Person) ON (n.id)" in statements
    assert "CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'content')" in statements
    assert "CREATE VECTOR INDEX FOR (n:Document) ON (n.embedding)" in statements
    assert "dimension: 1536" in statements


def test_clearance_rules():
    assert (CLEARANCE_MIN, CLEARANCE_MAX) == (0, 3)
    assert is_valid_clearance(0) and is_valid_clearance(3)
    assert not is_valid_clearance(-1) and not is_valid_clearance(4)
    assert not is_valid_clearance(True) and not is_valid_clearance("1")
    assert requires_access_owner(2) and requires_access_owner(3)
    assert not requires_access_owner(1)
    assert ACCESS_OWNER_REQUIRED_AT == 2


def test_visibility_clause_uses_the_clr_parameter():
    assert visibility_clause("n") == "n.clearance <= $clr"


def test_describe_schema_covers_everything():
    text = describe_schema()
    for label in LABELS:
        assert label in text
    for rel in RELATIONSHIPS:
        assert rel.type in text
    for label, props in NODE_PROPERTIES.items():
        for prop in props:
            assert prop.name in text
    for prop in COMMON_NODE_PROPERTIES:
        assert prop.name in text


def test_ensure_schema_is_idempotent_with_a_repeating_writer():
    writer = FakeWriter()
    first = ensure_schema(writer, dimension=1536)
    assert first.created_count == len(schema.INDEX_SPECS)
    second = ensure_schema(writer, dimension=1536)
    assert second.created_count == 0


def test_ensure_schema_surfaces_unexpected_errors():
    class Angry:
        def write_query(self, cypher, params=None, timeout=None):
            from noodle.graph import GraphError

            raise GraphError("write query failed: something else broke")

    with pytest.raises(Exception):
        ensure_schema(Angry(), dimension=1536)
