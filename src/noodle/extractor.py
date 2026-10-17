"""Multi-format document extraction using xberg.

Extracts text and metadata from files (PDF, DOCX, XML, Markdown, plain text, etc.)
so that documents can be ingested into Document nodes in the graph.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any


class ExtractionError(RuntimeError):
    """A file could not be turned into text."""


class ExtractionUnavailable(ExtractionError):
    """The extraction library (xberg) is not installed."""


def _await(coro):
    # xberg is async; ingestion is sync. Run in a fresh loop, or a worker thread.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def worker():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as caught:  # noqa: BLE001 - re-raised on calling thread
            box["error"] = caught

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _metadata(document: Any) -> dict[str, Any]:
    source = getattr(document, "metadata", None)
    if source is None:
        return {}
    meta: dict[str, Any] = {}
    for key in ("title", "language", "format"):
        value = getattr(source, key, None)
        if value:
            meta[key] = _plain(value)
    return meta


def extract_bytes(
    data: bytes,
    filename: str = "",
    content_type: str = "",
) -> tuple[str, dict[str, Any]]:
    """Extract text and metadata from file bytes with xberg."""
    try:
        from xberg import ExtractInput, extract
    except ImportError as caught:
        raise ExtractionUnavailable("xberg is not installed; run: pip install xberg") from caught

    kwargs: dict[str, Any] = {"kind": "bytes", "bytes": data}
    if filename:
        kwargs["filename"] = filename
    if content_type:
        kwargs["mime_type"] = content_type
    try:
        output = _await(extract(ExtractInput(**kwargs)))
    except Exception as caught:  # noqa: BLE001
        raise ExtractionError(str(caught)) from caught

    document = output.results[0] if getattr(output, "results", None) else None
    if document is None:
        errors = "; ".join(str(e) for e in getattr(output, "errors", None) or []) or "no readable content"
        raise ExtractionError(errors)
    content = (document.content or "").strip()
    if not content:
        raise ExtractionError("the file produced no text")
    return content, _metadata(document)
