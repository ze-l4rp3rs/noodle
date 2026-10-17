# AI disclosure

Every commit made with an AI assistant carries a trailer naming the tool:

```
AI-Assisted: <tool name>
```

An AI-assisted commit is one where an assistant wrote or edited files. The
trailer `AI-Assisted: opencode` marks those; a commit by a person with no
assistant carries no trailer. `git log --format='%h %s %(trailers:key=AI-Assisted)'`
shows the full attribution.

## Attribution by step

The build followed `noodle-spec/08-build-plan.md` one step at a time, tests
first. For each step: what the assistant produced and what a human must be able
to explain live (see `EXPLAIN.md`).

| Step | Assistant | What it produced | Human to explain live |
|---|---|---|---|
| 1 | opencode (big-pickle) | repo skeleton, config, graph client (read/write split), schema DDL, `up.sh`, `AI_DISCLOSURE.md` stub | the read-only `ro_query` vs the ingestion-only write path; idempotent `ensure_schema()` |
| 2 | opencode (big-pickle) | `schema.py` as single source, synthetic dataset + generator, hand fixture, dataset checks | the schema as single source; every planted trap and where it lives |
| 3 | opencode (big-pickle) | ingestion: plan-then-write, validation, batched writes, derived `current` flags, `meta:sync`, change report | why a second load writes nothing; how `meta:sync` only advances on a real change |
| 4 | opencode (big-pickle) | query library Q1–Q13b (`queries.py`), `UserCtx`, `QueryResult`, hidden twins, per-query RBAC | how `x.clearance <= $clr` filters every node; what a hidden twin is allowed to return |
| 5 | opencode (big-pickle) | tools over the query library, evidence conversion, stable refs, traces, recording graph client | the tool contract: identity from the system, failures raise, `ref` is the record id |
| 6 | opencode (big-pickle) | provider interface, fake provider, bounded tool loop, four standard tool tests | the forced final answer; why an unknown/failed tool is reported, not raised |
| 7 | opencode (big-pickle) | source registry, citation markers, quote matching + repair, sentence checks | how a one-letter-off quote is repaired with no model call; bracket escaping |
| 8 | opencode (big-pickle) | reviewer (R1–R7), verdict parsing, fix rounds, best-draft score, statuses | why the best draft wins over the latest; how a rejected draft is fixed with history |
| 9 | opencode (big-pickle) | `/ask`, `/health`, shared response builder, SSE events, `graph_trace`, CLI | how the CLI and `/ask` render the same run; why a DB outage is `503` |
| — | **ved-in (human teammate)** | multi-format document extraction (`extractor.py`) and ingestion tooling (`8d5db81`) | the extract → Document → graph flow; no AI trailer because no assistant wrote it |
| 10 | opencode (big-pickle) | fast/heavy router, live-intent detection, `get_freshness`, fixed as-of line | the fixed routing order; why "now" needs a word-boundary match |
| 11 | opencode (big-pickle) | soft dead-end end to end, `access_notices`, hidden-check in the pipeline, dev user switcher, auth middleware | the intern vs VP golden pair; why the notice carries no hidden title/id/type/count |
| 12 | opencode (big-pickle) | `/copilot/context`, `/copilot/draft`, `draft_followup`, `GET /graph/subgraph` + Q15, access notices | that a draft is cited or flagged and never sent; how the subgraph stays visible-only |
| 13 | opencode (big-pickle) | incremental diffing, origin-based relationship removal, prune, confirmed rebuild, token-protected `/ingest` | editing one record changes one node; superseding a decision flips the old flag |
| 14 | opencode (big-pickle) | 21 golden questions, keyless offline fact check, eval runner, vector-only baseline | how a fact is confirmed with no model/DB; what the baseline ignores (RBAC, `current`) |
| 15 | opencode (big-pickle) | root `README.md` (data model), `EXPLAIN.md`, this file, demo script (`scripts/demo.sh`), `serve.sh`, `up.sh` → serving `/ask`, `queries.md` drift check | the fresh-checkout path; how `queries.md` cannot drift from `queries.py` |

The synthetic dataset (`data/synthetic/`) and the golden questions (`eval/`) were
generated with assistant help under human direction; every value in them is
fabricated.

## Statement

All data in this repository is synthetic. Names, emails, documents, tickets,
decisions and chat messages are fabricated for the demo and do not describe real
people, companies or systems. No real mail, drive or chat was loaded.

AI assistants wrote code and synthetic data under human direction. The human team
owns the design decisions and can explain every query, the schema and the
pipeline live (see `EXPLAIN.md`).

## Secrets and data hygiene

No secrets are committed: `noodle.yaml` names environment variables, never keys,
and `scripts/check_tracked_files.py` fails the build if a `.env`, key file or
generated `runs/` artifact is tracked. Provider keys are read from the
environment only.
