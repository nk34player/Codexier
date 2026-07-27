from __future__ import annotations

import copy
import json
import os
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tomli_w

from .backup import atomic_write, backup_config
from .errors import ConfigError
from .models import Provider
from .settings import (
    DEFAULT_AUTO_COMPACT_TOKEN_LIMIT,
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_INPUT_MODALITIES,
    MAX_CONTEXT_WINDOW,
)


CONTEXT_WINDOW = DEFAULT_CONTEXT_WINDOW
AUTO_COMPACT_TOKEN_LIMIT = DEFAULT_AUTO_COMPACT_TOKEN_LIMIT


@dataclass(frozen=True)
class CodexProfileResult:
    config_path: Path
    catalog_path: Path
    profile_path: Path
    desktop_config_path: Path | None = None


def provider_route_id(provider: Provider) -> str:
    """Return a stable internal model-provider route identifier."""
    safe = "".join(char if char.isalnum() or char in "_-" else "-" for char in provider.id)
    return f"codexier-{safe.strip('-') or 'provider'}"


def provider_profile_id(provider: Provider) -> str:
    """Compatibility alias for callers from the per-profile implementation."""
    return provider_route_id(provider)


def ensure_unique_model_ids(providers: tuple[Provider, ...]) -> None:
    """Retained API: provider-first routing intentionally permits duplicate IDs."""


def launch_command(profile: str = "codexier") -> list[str]:
    """Return explicit Codex profile launch command."""
    return ["codex", "--profile", profile]


def codex_home(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _read_codex_config(config_path: Path | None = None) -> dict[str, Any] | None:
    """Read a Codex TOML configuration without exposing its credentials."""
    path = config_path or (codex_home() / "config.toml")
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def applied_provider_id(config_path: Path | None = None) -> str | None:
    """Return the persisted Codexier fallback id, when one is available."""
    data = _read_codex_config(config_path)
    if data is None:
        return None
    value = data.get("codexier_provider_id") or data.get("model_provider")
    return value if isinstance(value, str) and value.strip() else None


def legacy_codexier_provider_id(
    providers: tuple[Provider, ...], config_path: Path | None = None
) -> str | None:
    """Resolve a pre-routing Codexier connection to one saved provider.

    Older Codexier configurations used ``model_provider = "codexier"`` but
    did not retain a provider id.  Match the complete connection rather than
    guessing from a model name, so a migration cannot enable the wrong API
    account.  The credentials are compared in memory only and never logged.
    """
    data = _read_codex_config(config_path)
    if data is None:
        return None
    saved_id = data.get("codexier_provider_id")
    if isinstance(saved_id, str) and any(
        provider.id == saved_id for provider in providers
    ):
        return saved_id
    if data.get("model_provider") != "codexier":
        return None
    routes = data.get("model_providers")
    route = routes.get("codexier") if isinstance(routes, dict) else None
    if not isinstance(route, dict):
        return None
    base_url = route.get("base_url")
    token = route.get("experimental_bearer_token") or route.get("api_key")
    if not isinstance(base_url, str) or not isinstance(token, str):
        return None
    normalized_url = base_url.rstrip("/")
    matches = [
        provider
        for provider in providers
        if provider.base_url.rstrip("/") == normalized_url
        and provider.api_key == token
    ]
    return matches[0].id if len(matches) == 1 else None


def _catalog_model(provider: Provider, model_id: str, label: str, priority: int, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    context_window, compact_limit = _context_limits(settings)
    model = {
        "slug": model_id,
        "display_name": f"{label} ({provider.name})",
        "description": f"{label} via {provider.name}",
        "default_reasoning_level": "medium",
        "supported_reasoning_levels": [
            {"effort": "low", "description": "Fast"},
            {"effort": "medium", "description": "Balanced"},
            {"effort": "high", "description": "Thorough"},
        ],
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": priority,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "base_instructions": "You are a coding agent.",
        "supports_reasoning_summary_parameter": True,
        "supports_reasoning_summaries": False,
        "default_reasoning_summary": "auto",
        "support_verbosity": bool(settings.get("support_verbosity", False)),
        "default_verbosity": "low",
        "apply_patch_tool_type": "freeform",
        "truncation_policy": {"mode": "tokens", "limit": 8000},
        "supports_parallel_tool_calls": bool(settings.get("supports_parallel_tool_calls", False)),
        "supports_image_detail_original": False,
        "context_window": context_window,
        "max_context_window": context_window,
        "auto_compact_token_limit": compact_limit,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": _input_modalities(settings),
        "supports_search_tool": bool(settings.get("supports_search_tool", False)),
        "use_responses_lite": False,
        "model": model_id,
        "displayName": f"{label} ({provider.name})",
        "defaultReasoningEffort": "medium",
        "supportedReasoningEfforts": [
            {"reasoningEffort": "low", "description": "Fast"},
            {"reasoningEffort": "medium", "description": "Balanced"},
            {"reasoningEffort": "high", "description": "Thorough"},
        ],
    }
    web_search_type = settings.get("web_search_tool_type")
    if web_search_type:
        model["web_search_tool_type"] = str(web_search_type)
    return model


def _input_modalities(settings: dict[str, Any]) -> list[str]:
    value = settings.get("input_modalities", DEFAULT_INPUT_MODALITIES)
    if value == ["text"]:
        return ["text"]
    if value == ["text", "image"]:
        return ["text", "image"]
    return list(DEFAULT_INPUT_MODALITIES)


def _context_limits(settings: dict[str, Any]) -> tuple[int, int]:
    context_window = settings.get("context_window", CONTEXT_WINDOW)
    compact_limit = settings.get("auto_compact_token_limit", AUTO_COMPACT_TOKEN_LIMIT)
    if (
        isinstance(context_window, int)
        and not isinstance(context_window, bool)
        and 1 <= context_window <= MAX_CONTEXT_WINDOW
        and isinstance(compact_limit, int)
        and not isinstance(compact_limit, bool)
        and 1 <= compact_limit < context_window
    ):
        return context_window, compact_limit
    return CONTEXT_WINDOW, AUTO_COMPACT_TOKEN_LIMIT


def build_catalog(provider: Provider, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    models = [_catalog_model(provider, model.id, model.label, index, settings) for index, model in enumerate(provider.models, 1)]
    default = models[0]
    return {
        "models": models,
        "defaultModel": default,
        "default_model": default["slug"],
        "source": "local-codexier-model-catalog",
    }


def build_catalog_for_providers(
    providers: tuple[Provider, ...],
    selected_provider: Provider,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one desktop catalog while retaining the normal default provider.

    The desktop picker separates provider choice from model choice, so its
    catalog must expose every enabled provider's models.  Duplicate slugs are
    intentionally retained: the selected provider route disambiguates them.
    """
    model_pairs = [
        (provider, model)
        for provider in providers
        for model in provider.models
    ]
    models = [
        _catalog_model(provider, model.id, model.label, priority, settings)
        for priority, (provider, model) in enumerate(model_pairs, 1)
    ]
    default_index = next(
        index
        for index, (provider, model) in enumerate(model_pairs)
        if provider.id == selected_provider.id
        and model.id == selected_provider.models[0].id
    )
    default = models[default_index]
    return {
        "models": models,
        "defaultModel": default,
        "default_model": default["slug"],
        "source": "local-codexier-model-catalog",
    }


def build_desktop_provider_config(
    providers: tuple[Provider, ...], selected_provider: Provider
) -> dict[str, Any]:
    """Build provider-first routing data read by the desktop picker patch."""
    return {
        "version": 2,
        "default_provider": provider_route_id(selected_provider),
        "providers": [
            {
                "id": "openai",
                "label": "ChatGPT / OpenAI",
                "description": "Built-in provider; uses your signed-in ChatGPT account",
                "models": [],
            },
            *[
                {
                    "id": provider_route_id(provider),
                    "label": provider.name,
                    "description": f"Uses {provider.name} from Codexier",
                    "models": [
                        {"id": model.id, "label": model.label}
                        for model in provider.models
                    ],
                }
                for provider in providers
            ],
        ],
    }


def _merge_profile(
    data: dict[str, Any],
    provider: Provider,
    catalog_path: Path,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = copy.deepcopy(data)
    context_window, compact_limit = _context_limits(settings or {})
    result["model"] = provider.models[0].id
    result["model_provider"] = "codexier"
    result["codexier_provider_id"] = provider.id
    result["model_reasoning_effort"] = "medium"
    result["model_context_window"] = context_window
    result["model_auto_compact_token_limit"] = compact_limit
    result["model_auto_compact_token_limit_scope"] = "total"
    result["tool_output_token_limit"] = 8000
    result["model_catalog_json"] = str(catalog_path.resolve())
    providers = result.setdefault("model_providers", {})
    providers["codexier"] = {
        "name": "Codexier",
        "base_url": provider.base_url.rstrip("/") + "/",
        "wire_api": "responses",
        "wire_specification": "responses",
        "experimental_bearer_token": provider.api_key,
        "requires_openai_auth": False,
    }
    profiles = result.setdefault("profiles", {})
    profiles["codexier"] = {
        "name": "Codexier",
        "model": provider.models[0].id,
        "model_provider": "codexier",
        "model_context_window": context_window,
        "model_auto_compact_token_limit": compact_limit,
        "model_auto_compact_token_limit_scope": "total",
        "tool_output_token_limit": 8000,
        "model_catalog_json": str(catalog_path.resolve()),
    }
    return result


def _provider_route(provider: Provider) -> dict[str, Any]:
    return {
        "name": provider.name,
        "base_url": provider.base_url.rstrip("/") + "/",
        "wire_api": "responses",
        "wire_specification": "responses",
        "experimental_bearer_token": provider.api_key,
        "requires_openai_auth": False,
    }


def _normal_profile(
    provider: Provider, catalog_path: Path, settings: dict[str, Any] | None
) -> dict[str, Any]:
    context_window, compact_limit = _context_limits(settings or {})
    return {
        "name": "Codexier",
        "model": provider.models[0].id,
        "model_provider": "codexier",
        "model_context_window": context_window,
        "model_auto_compact_token_limit": compact_limit,
        "model_auto_compact_token_limit_scope": "total",
        "tool_output_token_limit": 8000,
        "model_catalog_json": str(catalog_path.resolve()),
    }


def apply_codex_profile(provider: Provider, home: Path | None = None, settings: dict[str, Any] | None = None) -> CodexProfileResult:
    if not provider.models:
        raise ConfigError("Provider must contain at least one selected model.")
    root = codex_home(home)
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.toml"
    catalog_path = root / "codexier.models.json"
    profile_path = root / "codexier.config.toml"
    if config_path.exists():
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"Could not parse Codex config: {config_path}") from exc
    else:
        data = {}
    backup_config(config_path)
    catalog = build_catalog(provider, settings)
    catalog["model_catalog_json"] = str(catalog_path.resolve())
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(catalog_path, (json.dumps(catalog, indent=2) + "\n").encode(), mode=0o600)
    merged = _merge_profile(data, provider, catalog_path, settings)
    atomic_write(config_path, tomli_w.dumps(merged).encode(), mode=0o600)
    context_window, compact_limit = _context_limits(settings or {})
    profile = {
        "model": provider.models[0].id,
        "model_provider": "codexier",
        "model_context_window": context_window,
        "model_auto_compact_token_limit": compact_limit,
        "model_auto_compact_token_limit_scope": "total",
        "model_catalog_json": str(catalog_path.resolve()),
        "tool_output_token_limit": 8000,
    }
    atomic_write(profile_path, tomli_w.dumps(profile).encode(), mode=0o600)
    return CodexProfileResult(config_path, catalog_path, profile_path)


def apply_codex_profiles(
    providers: tuple[Provider, ...],
    selected_provider: Provider,
    home: Path | None = None,
    settings: dict[str, Any] | None = None,
) -> CodexProfileResult:
    """Install one normal profile and desktop routes for enabled providers."""
    enabled_providers = tuple(provider for provider in providers if provider.enabled)
    if not enabled_providers:
        raise ConfigError("Enable at least one provider before applying.")
    if selected_provider.id not in {provider.id for provider in enabled_providers}:
        raise ConfigError("The selected default provider must be enabled.")
    if any(not provider.models for provider in enabled_providers):
        raise ConfigError("Every enabled provider must contain at least one selected model.")
    root = codex_home(home)
    root.mkdir(parents=True, exist_ok=True)
    config_path = root / "config.toml"
    catalog_path = root / "codexier.models.json"
    profile_path = root / "codexier.config.toml"
    desktop_config_path = root / "desktop-model-providers.json"
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not parse Codex config: {config_path}") from exc

    backup_config(config_path)
    # The normal Codexier profile can only call its selected provider. The
    # desktop patch reads the separate provider-first routing config below.
    catalog = build_catalog_for_providers(
        enabled_providers, selected_provider, settings
    )
    catalog["model_catalog_json"] = str(catalog_path.resolve())
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(catalog_path, (json.dumps(catalog, indent=2) + "\n").encode(), mode=0o600)

    result = copy.deepcopy(data)
    context_window, compact_limit = _context_limits(settings or {})
    result.update(
        {
            "model": selected_provider.models[0].id,
            "model_provider": "codexier",
            "codexier_provider_id": selected_provider.id,
            "model_reasoning_effort": "medium",
            "model_context_window": context_window,
            "model_auto_compact_token_limit": compact_limit,
            "model_auto_compact_token_limit_scope": "total",
            "tool_output_token_limit": 8000,
            "model_catalog_json": str(catalog_path.resolve()),
        }
    )
    provider_table = result.setdefault("model_providers", {})
    profiles = result.setdefault("profiles", {})
    # Remove only routes/profiles owned by Codexier; leave the user's other
    # model providers and profiles untouched.
    for route_id in tuple(provider_table):
        if route_id.startswith("codexier-"):
            del provider_table[route_id]
    for profile_id in tuple(profiles):
        if profile_id.startswith("codexier-"):
            del profiles[profile_id]
    provider_table["codexier"] = _provider_route(selected_provider)
    for provider in enabled_providers:
        provider_table[provider_route_id(provider)] = _provider_route(provider)
    profiles["codexier"] = _normal_profile(selected_provider, catalog_path, settings)
    atomic_write(config_path, tomli_w.dumps(result).encode(), mode=0o600)
    atomic_write(profile_path, tomli_w.dumps(profiles["codexier"]).encode(), mode=0o600)
    atomic_write(
        desktop_config_path,
        (
            json.dumps(
                build_desktop_provider_config(enabled_providers, selected_provider),
                indent=2,
            )
            + "\n"
        ).encode(),
        mode=0o600,
    )
    return CodexProfileResult(config_path, catalog_path, profile_path, desktop_config_path)
