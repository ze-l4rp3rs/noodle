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
scripts/db.sh up        # FalkorDB on localhost:6379, browser UI on http://localhost:3000
scripts/db.sh ingest    # loads data/synthetic/dataset.json (or any folder/file)
scripts/check.sh        # compile, lint, unit suite, config guards
scripts/db.sh down      # clean exit (keeps data); `scripts/db.sh reset` also removes data
```

FalkorDB is the product's graph database: there is no in-memory copy of the data
and no cached answer served when it is down.

### Ingestion

Ingest either a structured dataset `.json` file or an entire folder of source files
(including PDF, DOCX, XML, Markdown, and plain text extracted via `xberg`):

```bash
scripts/db.sh ingest [path/to/data]
python scripts/ingest.py [path] --dry-run   # test reconciliation without writing
```

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
