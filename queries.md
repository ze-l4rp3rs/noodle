# Query library

Generated from `src/noodle/queries.py` by `scripts/gen_queries_md.py`.
Do not edit by hand. Every query applies `x.clearance <= $clr`; the
hidden twin for each returns only a count and access owners.

## Q1 Hybrid search

Find documents by topic (vector, keyword fallback).

Params: `text`, `limit`, `embedding`
Hidden twin: `hidden_twin_search()`

```python
def q1_search(
    client: GraphClient,
    user: UserCtx,
    text: str,
    *,
    limit: int = 10,
    embedding: Optional[Sequence[float]] = None,
) -> QueryResult:
    """Search documents by topic. Vector when an embedding is supplied, else a
    fulltext keyword fallback with the same RBAC filter. Over-fetches (A3)."""

    n = max(1, int(limit))
    k = 5 * n
    if embedding is not None:
        cypher = (
            "CALL db.idx.vector.queryNodes('" + schema.LABEL_DOCUMENT + "', 'embedding', $k, vecf32($vec)) "
            "YIELD node AS doc, score "
            "WITH doc, score WHERE doc.clearance <= $clr "
            "OPTIONAL MATCH (doc)-[:AUTHORED_BY]->(a:Person) WHERE a.clearance <= $clr "
            "OPTIONAL MATCH (dec:Decision)-[:DOCUMENTED_IN]->(doc) WHERE dec.clearance <= $clr "
            "WITH doc, score, a, collect(DISTINCT dec.id) AS decision_ids "
            "ORDER BY score DESC LIMIT $n "
            "RETURN doc.id AS id, doc.title AS title, doc.content AS content, doc.family AS family, "
            "doc.version AS version, doc.status AS status, doc.current AS current, doc.url AS url, "
            "doc.tags AS tags, score, a.id AS author_id, a.name AS author, decision_ids"
        )
        params = user.params(text=text, k=k, n=n, vec=list(embedding))
    else:
        cypher = (
            "CALL db.idx.fulltext.queryNodes('" + schema.LABEL_DOCUMENT + "', $text) YIELD node AS doc, score "
            "WITH doc, score WHERE doc.clearance <= $clr "
            "OPTIONAL MATCH (doc)-[:AUTHORED_BY]->(a:Person) WHERE a.clearance <= $clr "
            "OPTIONAL MATCH (dec:Decision)-[:DOCUMENTED_IN]->(doc) WHERE dec.clearance <= $clr "
            "WITH doc, score, a, collect(DISTINCT dec.id) AS decision_ids "
            "ORDER BY score DESC LIMIT $n "
            "RETURN doc.id AS id, doc.title AS title, doc.content AS content, doc.family AS family, "
            "doc.version AS version, doc.status AS status, doc.current AS current, doc.url AS url, "
            "doc.tags AS tags, score, a.id AS author_id, a.name AS author, decision_ids"
        )
        params = user.params(text=text, n=n)
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_search(client, user, text))
```

## Q2 Owners

Formal responsibility for an entity now.

Params: `ref`
Hidden twin: `hidden_twin_entity()`

```python
def q2_owners(client: GraphClient, user: UserCtx, ref: str) -> QueryResult:
    """Who is formally responsible for an entity, right now."""

    cypher = (
        "MATCH (p:Person)-[r]-(x {id: $id}) "
        "WHERE x.clearance <= $clr AND p.clearance <= $clr AND type(r) IN $roles "
        "RETURN p.id AS person_id, p.name AS name, type(r) AS rel, x.id AS entity"
    )
    params = user.params(id=ref, roles=list(_OWNER_ROLES))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_entity(client, user, ref))
```

## Q3 Expertise

Who knows about a topic, with the items behind each hit.

Params: `ref`
Hidden twin: `hidden_twin_entity()`

```python
def q3_experts(client: GraphClient, user: UserCtx, ref: str) -> QueryResult:
    """Candidates one hop around the topic, with the items behind each hit."""

    cypher = (
        "MATCH (t {id: $id}) WHERE t.clearance <= $clr "
        "MATCH (t)-[*0..1]-(e) WHERE e.clearance <= $clr "
        "WITH collect(DISTINCT e) AS ents "
        "UNWIND ents AS e "
        "MATCH (p:Person)-[r]-(e) "
        "WHERE p.clearance <= $clr AND type(r) IN $roles "
        "RETURN DISTINCT p.id AS person_id, p.name AS name, type(r) AS rel, "
        "e.id AS via, coalesce(e.date, e.ts) AS on"
    )
    params = user.params(id=ref, roles=list(_EXPERT_ROLES))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_entity(client, user, ref))
```

## Q4 Decision trace

Why a decision was made: approvers, dissent, sources.

Params: `ref`
Hidden twin: `hidden_twin_sources()`

```python
def q4_decision_trace(client: GraphClient, user: UserCtx, ref: str) -> QueryResult:
    """Why a decision was made: approvers, dissent (with evidence), sources."""

    cypher = (
        "MATCH (d:" + schema.LABEL_DECISION + ") "
        "WHERE (d.id = $ref OR d.key = $ref OR toLower(d.title) = toLower($ref)) "
        "AND d.clearance <= $clr "
        "OPTIONAL MATCH (ap:Person)-[:APPROVED]->(d) WHERE ap.clearance <= $clr "
        "WITH d, collect(DISTINCT ap{.id, .name}) AS approvers "
        "OPTIONAL MATCH (ds:Person)-[dr:DISSENTS]->(d) WHERE ds.clearance <= $clr "
        "WITH d, approvers, "
        "[x IN collect(DISTINCT {who: ds{.id, .name}, evidence: dr.evidence}) WHERE x.who IS NOT NULL] AS dissent "
        "OPTIONAL MATCH (d)-[s]-(src) "
        "WHERE type(s) IN $source_types AND src.clearance <= $clr "
        "WITH d, approvers, dissent, "
        "collect(DISTINCT {id: src.id, kind: head(labels(src)), status: src.status, current: src.current, "
        "label: coalesce(src.title, src.key, src.channel, src.name), "
        "text: coalesce(src.content, src.text, src.description), "
        "date: coalesce(src.created, src.ts, src.date), url: src.url})[..12] AS sources "
        "RETURN d.id AS id, d.key AS key, d.title AS title, d.date AS date, d.status AS status, "
        "d.description AS description, d.rationale AS rationale, d.impact AS impact, "
        "d.details AS details, d.current AS current, approvers, dissent, sources"
    )
    params = user.params(ref=ref, source_types=list(_SOURCE_TYPES))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_sources(client, user, ref))
```

## Q4b Supersession chain

The chain around a decision, every node visible.

Params: `ref`
Hidden twin: `hidden_twin_entity()`

```python
def q4_decision_chain(
    client: GraphClient, user: UserCtx, ref: str, *, limit: int = 6
) -> QueryResult:
    """The supersession chain around a decision, both directions, every node on
    the path visible (A2)."""

    cypher = (
        "MATCH (d:" + schema.LABEL_DECISION + " {id: $id}) WHERE d.clearance <= $clr "
        "WITH d "
        "MATCH p = (d)-[:" + "|".join(_SUPERSEDE_TYPES) + "*1..6]-(o:" + schema.LABEL_DECISION + ") "
        "WHERE ALL(x IN nodes(p) WHERE x.clearance <= $clr) "
        "RETURN [x IN nodes(p) | x.id] AS nodes, [e IN relationships(p) | type(e)] AS rels, "
        "length(p) AS length ORDER BY length LIMIT $limit"
    )
    params = user.params(id=ref, limit=max(1, int(limit)))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_entity(client, user, ref))
```

## Q5 Still current

Head of the supersession chain for a decision.

Params: `ref`
Hidden twin: `hidden_twin_superseder()`

```python
def q5_currency(client: GraphClient, user: UserCtx, ref: str) -> QueryResult:
    """The head of the supersession chain for a decision, if visible."""

    cypher = (
        "MATCH (old:" + schema.LABEL_DECISION + " {id: $id}) WHERE old.clearance <= $clr "
        "OPTIONAL MATCH (h:" + schema.LABEL_DECISION + ")-[sr]-(old) "
        "WHERE type(sr) IN $supersede_types AND h.current = true AND h.clearance <= $clr "
        "RETURN old.id AS id, old.current AS is_current, "
        "h.id AS head_id, h.title AS head_title, h.date AS head_date"
    )
    params = user.params(id=ref, supersede_types=list(_SUPERSEDE_TYPES))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_superseder(client, user, ref))
```

## Q6 Timeline

History of an entity; value on a date.

Params: `ref`, `field`, `on`
Hidden twin: `hidden_twin_entity()`

```python
def q6_timeline(
    client: GraphClient,
    user: UserCtx,
    ref: str,
    *,
    field: Optional[str] = None,
    on: Optional[str] = None,
) -> QueryResult:
    """History of an entity (optionally one field). With ``on`` set, returns the
    value that held on that date."""

    if on is None:
        cypher = (
            "MATCH (c:" + schema.LABEL_CHANGE + ")-[:CHANGES]->(x {id: $id}) "
            "WHERE x.clearance <= $clr AND c.clearance <= $clr "
            "AND ($field IS NULL OR c.field = $field) "
            "OPTIONAL MATCH (p:Person)-[:MADE_CHANGE]->(c) WHERE p.clearance <= $clr "
            "RETURN c.id AS id, c.field AS field, c.old_value AS old_value, c.new_value AS new_value, "
            "c.date AS date, c.reason AS reason, c.version AS version, c.seq AS seq, "
            "c.current AS current, p.id AS by_id, p.name AS by "
            "ORDER BY c.date, c.seq"
        )
    else:
        cypher = (
            "MATCH (c:" + schema.LABEL_CHANGE + ")-[:CHANGES]->(x {id: $id}) "
            "WHERE x.clearance <= $clr AND c.clearance <= $clr "
            "AND ($field IS NULL OR c.field = $field) AND c.date <= $on "
            "OPTIONAL MATCH (p:Person)-[:MADE_CHANGE]->(c) WHERE p.clearance <= $clr "
            "RETURN c.id AS id, c.field AS field, c.old_value AS old_value, c.new_value AS new_value, "
            "c.date AS date, c.reason AS reason, c.version AS version, c.seq AS seq, "
            "c.current AS current, p.id AS by_id, p.name AS by "
            "ORDER BY c.date DESC, c.seq DESC LIMIT 1"
        )
    params = user.params(id=ref, field=field, on=on)
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_entity(client, user, ref))
```

## Q7 Conflicts

Two current decisions on one project.

Params: (none)

```python
def q7_conflicts(client: GraphClient, user: UserCtx) -> QueryResult:
    """Two current decisions affecting the same project, and whether they are
    explicitly flagged as contradicting."""

    cypher = (
        "MATCH (a:" + schema.LABEL_DECISION + " {current: true})-[:AFFECTS]->(p:" + schema.LABEL_PROJECT + ")"
        "<-[:AFFECTS]-(b:" + schema.LABEL_DECISION + " {current: true}) "
        "WHERE a.id < b.id AND a.clearance <= $clr AND b.clearance <= $clr AND p.clearance <= $clr "
        "RETURN p.id AS project_id, p.name AS project, "
        "a.id AS a_id, a.title AS a_title, b.id AS b_id, b.title AS b_title, "
        "EXISTS((a)-[:CONTRADICTS]-(b)) AS flagged"
    )
    params = user.params()
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())
```

## Q8 Stale documents

Current documents resting on a superseded decision.

Params: (none)

```python
def q8_stale_documents(client: GraphClient, user: UserCtx) -> QueryResult:
    """Current documents resting on a superseded decision."""

    cypher = (
        "MATCH (d:" + schema.LABEL_DECISION + ")-[:DOCUMENTED_IN]->"
        "(doc:" + schema.LABEL_DOCUMENT + " {current: true}) "
        # NB: ``NOT d.current`` instead of ``{current: false}``: after a Redis
        # persistence reload this engine fails boolean-false scan comparisons
        # while NOT / <> true keep working on the same data.
        "WHERE NOT d.current AND d.clearance <= $clr AND doc.clearance <= $clr "
        "RETURN doc.id AS doc_id, doc.title AS title, doc.content AS content, doc.url AS url, "
        "doc.family AS family, doc.version AS version, doc.created AS created, doc.current AS current, "
        "collect(DISTINCT d.id) AS superseded_decisions"
    )
    params = user.params()
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())
```

## Q9 Connection

How two entities are connected.

Params: `a`, `b`, `limit`
Hidden twin: `hidden_twin_connection()`

```python
def q9_connection(
    client: GraphClient, user: UserCtx, a: str, b: str, *, limit: int = 5, depth: int = 6
) -> QueryResult:
    """How two entities are connected. Uses a visible-only expansion so a hidden
    intermediate never masks a visible alternative (A2, 03 caveat)."""

    cypher = (
        "MATCH (a {id: $a}), (b {id: $b}) "
        "WHERE a.clearance <= $clr AND b.clearance <= $clr "
        "WITH a, b "
        "MATCH p = (a)-[*1.." + str(int(depth)) + "]-(b) "
        "WHERE ALL(x IN nodes(p) WHERE x.clearance <= $clr) "
        "RETURN [x IN nodes(p) | x.id] AS nodes, "
        "[x IN nodes(p) | coalesce(x.name, x.title, x.key, x.channel, x.id)] AS names, "
        "[e IN relationships(p) | type(e)] AS rels, "
        "length(p) AS length ORDER BY length LIMIT $limit"
    )
    params = user.params(a=a, b=b, limit=max(1, int(limit)))
    res = client.ro_query(cypher, params)
    edge_ids: list[str] = []
    for row in res.rows:
        nodes = row.get("nodes") or []
        rels = row.get("rels") or []
        edge_ids.extend(f"{nodes[i]}|{rels[i]}|{nodes[i + 1]}" for i in range(min(len(rels), len(nodes) - 1)))
    return _result(res, params, hidden_twin_connection(client, user, a, b), edge_ids=edge_ids)
```

## Q10 Team impact

Projects at risk if a team loses people.

Params: `team`, `lost`
Hidden twin: `hidden_twin_team()`

```python
def q10_team_impact(
    client: GraphClient, user: UserCtx, team: str, *, lost: int = 2
) -> QueryResult:
    """Projects at risk if a team loses people: where the team's staff is a
    large share of the assignees of unresolved tickets (plus downstream)."""

    cypher = (
        "MATCH (tm:" + schema.LABEL_TEAM + " {id: $team})<-[:MEMBER_OF]-(p:Person)"
        "<-[:ASSIGNED_TO]-(t:" + schema.LABEL_TICKET + ")-[:BELONGS_TO]->(proj:" + schema.LABEL_PROJECT + ") "
        "WHERE t.resolved IS NULL AND proj.clearance <= $clr AND p.clearance <= $clr "
        "WITH proj, collect(DISTINCT p) AS from_team "
        "MATCH (proj)<-[:BELONGS_TO]-(t2:" + schema.LABEL_TICKET + ")-[:ASSIGNED_TO]->(a:Person) "
        "WHERE t2.resolved IS NULL AND a.clearance <= $clr "
        "WITH proj, size(from_team) AS team_staff, count(DISTINCT a) AS total "
        "WHERE total - $lost < 2 OR team_staff >= total * 0.5 "
        "OPTIONAL MATCH (dep:" + schema.LABEL_PROJECT + ")-[:DEPENDS_ON*1..3]->(proj) "
        "WHERE dep.clearance <= $clr "
        "RETURN proj.id AS project_id, proj.name AS project, total, team_staff, "
        "collect(DISTINCT dep.id) AS downstream"
    )
    params = user.params(team=team, lost=int(lost))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_team(client, user, team))
```

## Q11 Blast radius

Projects affected by a decision (BFS over AFFECTS).

Params: `ref`, `depth`
Hidden twin: `hidden_twin_entity()`

```python
def q11_blast_radius(
    client: GraphClient, user: UserCtx, ref: str, *, depth: int = 3
) -> QueryResult:
    """Projects affected by a decision, breadth-first over AFFECTS."""

    cypher = (
        "MATCH (d:" + schema.LABEL_DECISION + " {id: $id}) WHERE d.clearance <= $clr "
        "CALL algo.BFS(d, $depth, 'AFFECTS') YIELD nodes "
        "UNWIND nodes AS n WITH n WHERE n.clearance <= $clr "
        "RETURN n.id AS id, coalesce(n.name, n.title, n.key) AS name, head(labels(n)) AS kind"
    )
    params = user.params(id=ref, depth=int(depth))
    res = client.ro_query(cypher, params)
    return _result(res, params, hidden_twin_entity(client, user, ref))
```

## Q12 Freshness

The dataset's as-of time.

Params: (none)

```python
def q12_freshness(client: GraphClient, user: UserCtx) -> QueryResult:
    """The dataset's as-of time. Always allowed; leaks nothing."""

    cypher = (
        "MATCH (m:" + schema.LABEL_META + " {id: 'meta:sync'}) "
        "RETURN m.last_synced_at AS last_synced_at, m.dataset_version AS version"
    )
    params: dict[str, Any] = {}
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())
```

## Q13 Hidden twin

Count and access owners of what RBAC filtered.

Params: `ref`

```python
def hidden_twin_entity(client: GraphClient, user: UserCtx, ref: str) -> HiddenInfo:
    """Hidden nodes at or adjacent to an entity (twin for Q2/Q3/Q6/Q11). Counts
    the entity itself if it is hidden, so an invisible anchor still raises the
    soft dead-end."""

    cypher = (
        "MATCH (x {id: $id}) "
        "OPTIONAL MATCH (x)-[]-(h) "
        "WITH x, collect(DISTINCT h) AS hs "
        "WITH CASE WHEN x.clearance > $clr THEN [x] ELSE hs END AS ns "
        "UNWIND ns AS n "
        "WITH n WHERE n IS NOT NULL AND n.clearance > $clr "
        "RETURN count(DISTINCT n) AS count, collect(DISTINCT n.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(id=ref)))
```
