from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from ..errors import ConfigError
from ..models import CodexSettings
from .base import ConfigMapping, data_with_settings, parse_mapping_path, settings_from_data


DEFAULT_MAPPING = ConfigMapping(("provider", "base_url"), ("provider", "api_key"), ("model_catalog",))


class JsonConfigAdapter:
    def load(self, path: Path) -> dict[str, Any]:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Could not parse JSON Codex config: {path}") from exc
        if not isinstance(data, dict):
            raise ConfigError("Codex JSON config root must be an object.")
        return data

    def detect_mapping(self, data: dict[str, Any]) -> ConfigMapping:
        if all(key in data for key in ("provider", "model_catalog")) and isinstance(data["provider"], dict):
            provider = data["provider"]
            if "base_url" in provider and "api_key" in provider:
                return DEFAULT_MAPPING
        raise ConfigError("Could not safely map Codex JSON config; provide codex_mapping settings.")

    def read_settings(self, data: dict[str, Any], mapping: ConfigMapping) -> CodexSettings | None:
        return settings_from_data(data, mapping)

    def write_settings(self, data: dict[str, Any], settings: CodexSettings, mapping: ConfigMapping) -> dict[str, Any]:
        return data_with_settings(copy.deepcopy(data), settings, mapping)

    def serialize(self, data: dict[str, Any]) -> bytes:
        return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
