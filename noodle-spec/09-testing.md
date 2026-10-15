# Testing and evaluation

## Rules

- **Unit tests never call a real model and never need a database.** Everything is simulated.
- **Integration tests** (`@integration`) need a live FalkorDB (Docker) loaded with the synthetic dataset. Still no real model.
- **Evaluation** (the runner) needs real keys and a loaded graph. It is not part of `check.sh`.
- Every test runs isolated: temp folder, no API keys or overrides from the environment, never reads a
  developer's secrets file, fresh settings. A test may change settings for its own duration only.
- Scores are reported, never committed.

## Simulations

| Fake | Use |
|---|---|
| Scripted model | Returns queued replies, records every request |
| Citing model | End to end: generator cites real source ids it was shown, reviewer approves every sentence |
| Recording graph client | For tools: records each query and params, answers with prepared rows |
| In-memory graph | Understands ingestion reads and writes so updates, removals and reports can be checked |
| Fake provider services | Canned responses per adapter |
| Fake embedder | Deterministic vectors from a hash of the text |

## Test matrix

| Layer | Must test |
|---|---|
| Schema | Every label, relationship and property written appears in the schema description; DDL idempotent |
| Ingestion | Counts derived independently from the dataset; authoring notes never stored; loading twice changes nothing; editing one record changes exactly one node; superseding flips exactly the right `current` flags; bad reference fails with the entry named and the graph untouched; failed run does not advance `last_synced_at` |
| Query library (integration) | Each Q has the clearance predicate (static check on the text); parameterised; correct rows on the dataset; hidden twin returns only count and owners; **A2** hidden intermediate hop; **A3** ranking not revealing hidden items; Q9 post-filter caveat; Q5 with a hidden superseder |
| Tools | Parameterised, never contain user text; evidence text and refs right; refs are record ids (two chat messages in one channel get different refs); failures fail; no tool accepts a user or clearance argument |
| Tool loop | Results shown back, forced final answer after the round limit, unknown tool, malformed args, reuse of repeated calls, leaked reasoning removed |
| Verification | Quote-match edge cases (case, spacing, typographic quotes, dashes, and nothing else); repair threshold 0.9; each sentence check; a full run where a misquote is repaired and an invented sentence is dropped |
| Reviewer | Verdict parsing with surrounding text; one retry; unjudged sentence counts as unconfirmed; tool call marks `not_checked`; inference sentence ("in progress" with no source stating it) is rejected |
| Router | Explicit mode wins; keyword rules; default fast; heavy-only tools not exposed in fast |
| Freshness | Live-intent detection table (positive and negative); as-of line present; same-day claim removed; sync time from the graph |
| Providers | Conversation conversion, retries, output-cap reduction, role alternation, tool-choice mapping |
| API | Response schema (validate with a JSON schema); CLI and API identical for one run; `503` when DB down; dev identity header disabled in prod; `/ingest` token, size, validation |
| Red team | All 12 in 06, with the hidden-string search over response and logs |
| Graph dependence | **Graph-off test:** stop FalkorDB, `/ask` returns `503`, no answer text. Proves hackathon rule 3 |

## Golden questions

Format (YAML, one file per question under `eval/golden/`):

```yaml
id: G-001
areas: [freshness, ownership, provenance]
user: person:intern        # who asks (clearance matters)
question: "..."
context_file: null
facts: [ ... ]             # what the offline check verifies in the dataset
checks:
  must_match: ["2026-10-01"]
  must_not_match_first_sentence: ["in progress"]
  first_sentence_must_match: []
  premise_corrected: null
  gap_stated: ["completion"]
  must_cite: ["ticket:PLAT-882", "chat:..."]
  must_not_cite: []
  must_not_leak: []        # hidden strings
  status: verified | partly_verified
```

### G-001 (derived from the sample in `message.txt`)

Question: "What is the status of the auth migration?"

| Check | Expectation |
|---|---|
| Current target date | Answer states 2026-10-01 and says it supersedes 2026-08-15; 2026-08-15 is not stated as current |
| Owner | Current rollout owner is the reassigned person, not the original assignee. The original assignee is named as the contact for the old system's internals |
| Roles | Author of the RFC, approver, assignee, reviewer are not confused |
| Gap | Completion percentage and the dependency ticket's status listed under "Not in company records" **and** present in `missing` |
| No inference | The headline does not say "in progress" unless a record states it (the sample answer does, with no source: this is the regression case) |
| Refs | The two chat messages and the two ticket records have **different** `nodeId`s; each reference has `freshness`; the RFC reference is `superseded` for the date |
| Trace | `graph_trace` shows the decision or timeline query that ran |

### Required coverage

| Area | Question type |
|---|---|
| Ownership | current owner of a reassigned project; owner vs assignee |
| Expertise | who knows X, with the items behind the ranking |
| Provenance | why was this decided, with documents and dissent |
| Currency | still current? chain walk; only-superseded sources answer |
| Stale doc | a document made stale by a later decision |
| Timeline | value of a field on a past date |
| False premise | question assumes something the data contradicts |
| Missing | a figure that does not exist |
| Conflict | two current decisions on one project |
| Impact | team loses people |
| Connection | how are A and B related, including a hidden-hop case |
| RBAC pair | the same question as intern and VP |
| Live | "what changed today" |
| Action | draft an update to stakeholders (heavy mode, nothing sent) |
| Context | a question about a supplied context file |

At least 20 questions. **Offline fact check:** every fact a question relies on is confirmed against the
dataset (field value, latest change, value on a date, dissent exists, message exists, document dated after a
decision, evidence lists, people computed from a decision, absence of an amount, blocked items and owners).
It knows only general fact kinds so it stays company-agnostic. **When a fact fails, fix the golden question, not the data.**

## Evaluation runner

- Runs every golden question (or a chosen subset) with a chosen provider and mode.
- Prints a pass/fail table with failed checks, score per area, status distribution, mean latency and rounds.
- Fails below a chosen threshold.
- **Baseline:** also runs a vector-only RAG over the same documents (no graph, no flags, no RBAC) on the same
  questions and prints both scores side by side. The gap is demo material.

## `scripts/check.sh` (run before any change is accepted)

1. Everything compiles; lint finds no undefined names.
2. Every module imports.
3. A simulated run through the CLI and the API confirms a misquote is repaired.
4. The unit suite passes. (Integration suite runs when FalkorDB is reachable, and in CI.)
5. Compose and start-up config are valid and every path they mention exists.
6. **No company-specific names** (names from the dataset) appear in `src/`, schema, instructions or config.
7. **Every query text in `queries.py` contains the clearance predicate** or is in the explicit allowlist (`ensure_schema`, `meta`).
8. No secrets or generated files are tracked.
9. `queries.md` is up to date with `queries.py`.
