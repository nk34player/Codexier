import json
import tomllib
from pathlib import Path

import pytest

from codexier.codex_profile import (
    apply_codex_profile,
    apply_codex_profiles,
    build_catalog,
    ensure_unique_model_ids,
    provider_profile_id,
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


def test_profile_launch_command_is_explicit():
    from codexier.codex_profile import launch_command

    assert launch_command() == ["codex", "--profile", "codexier"]


def test_apply_all_profiles_writes_all_providers_and_one_catalog(tmp_path: Path):
    other = Provider(
        "other", "Other", "https://other.example/v1", "other-secret",
        (ModelDefinition("other/model", "Other Model"),), {},
    )
    result = apply_codex_profiles((provider(), other), other, tmp_path / ".codex")
    config = tomllib.loads(result.config_path.read_text())
    catalog = json.loads(result.catalog_path.read_text())
    assert provider_profile_id(other) == "codexier-other"
    assert config["model_provider"] == "codexier-other"
    assert set(config["model_providers"]) >= {"codexier-demo", "codexier-other"}
    assert [model["slug"] for model in catalog["models"]] == [
        "gpt-5.2", "claude/opus 4.8", "other/model"
    ]
    desktop = json.loads(result.desktop_config_path.read_text())
    assert desktop["model_providers"]["other/model"] == "codexier-other"


def test_duplicate_model_ids_are_rejected_before_apply(tmp_path: Path):
    duplicate = Provider(
        "other", "Other", "https://other.example/v1", "secret",
        (ModelDefinition("gpt-5.2", "Different label"),), {},
    )
    with pytest.raises(ConfigError, match="gpt-5.2"):
        ensure_unique_model_ids((provider(), duplicate))
    with pytest.raises(ConfigError, match="gpt-5.2"):
        apply_codex_profiles((provider(), duplicate), provider(), tmp_path / ".codex")
