"""The query library: Q1 to Q13 (03) plus the RBAC scaffolding.

Every Cypher statement in Noodle lives here. Tools (step 5) call these
functions and never build Cypher. Rules enforced by construction and by
``scripts/check_clearance.py``:

* only the read doorway (:class:`~noodle.graph.GraphClient.ro_query`);
* every value is a parameter (``$name``), never interpolated;
* labels and relationship types come from :mod:`noodle.schema`, never from user
  or model text;
* every function takes a :class:`UserCtx` and applies ``x.clearance <= $clr``;
* a function returns a :class:`QueryResult` carrying the exact statement, the
  redacted params, the node/edge ids touched, and the :class:`HiddenInfo` twin
  (Q13) for the soft dead-end.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from . import schema
from .graph import GraphClient, GraphResult

# --------------------------------------------------------------------------- #
# User context and results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UserCtx:
    """The authenticated user, mapped to a Person node (06).

    Supplied by the system; the model can never pass or change it. An unknown
    user is clearance 0, which leaks nothing.
    """

    id: str
    clearance: int = schema.CLEARANCE_DEFAULT
    team: Optional[str] = None

    @classmethod
    def anonymous(cls) -> "UserCtx":
        return cls(id="person:unknown", clearance=schema.CLEARANCE_MIN)

    def params(self, **extra: Any) -> dict[str, Any]:
        return {schema.CLEARANCE_PARAM: self.clearance, **extra}


@dataclass(frozen=True)
class HiddenInfo:
    """What RBAC filtered out (06). Never carries titles, names, text or ids of
    hidden nodes -- only how many and who could grant access."""

    count: int = 0
    access_owners: tuple[str, ...] = ()

    @property
    def hidden(self) -> bool:
        return self.count > 0


def merge_hidden(*infos: HiddenInfo) -> HiddenInfo:
    """Combine the twins of several queries run by one tool. Only the boolean
    matters at the API level (06), so a node counted by two twins is harmless."""

    owners: set[str] = set()
    for info in infos:
        owners.update(info.access_owners)
    return HiddenInfo(
        count=sum(info.count for info in infos), access_owners=tuple(sorted(owners))
    )


@dataclass
class QueryResult:
    rows: list[dict[str, Any]]
    cypher: str
    params: dict[str, Any]
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    hidden: HiddenInfo = field(default_factory=HiddenInfo)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_NODE_ID_RE = re.compile(
    r"^(" + "|".join(sorted(schema.ID_PREFIX_TO_SECTION)) + r"):"
)

# Params that must never leave the query layer in a trace (06, A4).
_REDACT_KEYS = frozenset({"vec", "embedding", "clr", "user_id", "user"})

# Relationship sets, drawn from the schema allowlist.
_OWNER_ROLES = ("OWNS", "SPONSORS", "LEADS", "ASSIGNED_TO", "REPORTED_BY", "AUTHORED_BY", "APPROVED")
_EXPERT_ROLES = (
    "OWNS",
    "SPONSORS",
    "APPROVED",
    "DISSENTS",
    "ASSIGNED_TO",
    "REPORTED_BY",
    "POSTED",
    "MADE_CHANGE",
)
_SOURCE_TYPES = ("DOCUMENTED_IN", "ANNOUNCED_IN", "SOURCED_FROM")
_SUPERSEDE_TYPES = ("SUPERSEDES", "SUPERSEDED_BY", "OVERRIDES", "REVERSES")

_ROLE_LABELS = {
    "OWNS": "owner",
    "SPONSORS": "sponsor",
    "LEADS": "lead",
    "ASSIGNED_TO": "assignee",
    "REPORTED_BY": "reporter",
    "AUTHORED_BY": "author",
    "APPROVED": "approver",
}


def role_label(rel: str) -> str:
    """A human name for a formal responsibility edge (used by tools)."""

    return _ROLE_LABELS.get(rel, rel.lower())


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if key not in _REDACT_KEYS}


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _collect_node_ids(rows: Sequence[dict[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for value in _iter_strings(row):
            if _NODE_ID_RE.match(value):
                seen.setdefault(value, None)
    return list(seen)


def _hidden(res: Optional[GraphResult]) -> HiddenInfo:
    if not res or not res.rows:
        return HiddenInfo()
    row = res.rows[0]
    owners = tuple(sorted({o for o in (row.get("owners") or ()) if o}))
    return HiddenInfo(count=int(row.get("count") or 0), access_owners=owners)


def _result(
    res: GraphResult,
    params: dict[str, Any],
    hidden: HiddenInfo,
    *,
    edge_ids: Optional[list[str]] = None,
) -> QueryResult:
    return QueryResult(
        rows=res.rows,
        cypher=res.cypher,
        params=_redact(params),
        node_ids=_collect_node_ids(res.rows),
        edge_ids=edge_ids or [],
        hidden=hidden,
    )


# --------------------------------------------------------------------------- #
# Q1 hybrid search (vector entry nodes, graph expansion; keyword fallback)
# --------------------------------------------------------------------------- #


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


def hidden_twin_search(client: GraphClient, user: UserCtx, text: str) -> HiddenInfo:
    """Q13 twin for Q1: how many hidden documents match, owners only."""

    cypher = (
        "CALL db.idx.fulltext.queryNodes('" + schema.LABEL_DOCUMENT + "', $text) YIELD node AS doc "
        "WHERE doc.clearance > $clr "
        "RETURN count(DISTINCT doc) AS count, collect(DISTINCT doc.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(text=text)))


# --------------------------------------------------------------------------- #
# Q2 owners (formal responsibility now)
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q3 expertise (who knows what; weights applied by a pure function)
# --------------------------------------------------------------------------- #


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


# Expertise weights (03). Pure function over Q3 rows; the graph only ranks.
_EXPERT_WEIGHT = {
    "OWNS": 3.0,
    "SPONSORS": 3.0,
    "APPROVED": 3.0,
    "DISSENTS": 2.0,
    "AUTHORED_BY": 2.0,
    "ASSIGNED_TO": 2.0,
    "REPORTED_BY": 2.0,
    "POSTED": 1.0,
    "MADE_CHANGE": 1.0,
}
_EXPERT_CAPPED = ("POSTED", "MADE_CHANGE")


def score_experts(rows: Sequence[dict[str, Any]], *, topic_latest: Optional[str] = None) -> list[dict]:
    """Turn Q3 rows into ranked people, each listing the items behind the score.

    Recency is measured from ``topic_latest`` (ISO date) if given: 1.0 within 30
    days, 0.75 within 90, else 0.5. Chat and change evidence is capped at five
    items each per person.
    """

    def recency(date: Optional[str]) -> float:
        if not topic_latest or not date:
            return 1.0
        try:
            from datetime import date as _d

            a = _d.fromisoformat(str(topic_latest)[:10])
            b = _d.fromisoformat(str(date)[:10])
            days = abs((a - b).days)
        except ValueError:
            return 1.0
        if days <= 30:
            return 1.0
        if days <= 90:
            return 0.75
        return 0.5

    people: dict[str, dict] = {}
    for row in rows:
        person = row["person_id"]
        entry = people.setdefault(
            person, {"person_id": person, "name": row.get("name"), "score": 0.0, "items": []}
        )
        rel = row["rel"]
        weight = _EXPERT_WEIGHT.get(rel, 0.0)
        entry["score"] += weight * recency(row.get("on"))
        entry["items"].append(
            {"rel": rel, "role": role_label(rel), "via": row.get("via"), "date": row.get("on")}
        )
    for entry in people.values():
        capped: dict[str, int] = {}
        kept: list[dict] = []
        for item in sorted(entry["items"], key=lambda i: (i["rel"], i["via"] or "")):
            if item["rel"] in _EXPERT_CAPPED:
                capped[item["rel"]] = capped.get(item["rel"], 0) + 1
                if capped[item["rel"]] > 5:
                    continue
            kept.append(item)
        entry["items"] = kept
    return sorted(people.values(), key=lambda e: (-e["score"], e["person_id"]))


# --------------------------------------------------------------------------- #
# Q4 decision trace and supersession chain
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q5 is this still current
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q6 timeline and value on a date
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q7 conflicts
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q8 stale documents
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q9 connection
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q10 and Q11 impact
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Q15 subgraph (visualisation only; every node on the path must be visible)
# --------------------------------------------------------------------------- #


def q15_subgraph(
    client: GraphClient, user: UserCtx, ids: Sequence[str], *, depth: int = 1
) -> QueryResult:
    """Nodes and edges around the given ids, for the UI graph (07, C6).

    Every returned node is checked against ``$clr`` and every node on an expanded
    path is visible, so an edge with a hidden endpoint is never returned (A2).
    ``depth`` is a validated integer interpolated into the variable-length
    pattern, exactly as Q9 does; no label or relationship type is interpolated.
    There is no hidden twin: the visualisation simply omits what the caller may
    not see.
    """

    ordered = [str(i) for i in ids]
    hops = max(1, min(int(depth), 3))
    cypher = (
        "MATCH (s) WHERE s.id IN $ids AND s.clearance <= $clr "
        "OPTIONAL MATCH p = (s)-[*1.." + str(hops) + "]-(m) "
        "WHERE p IS NULL OR ALL(x IN nodes(p) WHERE x.clearance <= $clr) "
        "RETURN {id: s.id, label: head(labels(s)), "
        "name: coalesce(s.name, s.title, s.key, s.channel, s.id)} AS seed, "
        "[x IN nodes(p) | {id: x.id, label: head(labels(x)), "
        "name: coalesce(x.name, x.title, x.key, x.channel, x.id)}] AS nodes, "
        "[x IN nodes(p) | x.id] AS node_ids, "
        "[e IN relationships(p) | type(e)] AS rels"
    )
    params = user.params(ids=ordered)
    res = client.ro_query(cypher, params)
    edge_ids: list[str] = []
    for row in res.rows:
        node_ids = row.get("node_ids") or []
        rels = row.get("rels") or []
        edge_ids.extend(
            f"{node_ids[i]}|{rels[i]}|{node_ids[i + 1]}"
            for i in range(min(len(rels), len(node_ids) - 1))
        )
    return _result(res, params, HiddenInfo(), edge_ids=edge_ids)


# --------------------------------------------------------------------------- #
# Q12 freshness (allow-listed: no user clearance filter by design)
# --------------------------------------------------------------------------- #


def q12_freshness(client: GraphClient, user: UserCtx) -> QueryResult:
    """The dataset's as-of time. Always allowed; leaks nothing."""

    cypher = (
        "MATCH (m:" + schema.LABEL_META + " {id: 'meta:sync'}) "
        "RETURN m.last_synced_at AS last_synced_at, m.dataset_version AS version"
    )
    params: dict[str, Any] = {}
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())


# --------------------------------------------------------------------------- #
# Q14 identity and directory (allow-listed: no clearance filter by design)
# --------------------------------------------------------------------------- #


def q14_person(client: GraphClient, person_id: str) -> QueryResult:
    """One Person's directory fields by id: name, role, clearance, team.

    Allow-listed (no ``$clr``): it resolves an already-authenticated caller's own
    clearance (06) and an access owner's display name for the fixed notice. It
    returns directory fields only, never record content.
    """

    cypher = (
        "MATCH (p:" + schema.LABEL_PERSON + " {id: $id}) "
        "RETURN p.id AS id, p.name AS name, p.role AS role, "
        "p.clearance AS clearance, p.team AS team"
    )
    params: dict[str, Any] = {"id": person_id}
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())


def q14_people(client: GraphClient) -> QueryResult:
    """Every Person's directory fields, for the dev user switcher (07).

    Allow-listed (no ``$clr``): the switcher must offer demo users of every
    clearance, so it cannot filter by the caller's. The endpoint is dev-only and
    off in production (07); only directory fields are returned, never record
    content.
    """

    cypher = (
        "MATCH (p:" + schema.LABEL_PERSON + ") "
        "RETURN p.id AS id, p.name AS name, p.role AS role, p.clearance AS clearance "
        "ORDER BY p.clearance, p.name"
    )
    params: dict[str, Any] = {}
    res = client.ro_query(cypher, params)
    return _result(res, params, HiddenInfo())


def resolve_access_owner_names(client: GraphClient, hidden: HiddenInfo) -> dict[str, str]:
    """Resolve each access owner id to a display name for the fixed notice (06).

    The notice names the person who can grant access; an owner we cannot resolve
    is left out, and the caller falls back to the fixed "your administrator"
    text. A lookup failure never changes an answer. The lookup uses the
    allow-listed ``q14_person`` (identity/directory only), so it cannot leak
    record content.
    """

    names: dict[str, str] = {}
    for owner in hidden.access_owners:
        try:
            rows = q14_person(client, owner).rows
        except Exception:  # noqa: BLE001 - a failed name lookup is not fatal
            continue
        if rows and rows[0].get("name"):
            names[owner] = str(rows[0]["name"])
    return names


# --------------------------------------------------------------------------- #
# Q13 hidden twins -- only count and access owners, never content (06)
# --------------------------------------------------------------------------- #


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


def hidden_twin_sources(client: GraphClient, user: UserCtx, ref: str) -> HiddenInfo:
    """Hidden sources of a visible decision (twin for Q4; RT2)."""

    cypher = (
        "MATCH (d:" + schema.LABEL_DECISION + ") WHERE d.clearance <= $clr "
        "AND (d.id = $ref OR d.key = $ref OR toLower(d.title) = toLower($ref)) "
        "MATCH (d)-[s]-(src) WHERE type(s) IN $source_types AND src.clearance > $clr "
        "RETURN count(DISTINCT src) AS count, collect(DISTINCT src.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(ref=ref, source_types=list(_SOURCE_TYPES))))


def hidden_twin_superseder(client: GraphClient, user: UserCtx, ref: str) -> HiddenInfo:
    """A hidden decision superseding a visible one (twin for Q5; RT8)."""

    cypher = (
        "MATCH (old:" + schema.LABEL_DECISION + " {id: $id}) WHERE old.clearance <= $clr "
        "MATCH (old)-[sr]-(h:" + schema.LABEL_DECISION + ") "
        "WHERE type(sr) IN $supersede_types AND h.clearance > $clr "
        "RETURN count(DISTINCT h) AS count, collect(DISTINCT h.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(id=ref, supersede_types=list(_SUPERSEDE_TYPES))))


def hidden_twin_connection(
    client: GraphClient, user: UserCtx, a: str, b: str, *, depth: int = 6
) -> HiddenInfo:
    """Hidden nodes on any path between a and b (twin for Q9; RT9)."""

    cypher = (
        "MATCH (a {id: $a}), (b {id: $b}) "
        "WHERE a.clearance <= $clr AND b.clearance <= $clr "
        "WITH a, b "
        "MATCH p = (a)-[*1.." + str(int(depth)) + "]-(b) "
        "WITH p UNWIND nodes(p) AS n WITH n WHERE n.clearance > $clr "
        "RETURN count(DISTINCT n) AS count, collect(DISTINCT n.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(a=a, b=b)))


def hidden_twin_team(client: GraphClient, user: UserCtx, team: str) -> HiddenInfo:
    """Hidden team, people or projects in a visible team's scope (twin for Q10)."""

    cypher = (
        "MATCH (tm:" + schema.LABEL_TEAM + " {id: $team}) "
        "OPTIONAL MATCH (tm)<-[:MEMBER_OF]-(h:Person) WHERE h.clearance > $clr "
        "OPTIONAL MATCH (h)-[:ASSIGNED_TO|OWNS]->(proj:" + schema.LABEL_PROJECT + ") WHERE proj.clearance > $clr "
        "WITH tm, collect(DISTINCT h) + collect(DISTINCT proj) AS hs "
        "WITH CASE WHEN tm.clearance > $clr THEN [tm] ELSE hs END AS ns "
        "UNWIND ns AS n WITH n WHERE n IS NOT NULL AND n.clearance > $clr "
        "RETURN count(DISTINCT n) AS count, collect(DISTINCT n.access_owner_id) AS owners"
    )
    return _hidden(client.ro_query(cypher, user.params(team=team)))


# --------------------------------------------------------------------------- #
# queries.md generation (03: the file is the source for the submission artifact)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class QueryDoc:
    qid: str
    title: str
    purpose: str
    function: str
    params: tuple[str, ...]
    hidden_twin: Optional[str] = None


QUERY_DOCS: tuple[QueryDoc, ...] = (
    QueryDoc("Q1", "Hybrid search", "Find documents by topic (vector, keyword fallback).", "q1_search", ("text", "limit", "embedding"), "hidden_twin_search"),
    QueryDoc("Q2", "Owners", "Formal responsibility for an entity now.", "q2_owners", ("ref",), "hidden_twin_entity"),
    QueryDoc("Q3", "Expertise", "Who knows about a topic, with the items behind each hit.", "q3_experts", ("ref",), "hidden_twin_entity"),
    QueryDoc("Q4", "Decision trace", "Why a decision was made: approvers, dissent, sources.", "q4_decision_trace", ("ref",), "hidden_twin_sources"),
    QueryDoc("Q4b", "Supersession chain", "The chain around a decision, every node visible.", "q4_decision_chain", ("ref",), "hidden_twin_entity"),
    QueryDoc("Q5", "Still current", "Head of the supersession chain for a decision.", "q5_currency", ("ref",), "hidden_twin_superseder"),
    QueryDoc("Q6", "Timeline", "History of an entity; value on a date.", "q6_timeline", ("ref", "field", "on"), "hidden_twin_entity"),
    QueryDoc("Q7", "Conflicts", "Two current decisions on one project.", "q7_conflicts", (), None),
    QueryDoc("Q8", "Stale documents", "Current documents resting on a superseded decision.", "q8_stale_documents", (), None),
    QueryDoc("Q9", "Connection", "How two entities are connected.", "q9_connection", ("a", "b", "limit"), "hidden_twin_connection"),
    QueryDoc("Q10", "Team impact", "Projects at risk if a team loses people.", "q10_team_impact", ("team", "lost"), "hidden_twin_team"),
    QueryDoc("Q11", "Blast radius", "Projects affected by a decision (BFS over AFFECTS).", "q11_blast_radius", ("ref", "depth"), "hidden_twin_entity"),
    QueryDoc("Q12", "Freshness", "The dataset's as-of time.", "q12_freshness", (), None),
    QueryDoc("Q15", "Subgraph", "Nodes and edges around ids, visible to the caller only.", "q15_subgraph", ("ids", "depth"), None),
    QueryDoc("Q13", "Hidden twin", "Count and access owners of what RBAC filtered.", "hidden_twin_entity", ("ref",), None),
)


def render_markdown() -> str:
    """Render ``queries.md`` from this module. Stable output: the source is the
    single source of truth, so the file cannot drift."""

    module = inspect.getmodule(render_markdown) or inspect.getmodule(q1_search)
    lines: list[str] = [
        "# Query library",
        "",
        "Generated from `src/noodle/queries.py` by `scripts/gen_queries_md.py`.",
        "Do not edit by hand. Every query applies `x.clearance <= $clr`; the",
        "hidden twin for each returns only a count and access owners.",
        "",
    ]
    for doc in QUERY_DOCS:
        func = getattr(module, doc.function)
        lines.append(f"## {doc.qid} {doc.title}")
        lines.append("")
        lines.append(doc.purpose)
        lines.append("")
        lines.append("Params: " + (", ".join(f"`{p}`" for p in doc.params) or "(none)"))
        if doc.hidden_twin:
            lines.append(f"Hidden twin: `{doc.hidden_twin}()`")
        lines.append("")
        lines.append("```python")
        lines.append(inspect.getsource(func).rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


__all__ = [
    "UserCtx",
    "HiddenInfo",
    "merge_hidden",
    "QueryResult",
    "q1_search",
    "q2_owners",
    "q3_experts",
    "q4_decision_trace",
    "q4_decision_chain",
    "q5_currency",
    "q6_timeline",
    "q7_conflicts",
    "q8_stale_documents",
    "q9_connection",
    "q10_team_impact",
    "q11_blast_radius",
    "q12_freshness",
    "q14_person",
    "q14_people",
    "q15_subgraph",
    "resolve_access_owner_names",
    "hidden_twin_search",
    "hidden_twin_entity",
    "hidden_twin_sources",
    "hidden_twin_superseder",
    "hidden_twin_connection",
    "hidden_twin_team",
    "score_experts",
    "role_label",
    "render_markdown",
]
