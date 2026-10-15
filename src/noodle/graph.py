"""FalkorDB access.

Two doorways, deliberately split:

* :class:`GraphClient` is the **read-only** path. Every read in Noodle goes
  through it and uses ``ro_query`` (``GRAPH.RO_QUERY``).
* :class:`IngestWriter` is the **write** path. It exists only for ingestion and
  for ``ensure_schema()`` (the one allow-listed DDL exception). Nothing else may
  import it; a test enforces that.

Removing FalkorDB removes the product: there is no in-memory copy and no cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence
from urllib.parse import unquote, urlparse

from falkordb import FalkorDB

# Modules allowed to reference the write path. Keep in sync with tests and
# scripts/check.sh. ``noodle.schema`` runs the idempotent DDL in ensure_schema;
# ``noodle.bootstrap`` is the bring-up entrypoint that calls it.
WRITE_MODULES_ALLOWED = frozenset(
    {"noodle.ingestion", "noodle.schema", "noodle.bootstrap"}
)


class GraphError(Exception):
    """A graph operation failed. Callers must let this propagate, never turn it
    into answer text."""


@dataclass(frozen=True)
class ConnectionInfo:
    host: str
    port: int
    password: Optional[str] = None
    username: Optional[str] = None
    ssl: bool = False


def parse_graph_url(url: str) -> ConnectionInfo:
    """Parse ``host:port``, ``redis://``, ``rediss://`` and FalkorDB Cloud URLs."""

    raw = (url or "").strip()
    if not raw:
        return ConnectionInfo(host="localhost", port=6379)

    if "://" not in raw:
        raw = "redis://" + raw

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "redis").lower()
    if scheme not in {"redis", "rediss"}:
        raise GraphError(f"unsupported graph URL scheme: {scheme!r}")
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    return ConnectionInfo(
        host=host,
        port=port,
        password=unquote(parsed.password) if parsed.password else None,
        username=unquote(parsed.username) if parsed.username else None,
        ssl=scheme == "rediss",
    )


@dataclass
class GraphResult:
    """Named rows plus the exact statement executed, for the graph trace."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    cypher: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    run_time_ms: float = 0.0


def _header_names(header: Sequence[Any]) -> list[str]:
    names: list[str] = []
    for col in header or ():
        if isinstance(col, (list, tuple)):
            names.append(str(col[-1]))
        else:
            names.append(str(col))
    return names


def _to_graph_result(raw: Any, cypher: str, params: dict[str, Any]) -> GraphResult:
    names = _header_names(getattr(raw, "header", []))
    rows: list[dict[str, Any]] = []
    for record in getattr(raw, "result_set", []) or []:
        rows.append({name: value for name, value in zip(names, record)})
    return GraphResult(
        rows=rows,
        header=names,
        cypher=cypher,
        params=params,
        run_time_ms=float(getattr(raw, "run_time_ms", 0.0) or 0.0),
    )


def _build_graph(url: str, graph_name: str):
    info = parse_graph_url(url)
    driver = FalkorDB(
        host=info.host,
        port=info.port,
        username=info.username,
        password=info.password,
        ssl=info.ssl,
    )
    return driver, driver.select_graph(graph_name)


class _BaseGraph:
    """Shared connection handling. Not used directly."""

    def __init__(self, url: str, graph_name: str, graph: Any = None):
        self._url = url
        self._graph_name = graph_name
        if graph is not None:
            self._driver = None
            self._graph = graph
        else:
            self._driver, self._graph = _build_graph(url, graph_name)

    @property
    def graph_name(self) -> str:
        return self._graph_name

    def is_up(self) -> bool:
        """Server liveness. Uses a redis PING so it works on an empty graph.

        ``GRAPH.RO_QUERY`` refuses to run against a graph key that does not yet
        exist ("Invalid graph operation on empty key"), so it is not a valid
        liveness probe before ``ensure_schema()`` has created the key.
        """

        if self._driver is not None:
            try:
                return bool(self._driver.execute_command("PING"))
            except Exception:  # noqa: BLE001 - down is False, not an error string
                return False
        try:
            self._graph.ro_query("RETURN 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass


class GraphClient(_BaseGraph):
    """Read-only graph access. The only sanctioned read doorway."""

    def ro_query(
        self, cypher: str, params: Optional[dict[str, Any]] = None, timeout: Optional[int] = None
    ) -> GraphResult:
        if not cypher or not cypher.strip():
            raise GraphError("refusing to run an empty statement")
        try:
            raw = self._graph.ro_query(cypher, params or {}, timeout)
        except Exception as exc:  # noqa: BLE001 - re-raised as GraphError, never text
            raise GraphError(f"read query failed: {exc}") from exc
        return _to_graph_result(raw, cypher, params or {})

    def ping(self) -> bool:
        return self.is_up()


class IngestWriter(_BaseGraph):
    """Ingestion-only write access.

    Do not import this outside ``noodle.ingestion`` or ``noodle.schema``. Only
    ingestion mutates the graph; everything else must use :class:`GraphClient`.
    """

    def write_query(
        self, cypher: str, params: Optional[dict[str, Any]] = None, timeout: Optional[int] = None
    ) -> GraphResult:
        if not cypher or not cypher.strip():
            raise GraphError("refusing to run an empty statement")
        try:
            raw = self._graph.query(cypher, params or {}, timeout)
        except Exception as exc:  # noqa: BLE001 - re-raised as GraphError, never text
            raise GraphError(f"write query failed: {exc}") from exc
        return _to_graph_result(raw, cypher, params or {})


def open_reader(url: str, graph_name: str, graph: Any = None) -> GraphClient:
    return GraphClient(url, graph_name, graph=graph)


def open_writer(url: str, graph_name: str, graph: Any = None) -> IngestWriter:
    return IngestWriter(url, graph_name, graph=graph)
