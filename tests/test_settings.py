import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from textual.app import App
from textual.widgets import (
    Button,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

from codexier.desktop_patch import (
    DesktopBackup,
    DesktopPatchStatus,
    DesktopPatchTarget,
    MacOSAppInfo,
)
from codexier.errors import ConfigError
from codexier.models import ModelDefinition, Provider
from codexier.settings import DEFAULT_SETTINGS, MAX_CONTEXT_WINDOW, load_settings, save_settings
from codexier.setup_tui import (
    BackupManagerScreen,
    ContextProfilesScreen,
    DeleteConfirmScreen,
    ProviderManagerApp,
    RestoreConfirmScreen,
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
        "create_desktop_shortcut": True,
    }
    save_settings(provider_path, settings)
    assert load_settings(provider_path)["windows"] == settings["windows"]


def test_windows_settings_screen_exposes_official_and_portable_actions(tmp_path: Path):
    screen = WindowsDesktopScreen(tmp_path / "providers.json", platform="win32")
    copy = "\n".join(
        value for value in screen.compose.__code__.co_consts if isinstance(value, str)
    )
    assert "Official Codex App" in copy
    assert "Portable App" in copy
    assert "Apply and launch official app" in copy
    assert "Create portable app" in copy
    assert "Refresh portable status" in copy
    assert "Repair patch" in copy
    assert "Sync and launch portable app" in copy
    assert "Desktop shortcut:" in screen._shortcut_label()


def test_portable_shortcut_toggle_persists_preference(tmp_path: Path, monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(
            "codexier.windows_portable.portable_status",
            lambda: SimpleNamespace(executable=None),
        )
        app = App()
        screen = WindowsDesktopScreen(
            tmp_path / "providers.json",
            (
                Provider(
                    "example",
                    "Example",
                    "https://example.test/v1",
                    "key",
                    (),
                    {},
                    True,
                ),
            ),
            initial_tab="portable",
            platform="win32",
        )
        async with app.run_test() as pilot:
            app.push_screen(screen)
            await pilot.pause()
            await screen._toggle_portable_shortcut()
            assert load_settings(tmp_path / "providers.json")["windows"][
                "create_desktop_shortcut"
            ]
            assert str(screen.query_one("#portable-shortcut", Button).label).endswith("ON")

    asyncio.run(scenario())


def test_windows_tabs_and_portable_controls_use_arrow_keys(tmp_path: Path):
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
                tmp_path / "providers.json",
                (provider,),
                initial_tab="portable",
                platform="win32",
            )
            app.push_screen(screen)
            await pilot.pause()
            tabs = screen.query_one(TabbedContent).query_one(Tabs)
            view = screen.query_one("#portable-providers", ListView)
            assert app.focused is tabs
            await pilot.press("left")
            assert screen.query_one(TabbedContent).active == "official-tab"
            await pilot.press("right")
            assert screen.query_one(TabbedContent).active == "portable-tab"
            await pilot.press("down")
            assert app.focused is view
            await pilot.press("down")
            assert app.focused is screen.query_one("#portable-create", Button)
            await pilot.press("right")
            assert app.focused is screen.query_one("#portable-refresh", Button)
            await pilot.press("up")
            assert app.focused is screen.query_one("#portable-create", Button)
            await pilot.press("up")
            assert app.focused is view
            await pilot.press("up")
            assert app.focused is tabs

    asyncio.run(scenario())


def test_windows_portable_actions_reach_back_with_down_at_small_size(tmp_path: Path):
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
        async with app.run_test(size=(80, 24)) as pilot:
            screen = WindowsDesktopScreen(
                tmp_path / "providers.json",
                (provider,),
                initial_tab="portable",
                platform="win32",
            )
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press("down")
            await pilot.press("down")
            for _ in range(5):
                await pilot.press("down")
            assert app.focused is screen.query_one("#back", Button)

    asyncio.run(scenario())


def test_windows_back_returns_to_settings_without_exiting_the_manager(tmp_path: Path):
    async def scenario() -> None:
        provider = Provider("example", "Example", "https://example.test/v1", "key", (), {}, True)
        app = ProviderManagerApp(tmp_path / "providers.json", (provider,))
        settings = SettingsScreen(tmp_path / "providers.json", (provider,))
        desktop = WindowsDesktopScreen(
            tmp_path / "providers.json", (provider,), platform="win32"
        )

        async def skip_status_load() -> None:
            pass

        desktop._load_status = skip_status_load  # type: ignore[method-assign]
        async with app.run_test() as pilot:
            app.push_screen(settings)
            await pilot.pause()
            app.push_screen(desktop)
            await pilot.pause()
            desktop.query_one("#back", Button).focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is settings

    asyncio.run(scenario())


def test_application_type_shows_macos_tabs_and_backup_manager_returns_to_parent(
    tmp_path: Path, monkeypatch
):
    archive = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"patched")
    sidecar = archive.with_name("app.asar.bak")
    sidecar.write_bytes(b"original")
    target = DesktopPatchTarget("darwin", archive)
    backup = DesktopBackup(
        target,
        sidecar,
        sidecar,
        "Immutable original",
        len(b"original"),
        datetime.now(timezone.utc),
        True,
        "Original archive",
    )
    monkeypatch.setattr("codexier.desktop_patch.application_targets", lambda: (target,))
    monkeypatch.setattr(
        "codexier.desktop_patch.default_target", lambda _platform=None: target
    )
    monkeypatch.setattr(
        "codexier.desktop_patch.macos_app_info",
        lambda _target: MacOSAppInfo(
            target.archive_path.parents[2],
            "ChatGPT",
            "com.openai.chat",
            "1.2.3",
            "456",
            target.archive_path,
            len(b"patched"),
            datetime.now(timezone.utc),
            DesktopPatchStatus(target, True, True, "Codexier desktop patch is installed."),
            True,
        ),
    )
    monkeypatch.setattr(
        "codexier.desktop_patch.desktop_backups",
        lambda _target, _root: (backup,),
    )
    settings = load_settings(tmp_path / "providers.json")
    settings["windows"]["mode"] = "portable"
    save_settings(tmp_path / "providers.json", settings)

    async def scenario() -> None:
        app = App()
        desktop = WindowsDesktopScreen(tmp_path / "providers.json", platform="darwin")
        backups = BackupManagerScreen()
        async with app.run_test() as pilot:
            app.push_screen(desktop)
            await pilot.pause()
            tabs = desktop.query_one(TabbedContent).query_one(Tabs)
            assert app.focused is tabs
            assert desktop.query_one(TabbedContent).active == "official-tab"
            assert "Name        ChatGPT" in str(
                desktop.query_one("#macos-app-info", Static).render()
            )
            patches = desktop.query_one("#macos-patches", ListView)
            patch = desktop.query_one(
                "#macos-patch-custom-providers-models", ListItem
            )
            apply = desktop.query_one("#macos-apply-patches", Button)
            assert "SELECTED" in str(patch.query_one(Label).render())
            assert not apply.disabled
            assert str(desktop.query_one("#official-tab", TabPane)._title) == "Official App"
            assert str(desktop.query_one("#custom-patches-tab", TabPane)._title) == "Custom Patches"
            assert not desktop.query("#portable-providers")
            await pilot.press("right")
            assert desktop.query_one(TabbedContent).active == "custom-patches-tab"
            await pilot.press("down")
            assert app.focused is patches
            await pilot.press("enter")
            assert "OFF" in str(patch.query_one(Label).render())
            assert apply.disabled
            await pilot.press("enter")
            assert "SELECTED" in str(patch.query_one(Label).render())
            assert not apply.disabled
            await pilot.press("down")
            assert app.focused is apply
            await pilot.press("down")
            assert app.focused is desktop.query_one("#backups", Button)
            await pilot.press("up")
            assert app.focused is apply
            await pilot.press("up")
            assert app.focused is patches
            await pilot.press("up")
            assert app.focused is tabs
            app.push_screen(backups)
            await pilot.pause()
            assert "Platform   darwin" in str(backups.query_one("#backup-info", Static).render())
            assert "Immutable original" in str(backups.query_one("#backup-info", Static).render())
            backups.action_select()
            await pilot.pause()
            assert isinstance(app.screen, RestoreConfirmScreen)
            app.screen.dismiss(False)
            await pilot.pause()
            assert app.screen is backups
            back = backups.query_one("#back", Button)
            backups.set_focus(back)
            await pilot.press("enter")
            await pilot.pause()
            assert app.screen is desktop

    asyncio.run(scenario())


def test_macos_custom_patch_uses_existing_patch_flow(tmp_path: Path, monkeypatch):
    target = DesktopPatchTarget("darwin", tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "app.asar")
    applied = []
    monkeypatch.setattr(
        "codexier.desktop_patch.default_target", lambda _platform=None: target
    )

    def apply_patch(selected_target, backup_root, progress):
        applied.append((selected_target, backup_root))
        progress(100, "completed: custom patch applied")

    monkeypatch.setattr("codexier.desktop_patch.apply_desktop_patch", apply_patch)

    async def scenario() -> None:
        app = App()
        desktop = WindowsDesktopScreen(tmp_path / "providers.json", platform="darwin")
        async with app.run_test() as pilot:
            app.push_screen(desktop)
            await pilot.pause()
            await desktop._run_action("macos-apply-patches")
            assert applied == [(target, Path.home() / ".codex" / "codexier-desktop-backups")]
            assert isinstance(app.screen, WindowsProgressScreen)
            assert desktop.progress_screen.finished

    asyncio.run(scenario())


def test_backup_delete_confirmation_cancels_then_refreshes_inventory(
    tmp_path: Path, monkeypatch
):
    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    sidecar = archive.with_name("app.asar.bak")
    sidecar.write_bytes(b"original")
    target = DesktopPatchTarget("windows", archive)
    backup = DesktopBackup(
        target,
        sidecar,
        sidecar,
        "Immutable original",
        len(b"original"),
        datetime.now(timezone.utc),
        True,
        "Original archive",
    )
    monkeypatch.setattr("codexier.desktop_patch.application_targets", lambda: (target,))
    monkeypatch.setattr(
        "codexier.desktop_patch.desktop_backups",
        lambda _target, _root: (backup,) if sidecar.exists() else (),
    )

    def delete_backup(_target, path, _root, progress):
        progress(80, "verification: deleting selected backup")
        path.unlink()

    monkeypatch.setattr("codexier.desktop_patch.delete_desktop_backup", delete_backup)

    async def scenario() -> None:
        app = App()
        manager = BackupManagerScreen()
        async with app.run_test() as pilot:
            app.push_screen(manager)
            await pilot.pause()
            manager.action_delete()
            await pilot.pause()
            assert isinstance(app.screen, DeleteConfirmScreen)
            assert app.focused is app.screen.query_one("#cancel", Button)
            app.screen.dismiss(False)
            await pilot.pause()
            assert app.screen is manager
            assert sidecar.exists()

            manager.action_delete()
            await pilot.pause()
            assert isinstance(app.screen, DeleteConfirmScreen)
            app.screen.dismiss(True)
            await pilot.pause()
            await pilot.pause()
            assert not sidecar.exists()
            assert not manager.backups
            assert manager.progress_screen is not None
            assert manager.progress_screen.finished

    asyncio.run(scenario())


def test_backup_delete_failure_is_shown_in_progress_log(tmp_path: Path, monkeypatch):
    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    sidecar = archive.with_name("app.asar.bak")
    sidecar.write_bytes(b"original")
    target = DesktopPatchTarget("windows", archive)
    backup = DesktopBackup(
        target,
        sidecar,
        sidecar,
        "Immutable original",
        len(b"original"),
        datetime.now(timezone.utc),
        True,
        "Original archive",
    )
    monkeypatch.setattr("codexier.desktop_patch.application_targets", lambda: (target,))
    monkeypatch.setattr(
        "codexier.desktop_patch.desktop_backups",
        lambda _target, _root: (backup,),
    )
    monkeypatch.setattr(
        "codexier.desktop_patch.delete_desktop_backup",
        lambda *_args: (_ for _ in ()).throw(ConfigError("delete failed")),
    )

    async def scenario() -> None:
        app = App()
        manager = BackupManagerScreen()
        async with app.run_test() as pilot:
            app.push_screen(manager)
            await pilot.pause()
            await manager._delete_selected()
            assert isinstance(app.screen, WindowsProgressScreen)
            assert "delete failed" in str(
                manager.progress_screen.query_one("#progress-status", Static).render()
            )
            assert manager.progress_screen.query_one("#progress-log", RichLog).lines
            assert manager.progress_screen.finished

    asyncio.run(scenario())


def test_backup_restore_reports_success_and_keeps_selected_backup(tmp_path: Path, monkeypatch):
    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"patched")
    sidecar = archive.with_name("app.asar.bak")
    sidecar.write_bytes(b"original")
    target = DesktopPatchTarget("windows", archive)
    backup = DesktopBackup(
        target,
        sidecar,
        sidecar,
        "Immutable original",
        len(b"original"),
        datetime.now(timezone.utc),
        True,
        "Original archive",
    )
    restored = []
    monkeypatch.setattr("codexier.desktop_patch.application_targets", lambda: (target,))
    monkeypatch.setattr(
        "codexier.desktop_patch.desktop_backups",
        lambda _target, _root: (backup,),
    )

    def restore(selected_target, selected_backup, progress):
        restored.append((selected_target, selected_backup))
        progress(80, "verification: restored selected backup")

    monkeypatch.setattr("codexier.desktop_patch.restore_desktop_patch", restore)

    async def scenario() -> None:
        app = App()
        manager = BackupManagerScreen()
        async with app.run_test() as pilot:
            app.push_screen(manager)
            await pilot.pause()
            await manager._restore_selected()
            assert restored == [(target, sidecar)]
            assert isinstance(app.screen, WindowsProgressScreen)
            assert manager.progress_screen is app.screen
            assert manager.progress_screen.finished
            assert sidecar.read_bytes() == b"original"

    asyncio.run(scenario())


def test_backup_restore_failure_is_shown_in_progress_log(tmp_path: Path, monkeypatch):
    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    sidecar = archive.with_name("app.asar.bak")
    sidecar.write_bytes(b"original")
    target = DesktopPatchTarget("windows", archive)
    backup = DesktopBackup(
        target,
        sidecar,
        sidecar,
        "Immutable original",
        len(b"original"),
        datetime.now(timezone.utc),
        True,
        "Original archive",
    )
    monkeypatch.setattr("codexier.desktop_patch.application_targets", lambda: (target,))
    monkeypatch.setattr(
        "codexier.desktop_patch.desktop_backups",
        lambda _target, _root: (backup,),
    )
    monkeypatch.setattr(
        "codexier.desktop_patch.restore_desktop_patch",
        lambda *_args: (_ for _ in ()).throw(ConfigError("restore failed")),
    )

    async def scenario() -> None:
        app = App()
        manager = BackupManagerScreen()
        async with app.run_test() as pilot:
            app.push_screen(manager)
            await pilot.pause()
            await manager._restore_selected()
            assert isinstance(app.screen, WindowsProgressScreen)
            assert "restore failed" in str(
                manager.progress_screen.query_one("#progress-status", Static).render()
            )
            assert manager.progress_screen.query_one("#progress-log", RichLog).lines
            assert manager.progress_screen.finished

    asyncio.run(scenario())


def test_official_tab_shows_app_and_selected_configuration_without_provider_list(tmp_path: Path):
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
                tmp_path / "providers.json",
                (provider,),
                initial_tab="official",
                platform="win32",
            )
            app.push_screen(screen)
            await pilot.pause()
            tabs = screen.query_one(TabbedContent).query_one(Tabs)
            assert app.focused is tabs
            assert isinstance(screen.query_one("#official-app-info", Static), Static)
            assert isinstance(screen.query_one("#official-config-info", Static), Static)
            await pilot.press("down")
            assert app.focused is screen.query_one("#official-sync", Button)
            await pilot.press("up")
            assert app.focused is tabs

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
            tmp_path / "providers.json",
            (provider,),
            initial_tab="portable",
            platform="win32",
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


def test_settings_enter_toggles_the_existing_item_without_duplicate_ids(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            settings_view = screen.query_one("#settings", ListView)
            assert ("application_type", "Application type") in screen.setting_keys
            assert settings_view.highlighted_child is settings_view.children[0]
            await pilot.press("space")
            assert screen.settings["supports_parallel_tool_calls"] is False
            await pilot.press("enter")
            await pilot.pause()
            assert screen.settings["supports_parallel_tool_calls"] is True
            assert len(screen.query_one("#settings").children) == len(screen.setting_keys)

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
            await pilot.press("enter")
            assert screen.settings["input_modalities"] == ["text"]
            await pilot.press("enter")
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
            assert len(items) == len(screen.setting_keys)
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


def test_context_profiles_open_with_enter_and_save_from_action_button(tmp_path: Path):
    async def scenario() -> None:
        app = App()
        async with app.run_test() as pilot:
            app.push_screen(SettingsScreen(tmp_path / "providers.json"))
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            settings_view = screen.query_one("#settings")
            settings_view.index = next(
                i for i, (key, _) in enumerate(screen.setting_keys)
                if key == "context_profiles"
            )

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ContextProfilesScreen)
            app.pop_screen()
            await pilot.pause()

            settings_view = screen.query_one("#settings")
            settings_view.index = len(screen.setting_keys) - 1
            settings_view.focus()
            await pilot.press("down")
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
