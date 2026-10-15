#!/usr/bin/env python3
"""Build the synthetic dataset (spec 04). Deterministic; no real people, mail
or documents. Every planted trap in spec 04 is present and tagged.

Run:  python data/build_synthetic_dataset.py
Writes: data/synthetic/dataset.json

Names, keys, channels and text are fabricated for the demo. Authoring notes live
only at the top-level metadata and in trap descriptions; they are never ingested.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "synthetic" / "dataset.json"

LAST_SYNCED_AT = "2026-10-15T00:05:00Z"
DATASET_VERSION = "0.1.0"

FIRST = [
    "Ada", "Bram", "Cora", "Dev", "Elif", "Finn", "Gita", "Hugo", "Ines", "Jae",
    "Kai", "Lena", "Milo", "Nadia", "Omar", "Pia", "Quinn", "Rex", "Sara", "Timo",
    "Uma", "Vik", "Wren", "Xena", "Yusuf", "Zara", "Anya", "Bodhi", "Cleo", "Dario",
    "Esme", "Farid", "Greta", "Hana", "Ivo", "Jonas", "Kira", "Lars", "Mara", "Nils",
    "Oona", "Pavel", "Rosa", "Sven", "Tara", "Ulf", "Vera", "Wes", "Yara", "Zeno",
]
LAST = [
    "Abara", "Bello", "Cruz", "Duarte", "Eze", "Fenn", "Gupta", "Hale", "Iversen",
    "Joki", "Kovac", "Lund", "Moreau", "Nakamura", "Okoro", "Petrov", "Quill",
    "Reyes", "Sand", "Tran", "Ueda", "Volkov", "Wells", "Xu", "Yilmaz", "Zhao",
    "Adeyemi", "Brandt", "Costa", "Dumas", "Eng", "Farkas", "Grimaldi", "Hoff",
    "Igwe", "Jensen", "Kelly", "Larsen", "Mbeki", "Novak", "Ortega", "Pereira",
    "Rossi", "Silva", "Thorne", "Uddin", "Vargas", "Wagner", "Yamada", "Zola",
]

DEPARTMENTS = ["Engineering", "Product", "Security", "Data", "Operations"]
STATUSES = ["open", "in_review", "blocked", "done"]
PRIORITIES = ["low", "medium", "high", "critical"]

PROJECTS = [
    ("auth-migration", "Auth Migration", "AUTH"),
    ("identity-provider", "Identity Provider", "IDP"),
    ("search-rebuild", "Search Rebuild", "SRCH"),
    ("billing-revamp", "Billing Revamp", "BILL"),
    ("mobile-onboarding", "Mobile Onboarding", "MOB"),
    ("data-warehouse", "Data Warehouse", "DATA"),
    ("incident-tooling", "Incident Tooling", "INC"),
    ("api-gateway", "API Gateway", "APIGW"),
    ("notification-service", "Notification Service", "NOTIF"),
    ("analytics-dashboard", "Analytics Dashboard", "ANLT"),
    ("compliance-audit", "Compliance Audit", "COMP"),
    ("observability-platform", "Observability Platform", "OBS"),
    ("partner-integrations", "Partner Integrations", "PART"),
]

TEAMS = [
    ("platform", "Platform"),
    ("identity", "Identity"),
    ("search", "Search"),
    ("data", "Data"),
    ("core-infra", "Core Infra"),
    ("devex", "Developer Experience"),
    ("reliability", "Reliability"),
    ("black-projects", "Black Projects"),
]

RESTRICTED = 3
CONFIDENTIAL = 2
RESTRICTED_OWNER = "person:victor-hale"


def slug(text: str) -> str:
    return text.lower().replace(" ", "-").replace("_", "-")


def email_for(name: str) -> str:
    return name.lower().replace(" ", ".") + "@example.com"


def person(slug_, name, role, team=None, department="Engineering", clearance=None,
           access_owner=None, manager=None, start_date=None):
    rec = {
        "id": f"person:{slug_}",
        "name": name,
        "email": email_for(name),
        "role": role,
        "department": department,
        "start_date": start_date or "2024-01-15",
    }
    if manager:
        rec["manager"] = manager
    if team:
        rec["team"] = f"team:{team}"
    if clearance is not None:
        rec["clearance"] = clearance
    if access_owner:
        rec["access_owner_id"] = access_owner
    return rec


def build_people():
    people = [
        person("ada-wren", "Ada Wren", "Engineering Manager", "platform"),
        person("priya-nair", "Priya Nair", "Staff Engineer", "platform", manager="person:ada-wren"),
        person("noah-fenn", "Noah Fenn", "Senior Engineer", "identity", manager="person:ada-wren"),
        person("dana-okoro", "Dana Okoro", "Principal Engineer", "reliability"),
        person(
            "victor-hale", "Victor Hale", "VP Engineering", "platform",
            clearance=RESTRICTED, access_owner="person:victor-hale",
        ),
        person("ken-zhao", "Ken Zhao", "Security Lead", "core-infra", department="Security"),
        person("iris-lund", "Iris Lund", "Engineer", "data", department="Data"),
        person("mira-sand", "Mira Sand", "Product Manager", "platform", department="Product"),
        person("omar-reyes", "Omar Reyes", "Staff Engineer", "search"),
        person("zara-zhao", "Zara Zhao", "Staff Engineer", "devex"),
    ]
    # Filler people: 40 more, deterministic (50 total).
    story_teams = ["platform", "identity", "search", "data", "core-infra", "devex", "reliability"]
    for i in range(40):
        name = f"{FIRST[(i + 8) % len(FIRST)]} {LAST[i % len(LAST)]}"
        s = slug(name)
        team = story_teams[i % len(story_teams)]
        dept = DEPARTMENTS[i % len(DEPARTMENTS)]
        people.append(person(s, name, "Engineer", team, department=dept))
    return people


def build_teams():
    return [
        {"id": "team:platform", "name": "Platform", "lead": "person:ada-wren",
         "members": ["person:ada-wren", "person:priya-nair", "person:mira-sand"]},
        {"id": "team:identity", "name": "Identity", "lead": "person:noah-fenn",
         "members": ["person:noah-fenn"]},
        {"id": "team:search", "name": "Search", "lead": "person:omar-reyes",
         "members": ["person:omar-reyes"]},
        {"id": "team:data", "name": "Data", "lead": "person:iris-lund",
         "members": ["person:iris-lund"]},
        {"id": "team:core-infra", "name": "Core Infra", "lead": "person:ken-zhao",
         "members": ["person:ken-zhao"]},
        {"id": "team:devex", "name": "Developer Experience", "lead": "person:zara-zhao",
         "members": ["person:zara-zhao"]},
        {"id": "team:reliability", "name": "Reliability", "lead": "person:dana-okoro",
         "members": ["person:dana-okoro"]},
        # Trap 10: the only shared team between iris-lund and ken-zhao, hidden.
        {"id": "team:black-projects", "name": "Black Projects",
         "lead": "person:ken-zhao", "members": ["person:iris-lund", "person:ken-zhao"],
         "clearance": RESTRICTED, "access_owner_id": RESTRICTED_OWNER},
    ]


def _owner_pool(people):
    preferred = [
        "person:priya-nair", "person:noah-fenn", "person:omar-reyes", "person:mira-sand",
        "person:iris-lund", "person:dana-okoro", "person:ken-zhao", "person:ada-wren",
    ]
    ids = {p["id"] for p in people}
    pool = [pid for pid in preferred if pid in ids]
    pool.extend(sorted(pid for pid in ids if pid not in pool))
    return pool


def _project_owner(idx, people):
    pool = _owner_pool(people)
    return pool[idx % len(pool)]


def build_projects(people):
    sponsor = "person:victor-hale"
    projects = []
    for i, (pid, name, _code) in enumerate(PROJECTS):
        projects.append({
            "id": f"project:{pid}",
            "name": name,
            "department": DEPARTMENTS[i % len(DEPARTMENTS)],
            "status": STATUSES[i % len(STATUSES)],
            "priority": PRIORITIES[i % len(PRIORITIES)],
            "scope": f"Deliver the {name.lower()} programme.",
            "budget": 100000 + i * 25000,
            "target_end": f"2026-{((i % 9) + 3):02d}-15",
            "owner": _project_owner(i, people),
            "sponsor": sponsor,
        })
    # Anti-dependency: make sure owner/sponsor resolve to real people.
    ids = {p["id"] for p in people}
    for rec in projects:
        assert rec["owner"] in ids and rec["sponsor"] in ids
    return projects


def build_tickets(people):
    people_ids = [p["id"] for p in people]
    tickets = []
    for pi, (_pid, _name, code) in enumerate(PROJECTS):
        for t in range(8):
            n = pi * 8 + t + 1
            assignee = people_ids[(pi * 3 + t) % len(people_ids)]
            reporter = people_ids[(pi + t + 1) % len(people_ids)]
            resolved = None if t % 3 else f"2026-0{(t % 9) + 1}-20"
            tickets.append({
                "id": f"ticket:{code}-{100 + n}",
                "key": f"{code}-{100 + n}",
                "title": f"{_name}: work item {t + 1}",
                "status": "done" if resolved else STATUSES[t % len(STATUSES)],
                "priority": PRIORITIES[t % len(PRIORITIES)],
                "created": f"2026-0{(pi % 9) + 1}-{((t % 27) + 1):02d}",
                "resolved": resolved,
                "due": f"2026-{((t % 9) + 3):02d}-28",
                "description": f"Tracked work for {_name}.",
                "comments": [],
                "assignee": assignee,
                "reporter": reporter,
                "project": f"project:{_pid}",
            })
    # Trap 2: the auth migration tickets named in the golden question.
    tickets.append({
        "id": "ticket:PLAT-882",
        "key": "PLAT-882",
        "title": "Auth Migration rollout coordination",
        "status": "open",
        "priority": "high",
        "created": "2026-04-10",
        "resolved": None,
        "due": "2026-10-01",
        "description": "Coordinate the rollout of the auth migration.",
        "comments": ["Cutover checklist not finalised."],
        "assignee": "person:priya-nair",
        "reporter": "person:ada-wren",
        "project": "project:auth-migration",
    })
    tickets.append({
        "id": "ticket:PLAT-883",
        "key": "PLAT-883",
        "title": "Auth Migration dependency on identity provider",
        "status": "blocked",
        "priority": "high",
        "created": "2026-05-02",
        "resolved": None,
        "due": "2026-09-30",
        "description": "Depends on the identity provider work.",
        "comments": [],
        "assignee": "person:noah-fenn",
        "reporter": "person:ada-wren",
        "project": "project:auth-migration",
    })
    return tickets


def build_documents():
    docs = [
        {
            "id": "doc:rfc-031-auth-migration",
            "title": "rfc-031-auth-migration",
            "family": "auth-rfc",
            "version": 1,
            "created": "2026-04-01",
            "status": "accepted",
            "content": "Proposal to migrate authentication. Planned target date 2026-08-15.",
            "key_facts": ["target date 2026-08-15"],
            "url": "https://example.com/docs/rfc-031",
            "tags": ["auth", "rfc"],
            "author": "person:noah-fenn",
        },
        {
            "id": "doc:auth-internals-notes",
            "title": "Legacy auth internals notes",
            "family": "auth-notes",
            "version": 1,
            "created": "2025-11-02",
            "status": "reference",
            "content": "Notes on the internals of the legacy authentication system.",
            "key_facts": ["legacy session store"],
            "tags": ["auth", "legacy"],
            "author": "person:noah-fenn",
        },
        {
            "id": "doc:platform-strategy",
            "title": "Platform strategy",
            "family": "platform-strategy",
            "version": 1,
            "created": "2026-03-01",
            "status": "active",
            "content": "Platform strategy resting on the managed queue decision.",
            "key_facts": ["managed queue"],
            "tags": ["strategy"],
            "author": "person:ada-wren",
        },
        {
            "id": "doc:incident-postmortem",
            "title": "Queue incident postmortem (restricted)",
            "family": "queue-postmortem",
            "version": 1,
            "created": "2026-05-20",
            "status": "final",
            "content": "Restricted postmortem of the queue incident.",
            "key_facts": ["restricted incident detail"],
            "tags": ["incident"],
            "author": "person:ken-zhao",
            "clearance": RESTRICTED,
            "access_owner_id": RESTRICTED_OWNER,
        },
        {
            "id": "doc:queue-adr",
            "title": "ADR: managed queue",
            "family": "queue-adr",
            "version": 1,
            "created": "2026-05-02",
            "status": "accepted",
            "content": "Decision record adopting the managed queue provider.",
            "key_facts": ["managed queue provider"],
            "tags": ["adr", "queue"],
            "author": "person:mira-sand",
        },
        {
            "id": "doc:self-host-adr",
            "title": "ADR: self-hosted queue",
            "family": "self-host-adr",
            "version": 1,
            "created": "2026-09-12",
            "status": "accepted",
            "content": "Decision record to self-host the queue, superseding the managed queue.",
            "key_facts": ["self-hosted queue"],
            "tags": ["adr", "queue"],
            "author": "person:dana-okoro",
        },
        {
            "id": "doc:search-elastic-adr",
            "title": "ADR: search on elastic",
            "family": "search-adr-elastic",
            "version": 1,
            "created": "2026-06-01",
            "status": "accepted",
            "content": "Decision record to build search on the managed search engine.",
            "key_facts": ["managed search engine"],
            "tags": ["adr", "search"],
            "author": "person:omar-reyes",
        },
        {
            "id": "doc:search-postgres-adr",
            "title": "ADR: search on postgres",
            "family": "search-adr-postgres",
            "version": 1,
            "created": "2026-07-15",
            "status": "accepted",
            "content": "Decision record to build search on the relational database.",
            "key_facts": ["relational database search"],
            "tags": ["adr", "search"],
            "author": "person:iris-lund",
        },
        {
            "id": "doc:billing-rfc-v1",
            "title": "billing-rfc v1",
            "family": "billing-rfc",
            "version": 1,
            "created": "2026-02-01",
            "status": "superseded",
            "content": "First version of the billing proposal.",
            "key_facts": ["billing v1"],
            "tags": ["billing", "rfc"],
            "author": "person:mira-sand",
        },
        {
            "id": "doc:billing-rfc-v2",
            "title": "billing-rfc v2",
            "family": "billing-rfc",
            "version": 2,
            "created": "2026-06-01",
            "status": "accepted",
            "content": "Second version of the billing proposal.",
            "key_facts": ["billing v2"],
            "tags": ["billing", "rfc"],
            "author": "person:mira-sand",
        },
    ]
    # A filler RFC per remaining project to exercise the plural paths.
    for pid, name, _code in PROJECTS:
        if pid in {"auth-migration", "search-rebuild", "billing-revamp"}:
            continue
        docs.append({
            "id": f"doc:rfc-{pid}",
            "title": f"RFC: {name}",
            "family": f"rfc-{pid}",
            "version": 1,
            "created": "2026-03-10",
            "status": "draft",
            "content": f"Proposal for {name}.",
            "key_facts": [f"{name} draft"],
            "tags": ["rfc"],
            "author": "person:ada-wren",
        })
    return docs


def build_decisions(people):
    decisions = [
        {
            "id": "decision:managed-queue",
            "key": "DEC-101",
            "title": "Adopt the managed queue provider",
            "date": "2026-05-02",
            "status": "accepted",
            "description": "We adopt the managed queue provider.",
            "rationale": "Less operational load.",
            "impact": "Queue ownership moves to the provider.",
            "details": {"provider": "managed"},
            "approvers": ["person:victor-hale", "person:ada-wren"],
        },
        {
            "id": "decision:self-host-queue",
            "key": "DEC-114",
            "title": "Self-host the queue",
            "date": "2026-09-12",
            "status": "accepted",
            "description": "We self-host the queue, reversing the managed provider decision.",
            "rationale": "Cost and control.",
            "impact": "Queue operations move in-house.",
            "details": {"provider": "self-hosted"},
            "approvers": ["person:ada-wren"],
            "dissent": [
                {"id": "person:dana-okoro",
                 "evidence": ["chat:#platform-leads:2026-09-12T10:05:00Z"]}
            ],
        },
        {
            "id": "decision:search-elastic",
            "key": "DEC-105",
            "title": "Build search on the managed search engine",
            "date": "2026-06-01",
            "status": "accepted",
            "description": "Search is built on the managed search engine.",
            "rationale": "Fast to start.",
            "impact": "Search rebuild depends on the engine.",
            "details": {"engine": "managed"},
            "approvers": ["person:omar-reyes"],
        },
        {
            "id": "decision:search-postgres",
            "key": "DEC-108",
            "title": "Build search on the relational database",
            "date": "2026-07-15",
            "status": "accepted",
            "description": "Search is built on the relational database instead.",
            "rationale": "One less system.",
            "impact": "Search rebuild direction changes.",
            "details": {"engine": "relational"},
            "approvers": ["person:iris-lund"],
        },
        {
            "id": "decision:vendor-choice",
            "key": "DEC-090",
            "title": "Select the incumbent vendor",
            "date": "2026-04-01",
            "status": "accepted",
            "description": "We select the incumbent vendor.",
            "rationale": "Existing relationship.",
            "impact": "Procurement continues.",
            "details": {"vendor": "incumbent"},
            "approvers": ["person:ada-wren"],
        },
        {
            "id": "decision:vendor-choice-override",
            "key": "DEC-112",
            "title": "Override the vendor choice (restricted)",
            "date": "2026-08-20",
            "status": "accepted",
            "description": "Restricted decision overriding the vendor choice.",
            "rationale": "Restricted reason.",
            "impact": "Vendor selection changed.",
            "details": {"vendor": "replacement"},
            "approvers": ["person:victor-hale"],
            "clearance": RESTRICTED,
            "access_owner_id": RESTRICTED_OWNER,
        },
        {
            "id": "decision:freeze-hiring",
            "key": "DEC-110",
            "title": "Hiring freeze through the quarter",
            "date": "2026-07-01",
            "status": "accepted",
            "description": "Hiring is frozen through the quarter.",
            "rationale": "Budget control.",
            "impact": "No new roles open.",
            "details": {"freeze": True},
            "approvers": ["person:victor-hale"],
        },
    ]
    # Filler decisions so a project has owners and a plausible history.
    for i, (pid, name, _code) in enumerate(PROJECTS):
        if pid in {"auth-migration", "search-rebuild"}:
            continue
        decisions.append({
            "id": f"decision:approach-{pid}",
            "key": f"DEC-{200 + i}",
            "title": f"Approach for {name}",
            "date": "2026-02-20",
            "status": "accepted",
            "description": f"Chosen approach for {name}.",
            "rationale": "Pragmatic.",
            "impact": f"Shapes {name}.",
            "details": {},
            "approvers": [_project_owner(i, people)],
        })
    return decisions


def build_chat_messages():
    msgs = [
        {
            "id": "chat:#platform-leads:2026-09-12T09:00:00Z",
            "channel": "#platform-leads",
            "author_name": "Priya Nair",
            "ts": "2026-09-12T09:00:00Z",
            "text": "Auth migration target moves to 2026-10-01, superseding the 2026-08-15 date. Ping Noah Fenn for the cutover plan.",
            "thread": None,
            "reactions": [],
            "author": "person:priya-nair",
        },
        {
            "id": "chat:#platform-leads:2026-09-12T10:00:00Z",
            "channel": "#platform-leads",
            "author_name": "Ada Wren",
            "ts": "2026-09-12T10:00:00Z",
            "text": "Reversing the managed queue decision: we are self-hosting the queue now.",
            "thread": None,
            "reactions": [],
            "author": "person:ada-wren",
        },
        {
            "id": "chat:#platform-leads:2026-09-12T10:05:00Z",
            "channel": "#platform-leads",
            "author_name": "Dana Okoro",
            "ts": "2026-09-12T10:05:00Z",
            "text": "I dissent: self-hosting adds operational risk we cannot absorb this quarter.",
            "thread": "chat:#platform-leads:2026-09-12T10:00:00Z",
            "reactions": ["thumbsup"],
            "author": "person:dana-okoro",
        },
        {
            "id": "chat:#platform-leads:2026-10-15T09:30:00Z",
            "channel": "#platform-leads",
            "author_name": "Priya Nair",
            "ts": "2026-10-15T09:30:00Z",
            "text": "Cutover went out this morning, all green so far.",
            "thread": None,
            "reactions": [],
            "author": "person:priya-nair",
        },
    ]
    channels = ["#platform-leads", "#identity", "#search", "#data", "#general"]
    people = ["person:ada-wren", "person:priya-nair", "person:noah-fenn",
              "person:dana-okoro", "person:mira-sand", "person:ken-zhao"]
    names = {"person:ada-wren": "Ada Wren", "person:priya-nair": "Priya Nair",
             "person:noah-fenn": "Noah Fenn", "person:dana-okoro": "Dana Okoro",
             "person:mira-sand": "Mira Sand", "person:ken-zhao": "Ken Zhao"}
    for i in range(66):
        channel = channels[i % len(channels)]
        author = people[i % len(people)]
        ts = f"2026-0{(i % 9) + 1}-{((i % 27) + 1):02d}T{((i % 12) + 8):02d}:00:00Z"
        msgs.append({
            "id": f"chat:{channel}:{ts}",
            "channel": channel,
            "author_name": names[author],
            "ts": ts,
            "text": f"Status update on work item {i + 1}.",
            "thread": None,
            "reactions": [],
            "author": author,
        })
    return msgs


def build_history(people):
    history = [
        {
            "id": "change:ticket:PLAT-882:assignee:2",
            "entity_id": "ticket:PLAT-882",
            "entity_type": "ticket",
            "field": "assignee",
            "old_value": "person:noah-fenn",
            "new_value": "person:priya-nair",
            "date": "2026-09-01",
            "reason": "Ownership reassigned after the team move.",
            "version": 2,
            "seq": 2,
            "made_by": "person:ada-wren",
        },
        {
            "id": "change:project:auth-migration:target_end_date:2",
            "entity_id": "project:auth-migration",
            "entity_type": "project",
            "field": "target_end_date",
            "old_value": "2026-08-15",
            "new_value": "2026-10-01",
            "date": "2026-09-12",
            "reason": "Target moved after the queue decision.",
            "version": 2,
            "seq": 2,
            "made_by": "person:priya-nair",
        },
    ]
    for i, (pid, _name, _code) in enumerate(PROJECTS):
        history.append({
            "id": f"change:project:{pid}:status:1",
            "entity_id": f"project:{pid}",
            "entity_type": "project",
            "field": "status",
            "old_value": "planned",
            "new_value": STATUSES[i % len(STATUSES)],
            "date": f"2026-0{(i % 9) + 1}-05",
            "reason": "Routine status update.",
            "version": 1,
            "seq": 1,
            "made_by": _project_owner(i, people),
        })
    for i in range(25):
        pi = i % len(PROJECTS)
        t = i % 8
        _pid, _name, code = PROJECTS[pi]
        n = pi * 8 + t + 1
        history.append({
            "id": f"change:ticket:{code}-{100 + n}:status:1",
            "entity_id": f"ticket:{code}-{100 + n}",
            "entity_type": "ticket",
            "field": "status",
            "old_value": "open",
            "new_value": STATUSES[i % len(STATUSES)],
            "date": f"2026-0{(i % 9) + 1}-12",
            "reason": "Routine status update.",
            "version": 1,
            "seq": 1,
            "made_by": "person:ada-wren",
        })
    return history


def build_relationships():
    return {
        "SUPERSEDES": [
            {"src": "decision:self-host-queue", "dst": "decision:managed-queue",
             "origin": "chat:#platform-leads:2026-09-12T10:00:00Z"},
            {"src": "decision:vendor-choice-override", "dst": "decision:vendor-choice",
             "origin": "decision:vendor-choice-override:supersedes"},
            {"src": "doc:billing-rfc-v2", "dst": "doc:billing-rfc-v1",
             "origin": "doc:billing-rfc-v2:family"},
        ],
        "DEPENDS_ON": [
            {"src": "project:auth-migration", "dst": "project:identity-provider",
             "origin": "project:auth-migration:depends_on"},
        ],
        "CONTRADICTS": [
            {"src": "decision:search-elastic", "dst": "decision:search-postgres",
             "origin": "decision:search-elastic:contradicts"},
        ],
        "AFFECTS": [
            {"src": "decision:self-host-queue", "dst": "project:auth-migration",
             "origin": "decision:self-host-queue:affects"},
            {"src": "decision:search-elastic", "dst": "project:search-rebuild",
             "origin": "decision:search-elastic:affects"},
            {"src": "decision:search-postgres", "dst": "project:search-rebuild",
             "origin": "decision:search-postgres:affects"},
            {"src": "decision:managed-queue", "dst": "project:auth-migration",
             "origin": "decision:managed-queue:affects"},
        ],
        "DOCUMENTED_IN": [
            {"src": "decision:managed-queue", "dst": "doc:queue-adr",
             "origin": "decision:managed-queue:documented_in"},
            {"src": "decision:self-host-queue", "dst": "doc:self-host-adr",
             "origin": "decision:self-host-queue:documented_in"},
            {"src": "decision:search-elastic", "dst": "doc:search-elastic-adr",
             "origin": "decision:search-elastic:documented_in"},
            {"src": "decision:search-postgres", "dst": "doc:search-postgres-adr",
             "origin": "decision:search-postgres:documented_in"},
            # Trap 3: a current strategy doc resting on a superseded decision.
            {"src": "decision:managed-queue", "dst": "doc:platform-strategy",
             "origin": "decision:managed-queue:documented_in"},
            # Trap 9: a visible decision pointing at a restricted document.
            {"src": "decision:managed-queue", "dst": "doc:incident-postmortem",
             "origin": "decision:managed-queue:documented_in"},
        ],
        "ANNOUNCED_IN": [
            {"src": "decision:self-host-queue", "dst": "chat:#platform-leads:2026-09-12T10:00:00Z",
             "origin": "decision:self-host-queue:announced_in"},
        ],
        "TRIGGERED": [
            {"src": "doc:incident-postmortem", "dst": "decision:self-host-queue",
             "origin": "doc:incident-postmortem:triggered"},
        ],
        "MENTIONS": [
            {"src": "chat:#platform-leads:2026-09-12T09:00:00Z", "dst": "project:auth-migration",
             "origin": "chat:#platform-leads:2026-09-12T09:00:00Z:mentions"},
            {"src": "chat:#platform-leads:2026-09-12T10:00:00Z", "dst": "project:auth-migration",
             "origin": "chat:#platform-leads:2026-09-12T10:00:00Z:mentions"},
            {"src": "chat:#platform-leads:2026-10-15T09:30:00Z", "dst": "project:auth-migration",
             "origin": "chat:#platform-leads:2026-10-15T09:30:00Z:mentions"},
            {"src": "chat:#platform-leads:2026-09-12T09:00:00Z", "dst": "person:noah-fenn",
             "origin": "chat:#platform-leads:2026-09-12T09:00:00Z:mentions"},
        ],
        "SOURCED_FROM": [
            {"src": "decision:self-host-queue", "dst": "doc:self-host-adr",
             "origin": "decision:self-host-queue:source_from"},
        ],
    }


def build_traps():
    return [
        {"id": "decision-superseded-by-chat",
         "description": "A decision is superseded, and a chat message says so.",
         "entities": ["decision:managed-queue", "decision:self-host-queue",
                      "chat:#platform-leads:2026-09-12T10:00:00Z"]},
        {"id": "reassignment-owner-vs-expert",
         "description": "Owner moved teams but remains the expert on the old internals.",
         "entities": ["project:auth-migration", "person:priya-nair", "person:noah-fenn",
                      "ticket:PLAT-882", "doc:auth-internals-notes"]},
        {"id": "stale-strategy-document",
         "description": "A current document rests on a superseded decision.",
         "entities": ["doc:platform-strategy", "decision:managed-queue"]},
        {"id": "dissenter-with-evidence",
         "description": "A dissenting person with message evidence.",
         "entities": ["decision:self-host-queue", "person:dana-okoro",
                      "chat:#platform-leads:2026-09-12T10:05:00Z"]},
        {"id": "nonexistent-figure",
         "description": "No record states the auth migration completion percentage.",
         "entities": []},
        {"id": "false-premise",
         "description": "Hiring is frozen, so a premise that hiring is open is false.",
         "entities": ["decision:freeze-hiring"]},
        {"id": "two-conflicting-current-decisions",
         "description": "Two current decisions on one project contradict each other.",
         "entities": ["project:search-rebuild", "decision:search-elastic",
                      "decision:search-postgres"]},
        {"id": "hidden-depends-on",
         "description": "A non-obvious project dependency only recorded as DEPENDS_ON.",
         "entities": ["project:auth-migration", "project:identity-provider"]},
        {"id": "restricted-doc-linked-to-visible-decision",
         "description": "A clearance 3 document is linked to a visible decision.",
         "entities": ["doc:incident-postmortem", "decision:managed-queue"]},
        {"id": "restricted-only-intermediate-hop",
         "description": "Two visible people are linked only through a restricted team.",
         "entities": ["team:black-projects", "person:iris-lund", "person:ken-zhao"]},
        {"id": "restricted-superseder",
         "description": "A restricted decision supersedes a visible decision.",
         "entities": ["decision:vendor-choice", "decision:vendor-choice-override"]},
        {"id": "post-sync-records",
         "description": "A record is dated after last_synced_at.",
         "entities": ["chat:#platform-leads:2026-10-15T09:30:00Z"]},
    ]


def build() -> dict:
    people = build_people()
    sections = {
        "people": people,
        "teams": build_teams(),
        "projects": build_projects(people),
        "tickets": build_tickets(people),
        "documents": build_documents(),
        "decisions": build_decisions(people),
        "chat_messages": build_chat_messages(),
        "history": build_history(people),
    }
    record_counts = {k: len(v) for k, v in sections.items()}
    return {
        "metadata": {
            "dataset_version": DATASET_VERSION,
            "synthetic": True,
            "generated_by": "data/build_synthetic_dataset.py",
            "last_synced_at": LAST_SYNCED_AT,
            "record_counts": record_counts,
            "authoring_notes": (
                "Synthetic dataset for the demo. No real people, companies, mail, "
                "drive or chat. This metadata is never ingested."
            ),
        },
        "sections": sections,
        "relationships": build_relationships(),
        "traps": build_traps(),
    }


def main() -> int:
    dataset = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dataset, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")

    counts = {k: len(v) for k, v in dataset["sections"].items()}
    node_total = sum(counts.values())
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  nodes: {node_total} {counts}")
    print(f"  relationship types: {len(dataset['relationships'])}")
    print(f"  traps: {len(dataset['traps'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
