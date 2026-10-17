"""Step 12 unit tests: the copilot endpoints and the shared context.

No database, no model: scripted providers and the recording graph client drive
the run. Covers the done-when (selecting a node returns decisions, owners and
stale warnings; a draft cites graph records and flags unbacked claims), the
soft-dead-end notice on the context, and that a drafted action is never sent.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from noodle.api import create_app
from noodle.config import load_settings
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.service import Runtime
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent

# Distinct Cypher substrings, one per statement the context runs.
Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
Q4 = "d.id = $ref OR d.key = $ref"
Q4B = "SUPERSEDED_BY|OVERRIDES|REVERSES*1..6"
Q5 = "old.current AS is_current"
Q8 = "doc.id AS doc_id"
Q11 = "algo.BFS"
TWIN_ENTITY = "WITH CASE WHEN x.clearance > $clr"
TWIN_SOURCES = "src.clearance > $clr"
TWIN_SUPERSEDER = "h.clearance > $clr"
PERSON_LOOKUP = "p.clearance AS clearance, p.team AS team"

USER_ROW = {"id": "person:test-user", "name": "Test User", "role": "Engineer", "clearance": 1, "team": None}
OWNER_CONTACT = {"id": "person:k", "name": "Kim Access", "role": "VP", "clearance": 3, "team": None}

DECISION_ID = "decision:x"
APPROVER_ROW = {"person_id": "person:ap", "name": "Ada", "rel": "APPROVED", "entity": DECISION_ID}
DRAFT_OWNER_ROW = {"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}
DECISION_ROW = {
    "id": DECISION_ID,
    "key": "DEC-1",
    "title": "Adopt scheme X",
    "date": "2026-01-05",
    "status": "superseded",
    "description": "We adopted scheme X.",
    "rationale": "It was cheaper.",
    "impact": "One team.",
    "current": False,
    "approvers": [{"id": "person:ap", "name": "Ada"}],
    "dissent": [],
    "sources": [
        {
            "id": "doc:y",
            "kind": "Document",
            "status": "current",
            "current": True,
            "label": "Doc Y",
            "text": "Doc Y describes scheme X.",
            "date": "2026-01-05",
            "url": None,
        }
    ],
}
CURRENCY_ROW = {
    "id": DECISION_ID,
    "is_current": False,
    "head_id": "decision:new",
    "head_title": "Adopt scheme Z",
    "head_date": "2026-02-01",
}
CHAIN_ROW = {"nodes": [DECISION_ID, "decision:new"], "rels": ["SUPERSEDES"], "length": 1}
BLAST_ROW = {"id": "project:z", "name": "Zeta", "kind": "Project"}
STALE_ROW = {
    "doc_id": "doc:y",
    "title": "Doc Y",
    "content": "Doc Y describes scheme X.",
    "url": None,
    "family": "f",
    "version": 1,
    "created": "2026-01-05",
    "current": True,
    "superseded_decisions": [DECISION_ID],
}


class SeededRng:
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


def person_matcher(person_id: str, row: dict):
    """Answer the step-11 identity lookup for one person id (07)."""

    return lambda cypher, params: PERSON_LOOKUP in cypher and params.get("id") == person_id, graph_result([row])


def _source_id(messages) -> str:
    for message in reversed(messages):
        if message.role == "tool":
            for item in json.loads(message.text).get("evidence") or []:
                return item["source_id"]
    raise AssertionError("the generator was never shown a source")


def citing(claim: str):
    def reply(messages, tools):
        item_id = _source_id(messages)
        return Completion(text=f'{claim} [[{item_id}]] "Omar is the owner of project:y"')

    return reply


def unbacked(claim: str, extra: str):
    """A draft with a cited sentence and an uncited one, so the uncited is flagged."""

    def reply(messages, tools):
        item_id = _source_id(messages)
        text = f'{claim} [[{item_id}]] "Omar is the owner of project:y"\n{extra}'
        return Completion(text=text)

    return reply


def context_runtime(*, hidden_owners=None, extra: tuple = ()):
    """A runtime whose client answers the decision-context queries once each."""

    settings = load_settings(ROOT / "noodle.yaml")
    owner_twin = hidden_result(0, []) if not hidden_owners else hidden_result(1, list(hidden_owners))
    responses = [
        person_matcher("person:test-user", dict(USER_ROW)),
        (Q2, graph_result([dict(APPROVER_ROW)])),
        (Q4, graph_result([dict(DECISION_ROW)])),
        (Q4B, graph_result([dict(CHAIN_ROW)])),
        (Q5, graph_result([dict(CURRENCY_ROW)])),
        (Q8, graph_result([dict(STALE_ROW)])),
        (Q11, graph_result([dict(BLAST_ROW)])),
        (TWIN_ENTITY, owner_twin),
        # q4_decision_chain and q11_blast_radius also run the entity twin
        (TWIN_ENTITY, hidden_result(0, [])),
        (TWIN_ENTITY, hidden_result(0, [])),
        (TWIN_SOURCES, hidden_result(0, [])),
        (TWIN_SUPERSEDER, hidden_result(0, [])),
    ]
    responses.extend(extra)
    return Runtime(
        settings=settings,
        provider=ScriptedProvider([]),
        reviewer_provider=ScriptedProvider([]),
        client=RecordingGraphClient(*responses),
        rng=SeededRng(),
    )


def runtime_for(generator_replies, reviewer_replies, *, up: bool = True, hidden=None):
    settings = load_settings(ROOT / "noodle.yaml")
    client = RecordingGraphClient(
        person_matcher("person:test-user", dict(USER_ROW)),
        (Q2, graph_result([dict(DRAFT_OWNER_ROW)])),
        (TWIN_ENTITY, hidden if hidden is not None else hidden_result(0, [])),
        up=up,
    )
    return Runtime(
        settings=settings,
        provider=ScriptedProvider(generator_replies),
        reviewer_provider=ScriptedProvider(reviewer_replies),
        client=client,
        rng=SeededRng(),
    )


def api_client(runtime: Runtime) -> TestClient:
    return TestClient(create_app(runtime))


def headers() -> dict:
    return {"X-Noodle-User": "person:test-user"}


# --------------------------------------------------------------------------- #
# /copilot/context -- the done-when
# --------------------------------------------------------------------------- #


def test_context_returns_decisions_owners_and_stale_warnings():
    client = api_client(context_runtime())
    response = client.post("/copilot/context", json={"entity_id": DECISION_ID}, headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["entity_id"] == DECISION_ID and body["kind"] == "decision"

    assert [o["person_id"] for o in body["owners"]] == ["person:ap"]
    assert body["owners"][0]["role"] == "approver"

    assert body["decisions"][0]["id"] == DECISION_ID
    assert body["decisions"][0]["currency"] == "superseded"
    assert "Adopt scheme Z" in body["decisions"][0]["note"]

    assert [d["id"] for d in body["documents"]] == ["doc:y"]
    assert [d["id"] for d in body["dependencies"]] == ["project:z"]

    assert [s["doc_id"] for s in body["stale_warnings"]] == ["doc:y"]
    assert body["hidden"] is False
    assert body["access_notices"] == []


def test_context_returns_dependencies_for_a_team_node():
    client = api_client(
        context_runtime(
            extra=(
                ("OPTIONAL MATCH (tm)<-[:MEMBER_OF]-(h:Person)", hidden_result(0, [])),
                (
                    "MATCH (tm:Team {id: $team})<-[:MEMBER_OF]-(p:Person)",
                    graph_result(
                        [
                            {
                                "project_id": "project:z",
                                "project": "Zeta",
                                "total": 4,
                                "team_staff": 3,
                                "downstream": ["project:w"],
                            }
                        ]
                    ),
                ),
            )
        )
    )
    body = client.post("/copilot/context", json={"entity_id": "team:t"}, headers=headers()).json()

    assert body["kind"] == "team"
    assert body["dependencies"][0]["project_id"] == "project:z"
    assert body["dependencies"][0]["downstream"] == ["project:w"]


def test_context_hidden_twin_becomes_a_fixed_notice():
    client = api_client(
        context_runtime(
            hidden_owners=["person:k"],
            extra=(person_matcher("person:k", dict(OWNER_CONTACT)),),
        )
    )
    body = client.post("/copilot/context", json={"entity_id": DECISION_ID}, headers=headers()).json()

    assert body["hidden"] is True
    assert set(body["access_notices"][0]) == {"text", "contact"}
    assert body["access_notices"][0]["contact"] == "person:k"
    assert body["access_notices"][0]["text"] == (
        "There is relevant material above your access level. To request access, contact Kim Access."
    )


def test_context_rejects_a_missing_entity_id():
    client = api_client(context_runtime())
    assert client.post("/copilot/context", json={}, headers=headers()).status_code == 400
    assert client.post("/copilot/context", json={"entity_id": "  "}, headers=headers()).status_code == 400


def test_context_returns_503_when_the_graph_is_down():
    runtime = context_runtime()
    runtime.client.up = False
    response = api_client(runtime).post(
        "/copilot/context", json={"entity_id": DECISION_ID}, headers=headers()
    )
    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# /copilot/draft -- heavy-mode draft, nothing sent
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


def test_draft_returns_the_ask_shape_in_heavy_mode():
    runtime = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), citing("The owner is Omar.")], [approval(1)]
    )
    response = api_client(runtime).post(
        "/copilot/draft",
        json={"entity_id": "project:y", "instruction": "draft an update"},
        headers=headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert body["metadata"]["mode"] == "heavy"
    assert body["status"] == "verified"
    assert body["references"][0]["nodeId"] == "role:OWNS:person:x:project:y"


def test_draft_flags_an_unbacked_claim():
    # The uncited sentence survives every fix round, so the system flags it.
    runtime = runtime_for(
        [
            tool("find_owner", {"ref": "project:y"}),
            unbacked("The owner is Omar.", "The project is on track."),
            unbacked("The owner is Omar.", "The project is on track."),
            unbacked("The owner is Omar.", "The project is on track."),
        ],
        [approval(1), approval(1), approval(1)],
    )
    body = api_client(runtime).post(
        "/copilot/draft",
        json={"entity_id": "project:y", "instruction": "draft an update"},
        headers=headers(),
    ).json()

    assert body["status"] != "verified"
    flagged = " ".join(item["sentence"] for item in body["unverified"])
    assert "on track" in flagged


def test_draft_is_only_ever_a_proposed_action():
    def drafting(messages, tools):
        source_id = _source_id(messages)
        return Completion(
            tool_calls=[
                ToolCall(
                    "c2",
                    "draft_followup",
                    {"to": "person:x", "subject": "Update", "body": f"Owner: Omar [[{source_id}]]"},
                )
            ]
        )

    runtime = runtime_for(
        [tool("find_owner", {"ref": "project:y"}), drafting, citing("The owner is Omar.")],
        [approval(1)],
    )
    body = api_client(runtime).post(
        "/copilot/draft",
        json={"entity_id": "project:y", "instruction": "draft an update"},
        headers=headers(),
    ).json()

    assert body["proposed_actions"], "the drafted action is surfaced"
    action = body["proposed_actions"][0]
    assert action["kind"] == "message" and action["requires_confirmation"] is True
    assert action["recipient"] == "person:x"


def test_draft_rejects_a_missing_instruction():
    runtime = runtime_for([], [])
    client = api_client(runtime)
    assert client.post("/copilot/draft", json={"entity_id": "project:y"}, headers=headers()).status_code == 400
    assert (
        client.post(
            "/copilot/draft", json={"entity_id": "project:y", "instruction": " "}, headers=headers()
        ).status_code
        == 400
    )
