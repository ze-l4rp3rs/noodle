"""Step 9 unit tests: the API and the shared response.

No database, no model: scripted providers and the recording graph client drive
the run. Covers the done-when (the CLI and ``/ask`` return the same response for
the same run; a graph outage is ``503``) plus the 07 response shape, the exact
Cypher in ``graph_trace``, ``missing`` mirroring the gaps, the fixed access
notice, and the SSE event stream.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient

from noodle import queries as Q
from noodle.api import create_app
from noodle.cli import cli_answer
from noodle.config import load_settings
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.service import Runtime
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent

USER = Q.UserCtx("person:test-user", clearance=1)
QUESTION = "who owns project y?"
Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"

OWNER_ROW = {"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}


class SeededRng:
    """A deterministic source-id source, so citation ids are reproducible (09)."""

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
            payload = json.loads(message.text)
            for item in payload.get("evidence") or []:
                return item["source_id"]
    raise AssertionError("the generator was never shown a source")


def citing(claim: str):
    def reply(messages, tools):
        item_id = _source_id(messages)
        return Completion(text=f'{claim} [[{item_id}]] "Omar is the owner of project:y"')

    return reply


def gap_citing(claim: str):
    def reply(messages, tools):
        item_id = _source_id(messages)
        text = (
            f'{claim} [[{item_id}]] "Omar is the owner of project:y"\n'
            "Not in company records\n"
            "- the completion percentage"
        )
        return Completion(text=text)

    return reply


def runtime_for(generator_replies, reviewer_replies, *, up: bool = True, hidden=None):
    """A deterministic runtime: scripted providers, the owner client, a seeded rng."""

    settings = load_settings(ROOT / "noodle.yaml")
    generator = ScriptedProvider(generator_replies)
    reviewer = ScriptedProvider(reviewer_replies)
    client = RecordingGraphClient(
        (Q2, graph_result([dict(OWNER_ROW)])),
        (TWIN, hidden if hidden is not None else hidden_result(0, [])),
        up=up,
    )
    runtime = Runtime(
        settings=settings,
        provider=generator,
        reviewer_provider=reviewer,
        client=client,
        rng=SeededRng(),
    )
    return runtime, generator, reviewer, client


def api_client(runtime: Runtime) -> TestClient:
    return TestClient(create_app(runtime))


def ask_headers(accept: str | None = None) -> dict:
    headers = {"X-Noodle-User": "person:test-user"}
    if accept:
        headers["Accept"] = accept
    return headers


def without_timing(response: dict) -> dict:
    """Drop the measured fields that legitimately differ between two runs."""

    trimmed = copy.deepcopy(response)
    trimmed["metadata"].pop("processingTimeMs", None)
    for entry in trimmed["graph_trace"]:
        entry.pop("duration_ms", None)
    return trimmed


# --------------------------------------------------------------------------- #
# Done-when
# --------------------------------------------------------------------------- #


def test_cli_and_api_return_the_same_response_for_the_same_run():
    cli_runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    api_runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )

    cli_response = cli_answer(cli_runtime, USER, QUESTION, mode="fast")

    response = api_client(api_runtime).post(
        "/ask", json={"question": QUESTION, "mode": "fast"}, headers=ask_headers()
    )
    assert response.status_code == 200

    assert cli_response["status"] == "verified"
    assert without_timing(cli_response) == without_timing(response.json())


def test_ask_returns_503_when_the_graph_is_down():
    runtime, generator, _, _ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")],
        [approval(1)],
        up=False,
    )
    response = api_client(runtime).post("/ask", json={"question": QUESTION}, headers=ask_headers())

    assert response.status_code == 503
    assert response.json()["graph"] == "down"
    assert generator.requests == []  # never a model-only answer


def test_health_reports_up_and_down():
    up_runtime, *_ = runtime_for([], [], up=True)
    assert api_client(up_runtime).get("/health").json() == {"status": "ok", "graph": "up"}

    down_runtime, *_ = runtime_for([], [], up=False)
    down = api_client(down_runtime).get("/health")
    assert down.status_code == 503 and down.json()["graph"] == "down"


def test_ask_rejects_a_missing_question():
    runtime, *_ = runtime_for([], [])
    client = api_client(runtime)
    assert client.post("/ask", json={}, headers=ask_headers()).status_code == 400
    assert client.post("/ask", json={"question": "   "}, headers=ask_headers()).status_code == 400
    assert client.post("/ask", content=b"not json", headers=ask_headers()).status_code == 400


# --------------------------------------------------------------------------- #
# The 07 response shape
# --------------------------------------------------------------------------- #

RESPONSE_KEYS = {
    "answer",
    "references",
    "status",
    "unverified",
    "missing",
    "premise",
    "proposed_actions",
    "access_notices",
    "freshness",
    "graph_trace",
    "metadata",
}
REFERENCE_KEYS = {
    "id",
    "number",
    "type",
    "title",
    "excerpt",
    "quotes",
    "date",
    "url",
    "nodeId",
    "freshness",
    "freshness_note",
    "meta",
}
METADATA_KEYS = {"rounds", "approved", "processingTimeMs", "mode", "generator_model", "reviewer_model"}


def test_response_matches_the_documented_shape():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response = cli_answer(runtime, USER, QUESTION, mode="fast")

    assert set(response) == RESPONSE_KEYS
    assert response["status"] in {"verified", "partly_verified", "not_checked"}
    assert set(response["metadata"]) == METADATA_KEYS
    assert set(response["freshness"]) == {"as_of", "live_intent"}
    assert response["metadata"]["mode"] == "fast"
    assert response["metadata"]["generator_model"]  # the fast mode's model name

    reference = response["references"][0]
    assert set(reference) == REFERENCE_KEYS
    assert reference["nodeId"] == "role:OWNS:person:x:project:y"
    assert reference["freshness"] in {"current", "superseded", "unknown"}
    assert reference["number"] == 1


def test_graph_trace_carries_the_exact_cypher():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response = cli_answer(runtime, USER, QUESTION, mode="fast")

    assert response["graph_trace"], "the run called a tool"
    entry = next(item for item in response["graph_trace"] if Q2 in item["cypher"])
    assert entry["tool"] == "find_owner"
    assert entry["rows"] == 1
    assert entry["params"]["id"] == "project:y"
    assert "clr" not in entry["params"]  # clearance is redacted (A4)
    assert "person:x" in entry["node_ids"]


def test_missing_mirrors_the_answers_gaps():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), gap_citing("The owner is Omar.")], [approval(1)]
    )
    response = cli_answer(runtime, USER, "what is the status?", mode="fast")

    assert "the completion percentage" in response["missing"]
    assert response["status"] == "partly_verified"


def test_access_notice_is_system_text_with_a_contact():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")],
        [approval(1)],
        hidden=hidden_result(1, ["person:k"]),
    )
    response = cli_answer(runtime, USER, QUESTION, mode="fast")

    notices = response["access_notices"]
    assert len(notices) == 1
    assert notices[0]["contact"] == "person:k"
    assert notices[0]["text"] == (
        "There is relevant material above your access level. To request access, contact person:k."
    )


def test_no_access_notice_when_nothing_is_hidden():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response = cli_answer(runtime, USER, QUESTION, mode="fast")
    assert response["access_notices"] == []


# --------------------------------------------------------------------------- #
# Streaming
# --------------------------------------------------------------------------- #


def parse_sse(text: str) -> list[tuple[str, object]]:
    events: list[tuple[str, object]] = []
    for block in text.strip().split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name is not None:
            events.append((name, data))
    return events


def test_ask_streams_the_structured_events():
    runtime, *_ = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response = api_client(runtime).post(
        "/ask",
        json={"question": QUESTION, "mode": "fast"},
        headers=ask_headers(accept="text/event-stream"),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "router"
    assert "tool_call" in names and "tool_result" in names
    assert names[-1] == "answer"

    final = dict(events)["answer"]
    assert final["status"] == "verified" and final["answer"]
    assert dict(events)["router"] == {"mode": "fast"}
