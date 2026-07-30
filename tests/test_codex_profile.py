import json
import tomllib
from pathlib import Path

import pytest

from codexier.codex_profile import (
    apply_codex_profile,
    apply_codex_profiles,
    build_catalog,
    build_desktop_provider_config,
    provider_route_id,
)
from codexier.errors import ConfigError
from codexier.models import ModelDefinition, Provider


def provider():
    return Provider(
        "demo", "Demo", "https://demo.example/v1", "secret",
        (ModelDefinition("gpt-5.2", "GPT 5.2"), ModelDefinition("claude/opus 4.8", "Opus 4.8")),
        {},
    )


def test_build_catalog_contains_codex_model_metadata():
    catalog = build_catalog(provider())
    assert catalog["default_model"] == "gpt-5.2"
    assert catalog["models"][0]["slug"] == "gpt-5.2"
    assert all(model["context_window"] == 250000 for model in catalog["models"])
    assert all(model["max_context_window"] == 250000 for model in catalog["models"])
    assert all(model["auto_compact_token_limit"] == 70000 for model in catalog["models"])
    assert catalog["models"][0]["supports_search_tool"] is False
    assert "web_search_tool_type" not in catalog["models"][0]
    assert catalog["models"][0]["supports_parallel_tool_calls"] is False
    assert catalog["models"][0]["support_verbosity"] is False
    assert catalog["models"][0]["input_modalities"] == ["text", "image"]


def test_desktop_picker_uses_the_exact_tui_provider_name():
    named = Provider(
        "private-eu",
        "Private EU + Prod",
        "https://private.example/v1",
        "secret",
        (ModelDefinition("model-id", "Model Label"),),
        {},
    )

    config = build_desktop_provider_config((named,), named)

    assert config["providers"] == [
        {
            "id": "codexier-private-eu",
            "label": "Private EU + Prod",
            "description": "Custom Provider",
            "base_url": "https://private.example/v1/",
            "token": "secret",
            "models": [{"id": "model-id", "label": "Model Label"}],
        }
    ]


def test_build_catalog_can_disable_image_input():
    catalog = build_catalog(provider(), {"input_modalities": ["text"]})
    assert all(model["input_modalities"] == ["text"] for model in catalog["models"])


def test_build_catalog_uses_custom_context_profile():
    catalog = build_catalog(
        provider(),
        {"context_window": 200000, "auto_compact_token_limit": 60000},
    )
    assert all(model["context_window"] == 200000 for model in catalog["models"])
    assert all(model["max_context_window"] == 200000 for model in catalog["models"])
    assert all(model["auto_compact_token_limit"] == 60000 for model in catalog["models"])


def test_apply_codex_profile_writes_proxyagent_style_files(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    config = codex_home / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "old"\n\n[other]\nvalue = true\n')
    result = apply_codex_profile(provider(), codex_home)
    parsed = tomllib.loads(config.read_text())
    assert parsed["model"] == "gpt-5.2"
    assert parsed["model_provider"] == "codexier"
    assert parsed["model_context_window"] == 250000
    assert parsed["model_auto_compact_token_limit"] == 70000
    assert parsed["model_auto_compact_token_limit_scope"] == "total"
    assert parsed["model_catalog_json"] == str(codex_home / "codexier.models.json")
    assert parsed["other"]["value"] is True
    assert parsed["model_providers"]["codexier"]["base_url"] == "https://demo.example/v1/"
    assert parsed["model_providers"]["codexier"]["experimental_bearer_token"] == "secret"
    assert "api_key" not in parsed["model_providers"]["codexier"]
    assert "requires_api_key" not in parsed["model_providers"]["codexier"]
    assert parsed["model_providers"]["codexier"]["requires_openai_auth"] is False
    assert (codex_home / "codexier.models.json").exists()
    assert json.loads((codex_home / "codexier.models.json").read_text())["default_model"] == "gpt-5.2"
    assert result.config_path == config
    profile = tomllib.loads(result.profile_path.read_text())
    assert profile["model_context_window"] == 250000
    assert profile["model_auto_compact_token_limit"] == 70000


def test_apply_codex_profile_writes_custom_context_limits(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    result = apply_codex_profile(
        provider(),
        codex_home,
        {"context_window": 200000, "auto_compact_token_limit": 60000},
    )

    config = tomllib.loads(result.config_path.read_text())
    profile = tomllib.loads(result.profile_path.read_text())
    catalog = json.loads(result.catalog_path.read_text())
    assert config["model_context_window"] == 200000
    assert config["model_auto_compact_token_limit"] == 60000
    assert profile["model_context_window"] == 200000
    assert profile["model_auto_compact_token_limit"] == 60000
    assert all(model["context_window"] == 200000 for model in catalog["models"])
    assert all(model["auto_compact_token_limit"] == 60000 for model in catalog["models"])


def test_applied_provider_is_read_from_codex_config(tmp_path: Path):
    from codexier.codex_profile import applied_provider_id

    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "codexier"\n'
        '[model_providers.codexier]\n'
        'name = "Codexier"\n'
    )
    assert applied_provider_id(config) == "codexier"


def test_applied_provider_id_reads_catalog_provider_identity(tmp_path: Path):
    from codexier.codex_profile import applied_provider_id

    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "codexier"\n'
        'codexier_provider_id = "demo"\n'
    )
    assert applied_provider_id(config) == "demo"


def test_legacy_default_resolves_from_normal_codexier_connection(tmp_path: Path):
    from codexier.codex_profile import legacy_codexier_provider_id

    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "codexier"\n'
        '[model_providers.codexier]\n'
        'base_url = "https://demo.example/v1/"\n'
        'experimental_bearer_token = "secret"\n'
    )
    assert legacy_codexier_provider_id((provider(),), config) == "demo"


def test_legacy_default_does_not_guess_when_connection_is_ambiguous(tmp_path: Path):
    from codexier.codex_profile import legacy_codexier_provider_id

    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "codexier"\n'
        '[model_providers.codexier]\n'
        'base_url = "https://demo.example/v1/"\n'
        'experimental_bearer_token = "secret"\n'
    )
    duplicate = Provider(
        "duplicate", "Duplicate", "https://demo.example/v1", "secret",
        (ModelDefinition("other", "Other"),), {},
    )
    assert legacy_codexier_provider_id((provider(), duplicate), config) is None


def test_profile_launch_command_is_explicit():
    from codexier.codex_profile import launch_command

    assert launch_command() == ["codex", "--profile", "codexier"]


def test_apply_writes_one_normal_profile_and_enabled_provider_routes(tmp_path: Path):
    other = Provider(
        "other", "Other", "https://other.example/v1", "other-secret",
        (ModelDefinition("other/model", "Other Model"),), {},
    )
    result = apply_codex_profiles((provider(), other), other, tmp_path / ".codex")
    config = tomllib.loads(result.config_path.read_text())
    catalog = json.loads(result.catalog_path.read_text())
    assert provider_route_id(other) == "codexier-other"
    assert config["model_provider"] == "codexier"
    assert config["codexier_provider_id"] == "other"
    # Single shared route — no per-provider codexier-{id} TOML sections.
    assert set(config["model_providers"]) == {"codexier"}
    assert set(config["profiles"]) == {"codexier"}
    assert config["profiles"]["codexier"]["name"] == "Codexier"
    assert config["model_providers"]["codexier"]["name"] == "Codexier"
    assert [model["slug"] for model in catalog["models"]] == [
        "gpt-5.2", "claude/opus 4.8", "other/model"
    ]
    desktop = json.loads(result.desktop_config_path.read_text())
    assert desktop["version"] == 2
    assert desktop["default_provider"] == "codexier-other"
    assert desktop["providers"][1]["models"] == [
        {"id": "other/model", "label": "Other Model"}
    ]
    # Credentials live in desktop-model-providers.json, not TOML sections.
    assert desktop["providers"][1]["base_url"] == "https://other.example/v1/"
    assert desktop["providers"][1]["token"] == "other-secret"


def test_sync_preserves_unrelated_session_like_configuration_tables(tmp_path: Path):
    home = tmp_path / ".codex"
    home.mkdir()
    config_path = home / "config.toml"
    config_path.write_text(
        '[profiles.work]\nmodel = "existing"\n'
        '[model_providers.unrelated]\nname = "keep"\n'
        '[session_state]\nrecent = ["thread-1", "thread-2"]\n'
        '[workspace_state]\nselected = "project-a"\n'
    )
    apply_codex_profiles((provider(),), provider(), home)
    config = tomllib.loads(config_path.read_text())

    assert set(config["profiles"]) == {"work", "codexier"}
    assert config["profiles"]["codexier"]["name"] == "Codexier"
    assert config["model_providers"]["unrelated"] == {"name": "keep"}
    assert config["session_state"] == {"recent": ["thread-1", "thread-2"]}
    assert config["workspace_state"] == {"selected": "project-a"}


def test_duplicate_model_ids_are_disambiguated_by_provider_in_desktop_config(tmp_path: Path):
    duplicate = Provider(
        "other", "Other", "https://other.example/v1", "secret",
        (ModelDefinition("gpt-5.2", "Different label"),), {},
    )
    result = apply_codex_profiles((provider(), duplicate), provider(), tmp_path / ".codex")
    desktop = json.loads(result.desktop_config_path.read_text())
    assert desktop["providers"][0]["models"][0]["id"] == "gpt-5.2"
    assert desktop["providers"][1]["models"] == [
        {"id": "gpt-5.2", "label": "Different label"}
    ]


def test_apply_skips_disabled_providers_and_removes_stale_codexier_entries(
    tmp_path: Path,
):
    disabled = Provider(
        "off",
        "Off",
        "https://off.example/v1",
        "off-secret",
        (ModelDefinition("off-model", "Off model"),),
        {},
        enabled=False,
    )
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "config.toml").write_text(
        '[model_providers.codexier-old]\nname = "old"\n'
        '[profiles.codexier-old]\nmodel = "old"\n'
        '[model_providers.unrelated]\nname = "keep"\n'
    )
    result = apply_codex_profiles((provider(), disabled), provider(), home)
    config = tomllib.loads(result.config_path.read_text())
    desktop = json.loads(result.desktop_config_path.read_text())
    assert "codexier-off" not in config["model_providers"]
    assert "codexier-old" not in config["model_providers"]
    assert "codexier-old" not in config["profiles"]
    assert config["model_providers"]["unrelated"]["name"] == "keep"
    assert [item["id"] for item in desktop["providers"]] == ["codexier-demo"]
    assert all(item["id"] != "openai" for item in desktop["providers"])
