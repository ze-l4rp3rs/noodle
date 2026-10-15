# Noodle: build, test and review spec

Noodle is the backend of a company-brain agent. People, teams, projects, tickets, documents, decisions,
chat messages and recorded changes live in **one FalkorDB graph**. The agent answers from that graph,
every fact carries an exact quote from a specific record, and what a user may see is decided **inside the
Cypher**, not in a prompt. Track: **Company Brain**.

## How AI agents use this spec

| Role | Job | Reads |
|---|---|---|
| **Builder** | Implements one build step at a time, tests first | 08, then the file each step cites |
| **Tester** | Writes and runs fakes, unit, integration, golden and red-team tests | 09, 06 |
| **Reviewer** | Audits a finished step or the whole repo against the checklist, outputs a verdict | 10, then everything |

Protocol for every step: read the cited spec files, write tests, implement, run `scripts/check.sh`, commit
with an `ai:` trailer (see Submission), stop. If the spec is ambiguous or two files disagree, do **not**
guess: write the question and your chosen default to `DECISIONS.md` and continue with the default.

## Files

| # | File | Covers |
|---|---|---|
| 01 | README | This overview, architecture, rules, resolved conflicts |
| 02 | graph-schema | Nodes, edges, derived flags, clearance, FalkorDB indexes |
| 03 | queries-and-tools | The Cypher library, the tools built on it, evidence format |
| 04 | ingestion | Dataset, plan-then-write, incremental updates, synthetic traps |
| 05 | answer-pipeline | Router, generator, checks, reviewer (reflexion), fix rounds |
| 06 | rbac-and-guardrails | Clearance, soft dead-ends, freshness guard, security, red-team |
| 07 | api-and-config | Endpoints, response contract, streaming events, config, bring-up |
| 08 | build-plan | 15 ordered steps, each with a "done when" |
| 09 | testing | Fakes, test matrix, golden questions, eval, `check.sh` |
| 10 | review-checklist | Audit checklist, verdict format, regression items |

## Stack (default assumption; change this table if you change the stack)

| Layer | Choice |
|---|---|
| Language | Python 3.11+ |
| API | FastAPI, SSE for streaming |
| Graph | FalkorDB (Docker locally, FalkorDB Cloud for deploy) via the `falkordb` client |
| Models | Pluggable providers behind one interface (Anthropic, Google, OpenAI-compatible/Groq) |
| Tests | pytest, `@integration` marker for tests that need a live FalkorDB |

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

| Component | Responsibility |
|---|---|
| Router | Chooses fast or heavy model config per request |
| Answer workflow | Draft, checks, review, fix rounds, best-draft choice, logging |
| Tools | Thin wrappers over query functions; return evidence plus a graph trace |
| Query library | The only place Cypher lives. Every function takes the user context |
| Verification | Source registry, quote matching and repair, sentence checks |
| Ingestion | The only write path to the graph |
| Run log | Every step of every run, with secrets and hidden data redacted |

## Non-negotiables

1. **The graph does real work.** Remove FalkorDB and Noodle must fail, not degrade. No in-memory copy of
   the data, no cached answers served when the database is down.
2. **Every fact is cited with an exact quote.** Automatic checks verify it, a reviewer judges the rest.
3. **No assumptions.** If the records do not say it, the answer says "not in company records". An
   inference stated as fact is a defect.
4. **Errors are never sources.** A failing tool fails; it never returns text a model could cite.
5. **Access is enforced in Cypher.** The model never sees what the user may not see.
6. **No live information.** Data is only as fresh as the last sync, and the answer says so.
7. **Read-only agent.** Only ingestion writes. Actions are drafted, never executed.
8. **Generic by design.** No company, person or record identifier in logic, schema, config or prompts.
   Such names live in data, tests and golden questions only.
9. **Synthetic data only** (hackathon rule 6). Never load real mail, drive or chat.

## Conflicts between the source files, and how they are resolved

| Topic | Source A | Source B | Decision |
|---|---|---|---|
| Schema | `FALKORDB_SCHEMA.md` draft (SlackMessage, Email, Channel, WORKED_ON) | design docs (one Chat message type, Change records, current flags) | **Design docs win**: they match the sample response. From the draft keep only: optional `Chunk` nodes, `url`, `tags`, `embedding`, project `DEPENDS_ON` |
| Index syntax | draft: `CREATE INDEX ON :L(a,b)` | FalkorDB current: `CREATE INDEX FOR (n:L) ON (n.a)` | Use the current syntax (02) |
| Raw Cypher tool | design docs allow model-written read-only queries | RBAC must be enforced in Cypher | **Removed.** A model-written query can omit the clearance filter. Natural language maps to fixed query shapes only |
| Response shape | design docs (api.md) | `message.txt` sample | Sample shape plus the additions in 07 |
| Reflexion | generator/reviewer pipeline | "reflector sees all tool calls" | Same thing. The reviewer is the reflector (05) |
| Provider | draft names one provider | design docs: three adapters | Pluggable adapters, one interface |

## Submission artifacts (hackathon rule 10 to 13)

Generate and keep current: root `README.md` (setup and graph data model from 02), `queries.md` (derived
from 03, every Cypher the product depends on), `AI_DISCLOSURE.md` (which assistant wrote what; every AI
commit carries an `AI-Assisted: <tool>` trailer), `EXPLAIN.md` (one paragraph per query and component so a
team member can explain it live), demo script, demo video, live deployment or full local instructions,
and the track named in the submission.
