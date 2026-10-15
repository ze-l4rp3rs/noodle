"""Step 4 unit tests: the query library's pure parts and queries.md freshness.

No database is touched here. The live queries are covered by
``tests/integration/test_queries.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noodle import queries as Q
from noodle.graph import GraphError

ROOT = Path(__file__).resolve().parent.parent


class _BoomClient:
    """Every read fails; a query must surface the error, never swallow it."""

    def ro_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise GraphError("boom")


def test_a_failing_query_raises_rather_than_returning_an_error():
    with pytest.raises(GraphError):
        Q.q2_owners(_BoomClient(), Q.UserCtx("person:x", 1), "project:auth-migration")


# --------------------------------------------------------------------------- #
# User context, results, redaction
# --------------------------------------------------------------------------- #


def test_anonymous_user_is_clearance_zero():
    user = Q.UserCtx.anonymous()
    assert user.clearance == 0
    assert user.params()["clr"] == 0


def test_user_params_carry_clearance_and_extras():
    user = Q.UserCtx("person:ada-wren", clearance=2)
    params = user.params(id="x")
    assert params["clr"] == 2 and params["id"] == "x"


def test_hidden_info_reports_presence():
    assert not Q.HiddenInfo().hidden
    assert Q.HiddenInfo(count=1, access_owners=("person:x",)).hidden


def test_redaction_drops_embeddings_and_user_context():
    redacted = Q._redact({"clr": 1, "user_id": "person:x", "vec": [0.1], "embedding": [0.2], "text": "hi"})
    assert redacted == {"text": "hi"}


def test_collect_node_ids_finds_ids_anywhere_and_dedupes():
    rows = [
        {"a": "person:x", "b": ["ticket:Y", "not-an-id"]},
        {"nested": {"c": "doc:z"}},
        {"a": "person:x"},
    ]
    ids = Q._collect_node_ids(rows)
    assert ids == ["person:x", "ticket:Y", "doc:z"]


def test_role_label_names_the_edge():
    assert Q.role_label("ASSIGNED_TO") == "assignee"
    assert Q.role_label("UNKNOWN_REL") == "unknown_rel"


# --------------------------------------------------------------------------- #
# Expertise scoring (pure)
# --------------------------------------------------------------------------- #


def test_score_experts_weights_and_orders():
    rows = [
        {"person_id": "p:1", "name": "One", "rel": "OWNS", "via": "project:x", "on": "2026-09-12"},
        {"person_id": "p:2", "name": "Two", "rel": "POSTED", "via": "chat:y", "on": "2026-09-12"},
    ]
    ranked = Q.score_experts(rows)
    assert [e["person_id"] for e in ranked] == ["p:1", "p:2"]
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["items"][0]["role"] == "owner"


def test_score_experts_caps_chat_evidence_at_five():
    rows = [
        {"person_id": "p:1", "name": "One", "rel": "POSTED", "via": f"chat:{i}", "on": "2026-09-12"}
        for i in range(9)
    ]
    ranked = Q.score_experts(rows)
    assert len(ranked[0]["items"]) == 5


def test_score_experts_recency_scales_with_distance():
    recent = [{"person_id": "p:1", "rel": "OWNS", "via": "v", "on": "2026-09-12"}]
    old = [{"person_id": "p:1", "rel": "OWNS", "via": "v", "on": "2025-01-01"}]
    near = Q.score_experts(recent, topic_latest="2026-09-12")[0]["score"]
    far = Q.score_experts(old, topic_latest="2026-09-12")[0]["score"]
    assert near > far


# --------------------------------------------------------------------------- #
# queries.md is generated from the source of truth
# --------------------------------------------------------------------------- #


def test_query_docs_reference_real_functions():
    for doc in Q.QUERY_DOCS:
        assert callable(getattr(Q, doc.function, None)), doc.function
        if doc.hidden_twin:
            assert callable(getattr(Q, doc.hidden_twin, None)), doc.hidden_twin


def test_render_markdown_names_every_query_and_the_predicate():
    text = Q.render_markdown()
    for doc in Q.QUERY_DOCS:
        assert f"## {doc.qid} " in text
        assert doc.function in text
    assert "$clr" in text


def test_committed_queries_md_is_fresh():
    committed = (ROOT / "queries.md").read_text(encoding="utf-8")
    expected = Q.render_markdown()
    if not expected.endswith("\n"):
        expected += "\n"
    assert committed == expected, "run scripts/gen_queries_md.py"
