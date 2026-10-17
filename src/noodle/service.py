"""The answer service: one entry point shared by the HTTP API and the CLI.

``answer`` runs the pipeline for one question and returns both the 07 response
object and the run that produced it. The CLI prints the response; ``/ask``
returns it as JSON or turns the run into the SSE stream. Because both call this
one function, the same run yields the same response either way (07, step 9).

FalkorDB is never optional: if the graph is unreachable, :func:`answer` raises
:class:`GraphDownError` before any model call, so ``/ask`` can answer ``503`` and
never a cached or model-only answer (07).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import DEFAULT_FIX_ROUNDS, Settings, load_settings
from .graph import GraphClient, open_reader
from .loop import DEFAULT_MAX_TOOL_ROUNDS, ToolSet
from .pipeline import AnswerRun, run_answer
from .providers import (
    Provider,
    ProviderChoice,
    provider_from_settings,
    select_provider,
)
from .queries import UserCtx
from .response import build_response
from .router import route as route_request
from .sources import SourceRegistry
from .tools import TOOLS


class GraphDownError(RuntimeError):
    """FalkorDB is unreachable. The caller must answer 503, never an answer."""


@dataclass
class Runtime:
    """Everything one answer needs: config, the two providers and the graph.

    ``rng`` seeds the run's :class:`~noodle.sources.SourceRegistry`; a test sets
    it to a deterministic stand-in so citation ids are reproducible (09).
    """

    settings: Settings
    provider: Provider
    reviewer_provider: Provider
    client: GraphClient
    rng: Optional[object] = None
    generator_model: str = ""
    reviewer_model: str = ""
    ingest_writer: Optional[Any] = None

    def graph_up(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:  # noqa: BLE001 - a missing/failing probe is "down"
            return False


def build_runtime(
    settings: Optional[Settings] = None,
    *,
    provider: Optional[Provider] = None,
    reviewer_provider: Optional[Provider] = None,
    client: Optional[GraphClient] = None,
    rng: Optional[object] = None,
    provider_name: Optional[str] = None,
) -> Runtime:
    """Assemble a runtime. Tests inject the provider, the reviewer and the client."""

    settings = settings if settings is not None else load_settings()
    name = provider_name
    generator_model = ""
    reviewer_model = ""
    if provider is None:
        choices = [ProviderChoice(entry.name, entry.key_env) for entry in settings.providers]
        if name is None:
            name = select_provider(choices, default=settings.default_provider)
        chosen = next((entry for entry in settings.providers if entry.name == name), None)
        if chosen is not None:
            generator_model = chosen.generator_model
            reviewer_model = chosen.reviewer_model
        provider = provider_from_settings(name, choices)
    reviewer_provider = reviewer_provider if reviewer_provider is not None else provider
    if client is None:
        client = open_reader(settings.graph.url, settings.graph.name)
    return Runtime(
        settings=settings,
        provider=provider,
        reviewer_provider=reviewer_provider,
        client=client,
        rng=rng,
        generator_model=generator_model,
        reviewer_model=reviewer_model,
    )


def _mode(runtime: Runtime, mode: str):
    modes = runtime.settings.modes
    return modes.get(mode) or modes.get("fast")


def _build_question(question: str, context: Optional[str]) -> str:
    if context:
        return f"{question}\n\nContext:\n{context}"
    return question


def answer(
    runtime: Runtime,
    user: UserCtx,
    question: str,
    *,
    mode: Optional[str] = None,
    context: Optional[str] = None,
) -> tuple[dict[str, Any], AnswerRun]:
    """Answer ``question`` for ``user``. Returns ``(response, run)``.

    Raises :class:`GraphDownError` before any model call when the graph is down.
    The router picks the mode (an explicit ``mode`` wins, then keywords, then the
    default) and detects live intent, which turns on the freshness guard (05, 06).
    """

    if not runtime.graph_up():
        raise GraphDownError("FalkorDB is unreachable")

    decision = route_request(question, explicit=mode)
    mode_cfg = _mode(runtime, decision.mode)
    resolved_mode = mode_cfg.name if mode_cfg is not None else "fast"
    generator_model = (mode_cfg.generator_model if mode_cfg else "") or runtime.generator_model or None
    reviewer_model = (mode_cfg.reviewer_model if mode_cfg else "") or runtime.reviewer_model or None
    tools: ToolSet = list(mode_cfg.tools) if mode_cfg is not None and mode_cfg.tools else TOOLS
    tool_rounds = mode_cfg.tool_rounds if mode_cfg is not None else DEFAULT_MAX_TOOL_ROUNDS
    fix_rounds = mode_cfg.fix_rounds if mode_cfg is not None else DEFAULT_FIX_ROUNDS

    registry = SourceRegistry(rng=runtime.rng) if runtime.rng is not None else SourceRegistry()
    run = run_answer(
        runtime.provider,
        runtime.client,
        user,
        _build_question(question, context),
        reviewer_provider=runtime.reviewer_provider,
        tools=tools,
        max_tool_rounds=tool_rounds,
        fix_rounds=fix_rounds,
        generator_model=generator_model,
        reviewer_model=reviewer_model,
        max_tokens=runtime.settings.generation.max_tokens,
        temperature=runtime.settings.generation.temperature,
        registry=registry,
        live_intent=decision.live_intent,
    )
    response = build_response(
        run,
        mode=resolved_mode,
        generator_model=generator_model,
        reviewer_model=reviewer_model,
    )
    return response, run


__all__ = ["GraphDownError", "Runtime", "build_runtime", "answer"]
