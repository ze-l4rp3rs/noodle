"""Typed, read-only configuration loaded from a single YAML file.

Secrets never live here: provider settings name the environment variable that
holds a key, never the key itself. Only three environment variables override the
file: ``FALKORDB_URL``, ``NOODLE_ENV`` and ``NOODLE_CONFIG`` (plus provider keys,
which are read by the provider layer, not this module).

Settings are read once and cached. A test may reset the cache for its own run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("noodle.yaml")


class ConfigError(Exception):
    """Raised when the configuration file is missing or a setting is invalid."""


@dataclass(frozen=True)
class GraphSettings:
    url: str
    name: str
    embedding_dimension: int


@dataclass(frozen=True)
class ApiSettings:
    host: str
    port: int
    debug: bool
    max_live_update_size_mb: int


@dataclass(frozen=True)
class LogsSettings:
    folder: str


@dataclass(frozen=True)
class Settings:
    graph: GraphSettings
    api: ApiSettings
    logs: LogsSettings
    source: Path
    env: str

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"


_CACHE: Optional[Settings] = None


def _require_section(raw: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        raise ConfigError(f"{source}: missing required section '{key}'")
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: setting '{key}' must be a mapping")
    return value


def _get(section: dict[str, Any], key: str, default: Any, where: str) -> Any:
    if key in section and section[key] is not None:
        return section[key]
    if default is None:
        raise ConfigError(f"{where}: missing required setting '{key}'")
    return default


def load_settings(path: Optional[str | Path] = None) -> Settings:
    """Load and return settings. Errors name the setting that is wrong."""

    global _CACHE
    if path is None and _CACHE is not None:
        return _CACHE

    config_path = Path(path or os.environ.get("NOODLE_CONFIG") or DEFAULT_CONFIG_PATH)
    if not config_path.is_file():
        raise ConfigError(
            f"config file not found: {config_path} (set NOODLE_CONFIG to point elsewhere)"
        )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"{config_path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: top level must be a mapping")

    graph_raw = _require_section(raw, "graph", config_path)
    api_raw = _require_section(raw, "api", config_path)
    logs_raw = _require_section(raw, "logs", config_path)

    graph_url = os.environ.get("FALKORDB_URL") or _get(
        graph_raw, "url", "localhost:6379", f"{config_path}:graph"
    )
    dimension = int(_get(graph_raw, "embedding_dimension", 1536, f"{config_path}:graph"))
    if dimension <= 0:
        raise ConfigError(f"{config_path}: graph.embedding_dimension must be positive")

    settings = Settings(
        graph=GraphSettings(
            url=str(graph_url),
            name=str(_get(graph_raw, "name", "noodle", f"{config_path}:graph")),
            embedding_dimension=dimension,
        ),
        api=ApiSettings(
            host=str(_get(api_raw, "host", "0.0.0.0", f"{config_path}:api")),
            port=int(_get(api_raw, "port", 8000, f"{config_path}:api")),
            debug=bool(_get(api_raw, "debug", False, f"{config_path}:api")),
            max_live_update_size_mb=int(
                _get(api_raw, "max_live_update_size_mb", 1, f"{config_path}:api")
            ),
        ),
        logs=LogsSettings(
            folder=str(_get(logs_raw, "folder", "./runs", f"{config_path}:logs"))
        ),
        source=config_path,
        env=os.environ.get("NOODLE_ENV", "dev").strip().lower() or "dev",
    )

    if path is None and "NOODLE_CONFIG" not in os.environ:
        _CACHE = settings
    return settings


def reset_cache() -> None:
    """Drop the cached settings. Test-only."""

    global _CACHE
    _CACHE = None
