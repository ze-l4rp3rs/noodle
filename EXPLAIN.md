# EXPLAIN

One paragraph per query and per component, so a team member can explain each
live. Evidence points at `file:line`. See `README.md` for the data model and
`queries.md` for the full source of every query.

---

## Query library (`src/noodle/queries.py`)

Every answer path starts from one of these functions; no tool reads data
anywhere else (`README.md`, rule 1). Each takes the caller's `UserCtx` and
applies `x.clearance <= $clr` to every variable it returns, so access is decided
in Cypher. Every hidden-twin function returns only a count and the access owners
of what was filtered out. Value parameters are always Cypher parameters, never
string interpolation.

**Q1 Hybrid search (`q1_search`, queries.py:186)** — finds documents by topic.
It runs a vector search over `Document.embedding` when an embedding is supplied,
falling back to the fulltext index on title/content otherwise, and over-fetches
before the clearance filter so a low-clearance caller still gets enough visible
hits. It is the entry point for almost every question, because the graph is only
seeded by finding the records a question is about.

**Q2 Owners (`q2_owners`, queries.py:246)** — returns who is formally responsible
for an entity *now*: the `OWNS`/`SPONSORS`/`LEADS`/`APPROVED` edge (and the
record-level owner fields) for a given `ref`, with both endpoints visible. It
answers "who owns X".

**Q3 Expertise (`q3_experts`, queries.py:264)** — ranks people by everything the
graph connects them to for one topic: authored documents, owned or sponsored
projects, tickets they are assigned to or reported, decisions they approved or
dissented on, and messages they posted. Each score is backed by the concrete
items, so the answer can show "why" behind every person.

**Q4 Decision trace (`q4_decision_trace`, queries.py:352)** — reconstructs why a
decision was made: its approvers, dissenters (with the evidence carried on the
`DISSENTS` edge), rationale, impact and the sources it was documented in or
announced in. It is the provenance query.

**Q4b Supersession chain (`q4_decision_chain`, queries.py:380)** — walks the
`SUPERSEDES`/`SUPERSEDED_BY` edges around a decision to return the chain of
records that replaced or were replaced by it. Every node on the chain is
filtered, so a hidden superseder is not revealed.

**Q5 Still current (`q5_currency`, queries.py:404)** — returns the head of a
decision's supersession chain: the newest record that still stands. This is the
query behind "is decision X still current?", and it is the one that must hide the
fact that *something* supersedes a visible decision when that something is above
the caller's clearance (a hidden twin supplies the soft dead-end instead).

**Q6 Timeline (`q6_timeline`, queries.py:424)** — reads the `Change` nodes for an
entity (optionally one field) in date order, so the answer can say how a value
evolved or what it was on a given date. `current` marks the latest change per
field.

**Q7 Conflicts (`q7_conflicts`, queries.py:467)** — finds two *current* decisions
that both `AFFECTS` the same project, i.e. live but contradictory direction. It
is a structural conflict, not a text similarity, so it never guesses.

**Q8 Stale documents (`q8_stale_documents`, queries.py:489)** — finds current
documents that rest on a decision that has since been superseded. The document is
still "current" by structure; this query surfaces that its foundation moved,
which is the difference between *superseded* and *stale*.

**Q9 Connection (`q9_connection`, queries.py:513)** — returns the shortest path
(`shortestPath`) between two entities over the relationship allowlist, so the
answer can explain how two things are related. Both endpoints and every edge on
the path are visibility-filtered.

**Q10 Team impact (`q10_team_impact`, queries.py:545)** — given a team and a set
of people, returns the projects at risk: the projects owned or sponsored by team
members, and the projects those people are the last assignee/reporter on. It is
the "if these people leave, what breaks" query.

**Q11 Blast radius (`q11_blast_radius`, queries.py:570)** — a bounded BFS over
`AFFECTS` from a decision out to projects and then along `DEPENDS_ON`, so the
answer can list everything a decision indirectly touches. The traversal is in
Cypher, not a Python loop.

**Q12 Freshness (`q12_freshness`, queries.py:635)** — reads `meta:sync` for
`last_synced_at`. This is the graph's own as-of time, read from the database
rather than the wall clock, and it is what the fixed as-of line reports for a
live-intent question.

**Q15 Subgraph (`q15_subgraph`, queries.py:591)** — given a set of seed ids and a
depth, returns the visible nodes and edges around them for the visualisation. It
is the query behind `GET /graph/subgraph`, and by construction it returns only
what the caller may see.

**Q13 Hidden twin (`hidden_twin_entity`, queries.py:715)** — the shared count/
owner query used when RBAC filtered an entity out. It returns a boolean-shaped
count and the access owner ids, never the hidden node's title, id, type or count
in the response; the response layer turns it into the fixed soft-dead-end notice.

**Hidden twins** — `hidden_twin_search` (queries.py:230) for Q1,
`hidden_twin_entity` (:715), `hidden_twin_sources` (:732) for the provenance
query, `hidden_twin_superseder` (:744) for Q5, `hidden_twin_connection` (:756)
for Q9 and `hidden_twin_team` (:772) for Q10 — each mirrors its visible query but
returns only `{hidden, count, access_owners}`.

---

## Components

**Schema (`src/noodle/schema.py`)** — the single source of truth for the graph
model: labels, relationship types, per-label properties, reference fields (which
record field produces which edge), clearance rules and index DDL. Ingestion, the
query library, the dataset checks, the generated docs and the tests all read from
it, so a label or property is defined exactly once. `visibility_clause()` emits
the one RBAC predicate every query uses.

**Graph client (`src/noodle/graph.py`)** — splits the two doorways to FalkorDB.
`GraphClient` is read-only and uses `ro_query` (`GRAPH.RO_QUERY`) for every read;
`IngestWriter` is the write path, imported only by `ingestion`, `schema` (the one
allow-listed DDL exception) and `bootstrap`. A test
(`tests/test_graph.py::test_only_allowlisted_modules_reference_the_write_path`)
enforces that split, so removing FalkorDB removes the product.

**Config (`src/noodle/config.py`)** — loads one YAML file into typed, read-only
settings (graph, api, logs, providers, modes, generation). Secrets never live
here: a provider entry names the environment variable that holds the key, and a
regex rejects a bare key. Only `FALKORDB_URL`, `NOODLE_ENV` and `NOODLE_CONFIG`
override the file.

**Dataset (`src/noodle/dataset.py`)** — reads and validates the dataset JSON
before any write: every reference resolves, every planted trap is present, field
kinds match the schema. It knows nothing about the graph, so a bad dataset fails
before it can touch FalkorDB.

**Ingestion (`src/noodle/ingestion.py`)** — the only write path, plan-then-write.
`build_plan` turns a dataset into every node and relationship without touching
the graph; `ingest` diffs the plan against the graph by fingerprint, writes only
what is new or changed, removes relationships/records that disappeared (by
origin), flips derived flags, and advances `meta:sync` only when something
changed. Loading twice writes nothing; editing one record changes one node; a
partial live update through `POST /ingest` uses the same code with `partial=True`.

**Document extractor (`src/noodle/extractor.py`)** — turns non-JSON source files
(PDF, DOCX, XML, Markdown, plain text via `xberg`) into Document records so a
folder of files can be ingested through the same write path.

**Tools (`src/noodle/tools.py`)** — the model-facing layer over the query
library. A tool shapes arguments, calls one query function, converts rows into
`Evidence` items and collects the executed Cypher into a trace. The contract: the
user identity is supplied by the system and never appears in a model-facing
parameter list; a tool fails by raising (so an error can never become a source);
each evidence item is its own citable record and `ref` is always the record's own
graph id; the payload the model sees carries evidence only.

**Providers (`src/noodle/providers.py`)** — one interface and several adapters
(Anthropic, Google, OpenAI-compatible/Groq) behind a single `complete()` call.
Each adapter converts the neutral message list to its wire format through an
injectable transport and parses the reply into a `Completion`. SDKs are imported
lazily inside the default transport, so importing the module never pulls in an
unused provider. Separate "reasoning" output never reaches the answer.

**Loop (`src/noodle/loop.py`)** — the generator's bounded tool loop. Text the
model writes alongside a tool request is discarded; an unknown tool, malformed
arguments or a failing tool are *reported* to the model, never raised out or
turned into a source; an identical call is answered from the first result and
marked `reused`; after the round budget the next call runs with no tools (the
forced final answer). Evidence is registered in the run's `SourceRegistry` with a
run-random citation id.

**Source registry (`src/noodle/sources.py`)** — wraps each `Evidence` item in a
run-random eight-hex id (the `[[id]]` the model cites) while the record's own
graph id stays on the evidence and becomes the reference's `nodeId`. Retrieved
text cannot forge structure: the source block escapes citation brackets inside
records, and the ids are unguessable per run.

**Verification (`src/noodle/verification.py`)** — the automatic checks on a
draft: repair a broken citation or a quote that is not quite in its source (no
model call), split the draft into numbered sentences, check each one, remove the
unsupported ones, and render the survivor into the answer plus its reference
list. A citation pointing at material the caller may not see raises
`HiddenDataError` rather than being silently dropped.

**Reviewer (`src/noodle/reviewer.py`)** — a separate model call with no tools
that sees the same sources plus the tool trace and returns a structured verdict
on the numbered sentences. Strictness lives here: a factual sentence not
explicitly marked supported counts as unconfirmed, so a rubber-stamp cannot keep
an unsupported claim. A reviewer that asks for a tool marks the draft
`not_checked`.

**Pipeline (`src/noodle/pipeline.py`)** — one run is generate → check → review →
fix rounds, with the fix request going back into the generator's own conversation
so it keeps its history. The best draft wins, not the latest: drafts score on
*reviewed or not*, then *problem sentences*, then *gaps plus false premises*. The
winner is rendered with unsupported sentences removed, gaps mirrored into
`missing`, and a status.

**Router (`src/noodle/router.py`)** — decides fast or heavy and detects live
intent, in a fixed order: an explicit request choice, then a deterministic
keyword rule (word-boundary matched), then an optional injected classifier, then
the default. Keeping this out of the model means the same question always routes
the same way and a test needs no model.

**Response (`src/noodle/response.py`)** — the one builder that turns a finished
run into the 07 response object, shared by `/ask` and the CLI. It appends the
fixed soft-dead-end notice and the as-of line (written by the system, never the
model), emits the `graph_trace` with the exact Cypher and touched node/edge ids,
`freshness`, `proposed_actions`, and the SSE event stream.

**Auth (`src/noodle/auth.py`)** — resolves a request into a `UserCtx`. Identity
arrives as a bearer token (mapped through `NOODLE_AUTH_TOKENS`) or, in
development only, the `X-Noodle-User` header; the claimed person id is then
resolved against the graph so their clearance and team come from their `Person`
node. An unknown user is clearance 0, which leaks nothing.

**Service (`src/noodle/service.py`)** — `build_runtime` assembles config, the two
providers and the graph client; `answer` runs the router and pipeline for one
question and returns `(response, run)`. It raises `GraphDownError` before any
model call when the graph is unreachable, so `/ask` answers `503` and can never
serve a cached or model-only answer.

**API (`src/noodle/api.py`, `server.py`)** — the HTTP surface: `/health`,
`/ask` (JSON or SSE), `/users` (dev), `/graph/subgraph`, `/copilot/context`,
`/copilot/draft` and the token-protected `/ingest`. Graph outages are always
`503`; bad input is `400`; an oversized or invalid live update is `413`/`422`.

**CLI (`src/noodle/cli.py`)** — `python -m noodle "question"` prints the same
response object `/ask` returns, because both call `service.answer`.

**Evaluation (`src/noodle/evaluation.py`)** — loads the golden questions, runs a
keyless offline check of every fact against the dataset, scores a run through the
normal answer function, and runs a vector-only baseline over the same set for
comparison. `scripts/run_eval.py` drives it.

**Copilot (`src/noodle/copilot.py`)** — the operator surface: `context` gathers
owners, decisions, documents, dependencies and stale warnings for one node by
calling the query library (so every row is already clearance-filtered), and
`draft_question` turns a draft request into a heavy-mode question so the normal
citation/verification rules apply. A draft is never sent.

**Scripts (`scripts/`)** — `up.sh` brings the graph, schema, dataset and API up
from a fresh checkout; `serve.sh` runs the API; `demo.sh`/`demo.py` run the demo
scenarios; `db.sh` manages local FalkorDB; `check.sh` is the build gate
(compile, lint, unit suite, config/RBAC/name/tracked-file guards, `queries.md`
drift, offline eval); `run_eval.py` runs the evaluation; `gen_queries_md.py`
regenerates `queries.md` and checks it is current.
