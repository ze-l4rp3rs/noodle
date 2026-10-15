# Decisions and open questions

Any ambiguity or conflict between spec files is recorded here with the chosen
default, per the builder protocol.

## Step 1 — skeleton and FalkorDB

### D1.1 `schema.py` is created in step 1, not step 2

Step 1 requires `ensure_schema()`, which reads its DDL from `schema.py`. Step 2
lists `schema.py` as its build item. To avoid a missing dependency, the full
schema module lands in step 1; step 2 adds the dataset, the hand fixture and the
dataset checks and verifies the schema is complete.

### D1.2 FalkorDB DDL syntax verified (spec said "unverified")

Probed against a running `falkordb/falkordb:latest` container (graph module
version `42201`, i.e. FalkorDB 4.2.1, on redis 8.10.2):

- `CREATE INDEX FOR (n:Label) ON (n.prop)` — works.
- `CALL db.idx.fulltext.createNodeIndex('Label', 'a', 'b')` — works.
- `CREATE VECTOR INDEX FOR (n:Label) ON (n.embedding) OPTIONS {dimension: 1536, similarityFunction: 'cosine'}` — works.
- `CALL algo.BFS(d, 3, 'REL')` `YIELD nodes` — works (start node is not included in `nodes`).

**Correction / addition:** re-running any of these DDL statements does **not**
silently succeed; FalkorDB raises `... is already indexed`. `ensure_schema()`
therefore treats that specific message as success (idempotent) and re-raises
anything else. This is a real correction to "DDL is idempotent" in 02: it is made
idempotent by the wrapper, not by the engine.

### D1.3 `docker-compose.yml` has only the FalkorDB service in step 1

Spec 07's compose example also defines a `noodle` API service. The API does not
exist until step 9 and its image until step 15, so only `falkordb` is defined
now. The `noodle` service is added at step 15. `scripts/up.sh` starts `falkordb`
explicitly.

### D1.4 Provider-key check in `up.sh` warns, does not fail

`up.sh` is told to check "at least one provider key". Providers arrive at step 6,
so a missing key warns rather than blocks bring-up until then. The strict check
turns on with the provider layer.

### D1.5 Dataset load in `up.sh` deferred to step 3

Step 1's done-when needs only "FalkorDB healthy and `ensure_schema()` idempotent".
`bootstrap.py` reports an empty graph with a note; it loads the dataset once
ingestion exists (step 3).

### D1.6 Clearing of the embedding dimension on an existing vector index

If the configured `graph.embedding_dimension` changes after the vector index
exists, FalkorDB keeps the old dimension and `ensure_schema()` reports the index
as already present. A mismatch check is deferred; the vector index and the
embedding model must be configured together.

### D1.7 `person.manager` and `person.team` are stored as properties *and* `team` also yields `MEMBER_OF`

Spec 02 lists `manager` and `team` as Person properties, while spec 04 derives
relationships from reference fields and 02 says fields that became relationships
are not stored. There is no `MANAGER_OF` relationship type, so `manager` stays a
plain property. `team` stays a display property **and** produces a `MEMBER_OF`
edge (deduplicated against `team.members`).

### D1.8 Reference-field vs typed-list split in the dataset

The dataset stores singular references as record fields (owner, sponsor, lead,
members, assignee, reporter, project, author, approvers, dissent, chat author)
and plural/provenance edges as typed relationship lists in a `relationships`
section (DEPENDS_ON, SUPERSEDES, AFFECTS, DOCUMENTED_IN, MENTIONS, DISSENTS,
SOURCE_FROM, TRIGGERED, BLOCKED, etc.). This mirrors spec 04's "reference fields"
and "relationship lists" sources and keeps both ingestion paths exercised.

## Step 2 — schema module and dataset

### D2.1 The generated dataset is committed

`data/build_synthetic_dataset.py` is a deterministic generator; its output
`data/synthetic/dataset.json` is committed too. Steps 3 (ingestion) and 4
(queries) need the dataset, and step 15's "fresh checkout to a working `/ask`"
should not require an extra generation step. `tests/test_dataset.py::
test_dataset_is_reproducible` rebuilds the dataset in-process and asserts it
equals the committed file, so the two cannot drift. The dataset is data, not a
build artifact, so it is not git-ignored.

### D2.2 `validate_dataset` has relaxed modes for the hand fixture

The synthetic dataset must satisfy metadata, count-range and trap-presence
checks ("counts match, all references resolve, every trap present"). The 10-node
hand fixture is deliberately smaller and has no traps, so `validate_dataset`
takes `require_metadata` / `require_counts` / `require_traps` flags (default
true). The fixture is validated with counts and traps relaxed but reference
resolution always on.

### D2.3 Dissent evidence entry shape

A `decisions.dissent` entry is `{"id": <person-id>, "evidence": [<id>...]}`. The
reference is under a generic `id` key, so `dataset.py` needs no section-to-
singular name map; `schema.REFERENCE_FIELDS` already carries `target_section` and
`evidence=True`. Evidence ids must resolve (04: "Evidence ids that point to
nothing are reported, never silently dropped").

### D2.4 `DISSENTS` is produced from the decision field only

Spec 02 sources `DISSENTS` from the decision `dissent` field ("carries
evidence"). It is therefore **not** also listed in the dataset's `relationships`
section, to avoid two producers for one edge. `SOURCED_FROM` from decision
evidence is derived by ingestion (step 3) from the same field.

### D2.5 Metadata carries `record_counts`; "counts match" is checked against it

`metadata.record_counts` records the intended per-section counts and
`validate_dataset` fails if they differ from the actual section lengths. This
turns step 2's "counts match" into one explicit check on top of the range checks
in `COUNT_RANGES` / `MIN_COUNTS`.

### D2.6 Traps are tagged with the entity ids they rest on

Every trap in `REQUIRED_TRAP_IDS` also lists the dataset ids it depends on;
`validate_dataset` resolves those ids, so a trap cannot silently lose its data
while the id still appears in the list. The "nonexistent-figure" trap has no
entities by design (its point is that no record states the figure).

## Step 3 — ingestion

### D3.1 `meta:sync.last_synced_at` comes from the dataset, not the wall clock

Spec 04 writes the sync time "on success", which would make the graph's
freshness depend on when a test happened to run. Spec 07 fixes the demo value
(`07-api-and-config.md` line 39), and the dataset carries the matching
`metadata.last_synced_at` (`2026-10-15T00:05:00Z`). Ingestion writes the
dataset's time so stored freshness is deterministic; the wall clock is only a
fallback when a dataset omits the field.

### D3.2 A no-change run writes nothing and does not advance the sync

"Ingest is incremental" (04): a second run over an unchanged dataset reports
every record unchanged and issues no writes. The sync node is only rewritten
when something changed, or when the operator asks for it explicitly
(`--touch`), so `last_synced_at` tracks real content, not run count.

### D3.3 Embeddings are deferred to the provider step

The schema has `Document.embedding` / `Chunk.embedding` and a vector index, but
no embedding provider exists until step 6. Ingestion writes Documents and their
text without embeddings; Chunks and vectors are filled in when the provider
lands. The vector index is created empty at `ensure_schema()`.

### D3.4 Ingestion writes through the `IngestWriter`; reads go through `GraphClient`

The single write path (`WRITE_MODULES_ALLOWED` includes `noodle.ingestion`) is
honoured: ingestion issues every write via `IngestWriter`, and reads its current
state via the read-side `GraphClient`. This keeps the write-path isolation scan
in `tests/test_graph.py` green.

### D3.5 Unknown relationship lists are noted, not fatal

A dataset `relationships` key that is not a relationship type in `schema.py`
produces a plan note and is skipped, rather than aborting ingestion. This keeps
a richer hand-authored dataset from breaking bring-up while still surfacing the
omission.


## Step 4 — query library and RBAC

### D4.1 Every query returns a `QueryResult`; the hidden twin is folded in

Each public query returns a `QueryResult` carrying the rows, the exact Cypher,
the redacted params, the node and edge ids touched, and a `HiddenInfo`. The Q13
hidden twin runs alongside the query (not as a separate tool call) so the API
layer can always attach the soft dead-end notice without a second round trip.
`HiddenInfo` carries only a count and the access-owner ids, never a title, name,
text or id of a hidden node.

### D4.2 The hidden twins are allow-listed by name in `check_clearance.py`

Q13 twins deliberately invert the predicate (`clearance > $clr`) to find what
was filtered out, so they cannot contain `clearance <= $clr`. The static check
allow-lists function names starting with `hidden_`; the twins return only counts
and owners, so this is safe. Every other query still carries the predicate.

### D4.3 Q9 uses a visible-only bounded expansion, not `allShortestPaths`

FalkorDB's `allShortestPaths` needs an already-bound pair and, filtered to the
visible subgraph, can miss a real visible path when a hidden node sits on a
shorter route. Q9 instead matches a bounded undirected path and keeps only paths
whose every node is visible (`ALL(x IN nodes(p) WHERE x.clearance <= $clr)`),
ordered shortest-first. This proves A2 does not over-hide: iris-lund and ken-zhao
are visibly connected (5 paths at clearance 1), while wes-novak and freeze-hiring
have no visible path (the only route is through the restricted victor-hale).

### D4.4 A hidden anchor is still reported by the entity/team twins

`hidden_twin_entity` and `hidden_twin_team` count the anchor itself when it is
hidden, so asking (at clearance 0) about a clearance-1 entity raises the soft
dead-end instead of looking like "not found". When the anchor is visible the
twins count its hidden neighbours. Anchors with no `access_owner_id` (clearance
below 2) report a count with an empty owner list.

### D4.5 Q1 searches by keyword until the embedding provider lands

No embedder exists until step 6, so Q1's default path is a fulltext keyword
search with the same RBAC filter and over-fetch (5x limit, A3). A vector path is
implemented and selected when an `embedding` is supplied; it is exercised once
the provider exists. The hidden twin always uses fulltext, matching the default.

### D4.6 `queries.md` is generated from `queries.py`

Spec 03 makes the query file the source of truth and `queries.md` a required
artifact (check.sh item 9). `render_markdown()` walks `QUERY_DOCS` and embeds
each function's source; `scripts/gen_queries_md.py` writes the file, and a unit
test fails if the committed file is stale.
## Step 5 — tools and evidence

### D5.1 Queries return evidence-rich rows; tools convert, never query

Tools never build Cypher (a source-scan test pins it), so the queries that
feed evidence were extended to return the fields citations need: Q4 sources
now carry label/text/date/url per source record, Q8 returns the stale
document's content and metadata, Q9 returns the names along each path, and Q11
returns one row per affected node (id, name, kind) instead of a single list.

### D5.2 `ref` is the record's own graph id; derived statements compose theirs

Evidence refs are the record ids (two chat messages in one channel get
different refs, pinned by a unit test). Role statements combine
`role:{REL}:{person}:{entity}` and connections `connection:{a}|{b}|{index}` —
derived kinds mirroring the spec's role-statement pattern, one per path.

### D5.3 Currency is reported, not guessed, when the superseder is hidden

`trace_decision` folds Q4, Q5, the chain and their twins together. When a
decision is not current and the current head is visible, the tool names it;
when the twin shows a hidden superseder, the tool reports
`currency: "cannot_confirm"` and names nothing (06). A hidden anchor looked up
by id also raises the soft dead-end: the tool merges `hidden_twin_entity(ref)`
alongside the sources twin. Twin counts may double-count a node seen by two
twins; only the boolean is consumed at the API level.

### D5.4 What the model sees never includes hidden info or traces

`ToolResult` carries evidence, traces and the merged hidden twin, but
`to_payload()` (the model-facing shape) exposes evidence, note and payload
only — counts, owners and Cypher stay on the system side (A4). The loop's
reuse marker lives on `ToolResult.reused`, set later by the tool loop.

### D5.5 FalkorDB persistence reload breaks boolean-false scans; Q8 avoids them

After a container restart, Redis reload left the main graph unable to match
`{current: false}` or `= false` (0 rows) while `NOT x.current`, `<> true` and
direct property reads kept working; a freshly written graph matched all forms.
Q8 now filters with `NOT d.current`, which holds on both encodings, and the
main graph was rebuilt (`GRAPH.DELETE` + bootstrap). Ingestion fingerprints
are Python-side strings, so they cannot detect this encoding damage — a
re-ingest reports "unchanged" either way.
