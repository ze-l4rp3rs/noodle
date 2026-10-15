from __future__ import annotations

from pathlib import Path

import pytest

from noodle.graph import (
    WRITE_MODULES_ALLOWED,
    GraphClient,
    GraphError,
    IngestWriter,
    parse_graph_url,
)
from tests.fakes import FakeGraph


def test_parse_bare_host_port():
    info = parse_graph_url("localhost:6379")
    assert (info.host, info.port) == ("localhost", 6379)
    assert info.ssl is False


def test_parse_redis_url_with_credentials():
    info = parse_graph_url("redis://default:secret@cloud.example:1234")
    assert info.host == "cloud.example"
    assert info.port == 1234
    assert info.username == "default"
    assert info.password == "secret"


def test_parse_rediss_sets_ssl():
    assert parse_graph_url("rediss://cloud.example:6380").ssl is True


def test_parse_rejects_unknown_scheme():
    with pytest.raises(GraphError):
        parse_graph_url("http://example:1234")


def test_ro_query_returns_named_rows():
    fake = FakeGraph(header=[[1, "id"], [1, "name"]], result_set=[["person:a", "Ada"]])
    client = GraphClient("localhost:6379", "noodle", graph=fake)
    result = client.ro_query("MATCH (n) RETURN n.id AS id, n.name AS name")
    assert result.rows == [{"id": "person:a", "name": "Ada"}]
    assert fake.calls[0][0] == "ro_query"


def test_ro_query_empty_statement_is_refused():
    client = GraphClient("localhost:6379", "noodle", graph=FakeGraph())
    with pytest.raises(GraphError):
        client.ro_query("   ")


def test_ro_query_failure_is_a_graph_error_not_text():
    client = GraphClient("localhost:6379", "noodle", graph=FakeGraph(raise_on="ro_query"))
    with pytest.raises(GraphError):
        client.ro_query("RETURN 1")


def test_ping_true_and_false():
    assert GraphClient("x:1", "g", graph=FakeGraph()).ping() is True
    assert GraphClient("x:1", "g", graph=FakeGraph(raise_on="ro_query")).ping() is False


def test_writer_uses_write_call_not_read_call():
    fake = FakeGraph()
    writer = IngestWriter("localhost:6379", "noodle", graph=fake)
    writer.write_query("CREATE (n:Person {id: 'person:a'})")
    assert fake.calls[0][0] == "query"


def test_only_allowlisted_modules_reference_the_write_path():
    """Non-negotiable: only ingestion (and the DDL bootstrap) may write."""

    src = Path(__file__).resolve().parent.parent / "src" / "noodle"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        if path.name == "graph.py":
            continue  # the write path is defined here
        text = path.read_text(encoding="utf-8")
        module = "noodle." + path.relative_to(src).with_suffix("").as_posix().replace("/", ".")
        touches_write = "write_query(" in text or "open_writer(" in text or "IngestWriter(" in text
        if touches_write and module not in WRITE_MODULES_ALLOWED:
            offenders.append(module)
    assert offenders == [], f"modules using the write path: {offenders}"
