"""Step 11 unit tests: identity from the request and the dev user switcher (07).

No database, no model: the recording graph client answers the identity lookup and
the dev directory. Covers the auth rules that keep a caller from choosing their
own clearance (06, A6): a bearer token maps to a Person, the dev header works only
outside production, an unknown user is clearance 0, and ``GET /users`` is off in
production.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from noodle import auth
from noodle import queries as Q
from noodle.api import create_app
from noodle.config import load_settings
from noodle.graph import GraphError
from noodle.providers import Completion, ScriptedProvider, ToolCall
from noodle.service import Runtime
from tests.fakes import RecordingGraphClient, graph_result, hidden_result

ROOT = Path(__file__).resolve().parent.parent

PERSON_LOOKUP = "p.clearance AS clearance, p.team AS team"
PEOPLE_LOOKUP = "p.clearance AS clearance ORDER BY p.clearance, p.name"

VP_ROW = {"id": "person:vp", "name": "Vic President", "role": "VP", "clearance": 3, "team": None}
INTERN_ROW = {"id": "person:intern", "name": "Ivy Intern", "role": "Intern", "clearance": 1, "team": None}


class FakeRequest:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


def settings_for(env: str):
    settings = load_settings(ROOT / "noodle.yaml")
    return replace(settings, env=env)


def runtime_for(client, *, env: str = "dev") -> Runtime:
    return Runtime(
        settings=settings_for(env),
        provider=ScriptedProvider([]),
        reviewer_provider=ScriptedProvider([]),
        client=client,
    )


# --------------------------------------------------------------------------- #
# claim_from_headers: who the caller claims to be
# --------------------------------------------------------------------------- #


def test_dev_header_names_the_caller_only_outside_production():
    headers = {auth.DEV_HEADER: "person:intern"}
    assert auth.claim_from_headers(headers, is_prod=False) == "person:intern"
    assert auth.claim_from_headers(headers, is_prod=True) == ""  # E7: off in prod


def test_a_bearer_token_is_mapped_by_the_environment(monkeypatch):
    monkeypatch.setenv(auth.AUTH_TOKENS_ENV, json.dumps({"tok-1": "person:vp"}))
    assert auth.claim_from_headers({"authorization": "Bearer tok-1"}, is_prod=True) == "person:vp"


def test_an_unmapped_bearer_token_is_the_person_id_in_dev_only(monkeypatch):
    monkeypatch.setenv(auth.AUTH_TOKENS_ENV, "{}")
    assert auth.claim_from_headers({"authorization": "Bearer person:intern"}, is_prod=False) == "person:intern"
    assert auth.claim_from_headers({"authorization": "Bearer person:intern"}, is_prod=True) == ""


def test_no_credentials_is_anonymous():
    assert auth.claim_from_headers({}, is_prod=False) == ""
    assert auth.claim_from_headers({}, is_prod=True) == ""


def test_the_dev_header_wins_over_a_bearer_token_in_dev(monkeypatch):
    monkeypatch.setenv(auth.AUTH_TOKENS_ENV, json.dumps({"tok-1": "person:vp"}))
    headers = {auth.DEV_HEADER: "person:intern", "authorization": "Bearer tok-1"}
    assert auth.claim_from_headers(headers, is_prod=False) == "person:intern"


# --------------------------------------------------------------------------- #
# resolve_user: clearance comes from the graph, never the request
# --------------------------------------------------------------------------- #


def test_resolve_user_reads_clearance_from_the_graph():
    client = RecordingGraphClient((PERSON_LOOKUP, graph_result([dict(VP_ROW)])))
    user = auth.resolve_user(client, "person:vp")
    assert user.id == "person:vp" and user.clearance == 3


def test_an_unknown_person_is_clearance_zero_not_an_error():
    client = RecordingGraphClient((PERSON_LOOKUP, graph_result([])))
    user = auth.resolve_user(client, "person:ghost")
    assert user.id == "person:ghost" and user.clearance == 0


def test_a_failing_identity_lookup_degrades_to_clearance_zero():
    client = RecordingGraphClient((PERSON_LOOKUP, GraphError("boom")))
    user = auth.resolve_user(client, "person:vp")
    assert user.clearance == 0


def test_an_empty_claim_is_anonymous_without_a_lookup():
    client = RecordingGraphClient()
    assert auth.resolve_user(client, "") == Q.UserCtx.anonymous()
    assert client.calls == []


def test_a_graph_outage_never_breaks_resolution_and_skips_the_lookup():
    client = RecordingGraphClient(up=False)
    user = auth.resolve_user(client, "person:vp", graph_up=False)
    assert user.clearance == 0
    assert client.calls == []


# --------------------------------------------------------------------------- #
# user_for_request: the identity header is off in production (E7)
# --------------------------------------------------------------------------- #


def test_user_for_request_honours_the_dev_header_in_dev():
    client = RecordingGraphClient((PERSON_LOOKUP, graph_result([dict(INTERN_ROW)])))
    request = FakeRequest({auth.DEV_HEADER: "person:intern"})
    user = auth.user_for_request(request, runtime_for(client, env="dev"))
    assert user.id == "person:intern" and user.clearance == 1


def test_user_for_request_ignores_the_dev_header_in_production():
    client = RecordingGraphClient()
    request = FakeRequest({auth.DEV_HEADER: "person:vp"})
    user = auth.user_for_request(request, runtime_for(client, env="prod"))
    assert user == Q.UserCtx.anonymous()  # the dev header grants nothing in prod
    assert client.calls == []


def test_user_for_request_maps_a_bearer_token_in_production(monkeypatch):
    monkeypatch.setenv(auth.AUTH_TOKENS_ENV, json.dumps({"tok-1": "person:vp"}))
    client = RecordingGraphClient((PERSON_LOOKUP, graph_result([dict(VP_ROW)])))
    request = FakeRequest({"authorization": "Bearer tok-1"})
    user = auth.user_for_request(request, runtime_for(client, env="prod"))
    assert user.id == "person:vp" and user.clearance == 3


# --------------------------------------------------------------------------- #
# GET /users: the dev switcher is off in production
# --------------------------------------------------------------------------- #


def directory_client() -> RecordingGraphClient:
    return RecordingGraphClient((PEOPLE_LOOKUP, graph_result([dict(INTERN_ROW), dict(VP_ROW)])))


def test_users_endpoint_lists_the_directory_in_dev():
    app = create_app(runtime_for(directory_client(), env="dev"))
    response = TestClient(app).get("/users")

    assert response.status_code == 200
    users = response.json()["users"]
    assert {"id": "person:intern", "name": "Ivy Intern", "role": "Intern", "clearance": 1} in users
    assert {"id": "person:vp", "name": "Vic President", "role": "VP", "clearance": 3} in users


def test_users_endpoint_is_a_404_in_production():
    client = RecordingGraphClient()
    app = create_app(runtime_for(client, env="prod"))
    response = TestClient(app).get("/users")

    assert response.status_code == 404
    assert client.calls == []


# --------------------------------------------------------------------------- #
# The dev header grants nothing in production, even end to end (E7)
# --------------------------------------------------------------------------- #

Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"
OWNER_ROW = {"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}


class SeededRng:
    def __init__(self) -> None:
        self._n = 0

    def token_hex(self, n: int) -> str:
        self._n += 1
        return f"{self._n:0{2 * n}x}"


def _citing_reply(messages, tools):
    for message in reversed(messages):
        if message.role == "tool":
            item_id = json.loads(message.text)["evidence"][0]["source_id"]
            return Completion(text=f'The owner is Omar. [[{item_id}]] "Omar is the owner of project:y"')
    raise AssertionError("no source shown")


def ask_runtime(client, *, env: str) -> Runtime:
    return Runtime(
        settings=settings_for(env),
        provider=ScriptedProvider(
            [Completion(tool_calls=[ToolCall("c1", "find_owner", {"ref": "project:y"})]), _citing_reply]
        ),
        reviewer_provider=ScriptedProvider(
            [Completion(text=json.dumps({"sentences": [{"n": 1, "supported": True, "reason": "stated"}], "unanswered": [], "false_premises": []}))]
        ),
        client=client,
        rng=SeededRng(),
    )


def _clearances(client: RecordingGraphClient) -> set[int]:
    return {params["clr"] for _, params in client.calls if "clr" in params}


def test_the_dev_header_grants_clearance_in_dev_end_to_end():
    client = RecordingGraphClient(
        (PERSON_LOOKUP, graph_result([dict(INTERN_ROW)])),
        (Q2, graph_result([dict(OWNER_ROW)])),
        (TWIN, hidden_result(0, [])),
    )
    app = create_app(ask_runtime(client, env="dev"))
    response = TestClient(app).post(
        "/ask",
        json={"question": "who owns project y?", "mode": "fast"},
        headers={auth.DEV_HEADER: "person:intern"},
    )

    assert response.status_code == 200
    assert _clearances(client) == {1}  # the graph was queried as the clearance-1 person


def test_the_dev_header_grants_nothing_in_production_end_to_end():
    client = RecordingGraphClient(
        (Q2, graph_result([dict(OWNER_ROW)])),
        (TWIN, hidden_result(0, [])),
    )
    app = create_app(ask_runtime(client, env="prod"))
    response = TestClient(app).post(
        "/ask",
        json={"question": "who owns project y?", "mode": "fast"},
        headers={auth.DEV_HEADER: "person:vp"},
    )

    assert response.status_code == 200
    assert _clearances(client) == {0}  # the header was ignored: anonymous, clearance 0
