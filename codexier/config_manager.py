from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, NamedTuple

from .config_adapters import ConfigAdapter, ConfigMapping, JsonConfigAdapter, TomlConfigAdapter
from .errors import ConfigError


class ConfigTarget(NamedTuple):
    path: Path
    format: Literal["json", "toml"]
    adapter: ConfigAdapter


def detect_config_target(explicit: Path | None = None) -> ConfigTarget:
    if explicit:
        path = explicit.expanduser()
        return _target_for_path(path)
    codex_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    json_path = codex_root / "config.json"
    toml_path = codex_root / "config.toml"
    legacy_toml_path = Path.home() / ".config/codex/config.toml"
    # Codex-native TOML is authoritative. JSON is legacy fallback. This
    # prevents a stale config.json from blocking normal startup when the
    # active CODEX_HOME/config.toml exists.
    if toml_path.exists():
        return _target_for_path(toml_path)
    if json_path.exists():
        return _target_for_path(json_path)
    if legacy_toml_path.exists():
        return _target_for_path(legacy_toml_path)
    return _target_for_path(toml_path)


def _target_for_path(path: Path) -> ConfigTarget:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return ConfigTarget(path, "json", JsonConfigAdapter())
    if suffix == ".toml":
        return ConfigTarget(path, "toml", TomlConfigAdapter())
    raise ConfigError("Config path must end in .json or .toml.")


def load_target(target: ConfigTarget) -> tuple[dict, ConfigMapping]:
    data = target.adapter.load(target.path)
    mapping = target.adapter.detect_mapping(data) if data else ConfigMapping(
        ("provider", "base_url"), ("provider", "api_key"), ("model_catalog",)
    )
    return data, mapping
