#!/usr/bin/env python3
"""The demo runbook (08, "Demo script the build must support", step 15).

Runs the six demo scenarios against a live Noodle server and prints, for each,
the question, the caller, the answer headline, the status, the citations and the
executed Cypher. It talks only over the public HTTP API, so it demonstrates the
product exactly as a UI would.

This needs a running server (``scripts/up.sh``) and a provider key; it is not
part of ``check.sh``. Names and ids below belong to the shipped synthetic dataset
(they stay out of ``src/`` and the schema by design).

    scripts/demo.sh                 # against http://localhost:8000
    scripts/demo.sh --base-url URL
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://localhost:8000"
INTERN = "person:ines-abara"
VP = "person:victor-hale"

QUESTION_CURRENCY = "Is decision decision:vendor-choice still current?"
QUESTION_SUPERSESSION = "Trace decision:managed-queue and what superseded it."
QUESTION_EXPERT = "Who should I talk to about project:identity-provider?"
QUESTION_TODAY = "What happened today?"
DRAFT_ENTITY = "project:auth-migration"
DRAFT_INSTRUCTION = "Draft a short status update for the stakeholders."
SUBGRAPH_SEED = "project:identity-provider"


class DemoError(RuntimeError):
    pass


def _request(method: str, url: str, *, user: str | None = None, body: dict | None = None,
             accept: str | None = None, timeout: float = 120.0) -> tuple[int, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if user:
        request.add_header("X-Noodle-User", user)
    if accept:
        request.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            return response.status, payload
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload


def _ask(base: str, user: str, question: str, *, mode: str | None = None) -> dict:
    body: dict = {"question": question}
    if mode:
        body["mode"] = mode
    status, payload = _request("POST", f"{base}/ask", user=user, body=body)
    if status != 200:
        raise DemoError(f"/ask returned {status}: {payload}")
    return payload


def rule(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def show_answer(label: str, user: str, response: dict) -> None:
    metadata = response.get("metadata", {})
    print(f"  [{label}] user={user} status={response.get('status')} "
          f"mode={metadata.get('mode')} rounds={metadata.get('rounds')} "
          f"time={metadata.get('processingTimeMs')}ms")
    answer = (response.get("answer") or "").strip().replace("\n", " ")
    if len(answer) > 240:
        answer = answer[:237] + "..."
    print(f"  answer: {answer}")
    notices = response.get("access_notices") or []
    if notices:
        print(f"  access_notices: {notices}")
    missing = response.get("missing") or []
    if missing:
        print(f"  not in company records: {missing}")
    refs = response.get("references") or []
    for ref in refs[:4]:
        fresh = ref.get("freshness")
        print(f"    - {ref.get('nodeId')}  ({ref.get('type')})  freshness={fresh}")
    if len(refs) > 4:
        print(f"    … {len(refs) - 4} more references")


def show_trace(response: dict) -> None:
    trace = response.get("graph_trace") or []
    for entry in trace:
        cypher = (entry.get("cypher") or "").strip().replace("\n", " ")
        if len(cypher) > 200:
            cypher = cypher[:197] + "..."
        print(f"    cypher[{entry.get('tool')}]: {cypher}")


def scenario_rbac_pair(base: str) -> dict:
    rule("1. Same question as intern and VP: soft dead-end vs the full answer")
    print(f"  question: {QUESTION_CURRENCY}")
    intern = _ask(base, INTERN, QUESTION_CURRENCY)
    show_answer("intern", INTERN, intern)
    vp = _ask(base, VP, QUESTION_CURRENCY)
    show_answer("VP", VP, vp)
    return vp


def scenario_supersession(base: str) -> None:
    rule("2. Supersession chain: is the decision still current?")
    print(f"  question: {QUESTION_SUPERSESSION}")
    show_answer("VP", VP, _ask(base, VP, QUESTION_SUPERSESSION))


def scenario_experts(base: str) -> dict:
    rule("3. Who should I talk to? (ranked people with the items behind each hit)")
    print(f"  question: {QUESTION_EXPERT}")
    response = _ask(base, VP, QUESTION_EXPERT)
    show_answer("VP", VP, response)
    return response


def scenario_today(base: str) -> None:
    rule("4. Live intent: the as-of line, no same-day claims")
    print(f"  question: {QUESTION_TODAY}")
    show_answer("intern", INTERN, _ask(base, INTERN, QUESTION_TODAY))


def scenario_draft(base: str) -> None:
    rule("5. Copilot draft: heavy mode, graph context, cited, nothing sent")
    print(f"  entity: {DRAFT_ENTITY}  instruction: {DRAFT_INSTRUCTION}")
    status, payload = _request(
        "POST",
        f"{base}/copilot/draft",
        user=VP,
        body={"entity_id": DRAFT_ENTITY, "instruction": DRAFT_INSTRUCTION},
    )
    if status != 200:
        raise DemoError(f"/copilot/draft returned {status}: {payload}")
    response = payload
    metadata = response.get("metadata", {})
    print(f"  status={response.get('status')} mode={metadata.get('mode')}")
    draft = (response.get("answer") or "").strip().replace("\n", " ")
    if len(draft) > 240:
        draft = draft[:237] + "..."
    print(f"  draft: {draft}")
    actions = response.get("proposed_actions") or []
    print(f"  proposed_actions (drafts only, nothing sent): {actions}")


def scenario_cypher_and_nodes(base: str, response: dict) -> None:
    rule("6. The executed Cypher and the highlighted nodes")
    print("  executed Cypher for scenario 3:")
    show_trace(response)
    refs = response.get("references") or []
    seeds = [ref.get("nodeId") for ref in refs if ref.get("nodeId")]
    if SUBGRAPH_SEED not in seeds:
        seeds.append(SUBGRAPH_SEED)
    ids = ",".join(dict.fromkeys(seeds)) or SUBGRAPH_SEED
    print(f"  /graph/subgraph?ids={ids}&depth=1")
    status, payload = _request("GET", f"{base}/graph/subgraph?ids={ids}&depth=1", user=VP)
    if status != 200:
        raise DemoError(f"/graph/subgraph returned {status}: {payload}")
    nodes = payload.get("nodes", [])
    edges = payload.get("edges", [])
    print(f"    {len(nodes)} visible nodes, {len(edges)} edges")
    for node in nodes[:10]:
        print(f"    node {node.get('id')}  ({node.get('label')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Noodle demo scenarios.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="the running server")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")

    status, health = _request("GET", f"{base}/health")
    if status != 200:
        print(f"demo: server at {base} is not healthy ({status} {health})", file=sys.stderr)
        print("demo: start it with scripts/up.sh (needs a provider key)", file=sys.stderr)
        return 1
    print(f"Noodle demo against {base}")

    scenario_rbac_pair(base)
    scenario_supersession(base)
    experts = scenario_experts(base)
    scenario_today(base)
    scenario_draft(base)
    scenario_cypher_and_nodes(base, experts)

    rule("Demo complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DemoError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        raise SystemExit(1)
