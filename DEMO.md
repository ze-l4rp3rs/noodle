# Demo script

The demo runbook. `scripts/demo.sh` runs the six scenarios below against a live
server and prints the question, the caller, the answer, the status, the citations
and the executed Cypher for each. It talks only over the public HTTP API, so it
demonstrates the product exactly as a UI would.

## Prep (once)

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e '.[dev,api]'
cp .env.example .env      # set one provider key
scripts/up.sh             # graph + schema + synthetic dataset + API on :8000
```

`scripts/up.sh` ends by serving the API, so a fresh checkout reaches a working
`/ask` here. Leave it running and use a second terminal for the commands below.

## Run it

```bash
scripts/demo.sh                       # all six scenarios
scripts/demo.sh --base-url URL        # against another host
```

## The six scenarios

The build must support each of these (spec 08). The `curl` form is what to type
if you are driving live; `scripts/demo.sh` does the same and formats the output.

### 1. Same question as an intern and a VP — soft dead-end vs the full answer

The intern (`person:ines-abara`, clearance 1) gets the fixed soft-dead-end notice
and a contact, because the record that supersedes the visible decision is
restricted. The VP (`person:victor-hale`, clearance 3) gets the full answer.

```bash
Q='"question":"Is decision decision:vendor-choice still current?"'
curl -s localhost:8000/ask -H 'X-Noodle-User: person:ines-abara' -H 'Content-Type: application/json' -d "{$Q}" | python -m json.tool
curl -s localhost:8000/ask -H 'X-Noodle-User: person:victor-hale' -H 'Content-Type: application/json' -d "{$Q}" | python -m json.tool
```

Look for: `access_notices` (intern) with no hidden title/id/type/count, versus
the full answer and the supersession chain with `freshness` on the references
(VP).

### 2. "Is decision X still current?" walks the supersession chain

```bash
curl -s localhost:8000/ask -H 'X-Noodle-User: person:victor-hale' -H 'Content-Type: application/json' \
  -d '{"question":"Trace decision:managed-queue and what superseded it."}' | python -m json.tool
```

Look for: the new decision (`decision:self-host-queue`) as the head of the chain,
the old one marked not current, and a `freshness` value on each reference.

### 3. "Who should I talk to about Y?" ranks people with the items behind each score

```bash
curl -s localhost:8000/ask -H 'X-Noodle-User: person:victor-hale' -H 'Content-Type: application/json' \
  -d '{"question":"Who should I talk to about project:identity-provider?"}' | python -m json.tool
```

Look for: named people, each backed by the concrete records (Q3 expertise), and
every claim cited.

### 4. "What happened today?" returns the as-of line

```bash
curl -s localhost:8000/ask -H 'X-Noodle-User: person:ines-abara' -H 'Content-Type: application/json' \
  -d '{"question":"What happened today?"}' | python -m json.tool
```

Look for: `freshness.live_intent` true, the fixed as-of line naming
`last_synced_at` read from the graph, and no same-day claim that lacks a record
dated that day before the sync.

### 5. Draft an update to stakeholders of Z — heavy mode, cited, nothing sent

```bash
curl -s localhost:8000/copilot/draft -H 'X-Noodle-User: person:victor-hale' -H 'Content-Type: application/json' \
  -d '{"entity_id":"project:auth-migration","instruction":"Draft a short status update for the stakeholders."}' | python -m json.tool
```

Look for: `metadata.mode` heavy, a cited draft (or flagged unbacked sentences),
and `proposed_actions` with `requires_confirmation: true` — nothing was sent.

### 6. Show the executed Cypher and highlighted nodes for each answer

Every response carries `graph_trace` with the exact statement, its parameters and
the node/edge ids it touched. For the visualisation:

```bash
curl -s 'localhost:8000/graph/subgraph?ids=project:identity-provider&depth=1' -H 'X-Noodle-User: person:victor-hale' | python -m json.tool
```

Look for: the Cypher in `graph_trace[].cypher`, the highlighted `nodeId`s in
`references`, and the `nodes`/`edges` returned by `/graph/subgraph`, all visible
to the caller only.

## Demo video

The recorded demo (submission artifact) walks scenarios 1–6 in order, using
`scripts/demo.sh` for the answers and the browser UI on `http://localhost:3000`
(FalkorDB) plus `/graph/subgraph` for the graph picture. Video file:
`docs/demo.mp4` (recorded at submission time; not committed to keep the repo
light).
