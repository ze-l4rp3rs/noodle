# Ingestion

Ingestion is the only write path. It is generic: a new company needs three mappings (sections to entity
types, reference fields to relationships, relationship lists to relationship types).

## Dataset shape

- One section per entity type, each a list of records with an `id`.
- Typed relationship lists.
- Optional per-record `clearance` and `access_owner_id`.
- General metadata, **never ingested**.

The dataset is **synthetic**. Never load real mail, drive or chat.

## Plan first, then write

Plan everything before writing anything, so a bad reference fails without touching the graph.

1. **Records:** one per dataset record, plus Change records from history, plus Chunks if enabled.
2. **Relationships:** from reference fields, from relationship lists (unknown lists are reported and
   skipped), and from provenance (history, decision evidence, dissent, document supersession, names in chat).
3. **Validation:** every endpoint exists with the expected label or the run fails naming the entry. Evidence
   ids that point to nothing are reported, never silently dropped. Clearance must be 0 to 3, and records at
   clearance 2 or more need an `access_owner_id` that exists.
4. **Deduplicate:** each relationship written once, list properties merged, origin = first producer.
5. **Derived flags:** compute `current` over the graph as it will be after the update.

## Writing to FalkorDB

- Batch with `UNWIND $batch AS row`, one batch per label and per relationship type (types and labels come
  from the schema allowlist, never from data).
- Records: `MERGE (n:Label {id: row.id}) SET n = row.props` (replaces all properties, so stale ones vanish).
  `row.props` includes `id`.
- Relationships: `MATCH (a:LA {id: row.src}), (b:LB {id: row.dst}) MERGE (a)-[r:TYPE]->(b) SET r = row.props`.
- A failing batch is retried entry by entry so the error names the entry.
- Embeddings are computed only for added or changed Documents and Chunks, batched, and stored as `vecf32`.
- On success, write `meta:sync` with `last_synced_at = now (UTC)`. A failed run does **not** advance it.

## Incremental update (default)

1. Read what the graph holds: records by type with fingerprints, relationships that have an origin.
2. Write only added and changed records (fingerprint differs).
3. Remove a relationship whose origin record is in this input but which the input no longer implies (old
   owner after a reassignment). For relationship lists, only when the input is a complete dataset.
4. Remove Change records the record's history no longer contains.
5. Recompute `current` flags and rewrite only records whose flag flips.
6. Report: added, changed, unchanged per type; relationships written and removed; flags flipped; records removed.

| Mode | Effect |
|---|---|
| Default | Incremental. Loading the same data twice reports all unchanged and writes nothing (but still advances `last_synced_at` only if something changed or `--touch` is passed) |
| Prune | Also removes dataset records missing from input. Documents loaded from files are never pruned |
| Full rebuild | Empties the graph and reloads. Requires `--confirm-rebuild` |

## Live updates

`POST /ingest` accepts a partial dataset (e.g. one new decision). It may reference records already in the
graph, removes nothing outside the update, and returns the change report. Off unless `INGEST_TOKEN` is set.
Token compared in constant time, body size capped (default 1 MB), data validated before any write.

## Documents from files

An optional extraction library turns PDF, office, Markdown and text files into text and metadata. Each file
becomes a Document whose id derives from its path, so re-loading updates in place. Unextractable files are
reported and skipped. Without the library the feature says how to enable it.

## The synthetic dataset (planted traps)

About 40 to 60 people, 6 to 8 teams, 12 to 15 projects, 80 to 200 tickets, documents, decisions, chat.
Each trap has an id, and a golden question in 09 that depends on it:

| Trap | Tests |
|---|---|
| A decision superseded by a chat message that says so | current flag, freshness rules |
| A reassignment (owner moved teams, still the expert on old internals) | owner vs expert vs assignee |
| A strategy document made stale by a later decision | stale document detection |
| A dissenting person with message evidence | provenance, DISSENTS |
| A figure that does not exist in the data | "not in company records" |
| A question with a false premise | premise correction |
| Two current decisions on one project that conflict | Q7 |
| A hidden `DEPENDS_ON` between projects | Q10 impact |
| **Restricted (clearance 3) document linked to a visible decision** | soft dead-end |
| **Restricted node as the only intermediate hop between two visible nodes** | path leak (Q9) |
| **Restricted decision that supersedes a visible one** | "is it current" must not claim current |
| Records dated after `last_synced_at` | live-info guard |

Dataset checks: section counts, every reference resolves, every trap present. The offline fact check (09)
verifies every fact a golden question depends on.
