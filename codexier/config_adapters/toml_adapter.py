from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

try:
    import tomli_w
except ModuleNotFoundError:  # pragma: no cover - packaging guard
    tomli_w = None

from ..errors import ConfigError
from ..models import CodexSettings
from .base import ConfigMapping, data_with_settings, settings_from_data
from .json_adapter import DEFAULT_MAPPING


class TomlConfigAdapter:
    def load(self, path: Path) -> dict[str, Any]:
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream) if path.exists() else {}
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not parse TOML Codex config: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigError("Codex TOML config root must be a table.")
        return data

    def detect_mapping(self, data: dict[str, Any]) -> ConfigMapping:
        provider = data.get("provider")
        if isinstance(provider, dict) and "base_url" in provider and "api_key" in provider and "model_catalog" in data:
            return DEFAULT_MAPPING
        provider_name = data.get("model_provider")
        providers = data.get("model_providers")
        if isinstance(provider_name, str) and isinstance(providers, dict):
            provider_table = providers.get(provider_name)
            if isinstance(provider_table, dict) and isinstance(provider_table.get("base_url"), str):
                return ConfigMapping(
                    ("model_providers", provider_name, "base_url"),
                    ("model_providers", provider_name, "api_key"),
                    ("model",),
                )
        raise ConfigError("Could not safely map Codex TOML config; provide codex_mapping settings.")

    def read_settings(self, data: dict[str, Any], mapping: ConfigMapping) -> CodexSettings | None:
        return settings_from_data(data, mapping)

    def write_settings(self, data: dict[str, Any], settings: CodexSettings, mapping: ConfigMapping) -> dict[str, Any]:
        return data_with_settings(copy.deepcopy(data), settings, mapping)

    def serialize(self, data: dict[str, Any]) -> bytes:
        if tomli_w is None:
            raise ConfigError("TOML writing requires the tomli-w dependency.")
        return tomli_w.dumps(data).encode("utf-8")
