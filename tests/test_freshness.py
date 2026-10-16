"""Step 10 unit tests: the freshness guard (05 section 2.5, 06 section Freshness).

No database, no model: the freshness value comes from the recording graph client
and the model turns are scripted. Covers the done-when -- a "what happened today"
question returns the fixed as-of line and drops the same-day claim -- plus that
the as-of time is read from the graph, that a non-live question gets neither, and
that heavy-only tools are not offered in fast mode.
"""

from __future__ import annotations

import json
from pathlib import Path

from noodle import queries as Q
from noodle.config import load_settings
from noodle.pipeline import run_answer
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.response import AS_OF_TEXT, build_response
from noodle.service import Runtime, answer
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent
USER = Q.UserCtx("person:test-user", clearance=1)
SYNC = "2026-10-15T00:05:00Z"
Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"
OWNER_ROW = {"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}
OWNER_TEXT = "Omar is the owner of project:y"


class SeededRng:
    """A deterministic citation-id source, so two runs compare equal (09)."""

    def __init__(self) -> None:
        self._n = 0

    def token_hex(self, n: int) -> str:
        self._n += 1
        return f"{self._n:0{2 * n}x}"


def tool(name, arguments=None, call_id="c1"):
    return Completion(tool_calls=[ToolCall(call_id, name, arguments or {})])


def approval(*numbers: int) -> Completion:
    return Completion(
        text=json.dumps(
            {
                "sentences": [{"n": n, "supported": True, "reason": "stated"} for n in numbers],
                "unanswered": [],
                "false_premises": [],
            }
        )
    )


def _source_id(messages) -> str:
    for message in reversed(messages):
        if message.role == "tool":
            for item in json.loads(message.text).get("evidence") or []:
                return item["source_id"]
    raise AssertionError("the generator was never shown a source")


def citing(claim: str):
    def reply(messages, tools):
        source_id = _source_id(messages)
        return Completion(text=f'{claim} [[{source_id}]] "{OWNER_TEXT}"')

    return reply


def same_day_claim():
    """A draft with one sound sentence and one same-day claim on an undated source."""

    def reply(messages, tools):
        source_id = _source_id(messages)
        text = (
            f'The owner is Omar. [[{source_id}]] "{OWNER_TEXT}"\n'
            f'The service restarted today. [[{source_id}]] "{OWNER_TEXT}"'
        )
        return Completion(text=text)

    return reply


def owner_client() -> RecordingGraphClient:
    return RecordingGraphClient(
        ("meta:sync", graph_result([{"last_synced_at": SYNC, "version": "0.1.0"}])),
        (Q2, graph_result([dict(OWNER_ROW)])),
        (TWIN, hidden_result(0, [])),
    )


def runtime_for(generator_replies, reviewer_replies, *, up: bool = True) -> Runtime:
    settings = load_settings(ROOT / "noodle.yaml")
    return Runtime(
        settings=settings,
        provider=ScriptedProvider(generator_replies),
        reviewer_provider=ScriptedProvider(reviewer_replies),
        client=owner_client(),
        rng=SeededRng(),
    )


def offered_tools(runtime: Runtime) -> set[str]:
    specs = runtime.provider.requests[0]["tools"] or []
    return {spec["name"] for spec in specs}


# --------------------------------------------------------------------------- #
# Done-when: the as-of line, and no same-day claim
# --------------------------------------------------------------------------- #


def test_live_intent_appends_the_as_of_line_and_removes_a_same_day_claim():
    client = owner_client()
    run = run_answer(
        ScriptedProvider([tool("find_owner", {"ref": "project:y"}), same_day_claim()]),
        client,
        USER,
        "what happened today?",
        reviewer_provider=ScriptedProvider([approval(1)]),
        live_intent=True,
        fix_rounds=0,
    )
    response = build_response(run, mode="fast")

    assert response["freshness"] == {"as_of": SYNC, "live_intent": True}
    assert AS_OF_TEXT.format(as_of=SYNC) in response["answer"]
    assert "today" not in response["answer"].casefold()
    assert "The owner is Omar." in response["answer"]  # the sound sentence survives
    assert OWNER_TEXT in response["references"][0]["quotes"]
    assert any("today" in item["sentence"] for item in response["unverified"])


def test_the_as_of_time_is_read_from_the_graph():
    client = owner_client()
    run = run_answer(
        ScriptedProvider([tool("find_owner", {"ref": "project:y"}), same_day_claim()]),
        client,
        USER,
        "what happened today?",
        reviewer_provider=ScriptedProvider([approval(1)]),
        live_intent=True,
        fix_rounds=0,
    )

    # the freshness statement really ran, and it is the source of the as-of value
    assert run.as_of == SYNC
    assert any("meta:sync" in cypher for cypher, _ in client.calls)


def test_live_intent_records_the_freshness_query_in_the_trace():
    runtime = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response, _ = answer(runtime, USER, "what happened today?")

    freshness = [entry for entry in response["graph_trace"] if "meta:sync" in entry["cypher"]]
    assert freshness and freshness[0]["tool"] == "get_freshness"


def test_the_service_detects_live_intent_and_sets_the_flag():
    runtime = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response, _ = answer(runtime, USER, "what happened today?")  # no explicit mode

    assert response["metadata"]["mode"] == "fast"
    assert response["freshness"] == {"as_of": SYNC, "live_intent": True}
    assert AS_OF_TEXT.format(as_of=SYNC) in response["answer"]


# --------------------------------------------------------------------------- #
# A non-live question gets neither
# --------------------------------------------------------------------------- #


def test_a_normal_question_has_no_as_of_line_and_no_live_intent():
    runtime = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response, _ = answer(runtime, USER, "who owns project y?")

    assert response["freshness"] == {"as_of": "", "live_intent": False}
    assert "My data is current as of" not in response["answer"]
    assert not any("meta:sync" in cypher for cypher, _ in runtime.client.calls)


# --------------------------------------------------------------------------- #
# Heavy-only tools (05 section 0)
# --------------------------------------------------------------------------- #


def test_heavy_only_tools_are_not_offered_in_fast_mode():
    fast = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    answer(fast, USER, "who owns project y?", mode="fast")
    assert "draft_followup" not in offered_tools(fast)

    heavy = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    answer(heavy, USER, "draft a note", mode="heavy")
    assert "draft_followup" in offered_tools(heavy)
