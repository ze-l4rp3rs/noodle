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

## Step 6 — provider interface and tool loop

### D6.1 One interface, one service call; the loop runs the tools

`Provider.complete(messages, *, tools, system, model, max_tokens, temperature)`
makes exactly one request and returns a `Completion` (text, tool calls, model,
stop reason, usage, and separately `reasoning`). Running tools stays in
`noodle.loop`. Every adapter shares one neutral message shape
(`Message(role=user|assistant|tool, text, tool_calls, tool_call_id, name)`) and
converts it to its own wire format.

Reasoning the model returns separately is parsed into `Completion.reasoning`
and never enters the answer text or the conversation (05 section 1). Wiring a
provider's own reasoning budget (Anthropic extended thinking) is left for the
API step; the field is already carried and dropped.

### D6.2 Retries, output-cap reduction and provider selection

A short rate limit (`RateLimited`) is retried up to `max_retries`; a per-minute
output cap (`OutputCapExceeded`) halves a remembered `cap_scale` (floor 0.25)
and retries, so later calls start from the reduced cap. Each adapter's transport
wraps SDK exceptions and translates 429/overloaded text into `RateLimited` and
output-token-quota text into `OutputCapExceeded`; anything else is re-raised.
A test drives translation and retry through the injected transport.

Selection (`select_provider`) follows 05: one key use it; several keys and a
terminal ask; several keys and no terminal use the configured default; none
fails listing the options. A provider entry's `key_env` must match an env-var
name pattern, so pasting the actual key is rejected as a config error (07).

### D6.3 The bounded loop: reported failures, reuse, a forced final answer

A tool round is one model call that requests tools. The loop's guarantees:
text written alongside a tool request is discarded; an unknown tool, malformed
arguments and a tool failure are reported back to the model as a tool message
and never become a source (04); an identical call (same tool, same arguments) is
answered from the first result and marked `reused`, so the graph is touched
once; after `max_tool_rounds` (6, 05 section 8) the next call is made with no
tools at all and `forced` is set. `LoopResult` carries the draft, every
completion, the evidence, the merged hidden twin, the traces and a
`ToolCallRecord` per call (tool, args, status, error, rows, sources, node ids,
edge ids, duration, reused) — the 05 section 7 run log, with hidden content
never in a record (A4).

### D6.4 Config grows providers, modes and generation (07)

`noodle.yaml` now carries `providers` (default + per provider key env var and
generator/reviewer model), `modes` (fast/heavy: models, tool set, tool rounds,
fix rounds, timeout) and `generation` (max tokens, temperature). All three are
optional with defaults, so earlier configs still load. `Settings` exposes typed,
read-only views; the router (step 10) consumes `modes`.

### D6.5 Provider libraries are optional extras, imported lazily

`anthropic`, `google-generativeai` and `openai` are optional extras and are
imported only inside a default transport, so importing `noodle.providers` never
pulls in a library for an unused provider; a test asserts none is imported.
Unit tests never call a real model: adapters run through injected transports.

## Step 7 — sources, citations and automatic checks

### D7.1 The citation format is self-defined (`message.txt` is absent)

The spec extends "the sample in `message.txt`", but that file is not in the
repo, so the format is pinned by 05 section 1 and the 07 sample: each fact is
followed by `[[<8-hex-id>]]` and the exact words it rests on in straight or
typographic quotes, e.g. `The owner is Omar. [[09a121fe]] "The rollout owner is
Omar Diaz."`. The marker stays in `answer`; the quotes move into
`references[].excerpt`/`quotes` when the answer is rendered. A source id is
random per run (`secrets.token_hex(4)`), unique within the run, and cannot be
guessed from retrieved text. `SourceRegistry` dedupes by the record's own ref
(or, for a ref-less item, kind/text/date/label), so two tools reporting one
record yield one reference; an injectable rng makes the ids deterministic in
tests.

### D7.2 What quote matching forgives, and the repair threshold

Figure 3 of 05: case, whitespace, typographic quotes and dashes are forgiven,
**and nothing else**. `normalize` maps curly quotes/apostrophes and en/em dashes
to ASCII, collapses whitespace and case-folds. An ellipsis (`...` or `…`) may
skip words inside a quote; the remaining fragments must appear in order. Repair
replaces a quote that is not in its source with the most similar passage of
about the same word length when `SequenceMatcher` similarity >= 0.9
(`DEFAULT_REPAIR_SIMILARITY`), taking the passage in the source's own words; a
one-letter change in a normal-length quote clears 0.9, which is the step's
done-when, and no model is involved. `repair_citations` closes a citation with a
missing/doubled bracket (`[[id]`, `[id]]`, `[id]`).

### D7.3 A trailing citation belongs to its fact

A citation is written after the fact it supports, so the sentence splitter keeps
a marker (and its quote) that follows a full stop with the sentence it closes,
then resumes at a new sentence. Quotes, decimal/version dots, single-letter
initials, abbreviations and dots inside a URL/identifier never end a sentence.
Headings and the lines under the fixed "Not in company records" heading are
labelled `heading`/`gap` and need no citation; the gap lines become `missing`.
The same bracket rule applies to the split: dots inside `[[...]]` never split.

### D7.4 Failure kinds, and leaked reasoning means meta-reasoning

`check_draft` returns one `Problem` per failing sentence: `unknown_source`,
`quote_missing` (only when quotes are required), `quote_not_in_source`,
`no_citation`, `plain_source` (a `kind:id` written as text), `fake_link` (a
markdown link or url that is not a cited source's url), `leaked_reasoning`,
`live_claim` (a same-day word with no source dated the sync day). "Leaked
reasoning" is the explicit meta-reasoning leakage 05 section 2.3 names
("chain of thought", "as an AI", "let me think", ...); the softer inference
words ("likely", "seems", ...) are left to the reviewer's R1/R2 (06), since the
mechanical pass must not drop a sentence that quotes a record using them.

### D7.5 Rendering, and hidden material fails the run

Surviving sentences are rendered with their quotes stripped; references are
numbered by first appearance, carry the record's own graph id as `nodeId` (so
two chat messages differ), `type` mapped to 07's list (`connection` -> `role`,
`team` -> `project`), and `freshness` from the evidence flag with the
supersession note. If no fact sentence survives, the answer is the fixed
`NO_ANSWER` message with no references; `status` is `verified` only when nothing
was removed and nothing is missing, else `partly_verified`. Any citation whose
ref the caller may not see raises `HiddenDataError` and fails the run (06, step
2.4) rather than being silently dropped. `verification` imports no provider or
loop module (a test asserts it in a fresh interpreter).

## Step 8 — reviewer and fix rounds

### D8.1 The tool loop must show each source's run id

Step 6's loop showed the model the evidence's record `ref`; step 7's citation
format needs the run-random source id. The bridge belongs to this step, because
the reviewer and fix rounds need a citable draft. `run_tool_loop` now takes an
optional `SourceRegistry`, registers every evidence item a tool returns, and
puts that item's `source_id` in the JSON the model is shown (the `ref` stays for
the record identity). `LoopResult` carries the registry, so one registry spans
all tool rounds and all fix rounds; the generator cites `[[source_id]]`. This
keeps identity (the record's own id) and citation (the run id) separate, as 03
and 07 require. `loop` importing `sources` creates no cycle (`sources` ->
`tools`).

### D8.2 The reviewer: separate call, no tools, tolerant verdict, strict default

`reviewer.review` makes one provider call with `tools=None` and the R1 to R7
instruction, showing the source block, the tool-call trace (tool, args, status,
sources or error) and the draft's numbered sentences. The verdict is JSON; the
parser scans for a balanced `{...}` object (strings respected), so prose around
it is tolerated, and accepts string numbers/booleans. One retry is made when the
reply is unusable; a verdict that judges no statement (`sentences` empty/missing)
counts as unusable, not as approval. If the reviewer asks for a tool, the call
returns `None` at once (no retry) and the draft is `not_checked` (05 section 3).
Strictness: any factual sentence the reviewer did not explicitly mark supported
becomes a `reviewer_unjudged` problem, and a rejected one carries the reviewer's
reason as `reviewer_unsupported`. Headings and gap lines are not judged.

### D8.3 Fix rounds continue the generator's conversation; removal has one home

`pipeline.run_answer` runs the generator loop, checks and reviews a draft, and —
while problems, unanswered parts or false premises remain and the budget is not
spent — appends one user message with only the problems (with sentence numbers),
the unanswered parts and the uncorrected premises, plus the instruction to copy
every other statement word for word. The next loop starts from that same
conversation, tool exchanges included, so history is intact (05 section 4).
Reviewer verdicts become `Problem`s and are handed to `verification.verify` via a
new `extra_problems` argument: mechanical and reviewer removals share one
renderer, one `unverified` list and one `freshness` pass, so a sentence is never
removed twice.

### D8.4 Best draft, statuses and the missing list

Drafts are scored `(reviewed_or_not, problem_sentences, unanswered + false
premises)`; `min` keeps the earliest on a tie, so the best wins, not the latest
(05 section 5). `rounds` is the number of drafts reviewed and `fix_rounds` the
fix requests sent. Status: `not_checked` when the reviewer failed (or the run
would time out, added in step 9), `partly_verified` when a sentence was removed,
something is missing or a premise was corrected, else `verified`. `approved` is
true only for a reviewed draft with no problem sentences, no unanswered parts and
no false premises. `missing` is the rendered gap list plus the reviewer's
`unanswered`. `false_premises` becomes `premise`.

### D8.5 Instructions live in `prompts.py` until config carries them

07 lists an `instructions` config group (generator, fix request, reviewer), but
the config parser does not read it yet. The three instructions and the retry
line are module constants in `prompts.py`; `run_answer`/`review` accept overrides
so step 9 can wire config without touching this logic. None of the text contains
a dataset name (check 6 passes).

### D8.6 Plain-source detection ignores quoted source text

`_has_plain_source` scanned the whole sentence, so a quote that legitimately
contains a `kind:id` (common: a role sentence names its entity) was flagged
`plain_source` and removed — a false positive that would break real runs. The
check now scans `_strip_quotes(sentence.text)`: only the model's own prose, since
the quoted source is already verified verbatim. A `kind:id` written as prose is
still flagged; a quoted one is not.

## Step 9 — API and response

### D9.1 One response builder; the CLI and `/ask` share the service

`noodle.response.build_response` is the only renderer of the 07 response object,
and `noodle.service.answer` is the only run (generate → check → review → fix →
build). The CLI (`noodle.cli`) prints exactly the dict `/ask` returns, so the
same run yields the same response through either entry point (the step's
done-when). `answer` returns `(response, run)`: the API needs the run to emit the
SSE stream, and the CLI ignores it. Two runs never share citation ids
(`secrets.token_hex` per run), so the parity test and `check.sh` item 3 seed the
`SourceRegistry` rng and strip the measured timings (`processingTimeMs`,
`graph_trace[].duration_ms`) before comparing.

### D9.2 `graph_trace` is per Cypher statement, recorded by the loop

07 wants the exact Cypher a tool ran. A tool issues one to several statements, so
`ToolCallRecord` gained a `queries` list of redacted per-statement summaries
(`cypher`, `params`, `node_ids`, `edge_ids`, `rows`), populated on both the run
and the reuse paths in `noodle.loop`. `build_response` flattens one `graph_trace`
entry per statement, carrying the call's `duration_ms` and `reused`. Params stay
redacted (`clr` and friends never leave the query layer, A4) and hidden node ids
never appear — only what the visible query returned.

### D9.3 SSE is reconstructed from the finished run, not streamed live

The pipeline is synchronous, so the step-9 stream is a faithful reconstruction of
the 07 events (`router`, `tool_call`, `tool_result`, `draft`, `review`, `answer`)
from the completed run; `/ask` still runs the whole answer first. Live,
incremental emission belongs with the router (step 10), which is what actually
decides the mode and the tool rounds. The UI contract is identical either way, so
it can be built against this stream now.

### D9.4 A graph outage is 503, checked before any model call

`service.answer` probes the graph (`Runtime.graph_up`) before the first provider
call and raises `GraphDownError`; `/ask` maps it (and any `GraphError`) to `503`
with `{status:"down", graph:"down"}`. `GET /health` reports the same probe. This
makes "never a cached or model-only answer" (07) structural: the provider is not
reached when the graph is down.

### D9.5 Dev identity is the `X-Noodle-User` header; full auth is step 11

Until the auth middleware and `/users` land (step 11), `/ask` reads the dev header
`X-Noodle-User` as the caller's Person id (default clearance, 1) and treats an
absent header as anonymous (clearance 0, which leaks nothing). In production
(`NOODLE_ENV=prod`) the header is ignored. `Authorization: Bearer` mapping to a
Person is step 11.

### D9.6 Deferred fields keep the 07 shape complete

`freshness.as_of` is read from the run's own `get_freshness` trace when the model
called it, and `live_intent` is `false` until the router sets it (step 10); the
service does not yet force a freshness call. `proposed_actions` is derived from
`draft_followup` tool calls (empty in fast mode, which has no such tool) with
`requires_confirmation` always `true`; step 12 refines draft sources.
`access_notices` uses the access owner's person id as `contact` — 06 explicitly
allows returning `access_owner_id` — and falls back to "your administrator" text
when there is no owner; resolving the id to a display name is step 11. Every
field is present with the documented type now, so clients are not built against a
shifting schema.

### D9.7 `check.sh` item 3 is now a real simulated run

`scripts/check_simulated_run.py` answers one question through the CLI path and
through `/ask` with two identical deterministic runtimes and asserts the
responses match and that a one-letter-off quote was repaired from the source. It
replaces the step-1 placeholder skip in `scripts/check.sh`.

### D9.8 `provider_from_settings` builds the real adapters

`noodle.providers.provider_from_settings(name, entries)` maps a configured
provider name to its adapter (anthropic / google / openai / groq), reading the key
from the entry's `key_env` at call time; the key is never stored in config (07).
It is used by `service.build_runtime`, keeping tests free to inject a
`ScriptedProvider` and never construct a real client.
