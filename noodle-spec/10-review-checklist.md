# Review checklist

The Reviewer audits one step or the whole repo. Run `scripts/check.sh` first. If it fails, stop and report
that. Then walk every section, citing `file:line` evidence for each item. Do not mark pass without evidence.
Do not fix code; report. Severity: **blocker** (must fix before submit), **major**, **minor**.

## A. The graph does real work (hackathon rule 3)

| # | Check | Severity if failed |
|---|---|---|
| A1 | Every answer path starts from a query function in `queries.py`; no tool reads data from anywhere else | blocker |
| A2 | No in-memory copy of the dataset, no cache that serves answers when FalkorDB is down | blocker |
| A3 | The graph-off test exists and passes (503, no answer) | blocker |
| A4 | Traversal, supersession chain, conflict, impact and path logic are Cypher or a FalkorDB algorithm, not Python loops over fetched nodes | major |
| A5 | Vector search and graph expansion both run in FalkorDB | major |
| A6 | `graph_trace` carries the exact Cypher actually executed | major |

## B. No hallucination, no assumption

| # | Check | Severity |
|---|---|---|
| B1 | Every factual sentence has a citation with a quote, and the quote is in the source (mechanical check) | blocker |
| B2 | No tool error or exception text can become a source | blocker |
| B3 | The reviewer has no tools, sees the same sources and the tool trace | blocker |
| B4 | Reviewer prompt applies R1 to R7 explicitly | major |
| B5 | An answer headline that adds an unstated claim (status, intent, completeness) is removed or fixed | major |
| B6 | Gaps appear under "Not in company records" and in `missing` | major |
| B7 | False premises are corrected | major |
| B8 | Nothing verifiable leaves the fixed "couldn't find" message, not a guess | major |
| B9 | Superseded facts are never stated as current; refs carry `freshness` | blocker |
| B10 | Best draft chosen by score, not latest | minor |

## C. RBAC

| # | Check | Severity |
|---|---|---|
| C1 | Every query in `queries.py` applies `$clr`; paths filter every node; edges need both endpoints visible | blocker |
| C2 | The model cannot supply user or clearance; there is no raw-Cypher tool | blocker |
| C3 | Vector and fulltext search over-fetch before filtering | major |
| C4 | Hidden twins return only a count and owners; API exposes a boolean, not a count | blocker |
| C5 | Notice text is fixed and appended by the system; no hidden title, id, type or count | blocker |
| C6 | Logs, traces, SSE events, `/graph/subgraph` and `graph_trace` contain no hidden data | blocker |
| C7 | "Still current" and "connected" answers handle a hidden superseder or hidden hop (RT8, RT9) | blocker |
| C8 | All 12 red-team prompts pass | blocker |
| C9 | Chunks and embeddings inherit parent clearance; aggregates use visible nodes only | major |

## D. Freshness

| # | Check | Severity |
|---|---|---|
| D1 | `last_synced_at` is written by ingestion and read from the graph, not from the clock at answer time | major |
| D2 | Live intent triggers `get_freshness` and the fixed as-of line | major |
| D3 | Same-day claims are removed without a record dated that day before the sync time | major |
| D4 | A failed ingest does not advance `last_synced_at` | minor |

## E. Security

| # | Check | Severity |
|---|---|---|
| E1 | No secrets in repo, image or logs; config names env vars only | blocker |
| E2 | All graph reads use the read-only call; only ingestion writes | blocker |
| E3 | Every Cypher value is a parameter; labels and relationship types come from the schema allowlist | blocker |
| E4 | Retrieved text cannot close source markers or forge ids; injection test passes | major |
| E5 | `/ingest` off without token, constant-time compare, size cap, validation before write | major |
| E6 | Actions are drafts only; nothing is sent | blocker |
| E7 | Dev identity header disabled when `NOODLE_ENV=prod` | blocker |

## F. Schema and ingestion

| # | Check | Severity |
|---|---|---|
| F1 | `schema.py` is the single source; docs and DDL are generated or tested against it | major |
| F2 | Load twice changes nothing; edit one record changes one node | major |
| F3 | Authoring notes are never stored | blocker |
| F4 | `current` flags are computed from structure only | major |
| F5 | Dataset is synthetic and contains every trap in 04 | blocker |
| F6 | Evidence `ref` is the record's own id, so no two records share a `nodeId` | major |

## G. API contract

| # | Check | Severity |
|---|---|---|
| G1 | Response validates against the JSON schema in 07; CLI and API use one builder | major |
| G2 | `processingTimeMs`, `rounds` are measured | minor |
| G3 | Latency meets the budgets in 05 or the timeout path returns a best draft | major |
| G4 | Answer markdown has no nested bold and no stray headings | minor |
| G5 | `503` on DB outage, `400` on bad input | major |

## H. Tests

| # | Check | Severity |
|---|---|---|
| H1 | Unit tests need no network, DB or keys; integration tests are marked | major |
| H2 | At least 20 golden questions, offline fact check green, baseline comparison runs | major |
| H3 | `check.sh` includes the no-company-names check and the clearance-predicate check | major |
| H4 | Each tool has the four standard tests (03, "Adding a tool") | minor |

## I. Hackathon submission (rules 2, 3, 10 to 13)

| # | Check | Severity |
|---|---|---|
| I1 | FalkorDB is the primary graph database and central to the product | blocker |
| I2 | Public repo; README with setup and the graph data model; `queries.md`; demo video; deployment or full local instructions; track named | blocker |
| I3 | `AI_DISCLOSURE.md` is accurate; AI commits carry the trailer | blocker |
| I4 | `EXPLAIN.md` lets a team member explain each query, the schema and the pipeline live | major |
| I5 | No real, private or restricted data anywhere in the repo or graph | blocker |
| I6 | Submitted before the deadline (Oct 18, 11:59 PM IST), with margin | blocker |

## Regression items from the sample response (`message.txt`)

The sample is a good run with these defects. Each must have a test that would have caught it.

| # | Defect in the sample | Spec |
|---|---|---|
| S1 | Two different chat messages share `nodeId` `chat:#platform-leads`, and two different ticket records share `ticket:PLAT-882`. Identity is channel or ticket level instead of record level, which breaks UI highlighting and dedup | 03, 07 |
| S2 | `missing` is `[]` while the answer lists three gaps under "Not in company records" | 05, 07 |
| S3 | References carry no `freshness`; the RFC target date is superseded by a later message but nothing marks it | 07 |
| S4 | The headline says the migration "is in progress". No cited source says that. This is an inference stated as fact | 05, 06 (R1, R2) |
| S5 | `processingTimeMs` is about 85 s for 2 rounds | 05 (budgets) |
| S6 | Headline has nested bold, producing broken markdown | 07 |
| S7 | `status: verified` despite S2 and S4. The reviewer approved an unsupported headline | 05 (strictness) |
| S8 | No `proposed_actions`, `graph_trace`, `access_notices` or `freshness` fields | 07 |

## Verdict format

```
VERDICT: PASS | PASS WITH FIXES | FAIL
Scope: step N | whole repo
check.sh: pass | fail (output)

Blockers:
- [C5] file.py:42 — notice includes the hidden document title. Fix: build the notice from fixed text only.
Majors:
- ...
Minors:
- ...
Not verifiable (needs live DB or keys): ...
Items passed with evidence: A1 (queries.py:10-80), ...
```

Rules: a single blocker means FAIL. Report what you could not verify rather than guessing. Do not approve
your own code in the same session that wrote it.
