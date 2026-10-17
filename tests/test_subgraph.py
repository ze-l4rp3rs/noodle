"""Step 12 unit tests: ``GET /graph/subgraph`` for the visualisation (07, C6).

The query layer is what filters hidden nodes; these tests pin the endpoint's
shape, its input handling and the graph-down path, and that only the nodes and
edges the query returned (already visible) are serialised.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from noodle.api import create_app
from noodle.config import load_settings
from noodle.providers import ScriptedProvider
from noodle.service import Runtime
from tests.fakes import RecordingGraphClient, graph_result

ROOT = Path(__file__).resolve().parent.parent

SUBGRAPH = "OPTIONAL MATCH p = (s)-[*1.."
PERSON_LOOKUP = "p.clearance AS clearance, p.team AS team"

USER_ROW = {"id": "person:test-user", "name": "Test User", "role": "Engineer", "clearance": 1, "team": None}

NODE_Y = {"id": "project:y", "label": "Project", "name": "Y"}
NODE_X = {"id": "person:x", "label": "Person", "name": "Omar"}
ROW = {
    "seed": NODE_Y,
    "nodes": [NODE_Y, NODE_X],
    "node_ids": ["project:y", "person:x"],
    "rels": ["OWNS"],
}


def runtime(rows, *, up: bool = True):
    settings = load_settings(ROOT / "noodle.yaml")
    client = RecordingGraphClient(
        (PERSON_LOOKUP, graph_result([dict(USER_ROW)])),
        (SUBGRAPH, graph_result(rows)),
        up=up,
    )
    return Runtime(
        settings=settings,
        provider=ScriptedProvider([]),
        reviewer_provider=ScriptedProvider([]),
        client=client,
    )


def client_for(rows, *, up: bool = True) -> TestClient:
    return TestClient(create_app(runtime(rows, up=up)))


def headers() -> dict:
    return {"X-Noodle-User": "person:test-user"}


def test_subgraph_returns_nodes_and_edges_for_the_visualisation():
    response = client_for([dict(ROW)]).get(
        "/graph/subgraph", params={"ids": "project:y", "depth": 1}, headers=headers()
    )

    assert response.status_code == 200
    body = response.json()
    assert {node["id"] for node in body["nodes"]} == {"project:y", "person:x"}
    assert body["edges"] == [
        {"id": "project:y|OWNS|person:x", "source": "project:y", "target": "person:x", "type": "OWNS"}
    ]


def test_subgraph_deduplicates_across_seeds():
    second = {
        "seed": NODE_X,
        "nodes": [NODE_X, NODE_Y],
        "node_ids": ["person:x", "project:y"],
        "rels": ["OWNS"],
    }
    body = client_for([dict(ROW), second]).get(
        "/graph/subgraph", params={"ids": "project:y,person:x"}, headers=headers()
    ).json()

    assert len(body["nodes"]) == 2
    assert body["edges"]  # the same edge is not listed twice


def test_subgraph_requires_ids():
    response = client_for([]).get("/graph/subgraph", headers=headers())
    assert response.status_code == 400


def test_subgraph_returns_503_when_the_graph_is_down():
    response = client_for([], up=False).get(
        "/graph/subgraph", params={"ids": "project:y"}, headers=headers()
    )
    assert response.status_code == 503
