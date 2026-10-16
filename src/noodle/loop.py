"""The generator's bounded tool loop (05 sections 1 and 8).

The provider makes one service call; the loop runs the tools. A *tool round* is
one model call that requests tools: the results go back to the model as a tool
message, its own identity supplied by the system and never by the model. What
the loop guarantees:

* text the model writes alongside a tool request is discarded (05 section 1);
* an unknown tool, malformed arguments and a failing tool are all *reported* to
  the model as an error, never raised out and never turned into a source (04);
* an identical call (same tool, same arguments) is answered from the first
  result and marked ``reused``, so the graph is touched once;
* after ``max_tool_rounds`` the next call is made with no tools at all -- the
  forced final answer (05 section 8);
* reasoning the provider returns separately never enters the answer or the
  conversation (05 section 1).

The evidence a tool returns is registered in the run's :class:`SourceRegistry`,
and each evidence item the model is shown carries that record's run-random
citation id, so the generator can cite ``[[id]]`` (05 section 1, 07).

Hidden information is merged for the system's soft dead-end but never appears in
the run-log records (06, A4).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Union

from .graph import GraphClient
from .providers import Completion, Message, Provider, ToolCall
from .queries import HiddenInfo, QueryResult, UserCtx, merge_hidden
from .sources import SourceRegistry
from .tools import TOOLS, Evidence, Tool, ToolResult, tool_specs

# 05 section 8: tool rounds per model call, then a forced no-tools answer.
DEFAULT_MAX_TOOL_ROUNDS = 6

ToolSet = Union[Mapping[str, Tool], Sequence[str], None]


@dataclass
class ToolCallRecord:
    """One entry in the run log (05 section 7): what ran, what it cost and
    whether it wrote a source. Never carries hidden content.

    ``queries`` carries one redacted summary per Cypher statement the tool ran
    (the exact statement, its redacted params and the node/edge ids it touched),
    so the API's ``graph_trace`` can show the exact Cypher executed (07, A6).
    """

    tool: str
    args: dict[str, Any]
    status: str  # ok | reused | error | bad_args | unknown_tool
    error: Optional[str] = None
    rows: int = 0
    sources: list[str] = field(default_factory=list)
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    reused: bool = False
    queries: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LoopResult:
    """The generator's outcome: the draft, the evidence behind it, and the log."""

    text: str
    completions: list[Completion]
    tool_calls: list[ToolCallRecord]
    evidence: list[Evidence]
    hidden: HiddenInfo
    traces: list[QueryResult]
    tool_rounds: int
    forced: bool
    messages: list[Message]
    registry: SourceRegistry = field(default_factory=SourceRegistry)


def _as_messages(messages: Union[str, Sequence[Message]]) -> list[Message]:
    if isinstance(messages, str):
        return [Message(role="user", text=messages)]
    return list(messages)


def _resolve_tools(tools: ToolSet) -> dict[str, Tool]:
    if tools is None:
        return dict(TOOLS)
    if isinstance(tools, Mapping):
        return dict(tools)
    return {TOOLS[name].name: TOOLS[name] for name in tools}


def _cache_key(call: ToolCall) -> tuple[str, str]:
    return (call.name, json.dumps(dict(call.arguments), sort_keys=True, default=str))


def _redacted_query(trace: QueryResult) -> dict[str, Any]:
    """One graph_trace entry (07): the exact Cypher, its redacted params and the
    ids/row count it produced. Params are already redacted in the queries layer
    (A4); hidden counts never appear here (A4)."""

    return {
        "cypher": trace.cypher,
        "params": dict(trace.params),
        "node_ids": list(trace.node_ids),
        "edge_ids": list(trace.edge_ids),
        "rows": len(trace.rows),
    }


def _call_tool(
    tool: Optional[Tool], call: ToolCall, client: GraphClient, user: UserCtx
) -> tuple[ToolCallRecord, Optional[ToolResult]]:
    if tool is None:
        return (
            ToolCallRecord(call.name, dict(call.arguments), "unknown_tool", error=f"unknown tool: {call.name}"),
            None,
        )
    started = time.perf_counter()
    try:
        result = tool.run(client, user, call.arguments)
    except ValueError as exc:  # closed-world argument validation, or a tool's own check
        return (
            ToolCallRecord(
                call.name, dict(call.arguments), "bad_args", error=str(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001 - a failing tool is reported, not raised
        return (
            ToolCallRecord(
                call.name, dict(call.arguments), "error", error=str(exc),
                duration_ms=(time.perf_counter() - started) * 1000,
            ),
            None,
        )
    record = ToolCallRecord(
        call.name,
        dict(call.arguments),
        "ok",
        rows=len(result.evidence),
        sources=[e.ref for e in result.evidence],
        node_ids=[nid for trace in result.trace for nid in trace.node_ids],
        edge_ids=[eid for trace in result.trace for eid in trace.edge_ids],
        duration_ms=(time.perf_counter() - started) * 1000,
        queries=[_redacted_query(trace) for trace in result.trace],
    )
    return record, result


def _shown_payload(result: ToolResult, registry: SourceRegistry) -> str:
    """The tool result the model is shown, with each evidence item's run id.

    Registering the evidence here means a citable source exists for every record
    a tool returned, and the model sees the same ``[[id]]`` marker it must cite.
    """

    payload = result.to_payload()
    for item, evidence in zip(payload.get("evidence") or [], result.evidence):
        item["source_id"] = registry.add(evidence).id
    return json.dumps(payload, default=str, ensure_ascii=False)


def run_tool_loop(
    provider: Provider,
    client: GraphClient,
    user: UserCtx,
    messages: Union[str, Sequence[Message]],
    *,
    tools: ToolSet = TOOLS,
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 2000,
    temperature: float = 0.0,
    registry: Optional[SourceRegistry] = None,
) -> LoopResult:
    """Run the generator with tools until it answers or the round limit forces one."""

    registry = registry if registry is not None else SourceRegistry()
    available = _resolve_tools(tools)
    specs = tool_specs([tool.name for tool in available.values()]) or None
    conversation = _as_messages(messages)

    completions: list[Completion] = []
    records: list[ToolCallRecord] = []
    evidence: list[Evidence] = []
    traces: list[QueryResult] = []
    hidden = HiddenInfo()
    cache: dict[tuple[str, str], ToolResult] = {}
    rounds = 0
    forced = False

    while True:
        hit_limit = rounds >= max_tool_rounds
        offered = None if hit_limit else specs
        completion = provider.complete(
            conversation,
            tools=offered,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        completions.append(completion)

        if offered is None:
            # tools are off (round limit reached or none configured): no tool runs
            forced = hit_limit
            conversation.append(Message(role="assistant", text=completion.text))
            break
        if not completion.tool_calls:
            conversation.append(Message(role="assistant", text=completion.text))
            break

        rounds += 1
        # the model's side text alongside a tool request is discarded
        conversation.append(Message(role="assistant", tool_calls=list(completion.tool_calls)))

        for call in completion.tool_calls:
            key = _cache_key(call)
            if key in cache:
                cached = cache[key]
                cached.reused = True
                record = ToolCallRecord(
                    call.name, dict(call.arguments), "reused", reused=True,
                    rows=len(cached.evidence), sources=[e.ref for e in cached.evidence],
                    queries=[_redacted_query(trace) for trace in cached.trace],
                )
                records.append(record)
                conversation.append(
                    Message(role="tool", tool_call_id=call.id, name=call.name,
                            text=_shown_payload(cached, registry))
                )
                continue

            record, result = _call_tool(available.get(call.name), call, client, user)
            records.append(record)
            if result is not None:
                cache[key] = result
                evidence.extend(result.evidence)
                traces.extend(result.trace)
                hidden = merge_hidden(hidden, result.hidden)
                conversation.append(
                    Message(role="tool", tool_call_id=call.id, name=call.name,
                            text=_shown_payload(result, registry))
                )
            else:
                conversation.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        name=call.name,
                        text=json.dumps({"tool": call.name, "error": record.error}, ensure_ascii=False),
                    )
                )

    return LoopResult(
        text=completions[-1].text,
        completions=completions,
        tool_calls=records,
        evidence=evidence,
        hidden=hidden,
        traces=traces,
        tool_rounds=rounds,
        forced=forced,
        messages=conversation,
        registry=registry,
    )
