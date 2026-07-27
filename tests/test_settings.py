import json
import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Button, Label, ListItem, ListView, RichLog

from codexier.models import ModelDefinition, Provider
from codexier.settings import DEFAULT_SETTINGS, MAX_CONTEXT_WINDOW, load_settings, save_settings
from codexier.setup_tui import (
    ContextProfilesScreen,
    SettingsScreen,
    WindowsDesktopScreen,
    WindowsProgressScreen,
)


def test_settings_are_created_next_to_provider_catalog(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = load_settings(provider_path)
    assert settings == DEFAULT_SETTINGS
    assert settings["input_modalities"] == ["text", "image"]
    assert (tmp_path / "codexier.settings.json").exists()


def test_settings_round_trip(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = {
        "supports_parallel_tool_calls": True,
        "support_verbosity": False,
        "supports_search_tool": True,
        "web_search_tool_type": "text",
        "input_modalities": ["text"],
        "context_window": 200000,
        "auto_compact_token_limit": 60000,
    }
    save_settings(provider_path, settings)
    expected = {**settings, "windows": DEFAULT_SETTINGS["windows"]}
    assert load_settings(provider_path) == expected
    assert json.loads((tmp_path / "codexier.settings.json").read_text()) == expected


def test_windows_mode_selections_round_trip_independently(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = load_settings(provider_path)
    settings["windows"] = {
        "mode": "portable",
        "official_provider_id": "official-provider",
        "portable_default_provider_id": "portable-provider",
    }
    save_settings(provider_path, settings)
    assert load_settings(provider_path)["windows"] == settings["windows"]


def test_windows_settings_screen_exposes_official_and_portable_actions(tmp_path: Path):
    screen = WindowsDesktopScreen(tmp_path / "providers.json")
    copy = "\n".join(
        value for value in screen.compose.__code__.co_consts if isinstance(value, str)
    )
    assert "Official Codex App" in copy
    assert "Portable App" in copy
    assert "Sync and launch official app" in copy
    assert "Create portable app" in copy
    assert "Refresh portable status" in copy
    assert "Repair patch" in copy
    assert "Sync and launch portable app" in copy


def test_portable_screen_uses_arrow_keys_for_lists_and_buttons(tmp_path: Path):
    async def scenario() -> None:
        provider = Provider(
            "example",
            "Example",
            "https://example.test/v1",
            "key",
            (ModelDefinition("example-model", "Example Model"),),
            {},
            True,
        )
        app = App()
        async with app.run_test() as pilot:
            screen = WindowsDesktopScreen(
                tmp_path / "providers.json", (provider,), initial_tab="portable"
            )
            app.push_screen(screen)
            await pilot.pause()
            view = screen.query_one("#portable-providers", ListView)
            assert app.focused is view
            await pilot.press("down")
            assert app.focused is screen.query_one("#portable-create", Button)
            await pilot.press("down")
            assert app.focused is screen.query_one("#portable-refresh", Button)
            await pilot.press("up")
            assert app.focused is screen.query_one("#portable-create", Button)

    asyncio.run(scenario())


def test_progress_dialog_shows_an_auto_scrolling_console_by_default():
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            dialog = WindowsProgressScreen("Creating Portable Codex")
            app.push_screen(dialog)
            await pilot.pause()
            log = dialog.query_one("#progress-log", RichLog)
            assert log.display
            dialog.update_progress(25, "cloning: copying app files")
            assert dialog.query_one("#progress-bar").progress == 25
            dialog.finish("Completed.")
            assert dialog.query_one("#progress-close", Button).display

    asyncio.run(scenario())


def test_refresh_portable_status_does_not_require_a_provider(tmp_path: Path, monkeypatch):
    async def scenario() -> None:
        refreshed = []
        monkeypatch.setattr(
            "codexier.windows_portable.refresh_portable",
            lambda **_kwargs: refreshed.append(True),
        )
        provider = Provider(
            "disabled",
            "Disabled",
            "https://disabled.test/v1",
            "key",
            (),
            {},
            False,
        )
        app = App()
        screen = WindowsDesktopScreen(
            tmp_path / "providers.json", (provider,), initial_tab="portable"
        )

        async def skip_status_load() -> None:
            pass

        screen._load_status = skip_status_load  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await screen._run_action("portable-refresh")
            assert refreshed == [True]
            assert isinstance(app.screen, WindowsProgressScreen)

    asyncio.run(scenario())


def test_settings_accept_maximum_context_limit(tmp_path: Path):
    provider_path = tmp_path / "providers.json"
    settings = dict(DEFAULT_SETTINGS)
    settings["context_window"] = MAX_CONTEXT_WINDOW
    settings["auto_compact_token_limit"] = MAX_CONTEXT_WINDOW - 1
    save_settings(provider_path, settings)
    assert load_settings(provider_path)["context_window"] == 10_000_000


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
            assert len(screen.query_one("#settings").children) == 6

    asyncio.run(scenario())


def test_settings_toggle_switches_input_modalities(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            settings_view = screen.query_one("#settings")
            settings_view.index = 4
            await pilot.press("space")
            assert screen.settings["input_modalities"] == ["text"]
            await pilot.press("space")
            assert screen.settings["input_modalities"] == ["text", "image"]

    asyncio.run(scenario())


def test_settings_show_explanations_for_every_setting(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            items = list(screen.query("#settings > ListItem"))
            assert len(items) == len(screen.SETTING_KEYS)
            for item in items:
                text = str(item.query_one(Label).render())
                assert "\\n" in text
                assert "dim" in text

    asyncio.run(scenario())


def test_context_profile_editor_saves_both_limits(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(ContextProfilesScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ContextProfilesScreen)
            screen.query_one("#context-window").value = "200000"
            screen.query_one("#compact-limit").value = "60000"
            await pilot.press("enter")
            await pilot.pause()
            saved = load_settings(tmp_path / "providers.json")
            assert saved["context_window"] == 200000
            assert saved["auto_compact_token_limit"] == 60000

    asyncio.run(scenario())


def test_context_profiles_open_with_space_and_enter_saves_settings(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            settings_view = screen.query_one("#settings")
            settings_view.index = len(screen.SETTING_KEYS) - 1

            await pilot.press("space")
            await pilot.pause()
            assert isinstance(app.screen, ContextProfilesScreen)
            app.pop_screen()
            await pilot.pause()

            settings_view = screen.query_one("#settings")
            settings_view.index = len(screen.SETTING_KEYS) - 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is not screen
            assert load_settings(tmp_path / "providers.json")["context_window"] == 250_000

    asyncio.run(scenario())


def test_context_profile_rejects_compact_limit_at_or_above_max(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(ContextProfilesScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ContextProfilesScreen)
            screen.query_one("#context-window").value = "100000"
            screen.query_one("#compact-limit").value = "100000"
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is screen
            assert "below maximum" in str(screen.query_one("#status").render())

    asyncio.run(scenario())
