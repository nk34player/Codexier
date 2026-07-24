from pathlib import Path

import pytest

from codexier.config_manager import detect_config_target, load_target
from codexier.errors import ConfigError


def test_explicit_json_target(tmp_path: Path):
    target = detect_config_target(tmp_path / "config.json")
    assert target.format == "json"


def test_explicit_toml_target(tmp_path: Path):
    target = detect_config_target(tmp_path / "config.toml")
    assert target.format == "toml"


def test_unknown_extension_rejected(tmp_path: Path):
    with pytest.raises(ConfigError):
        detect_config_target(tmp_path / "config.yaml")


def test_codex_home_toml_wins_when_json_also_exists(monkeypatch, tmp_path: Path):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text("model = 'demo'\n")
    (home / "config.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(home))
    target = detect_config_target()
    assert target.path == home / "config.toml"
    assert target.format == "toml"


def test_proxyagent_style_toml_is_recognized(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        'model = "gpt-5.6-terra"\n'
        'model_provider = "proxyagent"\n'
        'model_catalog_json = "/tmp/proxyagent.models.json"\n\n'
        '[model_providers.proxyagent]\n'
        'name = "ProxyAgent"\n'
        'base_url = "http://localhost:8001/v1"\n'
    )
    target = detect_config_target(path)
    data, mapping = load_target(target)
    assert mapping.base_url_path == ("model_providers", "proxyagent", "base_url")
    assert data["model_provider"] == "proxyagent"
