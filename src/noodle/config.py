"""Typed, read-only configuration loaded from a single YAML file.

Secrets never live here: provider settings name the environment variable that
holds a key, never the key itself. Only three environment variables override the
file: ``FALKORDB_URL``, ``NOODLE_ENV`` and ``NOODLE_CONFIG`` (plus provider keys,
which are read by the provider layer, not this module).

Settings are read once and cached. A test may reset the cache for its own run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("noodle.yaml")
DEFAULT_TOOL_ROUNDS = 6
DEFAULT_FIX_ROUNDS = 2

# A provider entry names the env var that holds its key; it must not be the key.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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
class ProviderSettings:
    """One provider: its adapter name and the env var holding its key."""

    name: str
    key_env: str
    generator_model: str
    reviewer_model: str


@dataclass(frozen=True)
class ModeSettings:
    """A fast/heavy configuration (05 section 0)."""

    name: str
    generator_model: str
    reviewer_model: str
    tools: tuple[str, ...]
    tool_rounds: int
    fix_rounds: int
    timeout_s: float


@dataclass(frozen=True)
class GenerationSettings:
    max_tokens: int
    temperature: float


@dataclass(frozen=True)
class Settings:
    graph: GraphSettings
    api: ApiSettings
    logs: LogsSettings
    providers: tuple[ProviderSettings, ...]
    default_provider: Optional[str]
    modes: Mapping[str, ModeSettings]
    generation: GenerationSettings
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


def _parse_providers(raw: dict[str, Any], source: Path) -> tuple[tuple[ProviderSettings, ...], Optional[str]]:
    providers_raw = raw.get("providers") or {}
    if not isinstance(providers_raw, dict):
        raise ConfigError(f"{source}: setting 'providers' must be a mapping")
    default = providers_raw.get("default")
    entries: list[ProviderSettings] = []
    for name, cfg in providers_raw.items():
        if name == "default":
            continue
        where = f"{source}:providers.{name}"
        if not isinstance(cfg, dict):
            raise ConfigError(f"{where} must be a mapping")
        key_env = cfg.get("key_env")
        if not key_env:
            raise ConfigError(f"{where}: missing required setting 'key_env'")
        if not _ENV_NAME_RE.match(str(key_env)):
            raise ConfigError(
                f"{where}.key_env must be an environment variable name, not the key itself"
            )
        entries.append(
            ProviderSettings(
                name=str(name),
                key_env=str(key_env),
                generator_model=str(cfg.get("generator_model") or ""),
                reviewer_model=str(cfg.get("reviewer_model") or ""),
            )
        )
    if default is not None and default not in {entry.name for entry in entries}:
        raise ConfigError(f"{source}:providers.default names an unknown provider: {default!r}")
    return tuple(entries), (str(default) if default is not None else None)


def _parse_modes(raw: dict[str, Any], source: Path) -> dict[str, ModeSettings]:
    modes_raw = raw.get("modes") or {}
    if not isinstance(modes_raw, dict):
        raise ConfigError(f"{source}: setting 'modes' must be a mapping")
    modes: dict[str, ModeSettings] = {}
    for name, cfg in modes_raw.items():
        where = f"{source}:modes.{name}"
        if not isinstance(cfg, dict):
            raise ConfigError(f"{where} must be a mapping")
        modes[str(name)] = ModeSettings(
            name=str(name),
            generator_model=str(cfg.get("generator_model") or ""),
            reviewer_model=str(cfg.get("reviewer_model") or ""),
            tools=tuple(cfg.get("tools") or ()),
            tool_rounds=int(cfg.get("tool_rounds", DEFAULT_TOOL_ROUNDS)),
            fix_rounds=int(cfg.get("fix_rounds", DEFAULT_FIX_ROUNDS)),
            timeout_s=float(cfg.get("timeout_s", 45 if name == "fast" else 90)),
        )
    return modes


def _parse_generation(raw: dict[str, Any]) -> GenerationSettings:
    gen_raw = raw.get("generation") or {}
    if not isinstance(gen_raw, dict):
        raise ConfigError("setting 'generation' must be a mapping")
    return GenerationSettings(
        max_tokens=int(gen_raw.get("max_tokens", 2000)),
        temperature=float(gen_raw.get("temperature", 0.0)),
    )


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

    providers, default_provider = _parse_providers(raw, config_path)

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
        providers=providers,
        default_provider=default_provider,
        modes=_parse_modes(raw, config_path),
        generation=_parse_generation(raw),
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
