"""Sources: the run's citable records and the text block the model is shown.

A source wraps one :class:`~noodle.tools.Evidence` and gives it a run-random
eight-hex id. The id is what a citation references (``[[id]]``, 05 section 1);
the record's own graph id stays on the evidence and becomes the reference's
``nodeId`` (07), so two records never share a marker and the UI can highlight
the exact node behind each citation.

Retrieved text can never forge structure (06, security): :func:`source_block`
escapes the citation brackets inside every record, and because the ids are
random per run they cannot be guessed from retrieved text either. This module
holds no company, person or record name.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Iterable, Iterator, Optional

from .tools import Evidence

# Full-width brackets stand in for a record's literal brackets; a record can
# never close a marker the system opened.
_LBRACKET = "\uff3b"  # ［
_RBRACKET = "\uff3d"  # ］


@dataclass(frozen=True)
class Source:
    """One citable record: a run-random ``id`` and the evidence behind it."""

    id: str
    evidence: Evidence
    number: Optional[int] = None  # assigned when the answer is rendered

    @property
    def ref(self) -> str:
        return self.evidence.ref

    @property
    def kind(self) -> str:
        return self.evidence.kind

    @property
    def text(self) -> str:
        return self.evidence.text


class SourceRegistry:
    """The sources of one run, in the order they were first seen.

    A record is added once: adding the same ref (or, for a ref-less evidence
    item, the same kind/text/date/label) returns the existing source, so two
    tools reporting one record never produce two references.
    """

    def __init__(self, rng: Optional[object] = None):
        self._rng = rng or secrets
        self._by_key: dict[object, Source] = {}
        self._by_id: dict[str, Source] = {}
        self._order: list[Source] = []

    def _new_id(self) -> str:
        while True:
            candidate = str(self._rng.token_hex(4)).lower()
            if candidate not in self._by_id:
                return candidate

    def add(self, evidence: Evidence) -> Source:
        key: object = evidence.ref or (evidence.kind, evidence.text, evidence.date, evidence.label)
        existing = self._by_key.get(key)
        if existing is not None:
            return existing
        source = Source(id=self._new_id(), evidence=evidence)
        self._by_key[key] = source
        self._by_id[source.id] = source
        self._order.append(source)
        return source

    def add_all(self, evidence: Iterable[Evidence]) -> list[Source]:
        return [self.add(item) for item in evidence]

    def get(self, source_id: str) -> Optional[Source]:
        return self._by_id.get(source_id)

    def has(self, source_id: str) -> bool:
        return source_id in self._by_id

    def by_ref(self, ref: str) -> Optional[Source]:
        return self._by_key.get(ref)

    def ids(self) -> list[str]:
        return [source.id for source in self._order]

    @property
    def sources(self) -> list[Source]:
        return list(self._order)

    def __len__(self) -> int:
        return len(self._order)

    def __iter__(self) -> Iterator[Source]:
        return iter(self._order)


def escape_markers(text: str) -> str:
    """Neutralise citation brackets in retrieved text (06, security)."""

    return text.replace("[[", _LBRACKET * 2).replace("]]", _RBRACKET * 2)


def unescape_markers(text: str) -> str:
    """Reverse :func:`escape_markers`."""

    return text.replace(_LBRACKET * 2, "[[").replace(_RBRACKET * 2, "]]")


def source_block(registry: SourceRegistry, escape: bool = True) -> str:
    """The source listing the generator and reviewer are shown.

    One line per source: its citation marker, kind, label, date and text. The
    text is escaped unless ``escape`` is false, so retrieved content cannot open
    or close a marker.
    """

    lines: list[str] = []
    for source in registry:
        evidence = source.evidence
        label = evidence.label or evidence.ref or ""
        body = escape_markers(evidence.text) if escape else evidence.text
        date = f" ({evidence.date})" if evidence.date else ""
        lines.append(f'[[{source.id}]] {evidence.kind} "{label}"{date} {body}'.rstrip())
    return "\n".join(lines)


__all__ = [
    "Source",
    "SourceRegistry",
    "escape_markers",
    "unescape_markers",
    "source_block",
]
