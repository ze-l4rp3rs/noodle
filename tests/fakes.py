"""Test doubles. Unit tests never touch a database or a model."""

from __future__ import annotations

from typing import Any, Optional

from noodle.graph import GraphError


class FakeResult:
    def __init__(self, header: list, result_set: list, run_time_ms: float = 1.0):
        self.header = header
        self.result_set = result_set
        self.run_time_ms = run_time_ms


class FakeGraph:
    """A stand-in for a selected FalkorDB graph. Records every call."""

    def __init__(
        self,
        header: Optional[list] = None,
        result_set: Optional[list] = None,
        raise_on: Optional[str] = None,
    ):
        self.header = header if header is not None else [[1, "id"]]
        self.result_set = result_set if result_set is not None else []
        self.raise_on = raise_on
        self.calls: list[tuple] = []

    def ro_query(self, q: str, params: Optional[dict] = None, timeout: Any = None) -> FakeResult:
        self.calls.append(("ro_query", q, params))
        if self.raise_on == "ro_query":
            raise RuntimeError("boom")
        return FakeResult(self.header, self.result_set)

    def query(self, q: str, params: Optional[dict] = None, timeout: Any = None) -> FakeResult:
        self.calls.append(("query", q, params))
        if self.raise_on == "query":
            raise RuntimeError("boom")
        return FakeResult(self.header, self.result_set)


class FakeWriter:
    """A writer whose second attempt at a statement fails like FalkorDB does."""

    def __init__(self):
        self.seen: set[str] = set()
        self.calls: list[str] = []

    def write_query(self, cypher: str, params: Optional[dict] = None, timeout: Any = None):
        self.calls.append(cypher)
        if cypher in self.seen:
            field = cypher.split("ON (n.")[-1].split(")")[0] if "ON (n." in cypher else "x"
            raise GraphError(f"write query failed: Attribute '{field}' is already indexed")
        self.seen.add(cypher)
        return FakeResult([], [])
