import json
import tomllib
from pathlib import Path

from codexier.codex_profile import apply_codex_profile, build_catalog
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
    assert catalog["models"][0]["context_window"] == 1000000
    assert catalog["models"][0]["auto_compact_token_limit"] == 900000


def test_apply_codex_profile_writes_proxyagent_style_files(tmp_path: Path):
    codex_home = tmp_path / ".codex"
    config = codex_home / "config.toml"
    config.parent.mkdir()
    config.write_text('model = "old"\n\n[other]\nvalue = true\n')
    result = apply_codex_profile(provider(), codex_home)
    parsed = tomllib.loads(config.read_text())
    assert parsed["model"] == "gpt-5.2"
    assert parsed["model_provider"] == "codexier"
    assert parsed["model_catalog_json"] == str(codex_home / "codexier.models.json")
    assert parsed["other"]["value"] is True
    assert parsed["model_providers"]["codexier"]["base_url"] == "https://demo.example/v1"
    assert (codex_home / "codexier.models.json").exists()
    assert json.loads((codex_home / "codexier.models.json").read_text())["default_model"] == "gpt-5.2"
    assert result.config_path == config


def test_applied_provider_is_read_from_codex_config(tmp_path: Path):
    from codexier.codex_profile import applied_provider_id

    config = tmp_path / "config.toml"
    config.write_text(
        'model_provider = "codexier"\n'
        '[model_providers.codexier]\n'
        'name = "Codexier"\n'
    )
    assert applied_provider_id(config) == "codexier"
