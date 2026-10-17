# Noodle

Backend of a company-brain agent. People, teams, projects, tickets, documents,
decisions, chat messages and recorded changes live in **one FalkorDB graph**. The
agent answers from that graph, every fact carries an exact quote from a specific
record, and what a user may see is decided **inside the Cypher**, not in a prompt.

Track: **Company Brain** (Hackathon rule 10).

> The full build spec lives in `noodle-spec/`. `queries.md` (every Cypher the
> product depends on), `EXPLAIN.md` (a paragraph per query and component),
> `AI_DISCLOSURE.md` and `DEMO.md` (the demo script) are the other submission
> artifacts.

## Why it is a graph product

FalkorDB is the primary database and it does the work. Remove it and Noodle
fails, it does not degrade: there is no in-memory copy of the data and no cached
answer served when the graph is down (a request gets `503`). Traversal,
supersession chains, conflicts, impact and path-finding are Cypher — not Python
loops over fetched nodes — and the exact statement behind every answer is carried
in `graph_trace`. Access is enforced by `x.clearance <= $clr` in each query, so
the model never even sees a record the caller may not see.

## Quickstart

Requirements: Docker, Python 3.11+, and one provider key (Anthropic, Google,
OpenAI-compatible or Groq) for `/ask`.

```bash
# 1. Python environment
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev,api,extract]'

# 2. Secrets (never committed). At minimum set one provider key.
cp .env.example .env && $EDITOR .env

# 3. Bring everything up. This starts FalkorDB, creates the schema, ingests the
#    synthetic dataset and serves the API -- a fresh checkout reaches /ask here.
scripts/up.sh

#    ... or set up without serving:  scripts/up.sh --no-serve
```

With the server running (default `http://localhost:8000`, interactive docs at
`/docs`):

```bash
curl -s localhost:8000/health
curl -s localhost:8000/ask \
  -H 'X-Noodle-User: person:victor-hale' \
  -H 'Content-Type: application/json' \
  -d '{"question": "Who owns the Auth Migration project?"}' | python -m json.tool
```

Run the demo scenarios end to end:

```bash
scripts/demo.sh                     # the six demo scenarios, against a live server
scripts/check.sh                    # compile, lint, unit suite, config/RBAC/name guards, eval
```

Other commands:

```bash
scripts/serve.sh                    # serve the API only (graph must be up)
scripts/db.sh ingest [path]         # (re)load a dataset file or source folder
scripts/db.sh down                  # stop the graph (keeps data); `reset` also removes it
python -m noodle "question" --user person:victor-hale    # the CLI, same response as /ask
python scripts/gen_queries_md.py    # regenerate queries.md from queries.py
```

`scripts/check.sh` needs no network, database or keys. Integration tests
(`-m integration`) and the eval runner (`scripts/run_eval.py`) need a live
FalkorDB and a provider key; they are skipped automatically when neither is
present.

## Architecture

```
 UI / CLI ──► API (/ask, /copilot, /ingest) ──► Router (fast | heavy)
                                                   │
                       ┌───────────────────────────▼──────────────────────────┐
                       │ Answer workflow: generator ⇄ tools ─► checks ─► reviewer ─► fix rounds │
                       └───────┬──────────────────────────────┬───────────────┘
                               ▼                              ▼
                      Tools (read-only)                Source registry + verification
                               ▼
                      Query library (parameterised Cypher, RBAC filter)
                               ▼
                      FalkorDB  ◄── Ingestion (dataset, files, live deltas)
```

| Component | Responsibility | Code |
|---|---|---|
| API | HTTP surface, SSE streaming, 503 on outage | `src/noodle/api.py`, `server.py` |
| Router | fast/heavy mode and live-intent detection | `src/noodle/router.py` |
| Answer workflow | draft, check, review, fix rounds, best-draft choice | `src/noodle/pipeline.py`, `loop.py` |
| Tools | thin wrappers over the query library; return evidence + trace | `src/noodle/tools.py` |
| Query library | the only place Cypher lives; every function takes the user context | `src/noodle/queries.py` |
| Verification | source registry, quote repair, sentence checks | `src/noodle/sources.py`, `verification.py`, `reviewer.py` |
| Ingestion | the only write path | `src/noodle/ingestion.py`, `dataset.py`, `extractor.py` |
| Graph client | read-only `ro_query` and the ingestion-only write path | `src/noodle/graph.py` |

See `EXPLAIN.md` for a paragraph on each component and each query.

## Graph data model

`src/noodle/schema.py` is the single source of truth. The dataset checks, the
query library, the generated docs and the tests all read labels, relationships,
properties and clearance rules from it; nothing is hard-coded elsewhere.

### Nodes

| Label | Main properties |
|---|---|
| `Person` | id, name, email, role, department, start_date, manager, team |
| `Team` | id, name |
| `Project` | id, name, department, status, priority, scope, budget, target_end |
| `Ticket` | id, key, title, status, priority, created, resolved, due, description, comments |
| `Document` | id, title, family, version, created, status, content, key_facts, `current`, url, tags, embedding |
| `Decision` | id, key, title, date, status, description, rationale, impact, details, `current` |
| `ChatMessage` | id, channel, author_name, ts, text, thread, reactions |
| `Change` | id, entity_id, entity_type, field, old_value, new_value, date, reason, version, seq, `current` |
| `Chunk` | id, document_id, text, offset, embedding |
| `Meta` | one node, `meta:sync`: last_synced_at, dataset_version, record_counts |

Every node also carries `fingerprint` (a hash of the source record, minus
authoring notes; drives incremental updates), `written_at`, `clearance` (0
public, 1 internal, 2 confidential, 3 restricted; default 1) and
`access_owner_id` (required when clearance ≥ 2).

**IDs** are `type:slug` and stable (`doc:rfc-031-auth-migration`,
`ticket:PLAT-882`, `chat:<channel>:<ts>`, `change:<owner-id>:<kind>:<position>`).
Evidence and UI highlighting use these, never a channel or a title. Dates are ISO
strings so they sort as strings; nested values are stored as JSON text.

### Relationships

Direction carries meaning: a newer record points at the older one it replaces.

| Type | From → To | Source |
|---|---|---|
| `OWNS`, `SPONSORS` | Person → Project | owner, sponsor |
| `LEADS`, `MEMBER_OF` | Person → Team | team lead, members |
| `APPROVED`, `DISSENTS` | Person → Decision | approver, dissent (carries evidence) |
| `AUTHORED_BY` | Document → Person | author |
| `ASSIGNED_TO`, `REPORTED_BY` | Ticket → Person | assignee, reporter |
| `POSTED` | Person → ChatMessage | author |
| `MENTIONS` | ChatMessage → Project/Ticket/Person | lists, names in text |
| `BELONGS_TO` | Ticket → Project | ticket list |
| `BLOCKS`, `SPLIT_FROM`, `RELATED_TO` | Ticket → Ticket | ticket lists |
| `DEPENDS_ON` | Project → Project | project list (impact queries) |
| `SUPERSEDES` | newer Decision/Document → older | lists, document `supersedes` |
| `SUPERSEDED_BY` | older Decision → newer | only when no `SUPERSEDES` records it |
| `OVERRIDES`, `REVERSES`, `CONTRADICTS` | Decision → Decision | fields, lists |
| `AFFECTS` | Decision → Project | decision list |
| `DOCUMENTED_IN`, `ANNOUNCED_IN` | Decision → Document/ChatMessage | decision lists |
| `TRIGGERED` | Document → Decision | document list |
| `MADE_CHANGE` | Person → Change | who made it |
| `CHANGES` | Change → Project/Ticket | the record whose history it is |
| `SOURCED_FROM` | Change/Decision → Document/ChatMessage/Decision | evidence lists |
| `PART_OF` | Chunk → Document | chunking |

Every relationship stores `origin` (the record or list it was read from) plus
`date` or `evidence` where the source gives one. A relationship is visible only
if **both endpoints** are visible.

### Derived data (computed from structure only)

| Flag | Rule |
|---|---|
| Document `current` | false if another document supersedes it or its family has a later version |
| Decision `current` | false if superseded or reversed, or status says superseded |
| Change `current` | true only for the latest change to that field of that entity |

Structure marks what is superseded; the model decides whether a document's
content is *stale* (a current document resting on a superseded decision).

### Clearance (RBAC)

Clearance is an integer 0–3. Every query filters each node with
`x.clearance <= $clr`, and an edge needs both endpoints visible. The caller's
clearance comes from their `Person` node — the model can never supply it. When a
query filters material out, a **hidden twin** returns only a count and the access
owners (`access_owner_id`), and the system appends a fixed soft-dead-end notice
naming a contact — never a hidden title, id, type or count.

### Indexes (DDL)

Created idempotently by `ensure_schema()` at startup:

```cypher
CREATE INDEX FOR (n:Person|Team|Project|Ticket|Document|Decision|ChatMessage|Change) ON (n.id);
CREATE INDEX FOR (n:Decision) ON (n.current);
CREATE INDEX FOR (n:Document) ON (n.current);
CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'content');
CALL db.idx.fulltext.createNodeIndex('ChatMessage', 'text');
CALL db.idx.fulltext.createNodeIndex('Decision', 'title', 'description', 'rationale');
CREATE VECTOR INDEX FOR (d:Document) ON (d.embedding)
  OPTIONS {dimension: 1536, similarityFunction: 'cosine'};
```

The embedding dimension comes from `noodle.yaml` and must match the embedding
model.

### Never stored

Authoring commentary (it can contain the answer key), fields that became
relationships (owners, members, evidence lists, dissent, supersession), nested
history (it becomes `Change` nodes), secrets or raw model output.

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | `200 {status: ok, graph: up}` or `503` when the graph is down |
| `POST` | `/ask` | answer a question; JSON, or SSE when `Accept: text/event-stream` |
| `GET` | `/users` | dev-only user switcher (404 in prod) |
| `GET` | `/graph/subgraph?ids=&depth=` | visible nodes/edges for the visualisation |
| `POST` | `/copilot/context` | owners, decisions, documents, deps, stale warnings for a node |
| `POST` | `/copilot/draft` | a heavy-mode draft, cited and flagged, never sent |
| `POST` | `/ingest` | token-protected live partial ingest (off unless `INGEST_TOKEN` is set) |

Identity comes from `Authorization: Bearer <token>` mapped through
`NOODLE_AUTH_TOKENS`, or — in development only — the `X-Noodle-User` header
(disabled when `NOODLE_ENV=prod`). The `id` in every response reference is the
run-random citation marker; `nodeId` is the record's own stable id.

## Ingestion

Ingest a structured dataset `.json` file, or an entire folder of source files
(PDF, DOCX, XML, Markdown, plain text via `xberg`):

```bash
scripts/db.sh ingest [path/to/data]
python scripts/ingest.py [path] --dry-run    # reconcile without writing
```

Ingestion is the only write path. It plans the whole dataset first, validates
every reference, then diffs against the graph by fingerprint and writes only what
is new or changed. Loading the same dataset twice writes nothing and reports
everything unchanged; editing one record changes exactly one node. Nothing writes
`meta:sync.last_synced_at` unless something actually changed.

## Evaluation

`eval/golden/` holds 21 golden questions covering ownership, expertise,
provenance, currency, stale documents, timelines, false premises, gaps,
conflicts, impact, connections, the RBAC pair, live intent, actions and context.
Each question's facts are confirmed against the dataset with no model and no
database:

```bash
scripts/run_eval.py --offline       # keyless fact check (also run by check.sh)
scripts/run_eval.py                 # graph agent vs vector-only baseline (needs keys + graph)
```

## Layout

| Path | What |
|---|---|
| `src/noodle/schema.py` | single source of truth: labels, relationships, properties, indexes, clearance |
| `src/noodle/queries.py` | the query library — the only place Cypher lives |
| `src/noodle/graph.py` | read-only `ro_query` client and the ingestion-only write path |
| `src/noodle/api.py`, `server.py` | the HTTP API and the ASGI entry point |
| `src/noodle/ingestion.py` | the only write path (plan → diff → write) |
| `eval/` | golden questions and their offline fact check |
| `data/synthetic/` | synthetic dataset (every trap in spec 04) |
| `fixtures/` | 10-node hand fixture for early tests |
| `scripts/` | `up.sh`, `serve.sh`, `demo.sh`, `check.sh` and the check helpers |
| `noodle-spec/` | the build spec |
| `queries.md`, `EXPLAIN.md`, `AI_DISCLOSURE.md`, `DEMO.md` | submission artifacts |

## Non-negotiables

1. **The graph does real work.** Remove FalkorDB and Noodle must fail, not degrade.
2. **Every fact is cited with an exact quote**, verified automatically and by a reviewer.
3. **No assumptions.** If the records do not say it, the answer says "not in company records".
4. **Errors are never sources.** A failing tool fails; it never returns text a model could cite.
5. **Access is enforced in Cypher.** The model never sees what the user may not see.
6. **No live information.** Data is only as fresh as the last sync, and the answer says so.
7. **Read-only agent.** Only ingestion writes. Actions are drafted, never executed.
8. **Generic by design.** No company, person or record identifier in logic, schema, config or prompts.
9. **Synthetic data only.** Never load real mail, drive or chat.

## License / data

All data in this repository is synthetic and fabricated for the demo; it does not
describe real people, companies or systems. See `AI_DISCLOSURE.md`.
