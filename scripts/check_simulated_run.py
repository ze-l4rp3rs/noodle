#!/usr/bin/env python3
"""A simulated run through both entry points proves the shared response builder.

An independent check for ``scripts/check.sh`` item 3 (07, step 9): build two
identical deterministic runtimes (scripted generator, recording graph client,
seeded citation ids), answer the same question through the CLI path and through
``/ask``, and fail unless

* the two responses match apart from the measured timings;
* the run is ``verified``; and
* the one-letter-off quote in the draft was repaired from the source (07, step 7).

No database and no model are touched. Exit 0 on success, 1 with a message on
failure.
"""

from __future__ import annotations

import copy
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for path in (str(ROOT / "src"), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

warnings.filterwarnings("ignore", category=DeprecationWarning)

from fastapi.testclient import TestClient  # noqa: E402

from noodle import queries as Q  # noqa: E402
from noodle.api import create_app  # noqa: E402
from noodle.cli import cli_answer  # noqa: E402
from noodle.config import load_settings  # noqa: E402
from noodle.providers import Completion, ScriptedProvider, ToolCall  # noqa: E402
from noodle.service import Runtime  # noqa: E402
from tests.fakes import RecordingGraphClient, graph_result, hidden_result  # noqa: E402

USER = Q.UserCtx("person:test-user", clearance=1)
QUESTION = "who owns project y?"
Q2 = "MATCH (p:Person)-[r]-(x {id: $id})"
TWIN = "WITH CASE WHEN x.clearance > $clr"
SOURCE_TEXT = "Omar is the owner of project:y"
OWNER_ROW = {"person_id": "person:x", "name": "Omar", "rel": "OWNS", "entity": "project:y"}


class SeededRng:
    def __init__(self) -> None:
        self._n = 0

    def token_hex(self, n: int) -> str:
        self._n += 1
        return f"{self._n:0{2 * n}x}"


def tool():
    return Completion(tool_calls=[ToolCall("c1", "find_owner", {"ref": "project:y"})])


def _source_id(messages) -> str:
    for message in reversed(messages):
        if message.role == "tool":
            for item in json.loads(message.text).get("evidence") or []:
                return item["source_id"]
    raise AssertionError("the generator was never shown a source")


def misquoting():
    """A draft whose quote is one letter off the source, so repair must fix it."""

    def reply(messages, tools):
        source_id = _source_id(messages)
        typo = SOURCE_TEXT.replace("owner", "ownr")
        return Completion(text=f'The owner is Omar. [[{source_id}]] "{typo}"')

    return reply


def approving():
    return Completion(
        text=json.dumps(
            {
                "sentences": [{"n": 1, "supported": True, "reason": "stated"}],
                "unanswered": [],
                "false_premises": [],
            }
        )
    )


def runtime() -> Runtime:
    settings = load_settings(ROOT / "noodle.yaml")
    return Runtime(
        settings=settings,
        provider=ScriptedProvider([tool(), misquoting()]),
        reviewer_provider=ScriptedProvider([approving()]),
        client=RecordingGraphClient(
            (Q2, graph_result([dict(OWNER_ROW)])),
            (TWIN, hidden_result(0, [])),
        ),
        rng=SeededRng(),
    )


def without_timing(response: dict) -> dict:
    trimmed = copy.deepcopy(response)
    trimmed["metadata"].pop("processingTimeMs", None)
    for entry in trimmed["graph_trace"]:
        entry.pop("duration_ms", None)
    return trimmed


def fail(message: str) -> int:
    print(f"simulated run: {message}")
    return 1


def main() -> int:
    cli_response = cli_answer(runtime(), USER, QUESTION, mode="fast")

    client = TestClient(create_app(runtime()))
    http = client.post(
        "/ask",
        json={"question": QUESTION, "mode": "fast"},
        headers={"X-Noodle-User": "person:test-user"},
    )
    if http.status_code != 200:
        return fail(f"/ask returned {http.status_code}")
    api_response = http.json()

    if cli_response["status"] != "verified":
        return fail(f"expected a verified answer, got {cli_response['status']!r}")

    quotes = cli_response["references"][0]["quotes"] if cli_response["references"] else []
    if SOURCE_TEXT not in quotes:
        return fail(f"the misquote was not repaired; quotes={quotes!r}")

    if without_timing(cli_response) != without_timing(api_response):
        return fail("the CLI and the API returned different responses for the same run")

    print("simulated run: CLI and API agree; the misquote was repaired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
