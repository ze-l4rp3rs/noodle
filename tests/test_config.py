from __future__ import annotations

import pytest

from noodle.config import ConfigError, load_settings


def _write(tmp_path, text: str):
    path = tmp_path / "noodle.yaml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD = """
graph:
  url: localhost:6379
  name: noodle
  embedding_dimension: 1536
api:
  host: 0.0.0.0
  port: 8000
  debug: false
  max_live_update_size_mb: 1
logs:
  folder: ./runs
"""


def test_loads_defaults(tmp_path):
    path = _write(tmp_path, GOOD)
    settings = load_settings(path)
    assert settings.graph.name == "noodle"
    assert settings.graph.embedding_dimension == 1536
    assert settings.api.port == 8000
    assert settings.is_prod is False


def test_falkordb_url_env_overrides_file(tmp_path, monkeypatch):
    path = _write(tmp_path, GOOD)
    monkeypatch.setenv("FALKORDB_URL", "redis://example:1234")
    settings = load_settings(path)
    assert settings.graph.url == "redis://example:1234"


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_settings(tmp_path / "nope.yaml")
    assert "nope.yaml" in str(exc.value)


def test_bad_setting_names_the_setting(tmp_path):
    bad = GOOD.replace("embedding_dimension: 1536", "embedding_dimension: 0")
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(tmp_path, bad))
    assert "embedding_dimension" in str(exc.value)


def test_missing_section_is_reported(tmp_path):
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(tmp_path, "graph: {url: x}\n"))
    assert "api" in str(exc.value)


def test_prod_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOODLE_ENV", "prod")
    settings = load_settings(_write(tmp_path, GOOD))
    assert settings.is_prod is True
