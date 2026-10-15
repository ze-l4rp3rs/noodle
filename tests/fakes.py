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


class FakeIngestGraph:
    """An in-memory stand-in for a FalkorDB graph that understands the two
    batched statements ingestion emits, so the diff/write loop can be tested
    without a database.

    Set ``fail_batch_over`` to make any batch larger than that many entries fail
    once (used to prove the per-entry retry), and ``fail_ids`` to make a
    specific entry keep failing (used to prove the failing entry is named).
    """

    def __init__(self, empty_key: bool = False):
        self.nodes: dict[str, dict] = {}
        self.rels: dict[tuple, dict] = {}
        self.meta: Optional[dict] = None
        self.empty_key = empty_key
        self.fail_batch_over: Optional[int] = None
        self.fail_ids: set[str] = set()
        self.node_writes = 0
        self.rel_writes = 0
        self.meta_writes = 0

    def _identity(self, row: dict) -> str:
        return str(row.get("id") or row.get("src") or "")

    def _guard(self, batch: list) -> None:
        if not isinstance(batch, list) or not batch:
            return
        if self.fail_batch_over is not None and len(batch) > self.fail_batch_over:
            raise GraphError(f"simulated batch failure for {len(batch)} entries")
        for row in batch:
            identity = self._identity(row)
            if identity in self.fail_ids:
                raise GraphError(f"simulated failure for {identity}")

    def ro_query(self, q: str, params: Optional[dict] = None, timeout: Any = None) -> FakeResult:
        if self.empty_key:
            raise GraphError("read query failed: Invalid graph operation on empty key")
        if "n.fingerprint" in q:
            label = q.split("(n:", 1)[1].split(")", 1)[0]
            rows = [
                [p.get("id"), p.get("fingerprint"), p.get("current")]
                for p in self.nodes.values()
                if p.get("_label") == label
            ]
            return FakeResult([[1, "id"], [1, "fingerprint"], [1, "current"]], rows)
        if "type(r)" in q:
            rows = [[t, s, d, p.get("origin")] for (t, s, d), p in self.rels.items()]
            return FakeResult(
                [[1, "type"], [1, "src"], [1, "dst"], [1, "origin"]], rows
            )
        return FakeResult([], [])

    def query(self, q: str, params: Optional[dict] = None, timeout: Any = None) -> FakeResult:
        params = params or {}
        batch = params.get("batch")
        if "SET n = row.props" in q:
            self._guard(batch)
            label = q.split("MERGE (n:", 1)[1].split(" ", 1)[0]
            for row in batch:
                props = dict(row["props"])
                props["_label"] = label
                self.nodes[props["id"]] = props
            self.node_writes += 1
            return FakeResult([], [])
        if "SET r = row.props" in q:
            self._guard(batch)
            rel_type = q.split("[r:", 1)[1].split("]", 1)[0]
            for row in batch:
                self.rels[(rel_type, row["src"], row["dst"])] = row["props"]
            self.rel_writes += 1
            return FakeResult([], [])
        if "last_synced_at" in q:
            self.meta = dict(params)
            self.meta_writes += 1
            return FakeResult([], [])
        return FakeResult([], [])

