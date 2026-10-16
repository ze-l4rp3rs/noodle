"""Step 6 unit tests: the bounded tool loop (05 sections 1 and 8, 09 matrix).

No database and no model: a scripted provider queues the model's turns and the
recording graph client answers the tool queries. Covers the done-when (a model
that asks for the time gets it; an unknown tool is reported, not crashed) plus
malformed arguments, tool failure, reuse, the forced final answer, discarded
side text and leaked reasoning.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from noodle import queries as Q
from noodle.graph import GraphError
from noodle.loop import DEFAULT_MAX_TOOL_ROUNDS, run_tool_loop
from noodle.providers import Completion, ScriptedProvider, ToolCall
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

USER = Q.UserCtx("person:test-user", clearance=1)

Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN_ENTITY = "WITH CASE WHEN x.clearance > $clr"
FRESH = "meta:sync"


def tool(name, arguments=None, call_id="c1"):
    return Completion(tool_calls=[ToolCall(call_id, name, arguments or {})])


def _freshness_ok():
    return ("meta:sync", graph_result([{"last_synced_at": "2026-10-15T00:05:00Z", "version": "0.1.0"}]))


def _second_messages(provider):
    return provider.requests[1]["messages"]


def _tool_messages(messages):
    return [m for m in messages if m.role == "tool"]


# --------------------------------------------------------------------------- #
# Done-when
# --------------------------------------------------------------------------- #


def test_scripted_model_asks_for_the_time_and_gets_it():
    provider = ScriptedProvider([
        tool("current_time"),
        Completion(text="It is now noon."),
    ])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "what time is it?")

    assert res.text == "It is now noon."
    assert res.forced is False and res.tool_rounds == 1
    record = res.tool_calls[0]
    assert record.tool == "current_time" and record.status == "ok"
    # the model was shown the result in a tool message
    shown = _tool_messages(_second_messages(provider))
    assert shown and "now" in shown[0].text


def test_unknown_tool_is_reported_not_crashed():
    provider = ScriptedProvider([
        tool("delete_everything"),
        Completion(text="I cannot do that."),
    ])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "wipe it")

    record = res.tool_calls[0]
    assert record.status == "unknown_tool"
    assert "delete_everything" in record.error
    assert res.text == "I cannot do that."
    # the model is told, so it can recover
    shown = _tool_messages(_second_messages(provider))
    assert "delete_everything" in shown[0].text


# --------------------------------------------------------------------------- #
# Bad arguments and tool failure
# --------------------------------------------------------------------------- #


def test_malformed_arguments_are_reported_before_any_graph_call():
    provider = ScriptedProvider([tool("find_owner", {}), Completion(text="ok")])
    # a client with no prepared answers: any query would fail the test
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "who owns it?")
    record = res.tool_calls[0]
    assert record.status == "bad_args" and "ref" in record.error
    assert res.evidence == []


def test_a_failing_tool_is_reported_as_an_error_and_is_never_a_source():
    client = RecordingGraphClient((FRESH, GraphError("database is down")))
    provider = ScriptedProvider([tool("get_freshness"), Completion(text="I could not check.")])
    res = run_tool_loop(provider, client, USER, "is it current?")

    record = res.tool_calls[0]
    assert record.status == "error" and "down" in record.error
    assert res.evidence == []
    shown = _tool_messages(_second_messages(provider))
    assert "error" in shown[0].text.lower()


# --------------------------------------------------------------------------- #
# Reuse, rounds and the forced final answer
# --------------------------------------------------------------------------- #


def test_identical_calls_are_reused_not_rerun():
    client = RecordingGraphClient(_freshness_ok())
    provider = ScriptedProvider([
        tool("get_freshness", call_id="c1"),
        tool("get_freshness", call_id="c2"),
        Completion(text="done"),
    ])
    res = run_tool_loop(provider, client, USER, "as of when?")

    assert len(client.calls) == 1  # the second call never hit the graph
    assert res.tool_calls[1].reused is True and res.tool_calls[1].status == "reused"
    assert res.text == "done"


def test_the_round_limit_forces_a_no_tools_answer():
    provider = ScriptedProvider([
        tool("current_time", call_id="c1"),
        tool("current_time", call_id="c2"),
        Completion(text="final answer"),
    ])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "hi", max_tool_rounds=2)

    assert res.forced is True and res.tool_rounds == 2
    assert res.text == "final answer"
    assert provider.requests[2]["tools"] is None  # the forced call offers no tools
    assert len(res.tool_calls) == 2


def test_tools_in_a_forced_answer_are_ignored():
    provider = ScriptedProvider([
        tool("current_time", call_id="c1"),
        tool("current_time", call_id="c2"),
        tool("current_time", call_id="c3"),
    ])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "hi", max_tool_rounds=2)
    assert res.forced is True
    assert len(res.tool_calls) == 2  # c3 arrived with tools off and was not run


def test_default_round_budget_matches_the_spec():
    assert DEFAULT_MAX_TOOL_ROUNDS == 6


# --------------------------------------------------------------------------- #
# Discarded side text and leaked reasoning
# --------------------------------------------------------------------------- #


def test_text_alongside_a_tool_request_is_discarded():
    provider = ScriptedProvider([
        Completion(text="I will think out loud here", tool_calls=[ToolCall("c1", "current_time", {})]),
        Completion(text="answer"),
    ])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "hi")
    assert res.text == "answer"
    assert "think out loud" not in res.text
    assistant = [m for m in _second_messages(provider) if m.role == "assistant"][0]
    assert assistant.text == ""


def test_leaked_reasoning_never_reaches_the_answer_or_the_conversation():
    provider = ScriptedProvider([Completion(text="answer", reasoning="secret chain of thought")])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "hi")
    assert res.text == "answer"
    blob = json.dumps([{"role": m.role, "text": m.text} for m in res.messages])
    assert "secret" not in blob


# --------------------------------------------------------------------------- #
# Evidence, traces, hidden and the run-log records
# --------------------------------------------------------------------------- #


def test_evidence_traces_and_hidden_are_collected_but_not_in_the_records():
    client = RecordingGraphClient(
        (Q2, graph_result([{"person_id": "person:x", "name": "X", "rel": "OWNS", "entity": "project:y"}])),
        (TWIN_ENTITY, hidden_result(2, ["person:boss"])),
    )
    provider = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), Completion(text="X owns it.")])
    res = run_tool_loop(provider, client, USER, "who owns project y?")

    assert [e.ref for e in res.evidence] == ["role:OWNS:person:x:project:y"]
    assert res.traces and res.hidden.count == 2
    assert res.hidden.access_owners == ("person:boss",)

    record = res.tool_calls[0]
    assert record.status == "ok" and record.rows == 1
    assert record.sources == ["role:OWNS:person:x:project:y"]
    assert record.duration_ms >= 0
    blob = json.dumps(asdict(record))
    assert "boss" not in blob and "hidden" not in asdict(record)


def test_the_conversation_keeps_the_whole_tool_exchange():
    provider = ScriptedProvider([tool("current_time"), Completion(text="done")])
    res = run_tool_loop(provider, RecordingGraphClient(), USER, "hi")
    assert [m.role for m in res.messages] == ["user", "assistant", "tool", "assistant"]


def test_the_model_is_shown_each_source_id_and_can_cite_it():
    client = RecordingGraphClient(
        (Q2, graph_result([{"person_id": "person:x", "name": "X", "rel": "OWNS", "entity": "project:y"}])),
        (TWIN_ENTITY, hidden_result(0, [])),
    )
    provider = ScriptedProvider([tool("find_owner", {"ref": "project:y"}), Completion(text="X owns it.")])
    res = run_tool_loop(provider, client, USER, "who owns it?")

    shown = json.loads(_tool_messages(_second_messages(provider))[0].text)
    source_id = shown["evidence"][0]["source_id"]
    assert len(source_id) == 8
    assert res.registry.has(source_id)
    assert res.registry.get(source_id).ref == "role:OWNS:person:x:project:y"


def test_a_tool_subset_can_be_offered():
    provider = ScriptedProvider([Completion(text="done")])
    run_tool_loop(provider, RecordingGraphClient(), USER, "hi", tools=["current_time"])
    offered = {spec["name"] for spec in provider.requests[0]["tools"]}
    assert offered == {"current_time"}


def test_an_empty_tool_subset_forces_an_answer_immediately():
    provider = ScriptedProvider([Completion(text="done")])
    run_tool_loop(provider, RecordingGraphClient(), USER, "hi", tools=[])
    assert provider.requests[0]["tools"] is None
