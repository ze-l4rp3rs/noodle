from __future__ import annotations

import json
from pathlib import Path

from noodle.ingestion import (
    load_source_path,
    normalize_dataset,
    sha256_file,
)


def test_normalize_dataset_nested_and_flat():
    flat = {
        "metadata": {"test": True},
        "people": [{"id": "person:p1", "name": "Alice"}],
        "slack_messages": [{"id": "chat:c1", "text": "hello"}],
    }
    normalized = normalize_dataset(flat)
    assert "people" in normalized["sections"]
    assert "chat_messages" in normalized["sections"]
    assert normalized["sections"]["chat_messages"][0]["text"] == "hello"

    nested = {
        "metadata": {"test": True},
        "sections": {
            "teams": [{"id": "team:t1", "name": "Core"}],
        },
    }
    normalized_nested = normalize_dataset(nested)
    assert "teams" in normalized_nested["sections"]


def test_load_source_path_json(tmp_path: Path):
    data_file = tmp_path / "data.json"
    data = {
        "sections": {
            "projects": [{"id": "project:p1", "name": "Alpha"}],
        }
    }
    data_file.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_source_path(data_file)
    assert "projects" in loaded["sections"]
    assert loaded["sections"]["projects"][0]["name"] == "Alpha"


def test_load_source_path_text_document(tmp_path: Path):
    doc_file = tmp_path / "architecture.md"
    doc_file.write_text("# Title\nArchitecture content goes here.", encoding="utf-8")
    loaded = load_source_path(doc_file)
    assert "documents" in loaded["sections"]
    doc = loaded["sections"]["documents"][0]
    assert doc["id"] == "architecture.md"
    assert "Architecture content" in doc["content"]


def test_load_source_path_directory(tmp_path: Path):
    sub = tmp_path / "data"
    sub.mkdir()
    (sub / "notes.txt").write_text("Meeting notes from Q1", encoding="utf-8")
    (sub / "dataset.json").write_text(
        json.dumps({"tickets": [{"id": "ticket:t1", "title": "Fix bug"}]}),
        encoding="utf-8",
    )
    loaded = load_source_path(sub)
    assert "tickets" in loaded["sections"]
    assert "documents" in loaded["sections"]
    doc = loaded["sections"]["documents"][0]
    assert doc["id"] == "notes.txt"
    assert "Meeting notes" in doc["content"]


def test_sha256_file(tmp_path: Path):
    test_file = tmp_path / "test.txt"
    test_file.write_text("abc", encoding="utf-8")
    h = sha256_file(test_file)
    assert len(h) == 64
