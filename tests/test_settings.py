import json
import asyncio
from pathlib import Path

from textual.app import App

from codexier.settings import DEFAULT_SETTINGS, load_settings, save_settings
from codexier.setup_tui import SettingsScreen


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


def test_settings_toggle_updates_the_existing_item_without_duplicate_ids(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            assert screen.settings["supports_parallel_tool_calls"] is True
            assert len(screen.query_one("#settings").children) == 4

    asyncio.run(scenario())
