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


def provider_profile_id(provider: Provider) -> str:
    """Return a stable, TOML-safe Codex provider/profile identifier."""
    safe = "".join(char if char.isalnum() or char in "_-" else "-" for char in provider.id)
    return f"codexier-{safe.strip('-') or 'provider'}"


def ensure_unique_model_ids(providers: tuple[Provider, ...]) -> None:
    owners: dict[str, str] = {}
    duplicates: set[str] = set()
    for provider in providers:
        for model in provider.models:
            previous = owners.setdefault(model.id, provider.name)
            if previous != provider.name:
                duplicates.add(model.id)
    if duplicates:
        raise ConfigError(
            "Model IDs must be unique across providers: " + ", ".join(sorted(duplicates))
        )


def launch_command(profile: str = "codexier") -> list[str]:
    """Return explicit Codex profile launch command."""
    return ["codex", "--profile", profile]


def codex_home(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser()
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def applied_provider_id(config_path: Path | None = None) -> str | None:
    """Return model_provider from Codex config, if readable."""
    path = config_path or (codex_home() / "config.toml")
    if not path.exists():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("codexier_provider_id") or data.get("model_provider")
    return value if isinstance(value, str) and value.strip() else None


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


def build_multi_catalog(
    providers: tuple[Provider, ...],
    selected_provider: Provider,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_unique_model_ids(providers)
    model_pairs = [
        (provider, model)
        for provider in providers
        for model in provider.models
    ]
    models = [
        _catalog_model(provider, model.id, model.label, priority, settings)
        for priority, (provider, model) in enumerate(model_pairs, 1)
    ]
    if not models:
        raise ConfigError("Select at least one model before applying providers.")
    default = next(
        model for model in models if model["slug"] == selected_provider.models[0].id
    )
    return {
        "models": models,
        "defaultModel": default,
        "default_model": default["slug"],
        "source": "local-codexier-model-catalog",
    }


def build_desktop_provider_config(
    providers: tuple[Provider, ...], selected_provider: Provider
) -> dict[str, Any]:
    """Build config read by the supported desktop provider-menu patch."""
    ensure_unique_model_ids(providers)
    return {
        "version": 1,
        "default_provider": provider_profile_id(selected_provider),
        "providers": [
            {
                "id": "openai",
                "label": "ChatGPT / OpenAI",
                "description": "Built-in provider; uses your signed-in ChatGPT account",
            },
            *[
                {
                    "id": provider_profile_id(provider),
                    "label": provider.name,
                    "description": f"Uses {provider.name} from Codexier",
                }
                for provider in providers
            ],
        ],
        "model_providers": {
            model.id: provider_profile_id(provider)
            for provider in providers
            for model in provider.models
        },
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
    """Install every saved provider, with selected provider active by default."""
    if not providers or selected_provider.id not in {provider.id for provider in providers}:
        raise ConfigError("Selected provider is not in the saved provider catalog.")
    if any(not provider.models for provider in providers):
        raise ConfigError("Every saved provider must contain at least one selected model.")
    ensure_unique_model_ids(providers)
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
    catalog = build_multi_catalog(providers, selected_provider, settings)
    catalog["model_catalog_json"] = str(catalog_path.resolve())
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write(catalog_path, (json.dumps(catalog, indent=2) + "\n").encode(), mode=0o600)

    result = copy.deepcopy(data)
    selected_id = provider_profile_id(selected_provider)
    context_window, compact_limit = _context_limits(settings or {})
    result.update(
        {
            "model": selected_provider.models[0].id,
            "model_provider": selected_id,
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
    for provider in providers:
        profile_id = provider_profile_id(provider)
        provider_table[profile_id] = {
            "name": provider.name,
            "base_url": provider.base_url.rstrip("/") + "/",
            "wire_api": "responses",
            "wire_specification": "responses",
            "experimental_bearer_token": provider.api_key,
            "requires_openai_auth": False,
        }
        profiles[profile_id] = {
            "name": provider.name,
            "model": provider.models[0].id,
            "model_provider": profile_id,
            "model_context_window": context_window,
            "model_auto_compact_token_limit": compact_limit,
            "model_auto_compact_token_limit_scope": "total",
            "tool_output_token_limit": 8000,
            "model_catalog_json": str(catalog_path.resolve()),
        }
    atomic_write(config_path, tomli_w.dumps(result).encode(), mode=0o600)
    atomic_write(profile_path, tomli_w.dumps(profiles[selected_id]).encode(), mode=0o600)
    atomic_write(
        desktop_config_path,
        (json.dumps(build_desktop_provider_config(providers, selected_provider), indent=2) + "\n").encode(),
        mode=0o600,
    )
    return CodexProfileResult(config_path, catalog_path, profile_path, desktop_config_path)
