"""Caller identity: turn a request into a :class:`~noodle.queries.UserCtx` (07).

The user is never taken from the request body; the model can never supply or
change it (06). Identity arrives as ``Authorization: Bearer <token>``, or, in
development only, as the ``X-Noodle-User: person:xyz`` header (07). The claimed
person id is then resolved against the graph: their clearance and team come from
their Person node, and an unknown user is clearance 0 (06), which leaks nothing.

No company, person or record name appears in this module.
"""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

from . import schema
from .graph import GraphClient, GraphError
from .queries import UserCtx, q14_person

# 07: the development identity header, disabled when NOODLE_ENV=prod.
DEV_HEADER = "x-noodle-user"
# An optional JSON map, token -> person id, for demo/staging bearer tokens. This
# is not a secret store: a real single-sign-on service replaces it in production.
AUTH_TOKENS_ENV = "NOODLE_AUTH_TOKENS"


def _token_map() -> dict[str, str]:
    """The configured bearer-token map, or empty. Malformed config is ignored."""

    raw = os.environ.get(AUTH_TOKENS_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if isinstance(k, str) and isinstance(v, str)}


def claim_from_headers(headers: Mapping[str, Any], *, is_prod: bool) -> str:
    """The person id the request claims to be, or ``""`` for anonymous (07).

    In development the ``X-Noodle-User`` header wins. A bearer token is mapped
    through ``NOODLE_AUTH_TOKENS`` when configured; in development an unmapped
    token is taken as the person id itself, so a token can name a demo user. In
    production an unmapped token is refused (anonymous).
    """

    if not is_prod:
        dev = headers.get(DEV_HEADER)
        if dev and str(dev).strip():
            return str(dev).strip()

    authorization = str(headers.get("authorization") or "")
    if authorization.lower().startswith("bearer "):
        token = authorization[len("bearer ") :].strip()
        if token:
            mapping = _token_map()
            if token in mapping:
                return mapping[token]
            if not is_prod:
                return token
    return ""


def _clearance(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        if schema.CLEARANCE_MIN <= value <= schema.CLEARANCE_MAX:
            return value
    return schema.CLEARANCE_MIN


def resolve_user(client: GraphClient, claimed: str, *, graph_up: bool = True) -> UserCtx:
    """Map a claimed person id to a :class:`UserCtx`, reading clearance from the graph.

    An empty claim or an unknown user is clearance 0 (06). When the graph is down
    the lookup is skipped and the claim stays clearance 0, so a resolution failure
    can never *raise* a caller's access.
    """

    if not claimed:
        return UserCtx.anonymous()
    if not graph_up:
        return UserCtx(id=claimed, clearance=schema.CLEARANCE_MIN)
    try:
        rows = q14_person(client, claimed).rows
    except GraphError:
        return UserCtx(id=claimed, clearance=schema.CLEARANCE_MIN)
    if not rows:
        return UserCtx(id=claimed, clearance=schema.CLEARANCE_MIN)
    row = rows[0]
    team = row.get("team") or None
    return UserCtx(id=claimed, clearance=_clearance(row.get("clearance")), team=team)


def user_for_request(request: Any, runtime: Any) -> UserCtx:
    """Resolve the caller for one HTTP request against the runtime."""

    claimed = claim_from_headers(request.headers, is_prod=runtime.settings.is_prod)
    return resolve_user(runtime.client, claimed, graph_up=runtime.graph_up())


__all__ = ["DEV_HEADER", "AUTH_TOKENS_ENV", "claim_from_headers", "resolve_user", "user_for_request"]
