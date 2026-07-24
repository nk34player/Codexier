from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import CodexSettings


@dataclass(frozen=True)
class ConfigMapping:
    base_url_path: tuple[str, ...]
    api_key_path: tuple[str, ...]
    models_path: tuple[str, ...]


class ConfigAdapter(Protocol):
    def load(self, path): ...
    def detect_mapping(self, data: dict[str, Any]) -> ConfigMapping: ...
    def read_settings(self, data: dict[str, Any], mapping: ConfigMapping) -> CodexSettings | None: ...
    def write_settings(self, data: dict[str, Any], settings: CodexSettings, mapping: ConfigMapping) -> dict[str, Any]: ...
    def serialize(self, data: dict[str, Any]) -> bytes: ...


def get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise KeyError(".".join(path))
        current = current[key]
    return current


def set_nested(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = data
    for key in path[:-1]:
        child = current.get(key)
        if child is None:
            child = {}
            current[key] = child
        if not isinstance(child, dict):
            raise ValueError("Cannot replace non-table configuration value.")
        current = child
    current[path[-1]] = value


def parse_mapping_path(value: str) -> tuple[str, ...]:
    path = tuple(part.strip() for part in value.split("."))
    if not path or any(not part for part in path):
        raise ValueError(f"Invalid configuration mapping path: {value!r}")
    return path


def settings_from_data(data: dict[str, Any], mapping: ConfigMapping) -> CodexSettings | None:
    try:
        base_url = get_nested(data, mapping.base_url_path)
        api_key = get_nested(data, mapping.api_key_path)
        models = get_nested(data, mapping.models_path)
    except KeyError:
        return None
    if not isinstance(base_url, str) or not isinstance(api_key, str) or not isinstance(models, list):
        return None
    if not all(isinstance(model, str) for model in models):
        return None
    return CodexSettings(base_url, api_key, tuple(models))


def data_with_settings(data: dict[str, Any], settings: CodexSettings, mapping: ConfigMapping) -> dict[str, Any]:
    set_nested(data, mapping.base_url_path, settings.base_url)
    set_nested(data, mapping.api_key_path, settings.api_key)
    set_nested(data, mapping.models_path, list(settings.models))
    return data
