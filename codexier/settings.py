from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_SETTINGS: dict[str, Any] = {
    "supports_parallel_tool_calls": False,
    "support_verbosity": False,
    "supports_search_tool": False,
    "web_search_tool_type": None,
}


def settings_path(provider_catalog: Path) -> Path:
    return provider_catalog.expanduser().with_name("codexier.settings.json")


def load_settings(provider_catalog: Path) -> dict[str, Any]:
    path = settings_path(provider_catalog)
    if not path.exists():
        save_settings(provider_catalog, DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read settings file: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Settings file must contain a JSON object.")
    result = dict(DEFAULT_SETTINGS)
    result.update(data)
    return result


def save_settings(provider_catalog: Path, settings: dict[str, Any]) -> Path:
    path = settings_path(provider_catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
