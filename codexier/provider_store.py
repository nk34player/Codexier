from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import CatalogError
from .models import ModelDefinition, Provider, validate_provider


def resolve_provider_path(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser()
    candidates = (Path.cwd() / "providers.json", Path.home() / ".config/codexier/providers.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def create_provider_catalog(path: Path) -> Path:
    """Create first-run catalog and keep API keys private on disk."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps({"version": 1, "providers": []}, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def add_provider(path: Path, provider: Provider) -> None:
    """Append validated provider to catalog without exposing its key."""
    validate_provider(provider)
    create_provider_catalog(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        providers = raw["providers"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CatalogError("Could not update provider catalog.") from exc
    if any(item.get("id") == provider.id for item in providers):
        raise CatalogError(f"Provider already exists: {provider.name}")
    providers.append({
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "api_key": provider.api_key,
        "models": [{"id": model.id, "label": model.label} for model in provider.models],
        "presets": {"default": [model.id for model in provider.models]},
    })
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _read_catalog(path: Path) -> dict[str, Any]:
    create_provider_catalog(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("providers"), list):
            raise ValueError
        return raw
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise CatalogError("Could not update provider catalog.") from exc


def _write_catalog(path: Path, raw: dict[str, Any]) -> None:
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def update_provider(path: Path, provider: Provider) -> None:
    validate_provider(provider)
    raw = _read_catalog(path)
    entries = raw["providers"]
    for index, item in enumerate(entries):
        if item.get("id") == provider.id:
            entries[index] = {
                "id": provider.id,
                "name": provider.name,
                "base_url": provider.base_url,
                "api_key": provider.api_key,
                "models": [{"id": model.id, "label": model.label} for model in provider.models],
                "presets": {"default": [model.id for model in provider.models]},
            }
            _write_catalog(path, raw)
            return
    raise CatalogError(f"Unknown provider: {provider.id}")


def delete_provider(path: Path, provider_id: str) -> None:
    raw = _read_catalog(path)
    entries = raw["providers"]
    remaining = [item for item in entries if item.get("id") != provider_id]
    if len(remaining) == len(entries):
        raise CatalogError(f"Unknown provider: {provider_id}")
    raw["providers"] = remaining
    _write_catalog(path, raw)


class ProviderStore:
    def __init__(self, path: Path):
        self.path = path.expanduser()
        self._providers: tuple[Provider, ...] | None = None

    def load(self) -> tuple[Provider, ...]:
        if self._providers is not None:
            return self._providers
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise CatalogError(f"Provider catalog not found: {self.path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"Could not read provider catalog: {self.path}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("providers"), list):
            raise CatalogError("Provider catalog must contain a providers array.")
        providers: list[Provider] = []
        seen: set[str] = set()
        for item in raw["providers"]:
            try:
                provider = self._parse_provider(item)
            except (KeyError, TypeError, ValueError) as exc:
                raise CatalogError("Provider catalog contains an invalid provider entry.") from exc
            if provider.id in seen:
                raise CatalogError(f"Duplicate provider id: {provider.id}")
            validate_provider(provider)
            seen.add(provider.id)
            providers.append(provider)
        self._providers = tuple(providers)
        return self._providers

    def get(self, provider_id: str) -> Provider:
        for provider in self.load():
            if provider.id == provider_id:
                return provider
        raise CatalogError(f"Unknown provider: {provider_id}")

    @staticmethod
    def _parse_provider(item: dict[str, Any]) -> Provider:
        models = tuple(ModelDefinition(str(model["id"]), str(model.get("label", model["id"]))) for model in item["models"])
        presets = {str(name): tuple(str(model_id) for model_id in ids) for name, ids in item.get("presets", {}).items()}
        return Provider(
            id=str(item["id"]),
            name=str(item["name"]),
            base_url=str(item["base_url"]),
            api_key=str(item["api_key"]),
            models=models,
            presets=presets,
        )


def secure_catalog(path: Path) -> None:
    """Restrict catalog permissions when possible; never expose its contents."""
    try:
        path.chmod(0o600)
    except OSError:
        pass
