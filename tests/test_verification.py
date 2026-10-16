"""Step 7 unit tests: quote matching, citation repair and sentence checks.

No model is ever involved: verification is mechanical (05 step 2). The done-when
cases are the one-letter-off quote repair and the removal of an invented
sentence, both with no model call.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from noodle import verification as V
from noodle.sources import SourceRegistry
from noodle.tools import Evidence

ROOT = Path(__file__).resolve().parent.parent


def ev(ref: str = "doc:one", text: str = "body", kind: str = "doc", label: str = "", **kw) -> Evidence:
    return Evidence(kind=kind, label=label or ref, text=text, ref=ref, **kw)


def reg(*evidence: Evidence) -> SourceRegistry:
    registry = SourceRegistry()
    for item in evidence:
        registry.add(item)
    return registry


# --------------------------------------------------------------------------- #
# Normalisation and quote matching
# --------------------------------------------------------------------------- #


def test_normalize_forgives_case_whitespace_quotes_and_dashes():
    assert V.normalize("The  Cat\nsat") == "the cat sat"
    assert V.normalize("say \u201chello\u201d") == V.normalize('say "hello"')
    assert V.normalize("a\u2014b") == V.normalize("a-b")
    assert V.normalize("a\u2013b") == V.normalize("a-b")
    assert V.normalize("it\u2019s") == V.normalize("it's")


def test_quote_in_source_is_exact_and_forgiving():
    src = "The rollout owner is Omar Diaz."
    assert V.quote_in_source("The rollout owner is Omar Diaz.", src)
    assert V.quote_in_source("the rollout owner is omar diaz.", src)  # case
    assert V.quote_in_source("The  rollout\nowner is Omar Diaz.", src)  # whitespace
    assert V.quote_in_source("say \u201cquote\u201d", 'say "quote"')


def test_quote_in_source_rejects_a_changed_word():
    assert not V.quote_in_source("The rollout owner is Ana Diaz.", "The rollout owner is Omar Diaz.")


def test_quote_in_source_allows_ellipsis_to_skip_words():
    src = "The rollout owner is Omar Diaz and the target date is October first."
    assert V.quote_in_source("The rollout owner ... October first", src)
    assert not V.quote_in_source("October first ... The rollout owner", src)  # order matters


def test_best_passage_repairs_one_letter_off():
    passage, ratio = V.best_passage("The cat sat on the mat and purrd.", "The cat sat on the mat and purred.")
    assert passage == "The cat sat on the mat and purred."
    assert ratio >= 0.9


def test_best_passage_gives_up_when_unrelated():
    passage, ratio = V.best_passage("A completely different sentence here.", "The cat sat.")
    assert passage is None
    assert ratio < 0.9


# --------------------------------------------------------------------------- #
# Citation repair
# --------------------------------------------------------------------------- #


def test_repair_citations_closes_missing_brackets():
    assert V.repair_citations("fact [[09a121fe]]") == "fact [[09a121fe]]"
    assert V.repair_citations("fact [[09a121fe]") == "fact [[09a121fe]]"
    assert V.repair_citations("fact [09a121fe]]") == "fact [[09a121fe]]"
    assert V.repair_citations("fact [09a121fe]") == "fact [[09a121fe]]"
    assert V.repair_citations("see doc:one") == "see doc:one"  # not a citation


def test_repair_quotes_fixes_one_letter_off_without_a_model():
    registry = reg(ev(ref="doc:one", text="The cat sat on the mat and purred."))
    sid = registry.ids()[0]
    draft = f'X [[{sid}]] "The cat sat on the mat and purrd."'
    assert '"The cat sat on the mat and purred."' in V.repair_quotes(draft, registry)


def test_repair_quotes_leaves_a_valid_quote_alone():
    registry = reg(ev(ref="doc:one", text="The cat sat on the mat."))
    sid = registry.ids()[0]
    draft = f'X [[{sid}]] "The cat sat on the mat."'
    assert V.repair_quotes(draft, registry) == draft


def test_repair_quotes_leaves_an_unrepairable_quote_alone():
    registry = reg(ev(ref="doc:one", text="The cat sat on the mat."))
    sid = registry.ids()[0]
    draft = f'X [[{sid}]] "Completely unrelated words in this quote."'
    assert V.repair_quotes(draft, registry) == draft


# --------------------------------------------------------------------------- #
# Sentence splitting
# --------------------------------------------------------------------------- #


def test_split_sentences_basic():
    assert V.split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]


def test_split_sentences_keeps_abbreviations_initials_and_versions_together():
    text = "Use e.g. this, then J. Smith shipped v4.2.1. Done."
    assert V.split_sentences(text) == ["Use e.g. this, then J. Smith shipped v4.2.1.", "Done."]


def test_split_sentences_ignores_periods_inside_quotes():
    got = V.split_sentences('He said "It works. Really." and left. Done.')
    assert got == ['He said "It works. Really." and left.', "Done."]


def test_split_sentences_ignores_periods_inside_a_marker_and_quote():
    got = V.split_sentences('See [[09a121fe]] "a. b." and more. Y.')
    assert got == ['See [[09a121fe]] "a. b." and more.', "Y."]


def test_split_sentences_keeps_a_trailing_citation_with_its_fact():
    got = V.split_sentences('The cat sat. [[09a121fe]] "The cat sat." The dog ran.')
    assert got == ['The cat sat. [[09a121fe]] "The cat sat."', "The dog ran."]


def test_split_draft_classifies_headings_gaps_and_facts():
    registry = reg(ev(ref="doc:one", text="body"))
    sid = registry.ids()[0]
    draft = (
        "# Status\n"
        f'The owner is set. [[{sid}]] "body"\n'
        "\n"
        "Not in company records\n"
        "- the completion percentage\n"
        "- the dependency ticket status\n"
    )
    sentences = V.split_draft(draft)
    assert {s.kind for s in sentences} == {"heading", "gap", "fact"}
    assert [s.text for s in sentences if s.kind == "gap"] == [
        "- the completion percentage",
        "- the dependency ticket status",
    ]
    assert [s.number for s in sentences] == list(range(1, len(sentences) + 1))


# --------------------------------------------------------------------------- #
# Sentence checks
# --------------------------------------------------------------------------- #


def test_check_draft_accepts_a_clean_sentence():
    registry = reg(ev(ref="doc:one", text="The target date is October first."))
    sid = registry.ids()[0]
    sent = V.split_draft(f'The target date is October first. [[{sid}]] "The target date is October first."')
    assert V.check_draft(sent, registry) == []


def test_check_draft_flags_each_kind():
    registry = reg(ev(ref="doc:one", text="The target date is October first."))
    sid = registry.ids()[0]

    uncited = V.split_draft("An invented claim with no source.")
    assert [p.kind for p in V.check_draft(uncited, registry)] == ["no_citation"]

    unknown = V.split_draft('A claim. [[deadbeef]] "x"')
    assert "unknown_source" in [p.kind for p in V.check_draft(unknown, registry)]

    missing_quote = V.split_draft(f"A claim. [[{sid}]]")
    assert "quote_missing" in [p.kind for p in V.check_draft(missing_quote, registry)]

    not_in = V.split_draft(f'A claim. [[{sid}]] "not in the source at all"')
    assert "quote_not_in_source" in [p.kind for p in V.check_draft(not_in, registry)]


def test_check_draft_allows_a_citation_without_a_quote_when_quotes_not_required():
    registry = reg(ev(ref="doc:one", text="body"))
    sid = registry.ids()[0]
    sent = V.split_draft(f"[[{sid}]]")
    assert V.check_draft(sent, registry, quotes_required=False) == []


def test_check_draft_flags_plain_source_and_fake_link():
    registry = reg(ev(ref="doc:one", text="body", url="https://records.example/doc/one"))
    sid = registry.ids()[0]

    plain = V.split_draft(f'See doc:one for the detail. [[{sid}]] "body"')
    assert "plain_source" in [p.kind for p in V.check_draft(plain, registry)]

    fake = V.split_draft(f'See [the doc](https://evil.example/x). [[{sid}]] "body"')
    assert "fake_link" in [p.kind for p in V.check_draft(fake, registry)]

    legit = V.split_draft(f'See https://records.example/doc/one for detail. [[{sid}]] "body"')
    assert "fake_link" not in [p.kind for p in V.check_draft(legit, registry)]


def test_check_draft_flags_leaked_reasoning():
    registry = reg(ev(ref="doc:one", text="body"))
    sid = registry.ids()[0]
    sent = V.split_draft(f'Let me think about it. [[{sid}]] "body"')
    assert "leaked_reasoning" in [p.kind for p in V.check_draft(sent, registry)]


def test_check_draft_flags_a_same_day_claim_without_a_same_day_source():
    registry = reg(ev(ref="doc:one", text="The service restarted.", date="2026-10-01"))
    sid = registry.ids()[0]
    sent = V.split_draft(f'The service restarted today. [[{sid}]] "The service restarted."')
    probs = V.check_draft(sent, registry, last_synced_at="2026-10-15T00:05:00Z")
    assert "live_claim" in [p.kind for p in probs]


def test_check_draft_allows_a_same_day_claim_backed_by_a_same_day_source():
    registry = reg(ev(ref="doc:one", text="The service restarted.", date="2026-10-15"))
    sid = registry.ids()[0]
    sent = V.split_draft(f'The service restarted today. [[{sid}]] "The service restarted."')
    probs = V.check_draft(sent, registry, last_synced_at="2026-10-15T00:05:00Z")
    assert "live_claim" not in [p.kind for p in probs]


def test_check_draft_raises_on_hidden_material():
    registry = reg(ev(ref="secret:doc", text="restricted words"))
    sid = registry.ids()[0]
    sent = V.split_draft(f'A fact. [[{sid}]] "restricted words"')
    with pytest.raises(V.HiddenDataError):
        V.check_draft(sent, registry, forbidden_refs={"secret:doc"})


# --------------------------------------------------------------------------- #
# verify(): the whole mechanical pass
# --------------------------------------------------------------------------- #


def test_verify_repairs_a_misquote_and_drops_an_invented_sentence():
    registry = reg(ev(ref="doc:one", text="The cat sat on the mat and purred."))
    sid = registry.ids()[0]
    draft = (
        f'The cat sat on the mat and purrd. [[{sid}]] "The cat sat on the mat and purrd."\n'
        "The moon is made of cheese.\n"
    )
    result = V.verify(draft, registry)
    assert result.references[0].quotes == ["The cat sat on the mat and purred."]
    assert "cheese" not in result.text
    assert any("cheese" in u["sentence"] for u in result.unverified)
    assert result.status == "partly_verified"


def test_verify_numbers_references_and_carries_node_id_and_freshness():
    registry = reg(
        ev(kind="chat", ref="chat:1", text="first message"),
        ev(kind="chat", ref="chat:2", text="second message"),
        ev(kind="decision", ref="decision:x", text="old decision", status="superseded",
           note="superseded by a newer decision"),
    )
    a, b, c = registry.ids()
    draft = "\n".join([
        f'Alpha. [[{a}]] "first message"',
        f'Beta. [[{b}]] "second message"',
        f'Gamma. [[{c}]] "old decision"',
    ])
    result = V.verify(draft, registry)
    by_id = {ref.id: ref for ref in result.references}
    assert [by_id[a].number, by_id[b].number, by_id[c].number] == [1, 2, 3]
    assert by_id[a].node_id == "chat:1" and by_id[b].node_id == "chat:2"
    assert by_id[a].node_id != by_id[b].node_id
    assert by_id[a].type == "chat"
    assert by_id[a].excerpt == "first message"
    assert by_id[c].freshness == "superseded"
    assert by_id[c].freshness_note == "superseded by a newer decision"
    assert by_id[a].to_dict()["nodeId"] == "chat:1"


def test_verify_reference_type_maps_connection_to_role_and_team_to_project():
    registry = reg(
        ev(kind="connection", ref="connection:a|b|0", text="a -X- b", label="a to b"),
        ev(kind="team", ref="team:x", text="a team", label="a team"),
    )
    cid, tid = registry.ids()
    draft = f'A. [[{cid}]] "a -X- b"\nB. [[{tid}]] "a team"'
    result = V.verify(draft, registry)
    types = {ref.id: ref.type for ref in result.references}
    assert types[cid] == "role"
    assert types[tid] == "project"


def test_verify_collects_missing_from_the_gap_list():
    registry = reg(ev(ref="doc:one", text="body"))
    sid = registry.ids()[0]
    draft = (
        f'The owner is set. [[{sid}]] "body"\n'
        "\n"
        "Not in company records\n"
        "- the completion percentage\n"
        "- the dependency ticket status\n"
    )
    result = V.verify(draft, registry)
    assert "the completion percentage" in result.missing
    assert "the dependency ticket status" in result.missing
    assert result.status == "partly_verified"


def test_verify_falls_back_to_the_fixed_message_when_nothing_verifiable_remains():
    registry = reg(ev(ref="doc:one", text="body"))
    result = V.verify("An invented claim with no source at all.", registry)
    assert result.text == V.NO_ANSWER
    assert result.references == []
    assert result.status == "partly_verified"


def test_verify_raises_on_hidden_material():
    registry = reg(ev(ref="secret:doc", text="restricted words"))
    sid = registry.ids()[0]
    with pytest.raises(V.HiddenDataError):
        V.verify(f'A fact. [[{sid}]] "restricted words"', registry, forbidden_refs={"secret:doc"})


def test_verification_never_imports_a_provider_or_model_module():
    code = (
        "import sys, noodle.verification; "
        "assert 'noodle.providers' not in sys.modules, 'provider imported'; "
        "assert 'noodle.loop' not in sys.modules, 'loop imported'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
