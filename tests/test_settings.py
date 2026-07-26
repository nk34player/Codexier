import json
from pathlib import Path

from codexier.settings import DEFAULT_SETTINGS, load_settings, save_settings


def test_settings_are_created_next_to_provider_catalog(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = load_settings(provider_path)
    assert settings == DEFAULT_SETTINGS
    assert (tmp_path / "codexier.settings.json").exists()


def test_settings_round_trip(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = {
        "supports_parallel_tool_calls": True,
        "support_verbosity": False,
        "supports_search_tool": True,
        "web_search_tool_type": "text",
    }
    save_settings(provider_path, settings)
    assert load_settings(provider_path) == settings
    assert json.loads((tmp_path / "codexier.settings.json").read_text()) == settings
