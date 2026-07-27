from __future__ import annotations

import asyncio
import json
import sys
import time

from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    RichLog,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

from .backup import atomic_write
from .model_client import ModelFetchError, fetch_models
from .models import ModelDefinition, Provider
from .desktop_patch_macos import PatchError
from .errors import CatalogError, ConfigError, ValidationError
from .provider_store import add_provider, delete_provider, set_provider_enabled, update_provider
from .setup import normalize_base_url, provider_from_live_models
from .tui import widget_id
from .models import CodexSettings, mask_api_key
from .codex_profile import (
    applied_provider_id,
    build_desktop_provider_config,
)
from .settings import (
    DEFAULT_AUTO_COMPACT_TOKEN_LIMIT,
    DEFAULT_CONTEXT_WINDOW,
    MAX_CONTEXT_WINDOW,
    load_settings,
    save_settings,
)


_HIDDEN_PROVIDER_MANAGER_BINDINGS = [
    Binding(key, "ignore_manager_shortcut", show=False)
    for key in ("a", "e", "d", "q", "s", "space")
]


class _ProviderManagerShortcutIsolation:
    def action_ignore_manager_shortcut(self) -> None:
        """Prevent manager shortcuts from leaking into a child screen."""


class ProviderListItem(ListItem):
    class Clicked(Message):
        def __init__(self, item: "ProviderListItem") -> None:
            self.item = item
            super().__init__()

    def _on_click(self, _: events.Click) -> None:
        self.post_message(self.Clicked(self))


class ProviderListView(ListView):
    class Confirmed(Message):
        def __init__(self, list_view: "ProviderListView", item: ListItem) -> None:
            self.list_view = list_view
            self.item = item
            super().__init__()

    BINDINGS = [
        Binding("enter", "select_cursor", "Use provider"),
    ]
    _last_click_item: ListItem | None = None
    _last_click_at = 0.0

    @on(ProviderListItem.Clicked)
    def _on_provider_item_clicked(self, event: ProviderListItem.Clicked) -> None:
        """Highlight on one click; select only on a double-click."""
        event.stop()
        self.focus()
        self.index = self._nodes.index(event.item)
        now = time.monotonic()
        is_double_click = (
            event.item is self._last_click_item
            and now - self._last_click_at <= 0.4
        )
        self._last_click_item = None if is_double_click else event.item
        self._last_click_at = 0.0 if is_double_click else now
        if is_double_click:
            self.post_message(self.Confirmed(self, event.item))

    def action_select_cursor(self) -> None:
        if self.highlighted_child is not None:
            self.post_message(self.Confirmed(self, self.highlighted_child))


class SetupApp(App[bool]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header, Footer { background: #111a33; color: #8be9fd; }
    #shell { width: 96%; height: 1fr; min-height: 24; margin: 1 2; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #title { color: #8be9fd; text-style: bold; height: 2; }
    #intro { color: #c7d2fe; height: auto; min-height: 3; }
    .field-label { color: #8be9fd; margin-top: 1; }
    Input { height: 3; margin: 0 0 1 0; border: round #536d9e; background: #0b1020; color: #ffffff; }
    Input:focus { border: round #50fa7b; background: #111d3b; }
    #save { width: 100%; margin-top: 1; background: #2563eb; color: white; }
    #status { height: 4; color: #8be9fd; padding: 1 0; }
    .error { color: #ff6b8a; }
    .applied { color: #50fa7b; text-style: bold; }
    """
    BINDINGS = [
        Binding("enter", "submit", "Next / Fetch models", priority=True),
        Binding("escape", "quit", "Quit", priority=True),
    ]

    def __init__(self, catalog_path):
        super().__init__()
        self.catalog_path = catalog_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield Static("FIRST RUN  /  CREATE PROVIDER CATALOG", id="title")
            yield Static(
                "Add an OpenAI-compatible provider, then choose from its live /v1/models list.\n"
                "Tab moves between fields · Enter submits · Ctrl+C quits",
                id="intro",
            )
            yield Label("PROVIDER NAME", classes="field-label")
            yield Input(placeholder="Provider name", id="name")
            yield Label("OPENAI-COMPATIBLE BASE URL", classes="field-label")
            yield Input(placeholder="https://api.example.com/v1", id="url")
            yield Label("API KEY", classes="field-label")
            yield Input(placeholder="API key", password=True, id="key")
            yield Button("Fetch available models  ›", id="save", variant="primary")
            yield Static("Enter provider details, then fetch its available models.", id="status")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._advance_or_submit(event.input)

    def action_submit(self) -> None:
        focused = self.focused
        self._advance_or_submit(focused if isinstance(focused, Input) else None)

    def _advance_or_submit(self, input_widget: Input | None) -> None:
        if input_widget is not None and input_widget.id == "name":
            self.query_one("#url", Input).focus()
            return
        if input_widget is not None and input_widget.id == "url":
            self.query_one("#key", Input).focus()
            return
        self._submit_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit_form()

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()

    def _submit_form(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        url = self.query_one("#url", Input).value.strip()
        key = self.query_one("#key", Input).value.strip()
        status = self.query_one("#status", Static)
        if not name or not url or not key:
            status.update("Name, URL, and API key required.")
            status.add_class("error")
            return
        try:
            url = normalize_base_url(url)
        except ValidationError as exc:
            status.update(str(exc))
            status.add_class("error")
            return
        status.update("Fetching available models …")
        self.run_worker(self._fetch_and_save(name, url, key), exclusive=True)

    async def _fetch_and_save(self, name: str, url: str, key: str) -> None:
        status = self.query_one("#status", Static)
        try:
            models = await asyncio.to_thread(fetch_models, url, key)
            provider = provider_from_live_models(name, url, key, models)
            add_provider(self.catalog_path, provider)
        except (ModelFetchError, ValueError, OSError) as exc:
            status.update(f"Setup failed: {exc}")
            status.add_class("error")
            return
        status.update(f"Saved {provider.name} with {len(provider.models)} models. Starting …")
        self.exit(True)


def run_setup_tui(catalog_path) -> bool:
    return bool(SetupApp(catalog_path).run())


class ProviderManagerApp(App[Provider | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header, Footer { background: #111a33; color: #8be9fd; }
    #shell { width: 98%; height: 1fr; margin: 1 1; padding: 1 2; }
    #brand { height: auto; min-height: 5; padding: 1 2; background: #131d38; border: round #3b82f6; color: #8be9fd; }
    #providers { width: 1fr; height: 1fr; min-height: 12; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #providers > ListItem { min-height: 5; padding: 1 3; }
    #actions { height: 4; margin-top: 1; }
    ListItem { padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    Button { margin-right: 1; background: #2563eb; color: white; }
    #status { height: 3; color: #8be9fd; }
    .error { color: #ff6b8a; }
    """
    BINDINGS = [
        ("a", "add", "Add provider"),
        ("e", "edit", "Edit provider"),
        ("d", "remove", "Delete provider"),
        ("space", "toggle_enabled", "Enable / disable"),
        ("q", "quit", "Quit"),
        ("s", "settings", "Settings"),
    ]

    def __init__(
        self,
        catalog_path,
        providers: tuple[Provider, ...],
        target_path=None,
        *,
        applied_id: str | None = None,
        migration_message: str | None = None,
    ):
        super().__init__()
        self.title = "Codexier"
        self.catalog_path = catalog_path
        self.providers = providers
        self.target_path = target_path
        self.applied_id = (
            applied_id
            if applied_id is not None
            else (applied_provider_id(target_path) if target_path else None)
        )
        self.migration_message = migration_message
        self.result: Provider | None = None
        self.app_settings = load_settings(catalog_path)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield Static(
                "CODEXIER  /  PROVIDER CATALOG\n"
                "Toggle the providers you want to sync. Select an enabled provider as the normal Codexier fallback."
                + (
                    f"\nWindows mode: {self.app_settings['windows']['mode'].title()} Codex App"
                    if sys.platform == "win32"
                    else ""
                ),
                id="brand",
            )
            yield ProviderListView(id="providers")
            with Horizontal(id="actions"):
                yield Button("Sync enabled providers  ›", id="use", variant="primary")
                yield Button("Toggle enabled", id="toggle")
                yield Button("＋ Add provider", id="add")
                yield Button("✎ Edit provider", id="edit")
                yield Button("× Delete provider", id="delete", variant="error")
                yield Button("Back / Quit", id="back")
            yield Static("Selected provider: none", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        await self._render_providers()
        view = self.query_one("#providers", ListView)
        view.focus()
        if self.providers:
            index = next(
                (
                    position
                    for position, provider in enumerate(self.providers)
                    if provider.id == self.applied_id
                ),
                0,
            )
            view.index = index
            view.scroll_to(index, animate=False)
            if self.migration_message:
                self.query_one("#status", Static).update(self.migration_message)

    async def _render_providers(self) -> None:
        view = self.query_one("#providers", ListView)
        await view.clear()
        for provider in self.providers:
            await view.append(ProviderListItem(self._provider_label(provider), id=widget_id("provider", provider.id)))
        if self.providers:
            view.index = 0
            self._update_selected_status(self.providers[0])

    def _provider_label(self, provider: Provider) -> Label:
        if provider.id == self.applied_id:
            marker = "● DEFAULT"
        elif provider.enabled:
            marker = "● ENABLED"
        else:
            marker = "○ DISABLED"
        model_names = ", ".join(model.label for model in provider.models) or "no models selected"
        label = Label(
            f"{marker}  ◆  {provider.name}\n"
            f"   [dim]{provider.base_url} · {len(provider.models)} models[/dim]\n"
            f"   [cyan]{model_names}[/cyan]"
        )
        if provider.id == self.applied_id:
            label.add_class("applied")
        return label

    def _selected(self) -> Provider | None:
        try:
            item = self.query_one("#providers", ListView).highlighted_child
        except NoMatches:
            return None
        if not item or not item.id:
            return None
        return next((provider for provider in self.providers if widget_id("provider", provider.id) == item.id), None)

    def _update_selected_status(self, provider: Provider | None) -> None:
        try:
            status = self.query_one("#status", Static)
        except NoMatches:
            # ListView can emit Highlighted while screens are mounting or
            # being dismissed. Ignore transient events until status exists.
            return
        if provider is None:
            status.update("Toggle providers on, then choose an enabled provider as the default.")
        elif not provider.enabled:
            status.update(f"{provider.name} is disabled. Toggle it on before syncing.")
        else:
            status.update(
                f"Codexier fallback after sync: {provider.name} · {len(provider.models)} models · "
                f"{sum(item.enabled for item in self.providers)} providers enabled"
            )

    def action_add(self) -> None:
            self.push_screen(ProviderFormScreen(self.catalog_path, None), self._form_finished)

    def action_settings(self) -> None:
        self.push_screen(
            SettingsScreen(self.catalog_path, self.providers),
            self._settings_finished,
        )

    def _settings_finished(self, changed: bool | None) -> None:
        if changed:
            self.query_one("#status", Static).update(
                "Settings saved. Set a default provider and sync the catalog to update Codex."
            )

    def action_edit(self) -> None:
        provider = self._selected()
        if provider:
            self.push_screen(ProviderFormScreen(self.catalog_path, provider), self._form_finished)

    def action_remove(self) -> None:
        provider = self._selected()
        if not provider:
            return
        try:
            delete_provider(self.catalog_path, provider.id)
        except CatalogError as exc:
            status = self.query_one("#status", Static)
            status.update(str(exc))
            status.add_class("error")
            return
        self.providers = tuple(item for item in self.providers if item.id != provider.id)
        self.run_worker(self._render_providers(), exclusive=True)

    def action_toggle_enabled(self) -> None:
        provider = self._selected()
        if provider is None:
            return
        try:
            updated = set_provider_enabled(
                self.catalog_path, provider.id, not provider.enabled
            )
        except CatalogError as exc:
            status = self.query_one("#status", Static)
            status.update(str(exc))
            status.add_class("error")
            return
        self.providers = tuple(
            updated if item.id == updated.id else item for item in self.providers
        )
        self.run_worker(self._render_providers(), exclusive=True)
        state = "enabled" if updated.enabled else "disabled"
        self.query_one("#status", Static).update(
            f"{updated.name} is {state}. Sync exports enabled providers only."
        )

    def action_use(self) -> None:
        provider = self._selected()
        if provider is None:
            return
        if not provider.enabled:
            status = self.query_one("#status", Static)
            status.update(f"{provider.name} is disabled. Toggle it on before syncing.")
            status.add_class("error")
            return
        if not any(item.enabled for item in self.providers):
            status = self.query_one("#status", Static)
            status.update("Enable at least one provider before syncing.")
            status.add_class("error")
            return
        if sys.platform == "win32":
            mode = self.app_settings["windows"]["mode"]
            if mode == "portable":
                from .windows_portable import portable_status

                try:
                    installed = portable_status().installed
                except ConfigError:
                    installed = False
                if not installed:
                    self.push_screen(
                        WindowsDesktopScreen(
                            self.catalog_path,
                            self.providers,
                            initial_tab="portable",
                        ),
                        self._windows_settings_finished,
                    )
                    return
        if self.target_path is None:
            self.exit(provider)
            return
        self.push_screen(
            ApplyScreen(provider, self.settings_for(provider), self.target_path),
            self._apply_finished,
        )

    def _windows_settings_finished(self, changed: bool | None) -> None:
        self.app_settings = load_settings(self.catalog_path)
        if changed:
            self.query_one("#status", Static).update(
                "Windows desktop settings updated. Choose an enabled provider and sync."
            )

    def settings_for(self, provider: Provider) -> CodexSettings:
        return CodexSettings(
            provider.base_url,
            provider.api_key,
            tuple(model.id for model in provider.models),
        )

    def _apply_finished(self, applied: bool | None) -> None:
        if applied:
            provider = self._selected()
            if provider:
                self.exit(provider)

    @on(ProviderListView.Confirmed)
    def on_provider_confirmed(self, event: ProviderListView.Confirmed) -> None:
        self.action_use()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._update_selected_status(self._selected())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "add": self.action_add,
            "edit": self.action_edit,
            "delete": self.action_remove,
            "toggle": self.action_toggle_enabled,
            "use": self.action_use,
            "back": self.action_quit,
        }
        action = actions.get(event.button.id)
        if action:
            action()

    def action_quit(self) -> None:
        self.exit(None)

    def _form_finished(self, provider: Provider | None) -> None:
        if provider is None:
            return
        self.providers = tuple(provider if item.id == provider.id else item for item in self.providers)
        if not any(item.id == provider.id for item in self.providers):
            self.providers += (provider,)
        self.run_worker(self._render_providers(), exclusive=True)
        self.query_one("#status", Static).update(
            f"Saved {provider.name}. Toggle it on when you want to sync it."
        )


class SettingsScreen(_ProviderManagerShortcutIsolation, Screen[bool | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 96%; height: 1fr; margin: 1 2; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #settings { height: 1fr; min-height: 10; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #settings > ListItem { min-height: 5; padding: 1 3; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    #actions { height: 4; }
    Button { width: 1fr; background: #2563eb; color: white; }
    #back { background: #374151; }
    #status { height: 3; color: #8be9fd; }
    """
    BINDINGS = [
        ("space", "toggle", "Toggle / edit setting"),
        Binding("enter", "save", "Save settings", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    SETTING_KEYS = (
        ("supports_parallel_tool_calls", "Parallel tool calls"),
        ("support_verbosity", "Response verbosity"),
        ("supports_search_tool", "Search tool"),
        ("web_search_tool_type", "Web search type"),
        ("input_modalities", "Input modalities"),
        ("context_profiles", "Context profiles"),
    )
    SETTING_DESCRIPTIONS = {
        "supports_parallel_tool_calls": "Allows Codex to issue multiple independent tool calls together.",
        "support_verbosity": "Controls whether the provider receives Codex's response-verbosity preference.",
        "supports_search_tool": "Enables search-related tool support for generated Codex models.",
        "web_search_tool_type": "Selects the configured web-search mode sent to Codex.",
        "input_modalities": "Controls whether models accept text only or text plus images.",
        "context_profiles": "Configures maximum context tokens and when automatic compacting begins.",
        "windows_desktop": "Chooses the signed Official app or Codexier's writable Portable app.",
    }

    def __init__(self, catalog_path, providers: tuple[Provider, ...] = ()):
        super().__init__()
        self.catalog_path = catalog_path
        self.providers = providers
        self.settings = load_settings(catalog_path)
        self.index = 0
        self.setting_keys = self.SETTING_KEYS + (
            (("windows_desktop", "Windows desktop apps"),)
            if sys.platform == "win32"
            else ()
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("CODEXIER SETTINGS")
            yield Static(
                "Choose a setting to change its value. Each entry includes a short explanation.\n"
                "↑↓ move · Space toggle or edit · Enter save · Esc back",
                id="status",
            )
            yield ListView(id="settings")
            with Horizontal(id="actions"):
                yield Button("Save settings  ›", id="save", variant="primary")
                yield Button("Back to main menu", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._render_settings()
        self.query_one("#settings", ListView).focus()

    def _render_settings(self) -> None:
        view = self.query_one("#settings", ListView)
        for key, label in self.setting_keys:
            value = self._setting_value(key)
            display = self._setting_display(key, value)
            explanation = self.SETTING_DESCRIPTIONS[key]
            view.append(
                ListItem(
                    Label(f"{'●' if value else '○'}  {label}: {display}\n   [dim]{explanation}[/dim]"),
                    id=widget_id("setting", key),
                )
            )

    def _refresh_setting(self, key: str) -> None:
        label = next(label for setting_key, label in self.setting_keys if setting_key == key)
        value = self._setting_value(key)
        display = self._setting_display(key, value)
        explanation = self.SETTING_DESCRIPTIONS[key]
        item = self.query_one(f"#{widget_id('setting', key)}", ListItem)
        item.query_one(Label).update(f"{'●' if value else '○'}  {label}: {display}\n   [dim]{explanation}[/dim]")

    def _setting_value(self, key: str) -> object:
        if key == "context_profiles":
            return (
                self.settings["context_window"],
                self.settings["auto_compact_token_limit"],
            )
        if key == "windows_desktop":
            return self.settings["windows"]["mode"]
        return self.settings.get(key)

    @staticmethod
    def _setting_display(key: str, value: object) -> str:
        if key == "input_modalities":
            return "TEXT + IMAGES" if value == ["text", "image"] else "TEXT ONLY"
        if key == "context_profiles":
            maximum, compact = value
            return f"{maximum:,} MAX / {compact:,} COMPACT"
        if key == "windows_desktop":
            return f"{str(value).upper()} APP"
        return "OFF" if value in (False, None, "") else str(value).upper()

    def _selected_key(self) -> str:
        item = self.query_one("#settings", ListView).highlighted_child
        if item and item.id:
            return next((key for key, _ in self.setting_keys if widget_id("setting", key) == item.id), self.setting_keys[0][0])
        return self.setting_keys[0][0]

    def action_toggle(self) -> None:
        key = self._selected_key()
        if key == "context_profiles":
            self.app.push_screen(ContextProfilesScreen(self.catalog_path), self._context_profiles_finished)
            return
        if key == "windows_desktop":
            self.app.push_screen(
                WindowsDesktopScreen(self.catalog_path, self.providers),
                self._windows_desktop_finished,
            )
            return
        if key == "web_search_tool_type":
            self.settings[key] = None if self.settings.get(key) else "text"
        elif key == "input_modalities":
            self.settings[key] = ["text"] if self.settings.get(key) == ["text", "image"] else ["text", "image"]
        else:
            self.settings[key] = not bool(self.settings.get(key, False))
        self._refresh_setting(key)

    def action_save(self) -> None:
        save_settings(self.catalog_path, self.settings)
        self.dismiss(True)

    def _context_profiles_finished(self, changed: bool | None) -> None:
        if changed:
            self.settings = load_settings(self.catalog_path)
            self._refresh_setting("context_profiles")
            self.query_one("#status", Static).update("Context profile saved. Press Enter to save settings.")

    def _windows_desktop_finished(self, changed: bool | None) -> None:
        if changed:
            self.settings = load_settings(self.catalog_path)
            self._refresh_setting("windows_desktop")
            self.query_one("#status", Static).update(
                "Windows desktop mode saved. Press Enter to save settings."
            )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "back":
            self.action_cancel()


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


class WindowsProgressScreen(ModalScreen[None]):
    """Uncancellable progress view; the close button appears only after work ends."""

    CSS = """
    WindowsProgressScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #progress-shell { width: 88%; height: 80%; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #progress-title { color: #8be9fd; text-style: bold; }
    #progress-status { height: 2; color: #c7d2fe; }
    #progress-log { height: 1fr; margin-top: 1; border: round #263b68; background: #080d19; }
    #progress-close { display: none; margin-top: 1; background: #374151; color: white; }
    .error { color: #ff6b8a; }
    """

    def __init__(self, title: str):
        super().__init__()
        self.title = title
        self.finished = False

    def compose(self) -> ComposeResult:
        with Vertical(id="progress-shell"):
            yield Static(self.title, id="progress-title")
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Static("Starting …", id="progress-status")
            yield RichLog(id="progress-log", wrap=True, markup=True)
            yield Button("Close", id="progress-close")

    def on_key(self, event: events.Key) -> None:
        if not self.finished:
            event.stop()

    def update_progress(self, percent: int, detail: str) -> None:
        self.query_one("#progress-bar", ProgressBar).update(progress=percent)
        self.query_one("#progress-status", Static).update(f"{percent:3d}%  {detail}")
        self.query_one("#progress-log", RichLog).write(
            f"[cyan]{percent:3d}%[/cyan] {detail}", scroll_end=True
        )

    def finish(self, message: str, *, error: bool = False) -> None:
        self.finished = True
        status = self.query_one("#progress-status", Static)
        status.update(message)
        if error:
            status.add_class("error")
            self.query_one("#progress-log", RichLog).write(
                f"[red]ERROR[/red] {message}", scroll_end=True
            )
        self.query_one("#progress-close", Button).display = True
        self.query_one("#progress-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "progress-close" and self.finished:
            self.dismiss(None)


class WindowsDesktopScreen(_ProviderManagerShortcutIsolation, Screen[bool | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 98%; height: 1fr; margin: 1; padding: 1 2; border: round #3b82f6; background: #131d38; }
    TabbedContent { height: 1fr; }
    TabPane { padding: 1 2; }
    .official-layout { height: 1fr; min-height: 10; }
    .official-info { width: 2fr; padding: 1 2; border: round #263b68; background: #0f1730; }
    .official-selection { width: 1fr; margin-left: 1; padding: 1 2; border: round #3b82f6; background: #101b36; }
    .section-title { color: #8be9fd; text-style: bold; }
    .provider-list { height: 1fr; min-height: 8; border: round #263b68; background: #0f1730; }
    .provider-list > ListItem { min-height: 3; padding: 1 2; }
    .actions { height: auto; min-height: 4; margin-top: 1; }
    Button { margin-right: 1; background: #2563eb; color: white; }
    #back { background: #374151; }
    #windows-status { height: auto; min-height: 4; color: #8be9fd; padding: 1 0; }
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
    ):
        super().__init__()
        self.catalog_path = catalog_path
        self.providers = providers
        self.settings = load_settings(catalog_path)
        mode = initial_tab or self.settings["windows"]["mode"]
        self.initial_tab = f"{mode}-tab"
        self.progress_screen: WindowsProgressScreen | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("WINDOWS DESKTOP APPS  /  SHARED LOWERCASE codexier PROFILE")
            with TabbedContent(initial=self.initial_tab):
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
            yield Static("Loading Windows desktop status …", id="windows-status")
            with Horizontal(classes="actions"):
                yield Button("Back to settings", id="back")
        yield Footer()

    async def on_mount(self) -> None:
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
            ids = ("portable-create", "portable-refresh", "portable-repair", "portable-sync", "back")
        return tuple(self.query_one(f"#{button_id}", Button) for button_id in ids)

    def _focus_windows_tabs(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).focus()

    def _focus_active_content(self) -> None:
        if self.query_one(TabbedContent).active == "portable-tab":
            view = self.query_one("#portable-providers", ListView)
            if view.children:
                view.focus()
                return
        self._active_windows_actions()[0].focus()

    def _focus_first_windows_action(self) -> None:
        self._active_windows_actions()[0].focus()

    @on(TabbedContent.TabActivated)
    def _on_windows_tab_activated(self, _: TabbedContent.TabActivated) -> None:
        self.call_after_refresh(self._focus_windows_tabs)

    def on_key(self, event: events.Key) -> None:
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
            "portable-create": "Creating Portable Codex",
            "portable-refresh": "Refreshing Portable Codex status",
            "portable-repair": "Repairing Portable Codex patch",
            "portable-sync": "Syncing and launching Portable Codex",
        }[action]

    async def _run_action(self, action: str) -> None:
        status = self.query_one("#windows-status", Static)
        selected = (
            self._official_selected_provider()
            if action == "official-sync"
            else None
            if action == "portable-refresh"
            else self._selected_provider("portable-providers")
        )
        if action != "portable-refresh" and selected is None:
            status.update(
                "Apply a provider from the main screen first."
                if action == "official-sync"
                else "Enable and select a provider first."
            )
            status.add_class("error")
            return
        self.progress_screen = WindowsProgressScreen(self._action_title(action))
        await self.app.push_screen(self.progress_screen)
        try:
            from .windows_portable import (
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
                await asyncio.to_thread(
                    install_portable,
                    None,
                    prepare_config=lambda: self._write_desktop_config(selected),
                    progress=self._progress,
                )
                save_settings(self.catalog_path, self.settings)
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            self.dismiss(True)
        elif event.button.id:
            self.run_worker(self._run_action(event.button.id), exclusive=True)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ContextProfilesScreen(_ProviderManagerShortcutIsolation, Screen[bool | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 94%; height: auto; max-height: 1fr; margin: 2 3; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #intro { color: #c7d2fe; height: auto; min-height: 3; }
    .field-label { color: #8be9fd; margin-top: 1; }
    Input { height: 3; margin: 0 0 1 0; border: round #536d9e; background: #0b1020; color: #ffffff; }
    Input:focus { border: round #50fa7b; background: #111d3b; }
    #actions { height: 4; margin-top: 1; }
    Button { width: 1fr; background: #2563eb; color: white; }
    #back { background: #374151; }
    #status { height: 4; color: #8be9fd; padding: 1 0; }
    .error { color: #ff6b8a; }
    """
    BINDINGS = [
        Binding("enter", "save", "Save profile", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(self, catalog_path):
        super().__init__()
        self.catalog_path = catalog_path
        self.settings = load_settings(catalog_path)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("CONTEXT PROFILES")
            yield Static(
                "Set token limits used in every generated Codex model catalog.\n"
                f"Maximum context cannot exceed {MAX_CONTEXT_WINDOW:,} tokens. Compacting must be lower.",
                id="intro",
            )
            yield Label("MAXIMUM CONTEXT TOKENS", classes="field-label")
            yield Input(placeholder="250000", id="context-window", type="integer")
            yield Label("COMPACT AFTER TOKENS", classes="field-label")
            yield Input(placeholder="70000", id="compact-limit", type="integer")
            with Horizontal(id="actions"):
                yield Button("Save profile  ›", id="save", variant="primary")
                yield Button("Back", id="back")
            yield Static("Enter saves · Esc goes back", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#context-window", Input).value = str(self.settings["context_window"])
        self.query_one("#compact-limit", Input).value = str(self.settings["auto_compact_token_limit"])
        self.query_one("#context-window", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "context-window":
            self.query_one("#compact-limit", Input).focus()
        else:
            self.action_save()

    def action_save(self) -> None:
        status = self.query_one("#status", Static)
        try:
            context_window = int(self.query_one("#context-window", Input).value.strip())
            compact_limit = int(self.query_one("#compact-limit", Input).value.strip())
            updated = dict(self.settings)
            updated["context_window"] = context_window
            updated["auto_compact_token_limit"] = compact_limit
            save_settings(self.catalog_path, updated)
        except (ValueError, ConfigError, OSError) as exc:
            status.update(str(exc) or "Enter valid whole-number token limits.")
            status.add_class("error")
            return
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "back":
            self.action_cancel()


class ProviderFormScreen(_ProviderManagerShortcutIsolation, Screen[Provider | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 94%; height: auto; margin: 2 3; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #intro { color: #c7d2fe; height: auto; min-height: 3; }
    .field-label { color: #8be9fd; margin-top: 1; }
    Input { height: 3; margin: 0 0 1 0; border: round #536d9e; background: #0b1020; color: #ffffff; }
    Input:focus { border: round #50fa7b; background: #111d3b; }
    #form-actions { height: 4; margin-top: 1; }
    Button { width: 1fr; background: #2563eb; color: white; }
    #back { background: #374151; }
    #status { height: 4; color: #8be9fd; padding: 1 0; }
    .error { color: #ff6b8a; }
    """
    BINDINGS = [
        Binding("enter", "submit", "Next / Fetch models", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(self, catalog_path, provider: Provider | None):
        super().__init__()
        self.catalog_path = catalog_path
        self.existing_provider = provider

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("OPENAI-COMPATIBLE PROVIDER  /  ADD OR EDIT", id="title")
            yield Static(
                "Enter an OpenAI-compatible API base URL and key. Codexier fetches /v1/models "
                "before you choose which models to save.",
                id="intro",
            )
            yield Label("PROVIDER NAME", classes="field-label")
            yield Input(placeholder="Provider name", id="name")
            yield Label("OPENAI-COMPATIBLE BASE URL", classes="field-label")
            yield Input(placeholder="https://api.example.com/v1", id="url")
            yield Label("API KEY", classes="field-label")
            yield Input(placeholder="API key", password=True, id="key")
            with Horizontal(id="form-actions"):
                yield Button("Fetch available models  ›", id="save", variant="primary")
                yield Button("Back to main menu", id="back")
            yield Static("Enter advances fields · Esc returns to the provider catalog", id="status")
        yield Footer()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        focused = self.focused
        self._advance_or_submit(focused if isinstance(focused, Input) else None)

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()
        provider = self.existing_provider
        if provider:
            self.query_one("#name", Input).value = provider.name
            self.query_one("#url", Input).value = provider.base_url
            self.query_one("#key", Input).value = provider.api_key

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._advance_or_submit(event.input)

    def _advance_or_submit(self, input_widget: Input | None) -> None:
        if input_widget is not None and input_widget.id == "name":
            self.query_one("#url", Input).focus()
        elif input_widget is not None and input_widget.id == "url":
            self.query_one("#key", Input).focus()
        else:
            self._submit_form()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit_form()
        elif event.button.id == "back":
            self.action_cancel()

    def _submit_form(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        url = self.query_one("#url", Input).value.strip()
        key = self.query_one("#key", Input).value.strip()
        status = self.query_one("#status", Static)
        if not name or not url or not key:
            status.update("Name, URL, and API key required.")
            status.add_class("error")
            return
        try:
            url = normalize_base_url(url)
        except ValidationError as exc:
            status.update(str(exc))
            status.add_class("error")
            return
        status.update("Fetching available models …")
        self.run_worker(self._fetch_and_save(name, url, key), exclusive=True)

    async def _fetch_and_save(self, name: str, url: str, key: str) -> None:
        status = self.query_one("#status", Static)
        try:
            models = await asyncio.to_thread(fetch_models, url, key)
        except (ModelFetchError, ValueError, OSError) as exc:
            status.update(f"Save failed: {exc}")
            status.add_class("error")
            return
        # Screen instances do not expose push_screen; navigation belongs to
        # owning Textual app. Using app keeps worker completion inside same
        # provider-manager TUI instead of crashing after live fetch.
        self.app.push_screen(
            ModelPickerScreen(
                models,
                name,
                selected_ids=(
                    tuple(model.id for model in self.existing_provider.models)
                    if self.existing_provider
                    else ()
                ),
            ),
            lambda selected: self._save_selected(name, url, key, models, selected),
        )

    def _save_selected(self, name, url, key, models, selected) -> None:
        if not selected:
            return
        base = provider_from_live_models(name, url, key, models)
        provider = Provider(
            self.existing_provider.id if self.existing_provider else base.id,
            base.name,
            base.base_url,
            base.api_key,
            tuple(ModelDefinition(model.id, model.label) for model in models if model.id in selected),
            {},
            self.existing_provider.enabled if self.existing_provider else False,
        )
        try:
            if self.existing_provider:
                update_provider(self.catalog_path, provider)
            else:
                add_provider(self.catalog_path, provider)
        except (CatalogError, ValidationError, ValueError, OSError) as exc:
            status = self.query_one("#status", Static)
            status.update(f"Save failed: {exc}")
            status.add_class("error")
            return
        self.dismiss(provider)


class ModelPickerScreen(_ProviderManagerShortcutIsolation, Screen[tuple[str, ...] | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 96%; height: 1fr; margin: 1 2; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #intro { color: #c7d2fe; height: auto; min-height: 3; }
    #models { width: 1fr; height: 1fr; min-height: 12; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #models > ListItem { min-height: 4; padding: 1 3; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    #count { color: #50fa7b; height: 2; }
    #actions { height: 4; }
    #save { width: 1fr; background: #2563eb; color: white; }
    #back { width: 1fr; background: #374151; color: white; }
    """
    BINDINGS = [
        ("space", "toggle", "Toggle model"),
        Binding("enter", "save", "Save selected models", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(self, models, provider_name: str, selected_ids=()):
        super().__init__()
        self.models = tuple(models)
        self.provider_name = provider_name
        live_ids = {model.id for model in self.models}
        self.selected = [model_id for model_id in selected_ids if model_id in live_ids]

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(f"AVAILABLE MODELS  /  {self.provider_name}")
            yield Static(
                "Any model returned by this OpenAI-compatible API can be saved. "
                "Select one or more models; there is no selection limit.",
                id="intro",
            )
            yield Static("LOADING AVAILABLE MODELS …", id="count")
            yield ListView(id="models")
            with Horizontal(id="actions"):
                yield Button("Save selected models  ›", id="save", variant="primary")
                yield Button("Back to provider catalog", id="back")
        yield Footer()

    def on_mount(self) -> None:
        view = self.query_one("#models", ListView)
        view.focus()
        if not self.models:
            view.append(ListItem(Label("No models returned by this provider."), id="no-models"))
            self.query_one("#count", Static).update("NO AVAILABLE MODELS")
            self.query_one("#save", Button).disabled = True
            return
        for model in self.models:
            view.append(ListItem(Label(self._label(model.id)), id=widget_id("live-model", model.id)))
        view.index = 0
        view.scroll_to(0, animate=False)
        self.query_one("#count", Static).update(
            f"SELECTED  {len(self.selected)}   (minimum 1)"
        )
        self.query_one("#save", Button).disabled = not self.selected

    def action_toggle(self) -> None:
        item = self.query_one("#models", ListView).highlighted_child
        if not item or not item.id:
            return
        model_id = next((model.id for model in self.models if widget_id("live-model", model.id) == item.id), None)
        if model_id is None:
            return
        if model_id in self.selected:
            self.selected.remove(model_id)
        else:
            self.selected.append(model_id)
        item.query_one(Label).update(self._label(model_id))
        self.query_one("#count", Static).update(f"SELECTED  {len(self.selected)}   (minimum 1)")
        self.query_one("#save", Button).disabled = not self.selected

    def _label(self, model_id: str) -> str:
        model = next(model for model in self.models if model.id == model_id)
        mark = "●" if model_id in self.selected else "○"
        return f"{mark}  {model.label}\n   [dim]{model.id}[/dim]"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save" and self.selected:
            self.action_save()
        elif event.button.id == "back":
            self.action_cancel()

    def action_save(self) -> None:
        if self.selected:
            self.dismiss(tuple(self.selected))
        else:
            self.query_one("#count", Static).update("SELECTED  0   (minimum 1)")

    def action_cancel(self) -> None:
        self.dismiss(None)


def run_provider_manager(
    catalog_path,
    providers: tuple[Provider, ...],
    target_path=None,
    *,
    applied_id: str | None = None,
    migration_message: str | None = None,
) -> Provider | None:
    return ProviderManagerApp(
        catalog_path,
        providers,
        target_path,
        applied_id=applied_id,
        migration_message=migration_message,
    ).run()


class ApplyScreen(_ProviderManagerShortcutIsolation, Screen[bool]):
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 96%; height: auto; max-height: 1fr; margin: 2 2; padding: 2 4; border: round #3b82f6; background: #131d38; }
    #intro { color: #c7d2fe; height: auto; min-height: 3; }
    #summary { height: auto; padding: 1 0; color: #c7d2fe; }
    Button { width: 100%; margin-top: 1; background: #2563eb; color: white; }
    #cancel { background: #374151; }
    """
    BINDINGS = [
        Binding("enter", "activate", "Select", priority=True),
        Binding("escape", "cancel", "Back", priority=True),
        ("up", "focus_previous_button", "Previous button"),
        ("down", "focus_next_button", "Next button"),
        ("left", "focus_previous_button", "Previous button"),
        ("right", "focus_next_button", "Next button"),
        *_HIDDEN_PROVIDER_MANAGER_BINDINGS,
    ]

    def __init__(self, provider: Provider, settings: CodexSettings, target_path):
        super().__init__()
        self.provider = provider
        self.settings = settings
        self.target_path = target_path

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("SYNC ENABLED CODEXIER PROVIDERS")
            yield Static(
                "Sync enabled OpenAI-compatible providers and their selected models. "
                "Codexier keeps one normal profile; the selected provider is its fallback.",
                id="intro",
            )
            yield Static(
                f"Codexier fallback  {self.provider.name}\n"
                f"Base URL  {self.settings.base_url}\n"
                f"API key   {mask_api_key(self.settings.api_key)}\n"
                f"Fallback models    {', '.join(self.settings.models)}\n"
                "Sync scope        Enabled providers and their selected models\n"
                f"Target    {self.target_path}", id="summary"
            )
            yield Button("Sync enabled providers  ›", id="apply", variant="primary")
            yield Button("Back to provider catalog", id="cancel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#apply", Button).focus()

    def action_focus_previous_button(self) -> None:
        self._focus_button(-1)

    def action_focus_next_button(self) -> None:
        self._focus_button(1)

    def _focus_button(self, direction: int) -> None:
        buttons = list(self.query(Button))
        if not buttons:
            return
        try:
            index = buttons.index(self.focused)
        except ValueError:
            index = 0 if direction > 0 else len(buttons) - 1
        buttons[(index + direction) % len(buttons)].focus()

    def action_apply_profile(self) -> None:
        self.dismiss(True)

    def action_activate(self) -> None:
        focused = self.focused
        if isinstance(focused, Button) and focused.id == "cancel":
            self.action_cancel()
        else:
            self.action_apply_profile()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self.action_apply_profile()
        elif event.button.id == "cancel":
            self.action_cancel()


def run_apply_tui(provider: Provider, settings: CodexSettings, target_path) -> bool:
    """Deprecated compatibility helper; normal flow uses nested ApplyScreen."""
    return False
