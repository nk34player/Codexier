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
    validate_context_settings(result)
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


def save_settings(provider_catalog: Path, settings: dict[str, Any]) -> Path:
    validate_context_settings(settings)
    path = settings_path(provider_catalog)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path
