from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

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
from .provider_store import add_provider, delete_provider, update_provider
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

_MACOS_PATCHES = (
    (
        "custom-providers-models",
        "Custom Providers + Custom Models",
        "Add Codexier's provider and model selection patch to the installed app.",
    ),
)


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
        Binding("enter", "select_cursor", "Apply provider"),
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


class SettingsListView(ListView):
    """Move from the final setting to the action buttons with Down."""

    def action_cursor_down(self) -> None:
        if self.index is not None and self.index < len(self.children) - 1:
            super().action_cursor_down()
        else:
            self.screen._focus_first_settings_action()  # type: ignore[attr-defined]


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
    #shell { width: 98%; height: 1fr; min-height: 0; margin: 0 1; padding: 1 2; }
    #brand { height: auto; min-height: 5; padding: 1 2; background: #131d38; border: round #3b82f6; color: #8be9fd; }
    #providers { width: 1fr; height: 1fr; min-height: 0; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #providers > ListItem { min-height: 5; padding: 1 3; }
    ListItem { padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
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
                "Toggle the providers you want to sync. Select an enabled provider as the normal Codexier fallback.\n"
                "Press Enter to sync enabled providers; keyboard shortcuts are shown below."
                + (
                    f"\nApplication type: {self.app_settings['windows']['mode'].title()} app"
                    if sys.platform == "win32"
                    else ""
                ),
                id="brand",
            )
            yield ProviderListView(id="providers")
            yield Static("Selected provider: none", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        self._repair_default_provider()
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
        if provider.id == self.applied_id and provider.enabled:
            replacement = next(
                (item for item in self.providers if item.id != provider.id and item.enabled),
                None,
            )
            if replacement is None:
                self.query_one("#status", Static).update(
                    "The default provider must remain enabled until another provider is enabled."
                )
                return
            self.applied_id = replacement.id
        updated = replace(provider, enabled=not provider.enabled)
        self.providers = tuple(
            updated if item.id == updated.id else item for item in self.providers
        )
        self.run_worker(self._render_providers(), exclusive=True)
        state = "enabled" if updated.enabled else "disabled"
        self.query_one("#status", Static).update(
            f"{updated.name} is {state} for this session. Apply provider to save and sync."
        )

    def _repair_default_provider(self) -> None:
        if self.applied_id and any(
            item.id == self.applied_id and item.enabled for item in self.providers
        ):
            return
        self.applied_id = next(
            (item.id for item in self.providers if item.enabled),
            self.providers[0].id if self.providers else None,
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
            ApplyScreen(
                provider,
                self.settings_for(provider),
                self.target_path,
                enabled_providers=tuple(
                    item for item in self.providers if item.enabled
                ),
            ),
            self._apply_finished,
        )

    def _windows_settings_finished(self, changed: bool | None) -> None:
        self.app_settings = load_settings(self.catalog_path)
        if changed:
            self.query_one("#status", Static).update(
                "Application type settings updated. Choose an enabled provider and sync."
            )

    def settings_for(self, provider: Provider) -> CodexSettings:
        return CodexSettings(
            provider.base_url,
            provider.api_key,
            tuple(model.id for model in provider.models),
        )

    def _apply_finished(self, applied: bool | None) -> None:
        if applied:
            for provider in self.providers:
                update_provider(self.catalog_path, provider)
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
    #shell { width: 96%; height: 1fr; min-height: 0; margin: 0 1; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #settings { height: 1fr; min-height: 0; margin-top: 1; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    #settings > ListItem { min-height: 5; padding: 1 3; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    #actions { height: auto; min-height: 4; overflow-x: auto; scrollbar-size: 1 1; }
    Button { width: 1fr; background: #2563eb; color: white; }
    #back { background: #374151; }
    #status { height: 3; color: #8be9fd; }
    """
    BINDINGS = [
        Binding("enter", "select", "Select / save", priority=True),
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
        "application_type": "Chooses official app patching or Codexier's writable Portable app where supported.",
    }

    def __init__(self, catalog_path, providers: tuple[Provider, ...] = ()):
        super().__init__()
        self.catalog_path = catalog_path
        self.providers = providers
        self.settings = load_settings(catalog_path)
        self.index = 0
        self.application_type_state = self.settings["windows"]["mode"]
        self.setting_keys = self.SETTING_KEYS + (("application_type", "Application type"),)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("CODEXIER SETTINGS")
            yield Static(
                "Choose a setting to change its value. Each entry includes a short explanation.\n"
                "↑↓ move · Enter select, toggle, or save · Esc back",
                id="status",
            )
            yield SettingsListView(id="settings")
            with Horizontal(id="actions"):
                yield Button("Save settings  ›", id="save", variant="primary")
                yield Button("Back to main menu", id="back")
        yield Footer()

    def on_mount(self) -> None:
        self._render_settings()
        view = self.query_one("#settings", ListView)
        view.index = 0
        view.focus()
        if sys.platform == "darwin":
            self.run_worker(self._load_application_type_state(), exclusive=True)

    async def _load_application_type_state(self) -> None:
        try:
            from .desktop_patch import default_target, macos_app_info

            app = await asyncio.to_thread(macos_app_info, default_target("darwin"))
            self.application_type_state = "patched" if app.patch_status.patched else "official"
            self._refresh_setting("application_type")
        except (ConfigError, OSError):
            pass

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
        if key == "application_type":
            return self.application_type_state
        return self.settings.get(key)

    @staticmethod
    def _setting_display(key: str, value: object) -> str:
        if key == "input_modalities":
            return "TEXT + IMAGES" if value == ["text", "image"] else "TEXT ONLY"
        if key == "context_profiles":
            maximum, compact = value
            return f"{maximum:,} MAX / {compact:,} COMPACT"
        if key == "application_type":
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
        if key == "application_type":
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

    def _settings_actions(self) -> tuple[Button, Button]:
        return (
            self.query_one("#save", Button),
            self.query_one("#back", Button),
        )

    def _focus_first_settings_action(self) -> None:
        self._settings_actions()[0].focus()

    def _focus_settings_list(self) -> None:
        self.query_one("#settings", ListView).focus()

    def action_select(self) -> None:
        if isinstance(self.focused, ListView):
            self.action_toggle()
        elif isinstance(self.focused, Button):
            if self.focused.id == "save":
                self.action_save()
            elif self.focused.id == "back":
                self.action_cancel()

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
            self.query_one("#status", Static).update(
                "Application type saved. Press Enter to save settings."
            )
        if sys.platform == "darwin":
            self.run_worker(self._load_application_type_state(), exclusive=True)
        else:
            self._refresh_setting("application_type")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down", "left", "right"} or not isinstance(
            self.focused, Button
        ):
            return
        buttons = self._settings_actions()
        index = buttons.index(self.focused)
        if event.key == "up":
            self._focus_settings_list() if index == 0 else buttons[index - 1].focus()
        elif event.key == "down" and index < len(buttons) - 1:
            buttons[index + 1].focus()
        elif event.key == "left" and index > 0:
            buttons[index - 1].focus()
        elif event.key == "right" and index < len(buttons) - 1:
            buttons[index + 1].focus()
        event.stop()

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
        self.query_one("#progress-bar", ProgressBar).update(progress=100)
        status = self.query_one("#progress-status", Static)
        status.update(f"100%  {message}")
        self.query_one("#progress-log", RichLog).write(
            f"[{'red' if error else 'green'}]100%[/] {message}", scroll_end=True
        )
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


class ConfirmationDialog(ModalScreen[bool]):
    """Arrow-key navigation for two-button confirmation dialogs."""

    def on_key(self, event: events.Key) -> None:
        if event.key not in {"up", "down", "left", "right"}:
            return
        buttons = tuple(self.query("Button"))
        if self.focused not in buttons or len(buttons) < 2:
            return
        buttons[1 - buttons.index(self.focused)].focus()
        event.stop()


class RestoreConfirmScreen(ConfirmationDialog):
    CSS = """
    RestoreConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #restore-confirm { width: 76%; height: auto; padding: 1 2; border: round #3b82f6; background: #131d38; }
    #restore-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #2563eb; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="restore-confirm"):
            yield Static("RESTORE APPLICATION BACKUP")
            yield Static(
                f"{self.detail}\n\nThis replaces the current application archive. "
                "The selected backup will not be modified."
            )
            with Horizontal(id="restore-actions"):
                yield Button("Restore backup", id="restore", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "restore")


class DeleteConfirmScreen(ConfirmationDialog):
    CSS = """
    DeleteConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #delete-confirm { width: 76%; height: auto; padding: 1 2; border: round #ef4444; background: #131d38; }
    #delete-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, detail: str):
        super().__init__()
        self.detail = detail

    def compose(self) -> ComposeResult:
        with Vertical(id="delete-confirm"):
            yield Static("DELETE APPLICATION BACKUP")
            yield Static(
                f"{self.detail}\n\nThis permanently deletes the selected backup. "
                "It cannot be restored from Codexier."
            )
            with Horizontal(id="delete-actions"):
                yield Button("Delete permanently", id="delete", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete")


class ForceCloseConfirmScreen(ConfirmationDialog):
    CSS = """
    ForceCloseConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #force-close-confirm { width: 76%; height: auto; padding: 1 2; border: round #ef4444; background: #131d38; }
    #force-close-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def __init__(self, pids: tuple[int, ...]):
        super().__init__()
        self.pids = pids

    def compose(self) -> ComposeResult:
        with Vertical(id="force-close-confirm"):
            yield Static("CHATGPT IS STILL RUNNING")
            yield Static(
                "ChatGPT must be closed before patching. "
                f"Forcefully kill the detected ChatGPT processes (PIDs: {', '.join(map(str, self.pids))}) "
                "and continue applying patches?\n\nUnsaved work may be lost."
            )
            with Horizontal(id="force-close-actions"):
                yield Button("Force kill and apply patches", id="force", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "force")


class ReapplyConfirmScreen(ConfirmationDialog):
    CSS = """
    ReapplyConfirmScreen { align: center middle; background: rgba(0, 0, 0, 0.65); }
    #reapply-confirm { width: 76%; height: auto; padding: 1 2; border: round #f59e0b; background: #131d38; }
    #reapply-actions { height: auto; margin-top: 1; }
    Button { width: 1fr; margin-right: 1; background: #b91c1c; color: white; }
    #cancel { background: #374151; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="reapply-confirm"):
            yield Static("RE-APPLY CUSTOM PATCHES")
            yield Static(
                "This app is already patched. Re-applying requires restoring the "
                "immutable original backup first, then applying the selected patches."
            )
            with Horizontal(id="reapply-actions"):
                yield Button("Restore original and re-apply", id="reapply", variant="error")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "reapply")


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
                self.app.push_screen(
                    ForceCloseConfirmScreen(tuple(pid for pid, _ in processes)),
                    lambda confirmed: self._force_close_confirmed(confirmed),
                )
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

    def _force_close_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.run_worker(self._run_action_with_force_close(), exclusive=True)

    def _reapply_confirmed(self, confirmed: bool | None) -> None:
        if confirmed:
            self.run_worker(self._run_action("macos-apply-patches", True), exclusive=True)

    async def _run_action_with_force_close(self) -> None:
        self.progress_screen = WindowsProgressScreen(self._action_title("macos-apply-patches"))
        await self.app.push_screen(self.progress_screen)
        try:
            from .desktop_patch import apply_desktop_patch, default_target

            await asyncio.to_thread(
                apply_desktop_patch,
                default_target(),
                Path.home() / ".codex" / "codexier-desktop-backups",
                self._progress,
                True,
            )
            self.progress_screen.finish("Completed. Review the detailed log, then close.")
        except (ConfigError, PatchError, OSError) as exc:
            self.query_one("#windows-status", Static).update(str(exc))
            self.query_one("#windows-status", Static).add_class("error")
            self.progress_screen.finish(str(exc), error=True)

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

    def __init__(
        self,
        provider: Provider,
        settings: CodexSettings,
        target_path,
        *,
        enabled_providers: tuple[Provider, ...] = (),
    ):
        super().__init__()
        self.provider = provider
        self.settings = settings
        self.target_path = target_path
        self.enabled_providers = enabled_providers

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
                f"Enabled providers  {', '.join(provider.name for provider in self.enabled_providers)}\n"
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
