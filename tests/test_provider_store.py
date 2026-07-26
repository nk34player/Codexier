import json
import os
from pathlib import Path

import pytest

from codexier.errors import CatalogError
from codexier.models import ModelDefinition, Provider
from codexier.provider_store import ProviderStore, add_provider, create_provider_catalog, delete_provider, resolve_provider_path, update_provider


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


def test_delete_provider_removes_entry(tmp_path: Path):
    path = tmp_path / "providers.json"
    add_provider(path, Provider("demo", "Demo", "https://demo.example/v1", "key", (ModelDefinition("a", "A"),), {}))
    delete_provider(path, "demo")
    with pytest.raises(CatalogError, match="Unknown provider"):
        ProviderStore(path).get("demo")
