"""Step 10 unit tests: the router (05 section 0) and live-intent detection (06).

No database, no model: the router is deterministic. Covers the order the spec
sets out -- an explicit mode wins, then keyword rules, then an optional one-shot
classifier, then the default ``fast`` -- and the live-intent keyword table, which
decides whether the freshness guard turns on.
"""

from __future__ import annotations

from noodle.router import detect_live_intent, detect_mode, route

HEAVY_QUESTIONS = [
    "draft a note to the team",
    "write an update for stakeholders",
    "compose a summary of the project",
    "update the stakeholders of project z",
    "plan the rollout",
    "summarise all decisions this quarter",
]


# --------------------------------------------------------------------------- #
# Mode
# --------------------------------------------------------------------------- #


def test_an_explicit_mode_wins_over_a_keyword():
    decision = route("draft an email to the team", explicit="fast")
    assert decision.mode == "fast"
    assert decision.reason == "explicit"


def test_heavy_keywords_select_heavy():
    for question in HEAVY_QUESTIONS:
        assert route(question).mode == "heavy", question


def test_the_default_is_fast():
    decision = route("who owns project y?")
    assert decision.mode == "fast"
    assert decision.reason == "default"


def test_a_classifier_only_breaks_a_tie():
    assert route("an ambiguous question", classifier=lambda _: "heavy").mode == "heavy"

    # an explicit mode and a keyword still beat the classifier
    assert route("an ambiguous question", explicit="fast", classifier=lambda _: "heavy").mode == "fast"
    assert route("draft a note", classifier=lambda _: "fast").mode == "heavy"


def test_a_classifier_that_declines_leaves_the_default():
    mode, reason = detect_mode("who owns it?", classifier=lambda _: None)
    assert (mode, reason) == ("fast", "default")


# --------------------------------------------------------------------------- #
# Live intent (06)
# --------------------------------------------------------------------------- #

LIVE_POSITIVE = [
    "what happened today?",
    "what is the latest on the migration?",
    "is it done now?",
    "what is the status right now?",
    "anything this morning?",
    "what changed this hour?",
    "any breaking news?",
    "just checking for updates",
    "give me a status update",
    "which decision is currently in effect?",
]

LIVE_NEGATIVE = [
    "who owns project y?",
    "what is the status of the auth migration?",
    "why was decision x made?",
    "how many documents mention the rollout?",
    "is decision x still current?",
    "who should I talk to about authentication?",
]


def test_live_intent_detection_table():
    for question in LIVE_POSITIVE:
        assert detect_live_intent(question)[0] is True, question
    for question in LIVE_NEGATIVE:
        assert detect_live_intent(question)[0] is False, question


def test_live_intent_uses_a_classifier_as_a_fallback_only():
    assert detect_live_intent("an ambiguous question", classifier=lambda _: True)[0] is True
    assert detect_live_intent("an ambiguous question", classifier=lambda _: None)[0] is False

    # a keyword still wins, so the classifier is not consulted
    def exploding(_):
        raise AssertionError("the classifier must not run when a keyword matches")

    assert detect_live_intent("what happened today?", classifier=exploding)[0] is True


def test_route_reports_live_intent():
    assert route("what happened today?").live_intent is True
    assert route("who owns project y?").live_intent is False
