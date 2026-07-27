import json
import os
from pathlib import Path

import pytest

from codexier.errors import CatalogError
from codexier.models import ModelDefinition, Provider
from codexier.provider_store import (
    ProviderStore,
    add_provider,
    create_provider_catalog,
    delete_provider,
    migrate_legacy_enabled_provider,
    resolve_provider_path,
    set_provider_enabled,
    update_provider,
)


def test_load_catalog(tmp_path: Path):
    source = {
        "version": 1,
        "providers": [{
            "id": "demo", "name": "Demo", "base_url": "https://example.com/v1", "api_key": "secret",
            "models": [{"id": "one"}], "presets": {"default": ["one"]}
        }]
    }
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(source))
    providers = ProviderStore(path).load()
    assert providers[0].models[0].label == "one"
    assert providers[0].enabled is False


def test_legacy_toggle_migration_preserves_only_previous_default(tmp_path: Path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"version": 1, "providers": [
        {
            "id": "old", "name": "Old", "base_url": "https://old.example/v1",
            "api_key": "old-key", "models": [{"id": "old-model"}], "presets": {},
        },
        {
            "id": "active", "name": "Active", "base_url": "https://active.example/v1",
            "api_key": "active-key", "models": [{"id": "active-model"}], "presets": {},
        },
    ]}))

    result = migrate_legacy_enabled_provider(path, "active")

    assert result.had_legacy_entries is True
    assert result.enabled_provider_id == "active"
    saved = json.loads(path.read_text())
    assert [entry["enabled"] for entry in saved["providers"]] == [False, True]


def test_legacy_toggle_migration_keeps_unknown_default_disabled(tmp_path: Path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"version": 1, "providers": [{
        "id": "demo", "name": "Demo", "base_url": "https://demo.example/v1",
        "api_key": "secret", "models": [{"id": "model"}], "presets": {},
    }]}))

    result = migrate_legacy_enabled_provider(path, None)

    assert result.had_legacy_entries is True
    assert result.enabled_provider_id is None
    assert json.loads(path.read_text())["providers"][0]["enabled"] is False


def test_missing_catalog_fails(tmp_path: Path):
    with pytest.raises(CatalogError, match="not found"):
        ProviderStore(tmp_path / "missing.json").load()


def test_invalid_preset_fails(tmp_path: Path):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": [{
        "id": "demo", "name": "Demo", "base_url": "https://example.com", "api_key": "secret",
        "models": [{"id": "one"}], "presets": {"default": ["missing"]}
    }]}))
    with pytest.raises(Exception):
        ProviderStore(path).load()


def test_explicit_path_wins(tmp_path: Path):
    path = tmp_path / "custom.json"
    assert resolve_provider_path(path) == path


def test_create_provider_catalog_creates_secure_empty_catalog(tmp_path: Path):
    path = tmp_path / "providers.json"
    create_provider_catalog(path)
    assert json.loads(path.read_text()) == {"version": 1, "providers": []}
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_add_provider_persists_live_models_and_secure_key(tmp_path: Path):
    path = tmp_path / "providers.json"
    add_provider(path, Provider(
        "demo", "Demo", "https://demo.example/v1", "secret",
        (ModelDefinition("live-a", "Live A"),), {},
    ))
    loaded = ProviderStore(path).get("demo")
    assert loaded.models[0].id == "live-a"
    assert loaded.api_key == "secret"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_update_provider_replaces_existing_entry(tmp_path: Path):
    path = tmp_path / "providers.json"
    original = Provider("demo", "Demo", "https://demo.example/v1", "old", (ModelDefinition("a", "A"),), {})
    replacement = Provider("demo", "Demo 2", "https://other.example/v1", "new", (ModelDefinition("b", "B"),), {})
    add_provider(path, original)
    update_provider(path, replacement)
    loaded = ProviderStore(path).get("demo")
    assert loaded.name == "Demo 2"
    assert loaded.api_key == "new"


def test_set_provider_enabled_persists_home_screen_toggle(tmp_path: Path):
    path = tmp_path / "providers.json"
    add_provider(
        path,
        Provider(
            "demo",
            "Demo",
            "https://demo.example/v1",
            "key",
            (ModelDefinition("a", "A"),),
            {},
            enabled=False,
        ),
    )
    enabled = set_provider_enabled(path, "demo", True)
    assert enabled.enabled is True
    assert json.loads(path.read_text())["providers"][0]["enabled"] is True


def test_delete_provider_removes_entry(tmp_path: Path):
    path = tmp_path / "providers.json"
    add_provider(path, Provider("demo", "Demo", "https://demo.example/v1", "key", (ModelDefinition("a", "A"),), {}))
    delete_provider(path, "demo")
    with pytest.raises(CatalogError, match="Unknown provider"):
        ProviderStore(path).get("demo")
