from __future__ import annotations

import asyncio
import json
import sys
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Label,
    ListView,
    ListItem,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

from .backup import atomic_write
from .codex_profile import build_desktop_provider_config
from .desktop_patch_macos import PatchError
from .errors import ConfigError
from .models import Provider
from .settings import load_settings, save_settings
from .tui import widget_id
from .setup_tui_base import (
    _HIDDEN_PROVIDER_MANAGER_BINDINGS,
    _MACOS_PATCHES,
    _ProviderManagerShortcutIsolation,
    ProviderManagerResult,
)
from .setup_tui_dialogs import (
    DeleteConfirmScreen,
    ForceCloseConfirmScreen,
    ReapplyConfirmScreen,
    RestoreConfirmScreen,
    WindowsProgressScreen,
)


__all__ = [
    "BackupManagerScreen",
    "MacOSPatchListView",
    "WindowsDesktopScreen",
    "WindowsProviderListView",
]


class WindowsProviderListView(ListView):
    """Move between the Portable provider list, actions, and tabs."""

    def action_cursor_down(self) -> None:
        if self.index is not None and self.index < len(self.children) - 1:
            super().action_cursor_down()
        else:
            self.screen._focus_first_windows_action()  # type: ignore[attr-defined]

    def action_cursor_up(self) -> None:
        if self.index not in (None, 0):
            super().action_cursor_up()
        else:
            self.screen._focus_windows_tabs()  # type: ignore[attr-defined]


class MacOSPatchListView(ListView):
    """Toggle custom patches while preserving Settings-style list navigation."""

    def action_select_cursor(self) -> None:
        self.screen._toggle_selected_macos_patch()  # type: ignore[attr-defined]

    def action_cursor_down(self) -> None:
        if self.index is not None and self.index < len(self.children) - 1:
            super().action_cursor_down()
        else:
            self.screen._focus_macos_patch_apply()  # type: ignore[attr-defined]

    def action_cursor_up(self) -> None:
        if self.index not in (None, 0):
            super().action_cursor_up()
        else:
            self.screen._focus_windows_tabs()  # type: ignore[attr-defined]



class BackupManagerScreen(_ProviderManagerShortcutIsolation, Screen[bool | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 96%; height: 1fr; min-height: 0; margin: 0 1; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #backups { height: 1fr; min-height: 0; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; }
    #backups > ListItem { min-height: 4; padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    #backup-info { height: auto; min-height: 5; margin-top: 1; color: #c7d2fe; }
    #backup-actions { height: auto; min-height: 4; }
    Button { width: 1fr; margin-right: 1; background: #2563eb; color: white; }
    #delete { background: #b91c1c; }
    #back { background: #374151; }
    .error { color: #ff6b8a; }
    """
    BINDINGS = [
        Binding("enter", "activate", "Select", priority=True),
        Binding("b", "manual_backup", "Manual backup", priority=True),
        Binding("delete", "delete", "Delete", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(self):
        super().__init__()
        self.targets = ()
        self.backups = ()
        self.progress_screen: WindowsProgressScreen | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("APPLICATION BACKUPS")
            yield Static(
                "Immutable app.asar.bak files are never overwritten. "
                "Managed snapshots remain available for legacy restores. "
                "Press B to create a manual snapshot."
            )
            yield ListView(id="backups")
            yield Static("Loading backups …", id="backup-info")
            with Horizontal(id="backup-actions"):
                yield Button("Restore selected backup", id="restore", variant="primary")
                yield Button("Create manual backup", id="manual", variant="primary")
                yield Button("Delete selected backup", id="delete", variant="error")
                yield Button("Back", id="back")
        yield Footer()

    async def on_mount(self) -> None:
        await self._load_backups()
        self.query_one("#backups", ListView).focus()

    async def _load_backups(self) -> None:
        from .desktop_patch import application_targets, desktop_backups

        self.targets = application_targets()
        backup_root = Path.home() / ".codex" / "codexier-desktop-backups"
        self.backups = tuple(
            backup
            for target in self.targets
            for backup in desktop_backups(target, backup_root)
        )
        view = self.query_one("#backups", ListView)
        await view.clear()
        for index, backup in enumerate(self.backups):
            stamp = backup.modified_at.strftime("%Y-%m-%d %H:%M UTC")
            state = "ORIGINAL" if backup.valid else "INVALID"
            await view.append(
                ListItem(
                    Label(
                        f"●  {backup.target.platform.upper()} · {backup.kind}: {state}\n"
                        f"   [dim]{backup.path} · {backup.size_bytes:,} bytes · Backup made {stamp}[/dim]"
                    ),
                    id=f"backup-{index}",
                )
            )
        view.index = 0 if self.backups else None
        self._show_selected()

    def _selected_backup(self):
        item = self.query_one("#backups", ListView).highlighted_child
        if not item or not item.id:
            return None
        try:
            return self.backups[int(item.id.removeprefix("backup-"))]
        except (IndexError, ValueError):
            return None

    def _show_selected(self) -> None:
        backup = self._selected_backup()
        info = self.query_one("#backup-info", Static)
        if backup is None:
            info.update("No application backups found for this system.")
            return
        info.remove_class("error")
        info.update(
            f"Platform   {backup.target.platform}\n"
            f"Type       {backup.kind}\n"
            f"Archive    {backup.archive_path}\n"
            f"Size       {backup.size_bytes:,} bytes\n"
            f"Backup made {backup.modified_at:%Y-%m-%d %H:%M UTC}\n"
            f"Status     {backup.message}\n"
            f"Restore to {backup.target.archive_path}"
        )

    @on(ListView.Highlighted)
    def on_backup_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "backups":
            self._show_selected()

    def action_select(self) -> None:
        backup = self._selected_backup()
        info = self.query_one("#backup-info", Static)
        if backup is None or not backup.valid:
            info.update("Select a verified original backup before restoring.")
            info.add_class("error")
            return
        processes = []
        if backup.target.platform == "darwin":
            try:
                from .desktop_patch_macos import find_target_app_processes

                app = backup.target.archive_path.parents[2]
                processes = find_target_app_processes(app)
            except (ConfigError, OSError, PatchError):
                pass
        if processes:
            self.app.push_screen(
                ForceCloseConfirmScreen(tuple(pid for pid, _ in processes)),
                lambda confirmed: self._force_close_restore_confirmed(confirmed),
            )
            return
        self.app.push_screen(
            RestoreConfirmScreen(
                f"Restore {backup.kind.lower()}:\n{backup.path}\n\nto:\n{backup.target.archive_path}"
            ),
            self._restore_confirmed,
        )

    def action_activate(self) -> None:
        focused = self.focused
        if isinstance(focused, Button):
            actions = {
                "restore": self.action_select,
                "manual": self.action_manual_backup,
                "delete": self.action_delete,
                "back": self.action_cancel,
            }
            action = actions.get(focused.id)
            if action is not None:
                action()
            return
        self.action_select()

    def _restore_confirmed(
        self, confirmed: bool | None, force_close: bool = False
    ) -> None:
        if confirmed:
            self.run_worker(
                self._restore_selected(force_close=force_close), exclusive=True
            )

    def _force_close_restore_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            backup = self._selected_backup()
            if backup is None:
                return
            self.app.push_screen(
                RestoreConfirmScreen(
                    f"Restore {backup.kind.lower()}:\n{backup.path}\n\nto:\n{backup.target.archive_path}"
                ),
                lambda restored: self._restore_confirmed(restored, force_close=True),
            )

    def action_delete(self) -> None:
        backup = self._selected_backup()
        info = self.query_one("#backup-info", Static)
        if backup is None:
            info.update("Select a backup before deleting it.")
            info.add_class("error")
            return
        self.app.push_screen(
            DeleteConfirmScreen(f"Delete {backup.kind.lower()}:\n{backup.path}"),
            self._delete_confirmed,
        )

    def _delete_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.run_worker(self._delete_selected(), exclusive=True)

    def action_manual_backup(self) -> None:
        selected = self._selected_backup()
        target = selected.target if selected is not None else (
            self.targets[0] if self.targets else None
        )
        info = self.query_one("#backup-info", Static)
        if target is None:
            info.update("No supported installed application was found to back up.")
            info.add_class("error")
            return
        self.run_worker(self._manual_backup(target), exclusive=True)

    async def _manual_backup(self, target) -> None:
        self.progress_screen = WindowsProgressScreen("Creating manual application backup")
        await self.app.push_screen(self.progress_screen)
        try:
            from .desktop_patch import backup_desktop_patch

            backup = await asyncio.to_thread(
                backup_desktop_patch,
                target,
                Path.home() / ".codex" / "codexier-desktop-backups",
                self._progress,
            )
        except (ConfigError, OSError, PatchError) as exc:
            self.progress_screen.finish(str(exc), error=True)
            return
        self.progress_screen.finish(
            f"Manual backup created at {backup}. Review the log, then close."
        )
        await self._load_backups()

    def _progress(self, percent: int, detail: str) -> None:
        self.app.call_from_thread(self._show_progress, percent, detail)

    def _show_progress(self, percent: int, detail: str) -> None:
        if self.progress_screen is not None:
            self.progress_screen.update_progress(percent, detail)

    async def _restore_selected(self, force_close: bool = False) -> None:
        backup = self._selected_backup()
        if backup is None:
            return
        self.progress_screen = WindowsProgressScreen("Restoring application backup")
        await self.app.push_screen(self.progress_screen)
        try:
            from .desktop_patch import restore_desktop_patch
            if force_close:
                from .desktop_patch_macos import gracefully_close_target_app_processes

                await asyncio.to_thread(
                    gracefully_close_target_app_processes,
                    backup.target.archive_path.parents[2],
                    force=True,
                )

            await asyncio.to_thread(
                restore_desktop_patch,
                backup.target,
                backup.path,
                self._progress,
            )
        except (ConfigError, OSError, PatchError) as exc:
            self.progress_screen.finish(str(exc), error=True)
            return
        self.progress_screen.finish("Backup restored. Review the detailed log, then close.")
        await self._load_backups()

    async def _delete_selected(self) -> None:
        backup = self._selected_backup()
        if backup is None:
            return
        self.progress_screen = WindowsProgressScreen("Deleting application backup")
        await self.app.push_screen(self.progress_screen)
        try:
            from .desktop_patch import delete_desktop_backup

            await asyncio.to_thread(
                delete_desktop_backup,
                backup.target,
                backup.path,
                Path.home() / ".codex" / "codexier-desktop-backups",
                self._progress,
            )
        except (ConfigError, OSError, PatchError) as exc:
            self.progress_screen.finish(str(exc), error=True)
            return
        self.progress_screen.finish("Backup deleted permanently. Review the detailed log, then close.")
        await self._load_backups()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "restore":
            self.action_select()
        elif event.button.id == "manual":
            self.action_manual_backup()
        elif event.button.id == "delete":
            self.action_delete()
        elif event.button.id == "back":
            self.action_cancel()


class WindowsDesktopScreen(_ProviderManagerShortcutIsolation, Screen[bool | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 98%; height: 1fr; min-height: 0; margin: 0; padding: 0 1; border: round #3b82f6; background: #131d38; }
    TabbedContent { height: 1fr; min-height: 0; }
    TabPane { padding: 1 2; }
    .official-layout { height: 1fr; min-height: 0; }
    .official-info { width: 2fr; padding: 1 2; border: round #263b68; background: #0f1730; }
    .official-selection { width: 1fr; margin-left: 1; padding: 1 2; border: round #3b82f6; background: #101b36; }
    .macos-card { height: 1fr; min-height: 0; max-width: 96; padding: 1 2; border: round #263b68; background: #0f1730; }
    #macos-patches { height: 1fr; min-height: 0; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #macos-patches > ListItem { min-height: 5; padding: 1 3; }
    .section-title { color: #8be9fd; text-style: bold; }
    .provider-list { height: 1fr; min-height: 0; border: round #263b68; background: #0f1730; }
    .provider-list > ListItem { min-height: 3; padding: 1 2; }
    .actions { height: auto; min-height: 4; margin-top: 1; overflow-x: auto; scrollbar-size: 1 1; }
    Button { margin-right: 1; background: #2563eb; color: white; }
    #back { background: #374151; }
    #windows-status { height: auto; min-height: 2; color: #8be9fd; padding: 1 0; }
    .error { color: #ff6b8a; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(
        self,
        catalog_path,
        providers: tuple[Provider, ...] = (),
        *,
        initial_tab: str | None = None,
        platform: str | None = None,
    ):
        super().__init__()
        self.catalog_path = catalog_path
        self.providers = providers
        self.settings = load_settings(catalog_path)
        self.is_windows = (platform or sys.platform) == "win32"
        mode = initial_tab or self.settings["windows"]["mode"] if self.is_windows else "official"
        self.initial_tab = f"{mode}-tab"
        self.macos_selected_patches = {patch_id for patch_id, _, _ in _MACOS_PATCHES}
        self.progress_screen: WindowsProgressScreen | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("APPLICATION TYPE  /  SHARED LOWERCASE codexier PROFILE")
            with TabbedContent(initial=self.initial_tab):
                if self.is_windows:
                    with TabPane("Official Codex App", id="official-tab"):
                        with Horizontal(classes="official-layout"):
                            with Vertical(classes="official-info"):
                                yield Static("OFFICIAL APP", classes="section-title")
                                yield Static(
                                    "Loading official app information …",
                                    id="official-app-info",
                                )
                            with Vertical(classes="official-selection"):
                                yield Static("CURRENT CONFIGURATION", classes="section-title")
                                yield Static(
                                    "Loading selected configuration …",
                                    id="official-config-info",
                                )
                        with Horizontal(classes="actions"):
                            yield Button(
                                "Apply and launch official app",
                                id="official-sync",
                                variant="primary",
                            )
                    with TabPane("Portable App", id="portable-tab"):
                        yield Static(
                            "Codexier clones the installed Store payload into LocalAppData, "
                            "patches only that copy, and exposes every enabled provider."
                        )
                        yield WindowsProviderListView(id="portable-providers", classes="provider-list")
                        with Horizontal(classes="actions"):
                            yield Button("Create portable app", id="portable-create")
                            yield Button("Refresh portable status", id="portable-refresh")
                            yield Button("Repair patch", id="portable-repair")
                            yield Button(
                                "Sync and launch portable app",
                                id="portable-sync",
                                variant="primary",
                            )
                else:
                    with TabPane("Official App", id="official-tab"):
                        with Horizontal(classes="official-layout"):
                            with Vertical(classes="official-info"):
                                yield Static("INSTALLED APPLICATION", classes="section-title")
                                yield Static(
                                    "Loading installed application information …",
                                    id="macos-app-info",
                                )
                            with Vertical(classes="official-selection"):
                                yield Static("OFFICIAL APP", classes="section-title")
                                yield Static(
                                    "No editions or Codexier modifications are applied from this tab.\n"
                                    "To restore an earlier archive, use View application backups."
                                )
                                yield Static("CURRENT STATE  /  Loading …", id="macos-app-state")
                    with TabPane("Custom Patches", id="custom-patches-tab"):
                        with Vertical(classes="macos-card"):
                            yield Static("SELECT PATCHES TO APPLY", classes="section-title")
                            with MacOSPatchListView(id="macos-patches"):
                                for patch_id, title, description in _MACOS_PATCHES:
                                    yield ListItem(
                                        Label(
                                            self._macos_patch_label(
                                                patch_id, title, description
                                            )
                                        ),
                                        id=f"macos-patch-{patch_id}",
                                    )
                            with Horizontal(classes="actions"):
                                yield Button(
                                    "Apply selected patches",
                                    id="macos-apply-patches",
                                    variant="primary",
                                )
            yield Static(
                "Loading Windows desktop status …"
                if self.is_windows
                else "Choose a tab to view the official app or apply a custom patch.",
                id="windows-status",
            )
            with Horizontal(classes="actions"):
                if self.is_windows:
                    yield Button(self._shortcut_label(), id="portable-shortcut")
                yield Button("View application backups", id="backups")
                yield Button("Back to settings", id="back")
        yield Footer()

    async def on_mount(self) -> None:
        if not self.is_windows:
            self.query_one("#macos-patches", ListView).index = 0
            self.call_after_refresh(self._focus_windows_tabs)
            self.run_worker(self._load_macos_app_info(), exclusive=True)
            return
        if not self.providers:
            from .provider_store import ProviderStore

            self.providers = ProviderStore(self.catalog_path).load()
        enabled = tuple(provider for provider in self.providers if provider.enabled)
        view = self.query_one("#portable-providers", ListView)
        for provider in enabled:
            await view.append(
                ListItem(
                    Label(f"◆  {provider.name} · {len(provider.models)} models"),
                    id=widget_id("portable-providers", provider.id),
                )
            )
        selected_id = self.settings["windows"].get("portable_default_provider_id")
        view.index = next(
            (
                index
                for index, provider in enumerate(enabled)
                if provider.id == selected_id
            ),
            0 if enabled else None,
        )
        self.call_after_refresh(self._focus_windows_tabs)
        self.run_worker(self._load_status(), exclusive=True)

    async def _load_status(self) -> None:
        if not self.is_windows:
            return
        status = self.query_one("#windows-status", Static)
        selected = self._official_selected_provider()
        self.query_one("#official-config-info", Static).update(
            "Profile     codexier (displayed as Codexier)\n"
            "Mode        One enabled provider\n"
            + (
                f"Provider    {selected.name}\n"
                f"Models      {len(selected.models)}"
                if selected is not None
                else "Provider    Not applied yet\n"
                "Choose a provider on the main screen, then apply it here."
            )
        )
        try:
            from .windows_portable import discover_official_package, portable_status

            package = await asyncio.to_thread(discover_official_package)
            portable = portable_status(package)
            self.query_one("#official-app-info", Static).update(
                f"Version     {package.version}\n"
                "Type        Signed Microsoft Store/MSIX package\n"
                f"Path        {package.install_location}\n"
                f"Executable  {package.executable}\n"
                f"App ID      {package.aumid}"
            )
            status.update(
                f"Portable path     {portable.root}\n"
                f"Portable state    {portable.message}"
            )
        except (ConfigError, OSError) as exc:
            status.update(str(exc))
            status.add_class("error")

    async def _load_macos_app_info(self) -> None:
        info = self.query_one("#macos-app-info", Static)
        status = self.query_one("#windows-status", Static)
        try:
            from .desktop_patch import default_target, macos_app_info

            app = await asyncio.to_thread(macos_app_info, default_target("darwin"))
        except (ConfigError, OSError) as exc:
            info.update(str(exc))
            info.add_class("error")
            status.update("Installed application information is unavailable.")
            status.add_class("error")
            return
        state = "PATCHED" if app.patch_status.patched else "UNMODIFIED"
        backup = "Available" if app.sidecar_exists else "Not created yet"
        self.query_one("#macos-app-state", Static).update(
            f"CURRENT STATE  /  {'PATCHED' if app.patch_status.patched else 'OFFICIAL'}"
        )
        info.remove_class("error")
        info.update(
            f"Name        {app.app_name}\n"
            f"Version     {app.version} ({app.build})\n"
            f"Bundle ID   {app.bundle_identifier}\n"
            f"App path    {app.app_path}\n"
            f"Archive     {app.archive_path}\n"
            f"Archive     {app.archive_size_bytes:,} bytes · "
            f"{app.archive_modified_at:%Y-%m-%d %H:%M UTC}\n"
            f"Patch       {state} · {app.patch_status.message}\n"
            f"Backup      app.asar.bak · {backup}"
        )
        status.remove_class("error")
        status.update("Installed application information loaded.")

    def _official_selected_provider(self) -> Provider | None:
        selected_id = self.settings["windows"].get("official_provider_id")
        return next(
            (
                provider
                for provider in self.providers
                if provider.id == selected_id and provider.enabled
            ),
            None,
        )

    def _selected_provider(self, view_id: str) -> Provider | None:
        item = self.query_one(f"#{view_id}", ListView).highlighted_child
        if not item or not item.id:
            return None
        return next(
            (
                provider
                for provider in self.providers
                if widget_id(view_id, provider.id) == item.id
            ),
            None,
        )

    def _active_windows_actions(self) -> tuple[Button, ...]:
        if self.query_one(TabbedContent).active == "official-tab":
            ids = ("official-sync", "back")
        else:
            ids = (
                "portable-create",
                "portable-refresh",
                "portable-repair",
                "portable-sync",
                "portable-shortcut",
                "back",
            )
        return tuple(self.query_one(f"#{button_id}", Button) for button_id in ids)

    def _active_macos_actions(self) -> tuple[Button | ListView, ...]:
        ids = (
            ("backups", "back")
            if self.query_one(TabbedContent).active == "official-tab"
            else (
                "macos-patches",
                "macos-apply-patches",
                "backups",
                "back",
            )
        )
        return tuple(
            self.query_one(
                f"#{widget_id}",
                ListView if widget_id == "macos-patches" else Button,
            )
            for widget_id in ids
        )

    def _selected_macos_patches(self) -> tuple[str, ...]:
        return tuple(
            patch_id
            for patch_id, _, _ in _MACOS_PATCHES
            if patch_id in self.macos_selected_patches
        )

    def _refresh_macos_patch_button(self) -> None:
        self.query_one("#macos-apply-patches", Button).disabled = (
            not self._selected_macos_patches()
        )

    def _macos_patch_label(self, patch_id: str, title: str, description: str) -> str:
        selected = patch_id in self.macos_selected_patches
        return f"{'●' if selected else '○'}  {title}: {'SELECTED' if selected else 'OFF'}\n   [dim]{description}[/dim]"

    def _toggle_selected_macos_patch(self) -> None:
        view = self.query_one("#macos-patches", ListView)
        item = view.highlighted_child
        if item is None or item.id is None:
            return
        patch_id = item.id.removeprefix("macos-patch-")
        patch = next(
            (patch for patch in _MACOS_PATCHES if patch[0] == patch_id),
            None,
        )
        if patch is None:
            return
        if patch_id in self.macos_selected_patches:
            self.macos_selected_patches.remove(patch_id)
        else:
            self.macos_selected_patches.add(patch_id)
        item.query_one(Label).update(self._macos_patch_label(*patch))
        self._refresh_macos_patch_button()

    def _shortcut_label(self) -> str:
        state = "ON" if self.settings["windows"]["create_desktop_shortcut"] else "OFF"
        return f"Desktop shortcut: {state}"

    def _refresh_shortcut_button(self) -> None:
        self.query_one("#portable-shortcut", Button).label = self._shortcut_label()

    def _focus_windows_tabs(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).focus()

    def _focus_active_content(self) -> None:
        if not self.is_windows:
            self._active_macos_actions()[0].focus()
            return
        if self.query_one(TabbedContent).active == "portable-tab":
            view = self.query_one("#portable-providers", ListView)
            if view.children:
                view.focus()
                return
        self._active_windows_actions()[0].focus()

    def _focus_first_windows_action(self) -> None:
        self._active_windows_actions()[0].focus()

    def _focus_macos_patch_apply(self) -> None:
        self.query_one("#macos-apply-patches", Button).focus()

    @on(TabbedContent.TabActivated)
    def _on_windows_tab_activated(self, _: TabbedContent.TabActivated) -> None:
        self.call_after_refresh(self._focus_windows_tabs)

    def on_key(self, event: events.Key) -> None:
        if not self.is_windows:
            if event.key == "down" and isinstance(self.focused, Tabs):
                self._focus_active_content()
                event.stop()
                return
            if event.key not in {"up", "down", "left", "right"} or not isinstance(
                self.focused, Button
            ):
                return
            buttons = self._active_macos_actions()
            try:
                index = buttons.index(self.focused)
            except ValueError:
                return
            if event.key == "up":
                if index == 0:
                    self._focus_windows_tabs()
                else:
                    buttons[index - 1].focus()
            elif event.key == "down" and index < len(buttons) - 1:
                buttons[index + 1].focus()
            elif event.key == "left" and index > 0:
                buttons[index - 1].focus()
            elif event.key == "right" and index < len(buttons) - 1:
                buttons[index + 1].focus()
            event.stop()
            return
        if event.key == "down" and isinstance(self.focused, Tabs):
            self._focus_active_content()
            event.stop()
            return
        if event.key not in {"up", "down", "left", "right"} or not isinstance(self.focused, Button):
            return
        buttons = self._active_windows_actions()
        try:
            index = buttons.index(self.focused)
        except ValueError:
            return
        if event.key == "up":
            if index == 0:
                if self.query_one(TabbedContent).active == "portable-tab":
                    self.query_one("#portable-providers", ListView).focus()
                else:
                    self._focus_windows_tabs()
            else:
                buttons[index - 1].focus()
        elif event.key == "down" and index < len(buttons) - 1:
            buttons[index + 1].focus()
        elif event.key == "left" and index > 0:
            buttons[index - 1].focus()
        elif event.key == "right" and index < len(buttons) - 1:
            buttons[index + 1].focus()
        event.stop()

    def _write_desktop_config(self, selected: Provider) -> object:
        from .windows_portable import require_shared_codex_home

        enabled = tuple(provider for provider in self.providers if provider.enabled)
        if not enabled:
            raise ConfigError("Enable at least one provider before creating Portable Codex.")
        path = require_shared_codex_home() / "desktop-model-providers.json"
        atomic_write(
            path,
            (
                json.dumps(
                    build_desktop_provider_config(enabled, selected),
                    indent=2,
                )
                + "\n"
            ).encode(),
            mode=0o600,
        )
        return path

    def _progress(self, percent: int, detail: str) -> None:
        self.app.call_from_thread(self._show_progress, percent, detail)

    def _show_progress(self, percent: int, detail: str) -> None:
        self.query_one("#windows-status", Static).update(f"{percent:3d}%  {detail}")
        if self.progress_screen is not None:
            self.progress_screen.update_progress(percent, detail)

    @staticmethod
    def _action_title(action: str) -> str:
        return {
            "official-sync": "Syncing and launching Official Codex",
            "macos-apply-patches": "Applying selected custom patches",
            "portable-create": "Creating Portable Codex",
            "portable-refresh": "Refreshing Portable Codex status",
            "portable-repair": "Repairing Portable Codex patch",
            "portable-sync": "Syncing and launching Portable Codex",
        }[action]

    async def _run_action(self, action: str, reapply_confirmed: bool = False) -> None:
        status = self.query_one("#windows-status", Static)
        selected = (
            self._official_selected_provider()
            if action == "official-sync"
            else None
            if action in {"portable-refresh", "macos-apply-patches"}
            else self._selected_provider("portable-providers")
        )
        if action == "macos-apply-patches" and not self._selected_macos_patches():
            status.update("Select at least one custom patch first.")
            status.add_class("error")
            return
        if action not in {"portable-refresh", "macos-apply-patches"} and selected is None:
            status.update(
                "Apply a provider from the main screen first."
                if action == "official-sync"
                else "Enable and select a provider first."
            )
            status.add_class("error")
            return
        if action == "macos-apply-patches":
            from .desktop_patch import default_target
            from .desktop_patch_macos import find_target_app_processes

            target = default_target("darwin")
            app = target.archive_path.parents[2]
            if not reapply_confirmed:
                try:
                    from .desktop_patch import macos_app_info

                    app_info = await asyncio.to_thread(macos_app_info, target)
                except (ConfigError, OSError):
                    app_info = None
                if app_info is not None and app_info.patch_status.patched:
                    self.app.push_screen(
                        ReapplyConfirmScreen(),
                        lambda confirmed: self._reapply_confirmed(confirmed),
                    )
                    return
            try:
                processes = await asyncio.to_thread(find_target_app_processes, app)
            except PatchError:
                processes = []
            if processes:
                status.update(
                    "ChatGPT is running. Close it manually before applying patches."
                )
                status.add_class("error")
                return
        self.progress_screen = WindowsProgressScreen(self._action_title(action))
        await self.app.push_screen(self.progress_screen)
        try:
            if action == "macos-apply-patches":
                from .desktop_patch import apply_desktop_patch, default_target

                await asyncio.to_thread(
                    apply_desktop_patch,
                    default_target(),
                    Path.home() / ".codex" / "codexier-desktop-backups",
                    self._progress,
                )
                self.progress_screen.finish("Completed. Review the detailed log, then close.")
                return
            from .windows_portable import (
                create_portable_shortcut,
                install_portable,
                refresh_portable,
                repair_portable,
                sync_and_launch_windows,
            )

            self.settings = load_settings(self.catalog_path)
            windows = self.settings["windows"]
            if action == "official-sync":
                assert selected is not None
                windows["mode"] = "official"
                windows["official_provider_id"] = selected.id
                await asyncio.to_thread(
                    sync_and_launch_windows,
                    self.catalog_path,
                    self.providers,
                    selected,
                    self.settings,
                    progress=self._progress,
                )
            elif action == "portable-create":
                assert selected is not None
                windows["mode"] = "portable"
                windows["portable_default_provider_id"] = selected.id
                portable = await asyncio.to_thread(
                    install_portable,
                    None,
                    prepare_config=lambda: self._write_desktop_config(selected),
                    progress=self._progress,
                )
                save_settings(self.catalog_path, self.settings)
                if windows["create_desktop_shortcut"] and portable.executable is not None:
                    await asyncio.to_thread(create_portable_shortcut, portable.executable)
            elif action == "portable-refresh":
                await asyncio.to_thread(refresh_portable, progress=self._progress)
            elif action == "portable-repair":
                assert selected is not None
                await asyncio.to_thread(
                    repair_portable,
                    None,
                    prepare_config=lambda: self._write_desktop_config(selected),
                    progress=self._progress,
                )
            else:
                assert selected is not None
                windows["mode"] = "portable"
                windows["portable_default_provider_id"] = selected.id
                await asyncio.to_thread(
                    sync_and_launch_windows,
                    self.catalog_path,
                    self.providers,
                    selected,
                    self.settings,
                    progress=self._progress,
                )
        except (ConfigError, PatchError, OSError) as exc:
            status.update(str(exc))
            status.add_class("error")
            self.progress_screen.finish(str(exc), error=True)
            return
        status.remove_class("error")
        await self._load_status()
        self.progress_screen.finish("Completed. Review the detailed log, then close.")

    def _reapply_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.run_worker(self._run_action("macos-apply-patches", True), exclusive=True)

    async def _toggle_portable_shortcut(self) -> None:
        status = self.query_one("#windows-status", Static)
        self.settings = load_settings(self.catalog_path)
        windows = self.settings["windows"]
        enabled = not windows["create_desktop_shortcut"]
        if enabled:
            try:
                from .windows_portable import create_portable_shortcut, portable_status

                portable = await asyncio.to_thread(portable_status)
                if portable.executable is not None:
                    await asyncio.to_thread(create_portable_shortcut, portable.executable)
            except (ConfigError, OSError) as exc:
                status.update(str(exc))
                status.add_class("error")
                return
        windows["create_desktop_shortcut"] = enabled
        save_settings(self.catalog_path, self.settings)
        self._refresh_shortcut_button()
        status.remove_class("error")
        status.update(
            "Portable Codex desktop shortcut created."
            if enabled and "portable" in locals() and portable.executable is not None
            else "Portable Codex desktop shortcut will be created after the next portable create or sync."
            if enabled
            else "Desktop shortcut creation disabled. Existing shortcut was left unchanged."
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "back":
            self.dismiss(None)
        elif event.button.id == "backups":
            self.app.push_screen(BackupManagerScreen())
        elif event.button.id == "portable-shortcut":
            self.run_worker(self._toggle_portable_shortcut(), exclusive=True)
        elif event.button.id:
            self.run_worker(self._run_action(event.button.id), exclusive=True)

    def action_cancel(self) -> None:
        self.dismiss(None)


