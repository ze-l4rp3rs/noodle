"""The router: fast or heavy (05 section 0) and live-intent detection (06).

One loop, two configs. The chosen mode picks the generator and reviewer models,
the tool set and the limits; live intent turns on the freshness guard. Both
decisions follow the same order:

1. an explicit choice on the request wins;
2. otherwise a deterministic keyword rule;
3. otherwise an optional one-shot classifier (a model call the caller injects);
4. otherwise the default -- ``fast`` for the mode, and no live intent.

Keeping the rules here, not in the model, means the same question always routes
the same way, and a test needs no model to check it. Keywords are matched on word
boundaries, so "now" never fires on "know" and "just" never on "justice".

No company, person or record name appears in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

# 05 section 0: draft, write, compose, update the, plan, summarise all.
HEAVY_KEYWORDS = (
    "draft",
    "write",
    "compose",
    "update the",
    "plan",
    "summarise all",
)

# 06: today, now, right now, latest, currently, this morning, just, breaking,
# this hour, status update.
LIVE_INTENT_KEYWORDS = (
    "today",
    "now",
    "right now",
    "latest",
    "currently",
    "this morning",
    "just",
    "breaking",
    "this hour",
    "status update",
)

# A classifier is a one-shot model call supplied by the caller: it returns a mode
# ("fast"/"heavy") or, for live intent, a boolean, or None to decline.
ModeClassifier = Callable[[str], Optional[str]]
LiveClassifier = Callable[[str], Optional[bool]]


def _compile(word: str) -> re.Pattern[str]:
    phrase = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(r"\b" + phrase + r"\b", re.IGNORECASE)


_MODE_RULES = tuple((word, _compile(word)) for word in HEAVY_KEYWORDS)
_LIVE_RULES = tuple((word, _compile(word)) for word in LIVE_INTENT_KEYWORDS)


def _first_match(rules: tuple[tuple[str, "re.Pattern[str]"], ...], question: str) -> Optional[str]:
    for word, pattern in rules:
        if pattern.search(question):
            return word
    return None


@dataclass(frozen=True)
class RouteDecision:
    """What the router chose: the mode and whether the question is live-intent."""

    mode: str
    reason: str  # explicit | keyword:<word> | classifier | default
    live_intent: bool
    live_reason: str


def detect_mode(
    question: str,
    *,
    explicit: Optional[str] = None,
    classifier: Optional[ModeClassifier] = None,
) -> tuple[str, str]:
    """The mode for ``question``: explicit, then keyword, then classifier, then fast."""

    if explicit:
        return str(explicit), "explicit"
    hit = _first_match(_MODE_RULES, question)
    if hit is not None:
        return "heavy", f"keyword:{hit}"
    if classifier is not None:
        guess = classifier(question)
        if guess in ("fast", "heavy"):
            return guess, "classifier"
    return "fast", "default"


def detect_live_intent(
    question: str, *, classifier: Optional[LiveClassifier] = None
) -> tuple[bool, str]:
    """Whether ``question`` asks about live state, and why (06).

    A keyword decides; the classifier is only consulted when none matches, so a
    keyword question never spends a model call.
    """

    hit = _first_match(_LIVE_RULES, question)
    if hit is not None:
        return True, f"keyword:{hit}"
    if classifier is not None:
        guess = classifier(question)
        if guess is not None:
            return bool(guess), "classifier"
    return False, "default"


def route(
    question: str,
    *,
    explicit: Optional[str] = None,
    classifier: Optional[ModeClassifier] = None,
    live_classifier: Optional[LiveClassifier] = None,
) -> RouteDecision:
    """The full routing decision for one question (05 section 0, 06)."""

    mode, reason = detect_mode(question, explicit=explicit, classifier=classifier)
    live_intent, live_reason = detect_live_intent(question, classifier=live_classifier)
    return RouteDecision(mode, reason, live_intent, live_reason)


__all__ = [
    "HEAVY_KEYWORDS",
    "LIVE_INTENT_KEYWORDS",
    "RouteDecision",
    "detect_live_intent",
    "detect_mode",
    "route",
]
