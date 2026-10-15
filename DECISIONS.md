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

