# AI disclosure

Every commit made with an AI assistant carries a trailer naming the tool:

```
AI-Assisted: <tool name>
```

## What this file records

For each build step: which assistant wrote what, and what a human must be able
to explain live. Fill this in as the build proceeds.

| Step | Assistant | What it produced | Human to explain live |
|---|---|---|---|
| 1 | opencode (big-pickle) | repo skeleton, config, graph client, schema DDL, scripts | `scripts/up.sh`, `ensure_schema()`, the read/write split |
| 2 | opencode (big-pickle) | `schema.py`, synthetic dataset + generator, hand fixture, dataset checks | the schema as single source, the planted traps |

## Statement

All data in this repository is synthetic. Names, emails, documents, tickets,
decisions and chat messages are fabricated for the demo and do not describe real
people, companies or systems. No real mail, drive or chat was loaded.

AI assistants wrote code and synthetic data under human direction. The human
team owns the design decisions and can explain every query, the schema and the
pipeline (see `EXPLAIN.md`).
