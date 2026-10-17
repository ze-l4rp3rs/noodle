"""The HTTP API: ``/health``, ``/ask``, the copilot surface and ``/graph/subgraph``.

``/ingest`` arrives with step 13. The caller is resolved by :mod:`noodle.auth`: a
bearer token (or, in development only, the ``X-Noodle-User`` header) is mapped to
a Person and their clearance is read from the graph (step 11). ``GET /users`` is
the dev-only switcher (07).

Two rules from 07 drive the shape: ``/ask`` never blocks waiting for input, and a
graph outage is always ``503`` -- never a cached or model-only answer. The copilot
endpoints share that rule: ``/copilot/context`` reads the graph directly and
``/copilot/draft`` runs the same heavy-mode pipeline as ``/ask`` (step 12).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import __version__, auth, copilot
from .graph import GraphError
from .queries import UserCtx, q14_people, q15_subgraph
from .response import stream_events
from .service import GraphDownError, Runtime, answer


def _wants_stream(request: Request) -> bool:
    return "text/event-stream" in request.headers.get("accept", "")


def _resolve_user(request: Request, runtime: Runtime) -> UserCtx:
    """The caller for this request (07, step 11)."""

    return auth.user_for_request(request, runtime)


def _graph_down() -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "down", "graph": "down"})


def _format_subgraph(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Turn Q15 rows into deduplicated nodes and edges for the UI (07, C6).

    The query already filtered hidden nodes, so whatever it returned is visible;
    this only folds the per-seed path rows into one node list and one edge list.
    """

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}

    def add_node(node: Any) -> None:
        if isinstance(node, dict) and node.get("id"):
            nodes.setdefault(
                node["id"],
                {"id": node["id"], "label": node.get("label") or "", "name": node.get("name") or node["id"]},
            )

    for row in rows:
        add_node(row.get("seed"))
        for node in row.get("nodes") or []:
            add_node(node)
        node_ids = row.get("node_ids") or []
        rels = row.get("rels") or []
        for index in range(min(len(rels), len(node_ids) - 1)):
            edge_id = f"{node_ids[index]}|{rels[index]}|{node_ids[index + 1]}"
            edges.setdefault(
                edge_id,
                {
                    "id": edge_id,
                    "source": node_ids[index],
                    "target": node_ids[index + 1],
                    "type": rels[index],
                },
            )
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def create_app(runtime: Runtime) -> FastAPI:
    """Build the FastAPI app around a runtime.

    The runtime carries the seeded :class:`~noodle.sources.SourceRegistry` rng, so
    a test can make citation ids deterministic (09).
    """

    app = FastAPI(title="Noodle", version=__version__)
    app.state.runtime = runtime

    @app.get("/health")
    def health() -> JSONResponse:
        if runtime.graph_up():
            return JSONResponse(status_code=200, content={"status": "ok", "graph": "up"})
        return _graph_down()

    @app.get("/users")
    def users() -> JSONResponse:
        """The dev user switcher (07): demo users of every clearance.

        Off in production -- a production caller gets a 404, never the directory.
        """

        if runtime.settings.is_prod:
            return JSONResponse(status_code=404, content={"error": "not found"})
        result = q14_people(runtime.client)
        return JSONResponse(
            status_code=200,
            content={
                "users": [
                    {
                        "id": row.get("id"),
                        "name": row.get("name"),
                        "role": row.get("role"),
                        "clearance": row.get("clearance"),
                    }
                    for row in result.rows
                ]
            },
        )

    @app.get("/graph/subgraph")
    async def subgraph(request: Request, ids: str = "", depth: int = 1) -> JSONResponse:
        """Nodes and edges for the visualisation, visible to the caller only (07)."""

        selected = [part.strip() for part in ids.split(",") if part.strip()]
        if not selected:
            return JSONResponse(status_code=400, content={"error": "missing required query parameter 'ids'"})
        if not runtime.graph_up():
            return _graph_down()
        user = _resolve_user(request, runtime)
        try:
            result = await run_in_threadpool(q15_subgraph, runtime.client, user, selected, depth=depth)
        except (GraphError, GraphDownError):
            return _graph_down()
        return JSONResponse(status_code=200, content=_format_subgraph(result.rows))

    @app.post("/copilot/context")
    async def copilot_context(request: Request) -> JSONResponse:
        """Decisions, owners, documents, dependencies and stale warnings for a node."""

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any unreadable body is a 400
            return JSONResponse(status_code=400, content={"error": "unreadable request body"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})
        entity_id = body.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            return JSONResponse(status_code=400, content={"error": "missing required field 'entity_id'"})
        if not runtime.graph_up():
            return _graph_down()
        user = _resolve_user(request, runtime)
        try:
            context = await run_in_threadpool(copilot.context, runtime.client, user, entity_id.strip())
        except (GraphError, GraphDownError):
            return _graph_down()
        return JSONResponse(status_code=200, content=context)

    @app.post("/copilot/draft")
    async def copilot_draft(request: Request) -> JSONResponse:
        """A heavy-mode draft from graph context; cited, flagged, never sent (07, E6)."""

        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any unreadable body is a 400
            return JSONResponse(status_code=400, content={"error": "unreadable request body"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})
        entity_id = body.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            return JSONResponse(status_code=400, content={"error": "missing required field 'entity_id'"})
        instruction = body.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            return JSONResponse(status_code=400, content={"error": "missing required field 'instruction'"})
        draft_text = body.get("draft_text")
        if draft_text is not None and not isinstance(draft_text, str):
            return JSONResponse(status_code=400, content={"error": "field 'draft_text' must be a string"})

        user = _resolve_user(request, runtime)
        question = copilot.draft_question(entity_id.strip(), instruction.strip(), draft_text)
        try:
            response, _run = await run_in_threadpool(answer, runtime, user, question, mode="heavy")
        except (GraphDownError, GraphError):
            return _graph_down()
        return JSONResponse(status_code=200, content=response)

    @app.post("/ask")
    async def ask(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - any unreadable body is a 400
            return JSONResponse(status_code=400, content={"error": "unreadable request body"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=400, content={"error": "request body must be a JSON object"})
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            return JSONResponse(status_code=400, content={"error": "missing required field 'question'"})
        context = body.get("context")
        if context is not None and not isinstance(context, str):
            return JSONResponse(status_code=400, content={"error": "field 'context' must be a string"})
        mode = body.get("mode")
        if mode is not None and not isinstance(mode, str):
            return JSONResponse(status_code=400, content={"error": "field 'mode' must be a string"})

        user = _resolve_user(request, runtime)
        try:
            response, run = await run_in_threadpool(
                answer, runtime, user, question, mode=mode, context=context
            )
        except (GraphDownError, GraphError):
            return _graph_down()

        if _wants_stream(request):
            return StreamingResponse(
                stream_events(response, run, mode=response["metadata"]["mode"]),
                media_type="text/event-stream",
            )
        return JSONResponse(status_code=200, content=response)

    return app


__all__ = ["create_app"]
