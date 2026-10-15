# Graph schema

One schema module (`schema.py`) defines entity types, relationship types, property names, indexes and
clearance rules. Ingestion, the query library, tests and the generated README section all read from it.
Nothing is hard-coded elsewhere.

## Nodes

| Label | Source section | Main properties |
|---|---|---|
| Person | people | id, name, email, role, department, start_date, manager, team |
| Team | teams | id, name |
| Project | projects | id, name, department, status, priority, scope, budget, target_end |
| Ticket | tickets | id, key, title, status, priority, created, resolved, due, description, comments |
| Document | documents | id, title, family, version, created, status, content, key_facts, `current`, url, tags, embedding |
| Decision | decisions | id, key, title, date, status, description, rationale, impact, details, `current` |
| ChatMessage | chat_messages | id, channel, author_name, ts, text, thread, reactions |
| Change | derived from history | id, entity_id, entity_type, field, old_value, new_value, date, reason, version, seq, `current` |
| Chunk | optional, from Document | id, document_id, text, offset, embedding |
| Meta | one node, `meta:sync` | last_synced_at (UTC ISO), dataset_version, record_counts |

**Every node** also carries:

| Property | Meaning |
|---|---|
| `fingerprint` | Hash of the source record, minus authoring notes. Drives incremental updates |
| `written_at` | When ingestion last wrote it |
| `clearance` | Integer 0 to 3 (0 public, 1 internal, 2 confidential, 3 restricted). Default 1 |
| `access_owner_id` | Person who grants access. Required when clearance >= 2 |

**IDs** are `type:slug` and stable: `doc:rfc-031-auth-migration`, `ticket:PLAT-882`, `chat:<channel>:<ts>`,
`change:<owner-id>:<kind>:<position>`. Evidence and UI highlighting use these, never a channel or title.

**Property rules:** simple values and lists of simple values stored as-is; nested structures stored as JSON
text; empty values skipped. Dates are ISO strings so they sort as strings. Embeddings are `vecf32`.

## Relationships

Direction matters: a newer record points at the older one it replaces.

| Type | From to | Source |
|---|---|---|
| OWNS, SPONSORS | Person to Project | project owner, sponsor |
| LEADS, MEMBER_OF | Person to Team | team lead, members |
| APPROVED, DISSENTS | Person to Decision | approver, dissent (carries evidence) |
| AUTHORED_BY | Document to Person | author |
| ASSIGNED_TO, REPORTED_BY | Ticket to Person | assignee, reporter |
| POSTED | Person to ChatMessage | author |
| MENTIONS | ChatMessage to Project, Ticket or Person | lists, names in text |
| BELONGS_TO | Ticket to Project | list |
| BLOCKS, SPLIT_FROM, RELATED_TO | Ticket to Ticket | lists |
| DEPENDS_ON | Project to Project | list (added from the draft schema, used for impact queries) |
| SUPERSEDES | newer Decision or Document to older | lists, document `supersedes` field |
| SUPERSEDED_BY | older Decision to newer | only when no SUPERSEDES already records it |
| OVERRIDES, REVERSES, CONTRADICTS | Decision to Decision | fields, lists |
| AFFECTS | Decision to Project | list |
| DOCUMENTED_IN, ANNOUNCED_IN | Decision to Document or ChatMessage | lists |
| TRIGGERED | Document to Decision | list |
| MADE_CHANGE | Person to Change | who made it |
| CHANGES | Change to Project or Ticket | the record whose history it is |
| SOURCED_FROM | Change or Decision to Document, ChatMessage or Decision | evidence lists |
| PART_OF | Chunk to Document | chunking |

Every relationship stores `origin` (the record or list it was read from), plus `date` or `evidence` where
the source gives one. A relationship is visible only if **both endpoints** are visible.

## Derived data (computed from structure only)

| Flag | Rule |
|---|---|
| Document `current` | false if another document supersedes it or its family has a later version |
| Decision `current` | false if superseded or reversed, or status says superseded |
| Change `current` | true only for the latest change to that field of that entity |
| Source status | `current` or `superseded` from the flag, plus a short reason |

Structure marks what is superseded. The model decides whether a document's content is stale.

## FalkorDB DDL (verify syntax against the installed version on day 1)

```cypher
CREATE INDEX FOR (n:Person)      ON (n.id);
CREATE INDEX FOR (n:Team)        ON (n.id);
CREATE INDEX FOR (n:Project)     ON (n.id);
CREATE INDEX FOR (n:Ticket)      ON (n.id);
CREATE INDEX FOR (n:Document)    ON (n.id);
CREATE INDEX FOR (n:Decision)    ON (n.id);
CREATE INDEX FOR (n:ChatMessage) ON (n.id);
CREATE INDEX FOR (n:Change)      ON (n.id);
CREATE INDEX FOR (n:Decision)    ON (n.current);
CREATE INDEX FOR (n:Document)    ON (n.current);
CALL db.idx.fulltext.createNodeIndex('Document', 'title', 'content');
CALL db.idx.fulltext.createNodeIndex('ChatMessage', 'text');
CALL db.idx.fulltext.createNodeIndex('Decision', 'title', 'description', 'rationale');
CREATE VECTOR INDEX FOR (d:Document) ON (d.embedding)
  OPTIONS {dimension: 1536, similarityFunction: 'cosine'};
```

The embedding dimension comes from config and must match the embedding model. DDL is idempotent and
runs at startup (`ensure_schema()`).

## Never stored

- Authoring commentary (notes on records, relationship rows or the dataset). It can contain the answer key.
- Fields that became relationships (owners, members, evidence lists, dissent, mentions, supersession).
- Nested history. It becomes Change nodes.
- Secrets, API keys, raw model output.
