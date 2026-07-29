from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import ConfigError

DEFAULT_INPUT_MODALITIES = ["text", "image"]
DEFAULT_CONTEXT_WINDOW = 250_000
DEFAULT_AUTO_COMPACT_TOKEN_LIMIT = 70_000
MAX_CONTEXT_WINDOW = 10_000_000

DEFAULT_SETTINGS: dict[str, Any] = {
    "supports_parallel_tool_calls": False,
    "support_verbosity": False,
    "supports_search_tool": False,
    "web_search_tool_type": None,
    "input_modalities": DEFAULT_INPUT_MODALITIES,
    "context_window": DEFAULT_CONTEXT_WINDOW,
    "auto_compact_token_limit": DEFAULT_AUTO_COMPACT_TOKEN_LIMIT,
    "windows": {
        "mode": "official",
        "official_provider_id": None,
        "portable_default_provider_id": None,
        "create_desktop_shortcut": False,
    },
}


def _merged_settings(settings: dict[str, Any]) -> dict[str, Any]:
    # ponytail: shallow copy is safe — callers reassign keys rather than mutate
    # default values in place (see setup_tui.SettingsScreen.action_toggle).
    result = {
        key: value
        for key, value in DEFAULT_SETTINGS.items()
        if key != "windows"
    }
    result.update({key: value for key, value in settings.items() if key != "windows"})
    result["windows"] = dict(DEFAULT_SETTINGS["windows"])
    if isinstance(settings.get("windows"), dict):
        result["windows"].update(settings["windows"])
    return result


def settings_path(provider_catalog: Path) -> Path:
    return provider_catalog.expanduser().with_name("codexier.settings.json")


def load_settings(provider_catalog: Path) -> dict[str, Any]:
    path = settings_path(provider_catalog)
    if not path.exists():
        save_settings(provider_catalog, DEFAULT_SETTINGS)
        return _merged_settings(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read settings file: {path}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Settings file must contain a JSON object.")
    result = _merged_settings(data)
    validate_context_settings(result)
    validate_windows_settings(result)
    return result


def validate_context_settings(settings: dict[str, Any]) -> None:
    context_window = settings.get("context_window", DEFAULT_CONTEXT_WINDOW)
    compact_limit = settings.get("auto_compact_token_limit", DEFAULT_AUTO_COMPACT_TOKEN_LIMIT)
    if (
        isinstance(context_window, bool)
        or not isinstance(context_window, int)
        or not 1 <= context_window <= MAX_CONTEXT_WINDOW
    ):
        raise ConfigError(f"Maximum context must be an integer from 1 to {MAX_CONTEXT_WINDOW:,}.")
    if (
        isinstance(compact_limit, bool)
        or not isinstance(compact_limit, int)
        or not 1 <= compact_limit < context_window
    ):
        raise ConfigError("Context compacting limit must be a positive integer below maximum context.")


def validate_windows_settings(settings: dict[str, Any]) -> None:
    windows = settings.get("windows")
    if not isinstance(windows, dict):
        raise ConfigError("Windows settings must contain a JSON object.")
    if windows.get("mode") not in {"official", "portable"}:
        raise ConfigError("Windows mode must be 'official' or 'portable'.")
    for key in ("official_provider_id", "portable_default_provider_id"):
        value = windows.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ConfigError(f"Windows setting {key} must be a provider id or null.")
    if not isinstance(windows.get("create_desktop_shortcut"), bool):
        raise ConfigError("Windows setting create_desktop_shortcut must be true or false.")


def save_settings(provider_catalog: Path, settings: dict[str, Any]) -> Path:
    settings = _merged_settings(settings)
    validate_context_settings(settings)
    validate_windows_settings(settings)
    path = settings_path(provider_catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
