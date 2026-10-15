# Noodle

Backend of a company-brain agent. People, teams, projects, tickets, documents,
decisions, chat messages and recorded changes live in **one FalkorDB graph**. The
agent answers from that graph, every fact carries an exact quote, and what a user
may see is decided inside the Cypher.

Track: **Company Brain**.

> Status: early build. This README is completed at step 15 (setup, graph data
> model, demo). The spec lives in `noodle-spec/`.

## Local bring-up

```bash
docker compose up -d falkordb      # FalkorDB with a health check
scripts/up.sh                      # waits for health, runs ensure_schema()
scripts/check.sh                   # compile, lint, unit suite, config guards
```

FalkorDB is the product's graph database: there is no in-memory copy of the data
and no cached answer served when it is down.

## Layout

| Path | What |
|---|---|
| `src/noodle/schema.py` | single source of truth: labels, relationships, properties, indexes, clearance |
| `src/noodle/graph.py` | read-only `ro_query` client and the ingestion-only write path |
| `src/noodle/config.py` | typed read-only settings from `noodle.yaml` |
| `data/synthetic/` | synthetic dataset (traps per spec 04) |
| `fixtures/` | 10-node hand fixture for early tests |
| `scripts/` | `up.sh`, `check.sh` and the check helpers |
| `noodle-spec/` | the build spec |
