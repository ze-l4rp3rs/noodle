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


PROVIDERS = GOOD + """
providers:
  default: anthropic
  anthropic:
    key_env: ANTHROPIC_API_KEY
    generator_model: big
    reviewer_model: small
  google:
    key_env: GOOGLE_API_KEY
    generator_model: g
    reviewer_model: g
modes:
  fast:
    generator_model: small
    reviewer_model: small
    tools: [search_records]
    tool_rounds: 4
    fix_rounds: 1
    timeout_s: 30
generation:
  max_tokens: 1024
  temperature: 0.2
"""


def test_providers_and_default_are_parsed(tmp_path):
    settings = load_settings(_write(tmp_path, PROVIDERS))
    assert settings.default_provider == "anthropic"
    assert [p.name for p in settings.providers] == ["anthropic", "google"]
    anthropic = settings.providers[0]
    assert anthropic.key_env == "ANTHROPIC_API_KEY"
    assert anthropic.generator_model == "big" and anthropic.reviewer_model == "small"


def test_a_secret_looking_key_env_is_rejected(tmp_path):
    bad = PROVIDERS.replace("key_env: ANTHROPIC_API_KEY", "key_env: sk-ant-1234567890")
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(tmp_path, bad))
    assert "key_env" in str(exc.value)


def test_unknown_default_provider_is_rejected(tmp_path):
    bad = PROVIDERS.replace("default: anthropic", "default: nobody")
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(tmp_path, bad))
    assert "default" in str(exc.value)


def test_modes_and_generation_are_parsed(tmp_path):
    settings = load_settings(_write(tmp_path, PROVIDERS))
    fast = settings.modes["fast"]
    assert fast.tools == ("search_records",)
    assert fast.tool_rounds == 4 and fast.fix_rounds == 1 and fast.timeout_s == 30
    assert settings.generation.max_tokens == 1024
    assert settings.generation.temperature == 0.2


def test_providers_and_modes_default_when_absent(tmp_path):
    settings = load_settings(_write(tmp_path, GOOD))
    assert settings.providers == () and settings.default_provider is None
    assert settings.modes == {}
    assert settings.generation.max_tokens == 2000 and settings.generation.temperature == 0.0
