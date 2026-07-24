from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from urllib.parse import urlparse

from .errors import ValidationError

MAX_ACTIVE_MODELS = 5


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    label: str


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str
    api_key: str
    models: tuple[ModelDefinition, ...]
    presets: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CodexSettings:
    base_url: str
    api_key: str
    models: tuple[str, ...]


def validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValidationError("Base URL must be an HTTPS URL with a host.")
    if parsed.username or parsed.password:
        raise ValidationError("Base URL must not contain embedded credentials.")


def validate_provider(provider: Provider) -> None:
    if not provider.id.strip() or not provider.name.strip():
        raise ValidationError("Provider id and name are required.")
    validate_base_url(provider.base_url)
    if not provider.api_key.strip():
        raise ValidationError(f"Provider {provider.name!r} has an empty API key.")
    ids = [model.id for model in provider.models]
    if any(not model_id.strip() for model_id in ids) or len(ids) != len(set(ids)):
        raise ValidationError(f"Provider {provider.name!r} has invalid model IDs.")
    known = set(ids)
    for preset, selected in provider.presets.items():
        if len(selected) > MAX_ACTIVE_MODELS:
            raise ValidationError(f"Preset {preset!r} exceeds the five-model limit.")
        if len(selected) != len(set(selected)) or not set(selected) <= known:
            raise ValidationError(f"Preset {preset!r} references invalid models.")


def validate_settings(settings: CodexSettings) -> None:
    validate_base_url(settings.base_url)
    if not settings.api_key.strip():
        raise ValidationError("API key cannot be empty.")
    if not 1 <= len(settings.models) <= MAX_ACTIVE_MODELS:
        raise ValidationError("Select between 1 and 5 models.")
    if any(not model.strip() for model in settings.models):
        raise ValidationError("Model IDs cannot be empty.")
    if len(settings.models) != len(set(settings.models)):
        raise ValidationError("Model IDs must be unique.")


def select_models(provider: Provider, model_ids: Sequence[str]) -> tuple[str, ...]:
    selected = tuple(model_ids)
    known = {model.id for model in provider.models}
    if not 1 <= len(selected) <= MAX_ACTIVE_MODELS:
        raise ValidationError("Select between 1 and 5 models.")
    if len(selected) != len(set(selected)) or not set(selected) <= known:
        raise ValidationError("Selection contains unknown or duplicate models.")
    return selected


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}...{api_key[-4:]}"
