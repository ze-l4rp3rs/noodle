"""The HTTP API: ``/health`` and ``/ask`` over the shared answer service (07).

Only these two endpoints belong to this step. ``/copilot/*`` arrives with step
12, ``/ingest`` with step 13, and the full auth middleware and ``/users`` with
step 11; until then the dev ``X-Noodle-User`` header maps to a Person and
anonymous requests leak nothing.

Two rules from 07 drive the shape: ``/ask`` never blocks waiting for input, and a
graph outage is always ``503`` -- never a cached or model-only answer.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import __version__
from .graph import GraphError
from .queries import UserCtx
from .response import stream_events
from .service import GraphDownError, Runtime, answer


def _wants_stream(request: Request) -> bool:
    return "text/event-stream" in request.headers.get("accept", "")


def _resolve_user(request: Request, runtime: Runtime) -> UserCtx:
    """The caller for this request. Full auth is step 11.

    Outside production the dev ``X-Noodle-User`` header names the Person (07); an
    absent header is anonymous, which is clearance 0 and leaks nothing.
    """

    if runtime.settings.is_prod:
        return UserCtx.anonymous()
    raw = request.headers.get("x-noodle-user")
    if not raw:
        return UserCtx.anonymous()
    return UserCtx(id=raw.strip())


def _graph_down() -> JSONResponse:
    return JSONResponse(status_code=503, content={"status": "down", "graph": "down"})


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
