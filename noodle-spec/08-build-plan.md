# Build plan

15 steps. Each ends with a working system and a "done when" test. Order matters. From step 5 on, tests use
simulated models; no test calls a real model.

**Builder protocol per step:** read the cited files, write the tests first, implement, run
`scripts/check.sh`, commit (with the `AI-Assisted:` trailer), update `DECISIONS.md` if you deviated, stop.
Do not start the next step until "done when" passes. Hackathon pacing: steps 1 to 5 day 1, 6 to 11 day 2,
12 to 15 day 3 (freeze features midday on the last day).

| # | Step | Build | Done when |
|---|---|---|---|
| 1 | Skeleton and FalkorDB | Repo layout, `.gitignore` (secrets, `runs/`, generated files), `docker-compose.yml`, graph client (read-only `ro_query`, separate ingestion-only write path), `ensure_schema()` per 02, `AI_DISCLOSURE.md` stub | `scripts/up.sh` starts FalkorDB healthy and `ensure_schema()` is idempotent |
| 2 | Schema module and dataset | `schema.py` as the single source (02), synthetic dataset with every trap in 04, 10-node hand fixture for early work, dataset checks | Counts match, all references resolve, every trap present |
| 3 | Ingestion | Plan-then-write, validation, FalkorDB batches, derived `current` flags, `meta:sync`, change report (04) | Ingest loads every record. Second run reports all unchanged and writes nothing. Everything written appears in the schema description |
| 4 | Query library with RBAC | Q1 to Q13 (03), `UserCtx`, `QueryResult`, hidden twins | Each query passes against a live FalkorDB loaded with the dataset, for a clearance 0 and a clearance 3 user. Red-team RT2, RT3, RT9 pass at query level |
| 5 | Tools and evidence | Tools in 03, evidence conversion, stable refs, traces | Each tool passes with the recording graph client (parameterised, refs, failures fail) and against the real graph |
| 6 | Provider interface and tool loop | `complete()`, fake provider, bounded loop, unknown tool / bad args / tool failure reported to the model, reuse of identical calls, forced final answer | A scripted model that asks for time gets it. An unknown tool is reported, not crashed |
| 7 | Sources, citations, checks | Source registry with random ids, marker escaping, citation format, quote matching, repair, sentence checks (05 step 2) | A quote one letter off is repaired with no model call. An invented sentence is removed and listed |
| 8 | Reviewer and fix rounds | Reviewer with R1 to R7, structured verdict parsing, fix rounds, best-draft score, removal, statuses | A rejected draft is fixed in round 2 with history intact. Leaked reasoning never reaches the answer. Reviewer tool call marks `not_checked` |
| 9 | API and response | `/ask`, `/health`, response builder (07) shared with CLI, SSE events, `graph_trace`, `references[].freshness`, `missing` mirrors the gaps | CLI and API return the same response for the same simulated run. A DB outage gives `503` |
| 10 | Router and freshness guard | Fast/heavy router, `get_freshness`, live-intent detection, fixed as-of line, live-claim check (05, 06) | A "what happened today" question returns the as-of line with no same-day claims. Heavy-only tools are unavailable in fast mode |
| 11 | Soft dead-end end to end | Notice appended by the system, `access_notices`, hidden-check in pipeline, dev user switcher, auth middleware | Intern vs VP golden pair differs as specified. All 12 red-team prompts pass |
| 12 | Copilot and actions | `/copilot/context`, `/copilot/draft`, `draft_followup` (execute nothing), claims in drafts cited or flagged | Selecting a node returns decisions, owners, stale warnings. A draft cites graph records and flags unbacked claims |
| 13 | Incremental and live updates | Fingerprint diffing, origin-based removal, prune, confirmed rebuild, token-protected `/ingest` (04) | Editing one record changes exactly one node. Superseding a decision flips the old flag and the answer changes |
| 14 | Evaluation | Golden questions, offline fact check, eval runner, **vector-only baseline** run on the same set (09) | Offline check passes. Runner scores a fake model correctly. Report shows graph agent vs baseline |
| 15 | Submission | Root README with graph data model, generated `queries.md`, `EXPLAIN.md`, `AI_DISCLOSURE.md` complete, demo script, deploy | Fresh checkout goes from `scripts/up.sh` to a working `/ask`. Review checklist (10) passes |

## Demo script the build must support

1. Same question as intern and VP: intern gets the soft dead-end and the contact, VP gets the full answer.
2. "Is decision X still current?" walks the supersession chain.
3. "Who should I talk to about Y?" ranks people with the items behind each score.
4. "What happened today?" returns the as-of line.
5. Draft an update to stakeholders of Z: heavy mode, graph context, cited, nothing sent.
6. Show the executed Cypher and highlighted nodes for each answer.
