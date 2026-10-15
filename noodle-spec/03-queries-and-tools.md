# Queries and tools

All Cypher lives in `queries.py`. Tools call query functions and never build Cypher. This file is the
source for the root `queries.md` submission artifact.

## Query function contract

```python
@dataclass
class QueryResult:
    rows: list[dict]      # named fields only
    cypher: str           # exact text executed
    params: dict          # with embeddings and user context redacted
    node_ids: list[str]   # nodes touched by the result (for UI highlighting)
    edge_ids: list[str]   # edges touched, as "src|TYPE|dst"
    hidden: HiddenInfo    # count and access owner of what RBAC filtered (06), never contents
```

Rules:
- Run with the read-only call (`GRAPH.RO_QUERY` / client `ro_query`). Only ingestion uses write calls.
- Every value is a parameter. Relationship types and labels come from the schema allowlist, never from user or model text.
- Every function takes `user: UserCtx` and applies `$clr` (the user's clearance). A query function without it fails the build (review check C1).
- Every function returns `cypher` and `node_ids` so the UI can show the work.
- A failing query raises. It never returns an error string.

## The library

Visibility macro used below: `x.clearance <= $clr`.

**Q1 hybrid search** (vectors find entry nodes, graph expands). Over-fetch so filtering cannot reorder what the user sees.
```cypher
CALL db.idx.vector.queryNodes('Document', 'embedding', $k, vecf32($vec)) YIELD node, score
WHERE node.clearance <= $clr
OPTIONAL MATCH (node)-[:AUTHORED_BY]->(a:Person) WHERE a.clearance <= $clr
OPTIONAL MATCH (dec:Decision)-[:DOCUMENTED_IN]->(node) WHERE dec.clearance <= $clr
RETURN node, score, a, collect(DISTINCT dec) AS decisions
ORDER BY score DESC LIMIT $n
```
`$k = 5 * $n`. Keyword fallback uses `db.idx.fulltext.queryNodes` with the same filter.

**Q2 owners** (formal responsibility now)
```cypher
MATCH (p:Person)-[r:OWNS|SPONSORS|LEADS]->(x {id: $id})
WHERE x.clearance <= $clr AND p.clearance <= $clr
RETURN p, type(r) AS role, x
```
Also ticket assignee, document author, decision approver via the matching relationship.

**Q3 expertise** (who knows what). The graph selects candidates one hop around the topic; weights are applied in a pure function.
```cypher
MATCH (t {id: $id}) WHERE t.clearance <= $clr
MATCH (t)-[*0..1]-(e) WHERE e.clearance <= $clr
WITH collect(DISTINCT e) AS ents
UNWIND ents AS e
MATCH (p:Person)-[r:OWNS|SPONSORS|APPROVED|DISSENTS|ASSIGNED_TO|REPORTED_BY|POSTED|MADE_CHANGE]-(e)
WHERE p.clearance <= $clr
RETURN p.id AS person, p.name AS name, type(r) AS rel, e.id AS via, e.date AS date
```
Weights: owner or sponsor 3, approver 3, dissenter 2, author 2, assignee or reporter 2, chat message 1,
change 1 (max 5 of each of the last two per person). Recency measured from the topic's latest activity:
1.0 within 30 days, 0.75 within 90, 0.5 after. Each result lists the items behind the score.
Authored documents come from the `AUTHORED_BY` relationship (arrow points document to person).

**Q4 decision trace**
```cypher
MATCH (d:Decision) WHERE d.id = $ref OR d.key = $ref OR toLower(d.title) = toLower($ref)
WITH d WHERE d.clearance <= $clr
OPTIONAL MATCH (ap:Person)-[:APPROVED]->(d) WHERE ap.clearance <= $clr
OPTIONAL MATCH (ds:Person)-[dr:DISSENTS]->(d) WHERE ds.clearance <= $clr
OPTIONAL MATCH (d)-[:DOCUMENTED_IN|ANNOUNCED_IN|SOURCED_FROM]->(src) WHERE src.clearance <= $clr
RETURN d, ap, collect(DISTINCT {who: ds, evidence: dr.evidence}) AS dissent,
       collect(DISTINCT src)[..12] AS sources
```
Chain, both directions, every node on the path visible:
```cypher
MATCH (d:Decision {id: $id})
MATCH chain = (d)-[:SUPERSEDES|SUPERSEDED_BY|OVERRIDES|REVERSES*1..6]-(o:Decision)
WHERE ALL(x IN nodes(chain) WHERE x.clearance <= $clr)
RETURN chain
```
**Q5 is this still current** (head of the supersession chain)
```cypher
MATCH (old:Decision {id: $id}) WHERE old.clearance <= $clr
OPTIONAL MATCH (h:Decision)-[:SUPERSEDES*1..]->(old)
WHERE h.current = true AND h.clearance <= $clr
RETURN old.current AS is_current, h AS head
```
If a superseding decision exists but is hidden, the result sets `hidden.count > 0` and the answer must not claim "still current" (06).

**Q6 timeline and value on a date**
```cypher
MATCH (c:Change)-[:CHANGES]->(x {id: $id})
WHERE x.clearance <= $clr AND c.clearance <= $clr AND ($field IS NULL OR c.field = $field)
OPTIONAL MATCH (p:Person)-[:MADE_CHANGE]->(c)
RETURN c, p.name AS by ORDER BY c.date, c.seq
```
Value on a date: same match with `c.date <= $on`, `ORDER BY c.date DESC, c.seq DESC LIMIT 1`, return `c.new_value`.

**Q7 conflicts** (graph finds candidates, reviewer judges meaning)
```cypher
MATCH (a:Decision {current: true})-[:AFFECTS]->(p:Project)<-[:AFFECTS]-(b:Decision {current: true})
WHERE a.id < b.id AND a.clearance <= $clr AND b.clearance <= $clr AND p.clearance <= $clr
RETURN p.name AS project, a, b, EXISTS((a)-[:CONTRADICTS]-(b)) AS flagged
```
**Q8 stale documents** (current documents that rest on superseded decisions)
```cypher
MATCH (d:Decision {current: false})-[:DOCUMENTED_IN]->(doc:Document {current: true})
WHERE d.clearance <= $clr AND doc.clearance <= $clr
RETURN doc, collect(d) AS superseded_decisions
```
**Q9 how are A and B connected**
```cypher
MATCH (a {id: $a}), (b {id: $b})
WHERE a.clearance <= $clr AND b.clearance <= $clr
MATCH p = shortestPath((a)-[*..6]-(b))
WHERE ALL(x IN nodes(p) WHERE x.clearance <= $clr)
RETURN p
```
Caveat: if the engine filters after finding one shortest path, a hidden intermediate hides a visible
alternative. Test this (09). If it fails, use `allShortestPaths` and filter, or search inside a visible-only expansion.

**Q10 impact of a team losing people** (needs the project dependency list)
```cypher
MATCH (tm:Team {id: $team})<-[:MEMBER_OF]-(p:Person)<-[:ASSIGNED_TO]-(t:Ticket)-[:BELONGS_TO]->(proj:Project)
WHERE t.resolved IS NULL AND proj.clearance <= $clr
WITH proj, collect(DISTINCT p) AS from_team
MATCH (proj)<-[:BELONGS_TO]-(:Ticket {resolved: null})-[:ASSIGNED_TO]->(a:Person)
WITH proj, size(from_team) AS team_staff, count(DISTINCT a) AS total
WHERE total - $lost < 2 OR team_staff >= total * 0.5
OPTIONAL MATCH (dep:Project)-[:DEPENDS_ON*1..3]->(proj) WHERE dep.clearance <= $clr
RETURN proj.name AS project, total, team_staff, collect(DISTINCT dep.name) AS downstream
```
**Q11 blast radius of a superseded decision** (one real algorithm; verify the signature on day 1)
```cypher
MATCH (d:Decision {id: $id}) WHERE d.clearance <= $clr
CALL algo.BFS(d, 3, 'AFFECTS') YIELD nodes
UNWIND nodes AS n WITH n WHERE n.clearance <= $clr
RETURN n
```
**Q12 freshness**
```cypher
MATCH (m:Meta {id: 'meta:sync'}) RETURN m.last_synced_at AS last_synced_at, m.dataset_version AS version
```
**Q13 hidden counterpart** (soft dead-end). For each of Q1 to Q10, a twin with the predicate inverted
(`x.clearance > $clr`) that returns **only** `count(x)` and `collect(DISTINCT x.access_owner_id)`. Never
titles, names, text or ids of hidden nodes. Specified in 06.

## Tools

| Tool | Uses | Use it when |
|---|---|---|
| `search_records` | Q1 (+ fulltext) | Open question, topic lookup. Natural language only, mapped to fixed shapes: owners, people involved, list by type, keyword |
| `find_owner` | Q2 | "Who owns / is responsible for X" (formal, recorded now) |
| `find_experts` | Q3 | "Who knows about / should I ask about X" |
| `trace_decision` | Q4, Q5 | "Why was this decided", "is it still current", provenance |
| `get_timeline` | Q6 | History of a project or ticket, value on a date |
| `find_conflicts` | Q7, Q8 | Contradictions and out-of-date documents |
| `connect_entities` | Q9 | "How are A and B related" |
| `team_impact` | Q10, Q11 | "What is at risk if..." |
| `get_freshness` | Q12 | Any question about now, today, latest, recent |
| `draft_followup` | none writes | Drafts a message or ticket for a named person. Executes nothing |
| `current_time` | none | The current time |

There is **no raw-Cypher tool** (see 01, conflicts).

## Contract

- A tool returns a list of **evidence items** plus its `QueryResult` trace. Each evidence item becomes its own citable source.
- A tool reports failure by raising. The loop tells the model and logs it. An error is never a source.
- The user identity is supplied by the system. The model cannot pass or change it.
- Identical calls in one run are reused (the trace marks `reused: true`).

**Evidence item:** `kind, label, text, date, url, ref, meta, status, note`

| Record | kind | label | text (quotes are checked against this) |
|---|---|---|---|
| Document | doc | title | content |
| ChatMessage | chat | channel | message text (author shown beside it, not inside) |
| Ticket | ticket | key and title | description, status, resolution, blocker |
| Decision | decision | key and title | description, rationale, impact, status |
| Change | change | entity and field | "field changed from A to B on date by person, with the reason" |
| Project, Person | project, person | name | short summary |
| Role statement | role | role and entity | e.g. "X is the owner of Y"; ref combines role, person, entity |

- `ref` is the **record's own graph id** (message id, change id), never a channel or ticket key shared by several records.
- `status` is `current` or `superseded` from the flag, with `note` giving the reason.

## Adding a tool

1. Write the query function (takes `user`, returns `QueryResult`). 2. Write the tool returning evidence.
3. Write its description, saying **when** to use it instead of similar tools. 4. Register it and add one line to
the generator instructions. 5. Test with the recording graph client: parameterised, correct evidence and refs,
failures fail, RBAC predicate present, hidden twin returns no content.
