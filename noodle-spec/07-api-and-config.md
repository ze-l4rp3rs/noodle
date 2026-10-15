# API, response contract and configuration

## Endpoints

| Method and path | Accepts | Returns |
|---|---|---|
| `GET /health` | nothing | `{status:"ok", graph:"up"}`. `503` with `graph:"down"` if FalkorDB is unreachable |
| `POST /ask` | `{question, context?, mode?}` | Response (below). `Accept: text/event-stream` streams events |
| `POST /copilot/context` | `{entity_id}` | Decisions, owners, dependencies, prior documents, stale warnings for that node (Q2, Q4, Q5, Q8, Q10) |
| `POST /copilot/draft` | `{entity_id, instruction, draft_text?}` | Heavy-mode draft with citations and flagged claims, same response shape |
| `GET /graph/subgraph?ids=a,b,c&depth=1` | node ids | Nodes and edges for the visualisation, visible to the caller only |
| `GET /users` | nothing | Dev only: demo users for the user switcher. Off in production |
| `POST /ingest` | partial dataset | Change report. Hidden unless `INGEST_TOKEN` set; `401` without token, `413` too large, `422` invalid, `503` DB down |

**Caller identity:** `Authorization: Bearer <token>` maps to a Person. In dev mode, header
`X-Noodle-User: person:xyz` is accepted (disabled when `NOODLE_ENV=prod`). The body never carries identity.

Errors: missing question or unreadable body is `400`. FalkorDB unreachable is `503` and **never** a cached
or model-only answer. `/ask` never blocks waiting for input.

## Response (extends the sample in `message.txt`)

```json
{
  "answer": "text with [[source-id]] markers after each cited fact",
  "references": [{
    "id": "09a121fe", "number": 1, "type": "doc",
    "title": "rfc-031-auth-migration", "excerpt": "first quote", "quotes": ["..."],
    "date": "2026-06-02", "url": "", "nodeId": "doc:rfc-031-auth-migration",
    "freshness": "current", "freshness_note": "", "meta": {}
  }],
  "status": "verified | partly_verified | not_checked",
  "unverified": [{"sentence": "...", "reason": "plain language"}],
  "missing": ["parts of the question the records do not answer"],
  "premise": ["false assumptions in the question"],
  "proposed_actions": [{"kind": "message|ticket", "recipient": "person:id", "subject": "...",
                        "body": "...", "sources": ["09a121fe"], "requires_confirmation": true}],
  "access_notices": [{"text": "fixed notice", "contact": "person:id"}],
  "freshness": {"as_of": "2026-10-15T00:05:00Z", "live_intent": false},
  "graph_trace": [{"tool": "trace_decision", "cypher": "...", "params": {}, "node_ids": [], "edge_ids": [],
                   "rows": 7, "duration_ms": 12, "reused": false}],
  "metadata": {"rounds": 2, "approved": true, "processingTimeMs": 9120,
               "mode": "fast", "generator_model": "...", "reviewer_model": "..."}
}
```

| Field | Rules |
|---|---|
| `references[].id` | Random per run, 8 hex. The marker in `answer` is `[[id]]` |
| `references[].type` | `doc, chat, ticket, decision, change, project, person, role` |
| `references[].nodeId` | The **record's own graph id**. Two different chat messages never share a `nodeId`. UI highlighting depends on it |
| `references[].freshness` | `current`, `superseded`, `unknown`. Superseded sources are shown, labelled |
| `missing` | Must include every gap the answer lists under "Not in company records" |
| `graph_trace[].cypher` | The exact Cypher executed (hackathon: proves the graph does the work). Hidden-node ids never appear |
| `access_notices` | System text only. No hidden titles, ids, counts |
| `metadata.processingTimeMs` | Measured, never estimated |

Answer text: plain markdown, one level of bold at most per line (no nested bold), no headings except the
fixed "Not in company records".

## Streaming events (SSE)

`router` `{mode}`, `tool_call` `{tool, args}`, `tool_result` `{tool, rows, cypher, node_ids, edge_ids}`,
`draft` `{round}`, `review` `{round, problems}`, `answer` (full response object), `error` `{code, message}`.
Hidden content is never in any event. The UI builds against a mocked stream first.

## Configuration

One file, `noodle.yaml`, holds every setting and every model instruction. Secrets never.

| Group | Setting | Default |
|---|---|---|
| Providers | default provider; per provider: **env var name** of its key, generator model, reviewer model | first provider |
| Modes | `fast` and `heavy`: generator model, reviewer model, tool set, tool rounds, fix rounds, timeout | see 05 |
| Generation | output limit, temperature | 2000, 0 |
| Verification | quotes required, repair similarity, missing-info heading | yes, 0.9, "Not in company records" |
| Graph | url (host, port, or FalkorDB Cloud URL), graph name, embedding dimension | `localhost:6379`, `noodle`, 1536 |
| Embeddings | provider, model | configurable |
| Access | clearance labels, notice text, fallback contact text | see 06 |
| Freshness | live-intent keywords | see 06 |
| API | host, port, debug, max live-update size | `0.0.0.0`, 8000, off, 1 MB |
| Logs | folder | `./runs` |
| Instructions | generator, fix request, reviewer | |

Loading: read once, cached, typed, read-only. `NOODLE_CONFIG` points at another file. Errors name the
setting. Environment overrides only: `FALKORDB_URL`, default provider, `NOODLE_ENV`. Instruction
placeholders use plain string replacement (reviewer text contains literal JSON). Provider keys, `INGEST_TOKEN`
and the config location are the only other environment inputs. `.env.example` lists only what is actually read.

## Bring-up

`scripts/up.sh`: load secrets safely (values with spaces survive), check Python and Docker, check config and
at least one provider key, start FalkorDB and **wait for its health check** (no fixed sleeps), run
`ensure_schema()`, load the synthetic dataset if the graph is empty, print next steps.

```yaml
# docker-compose.yml (essentials)
services:
  falkordb:
    image: falkordb/falkordb:latest
    ports: ["6379:6379"]
    volumes: ["falkordb_data:/var/lib/falkordb/data"]
    healthcheck: {test: ["CMD", "redis-cli", "ping"], interval: 5s, retries: 10}
  noodle:
    build: .
    depends_on: {falkordb: {condition: service_healthy}}
    environment: [FALKORDB_URL=redis://falkordb:6379, ANTHROPIC_API_KEY, INGEST_TOKEN]
    ports: ["8000:8000"]
    volumes: ["./noodle.yaml:/app/noodle.yaml:ro", "./runs:/app/runs"]
volumes: {falkordb_data: {}}
```

The image copies files one by one, never the whole project, so a local secrets file cannot be included. No
keys in any image. Deploy: FalkorDB Cloud for the graph, any container host for the API.
