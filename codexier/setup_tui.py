from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.css.query import NoMatches
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from .model_client import ModelFetchError, fetch_models
from .models import ModelDefinition, Provider
from .errors import CatalogError, ValidationError
from .provider_store import add_provider, delete_provider, update_provider
from .setup import normalize_base_url, provider_from_live_models
from .tui import widget_id
from .models import CodexSettings, mask_api_key
from .codex_profile import applied_provider_id


class SetupApp(App[bool]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header, Footer { background: #111a33; color: #8be9fd; }
    #shell { width: 90%; height: auto; min-height: 24; margin: 1 5; padding: 2 3; border: round #3b82f6; background: #131d38; }
    #title { color: #8be9fd; text-style: bold; height: 2; }
    #intro { color: #c7d2fe; height: 3; }
    .field-label { color: #8be9fd; margin-top: 1; }
    Input { height: 3; margin: 0 0 1 0; border: round #536d9e; background: #0b1020; color: #ffffff; }
    Input:focus { border: round #50fa7b; background: #111d3b; }
    #save { width: 100%; margin-top: 1; background: #2563eb; color: white; }
    #status { height: 4; color: #8be9fd; padding: 1 0; }
    .error { color: #ff6b8a; }
    .applied { color: #50fa7b; text-style: bold; }
    """

    def __init__(self, catalog_path):
        super().__init__()
        self.catalog_path = catalog_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield Static("FIRST RUN  /  ADD PROVIDER", id="title")
            yield Static("No providers.json found. Add provider, then fetch live models.\nTab moves between fields · Enter submits · Ctrl+C quits", id="intro")
            yield Label("PROVIDER NAME", classes="field-label")
            yield Input(placeholder="Provider name", id="name")
            yield Label("BASE URL", classes="field-label")
            yield Input(placeholder="Base URL (https://.../v1)", id="url")
            yield Label("API KEY", classes="field-label")
            yield Input(placeholder="API key", password=True, id="key")
            yield Button("Fetch live models  ›", id="save", variant="primary")
            yield Static("Enter provider details, then press button or Enter in API-key field.", id="status")
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "name":
            self.query_one("#url", Input).focus()
            return
        if event.input.id == "url":
            self.query_one("#key", Input).focus()
            return
        if event.input.id != "key":
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
        status.update("Fetching live models …")
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
        status.update(f"Saved {provider.name} with {len(provider.models)} live models. Starting …")
        self.exit(True)


def run_setup_tui(catalog_path) -> bool:
    return bool(SetupApp(catalog_path).run())


class ProviderManagerApp(App[Provider | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header, Footer { background: #111a33; color: #8be9fd; }
    #shell { width: 94%; height: 1fr; margin: 1 3; }
    #brand { height: 5; padding: 1 2; background: #131d38; border: round #3b82f6; color: #8be9fd; }
    #providers { width: 1fr; height: 1fr; min-height: 8; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
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
        ("enter", "use", "Use provider"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, catalog_path, providers: tuple[Provider, ...], target_path=None):
        super().__init__()
        self.title = "Codexier"
        self.catalog_path = catalog_path
        self.providers = providers
        self.target_path = target_path
        self.applied_id = applied_provider_id(target_path) if target_path else None
        self.result: Provider | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield ListView(id="providers")
            with Horizontal(id="actions"):
                yield Button("Use selected  ›", id="use", variant="primary")
                yield Button("＋ Add", id="add")
                yield Button("✎ Edit", id="edit")
                yield Button("× Delete", id="delete", variant="error")
                yield Button("Back / Quit", id="back")
            yield Static("Selected provider: none", id="status")
        yield Footer()

    async def on_mount(self) -> None:
        await self._render_providers()
        view = self.query_one("#providers", ListView)
        view.focus()
        if self.providers:
            view.index = 0
            view.scroll_to(0, animate=False)

    async def _render_providers(self) -> None:
        view = self.query_one("#providers", ListView)
        await view.clear()
        for provider in self.providers:
            await view.append(ListItem(self._provider_label(provider), id=widget_id("provider", provider.id)))
        if self.providers:
            view.index = 0
            self._update_selected_status(self.providers[0])

    def _provider_label(self, provider: Provider) -> Label:
        marker = "● APPLIED" if provider.id == self.applied_id else "○"
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
            status.update("Selected provider: none")
        else:
            status.update(f"Selected provider: {provider.name} · {len(provider.models)} saved models")

    def action_add(self) -> None:
        self.push_screen(ProviderFormScreen(self.catalog_path, None), self._form_finished)

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

    def action_use(self) -> None:
        provider = self._selected()
        if provider:
            if self.target_path is None:
                self.exit(provider)
                return
            self.push_screen(
                ApplyScreen(provider, self.settings_for(provider), self.target_path),
                self._apply_finished,
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

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.action_use()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._update_selected_status(self._selected())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {"add": self.action_add, "edit": self.action_edit, "delete": self.action_remove, "use": self.action_use, "back": self.action_quit}
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
        self.query_one("#status", Static).update(f"Saved {provider.name}. Select it and press Enter to continue.")


class ProviderFormScreen(Screen[Provider | None]):
    TITLE = "Codexier"
    BINDINGS = [
        ("enter", "submit", "Fetch models"),
        ("escape", "cancel", "Back"),
    ]

    def __init__(self, catalog_path, provider: Provider | None):
        super().__init__()
        self.catalog_path = catalog_path
        self.existing_provider = provider

    def compose(self) -> ComposeResult:
        yield Static("PROVIDER  /  ADD OR EDIT", id="title")
        yield Static("Enter credentials, then fetch live models.", id="intro")
        yield Label("PROVIDER NAME", classes="field-label")
        yield Input(placeholder="Provider name", id="name")
        yield Label("BASE URL", classes="field-label")
        yield Input(placeholder="Base URL (https://.../v1)", id="url")
        yield Label("API KEY", classes="field-label")
        yield Input(placeholder="API key", password=True, id="key")
        with Horizontal(id="form-actions"):
            yield Button("Fetch live models  ›", id="save", variant="primary")
            yield Button("Back to main menu", id="back")
        yield Static("", id="status")
        yield Footer()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        self._submit_form()

    def on_mount(self) -> None:
        self.query_one("#name", Input).focus()
        provider = self.existing_provider
        if provider:
            self.query_one("#name", Input).value = provider.name
            self.query_one("#url", Input).value = provider.base_url
            self.query_one("#key", Input).value = provider.api_key

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "name":
            self.query_one("#url", Input).focus()
        elif event.input.id == "url":
            self.query_one("#key", Input).focus()
        elif event.input.id == "key":
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
        status.update("Fetching live models …")
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
            ModelPickerScreen(models, name),
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


class ModelPickerScreen(Screen[tuple[str, ...] | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 90%; height: 90%; margin: 2 5; padding: 2 3; border: round #3b82f6; background: #131d38; }
    #models { width: 1fr; height: 1fr; min-height: 8; border: round #263b68; background: #0f1730; overflow-y: scroll; scrollbar-size: 1 1; }
    ListItem { padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    #count { color: #50fa7b; height: 2; }
    #actions { height: 4; }
    #save { width: 1fr; background: #2563eb; color: white; }
    #back { width: 1fr; background: #374151; color: white; }
    """
    BINDINGS = [
        ("space", "toggle", "Toggle model"),
        ("enter", "apply", "Apply to Codex"),
        ("escape", "cancel", "Back"),
    ]

    def __init__(self, models, provider_name: str):
        super().__init__()
        self.models = tuple(models)
        self.provider_name = provider_name
        self.selected: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static(f"LIVE MODELS  /  {self.provider_name}")
            yield Static("LOADING LIVE MODELS …", id="count")
            yield ListView(id="models")
            with Horizontal(id="actions"):
                yield Button("Apply to Codex  ›", id="save", variant="primary")
                yield Button("Back to main menu", id="back")
        yield Footer()

    def on_mount(self) -> None:
        view = self.query_one("#models", ListView)
        view.focus()
        if not self.models:
            view.append(ListItem(Label("No live models returned by provider."), id="no-models"))
            self.query_one("#count", Static).update("NO MODELS AVAILABLE")
            self.query_one("#save", Button).disabled = True
            return
        for model in self.models:
            view.append(ListItem(Label(f"○  {model.label}\n   [dim]{model.id}[/dim]"), id=widget_id("live-model", model.id)))
        view.index = 0
        view.scroll_to(0, animate=False)
        self.query_one("#count", Static).update("SELECTED  0 / 5   (minimum 1)")
        self.query_one("#save", Button).disabled = True

    def action_toggle(self) -> None:
        item = self.query_one("#models", ListView).highlighted_child
        if not item or not item.id:
            return
        model_id = next((model.id for model in self.models if widget_id("live-model", model.id) == item.id), None)
        if model_id is None:
            return
        if model_id in self.selected:
            self.selected.remove(model_id)
        elif len(self.selected) < 5:
            self.selected.append(model_id)
        item.query_one(Label).update(self._label(model_id))
        self.query_one("#count", Static).update(f"SELECTED  {len(self.selected)} / 5   (minimum 1)")
        self.query_one("#save", Button).disabled = not self.selected

    def _label(self, model_id: str) -> str:
        model = next(model for model in self.models if model.id == model_id)
        mark = "●" if model_id in self.selected else "○"
        return f"{mark}  {model.label}\n   [dim]{model.id}[/dim]"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save" and self.selected:
            self.action_apply()
        elif event.button.id == "back":
            self.action_cancel()

    def action_apply(self) -> None:
        if self.selected:
            self.dismiss(tuple(self.selected))
        else:
            self.query_one("#count", Static).update("SELECTED  0 / 5   — choose at least 1 model")

    def action_cancel(self) -> None:
        self.dismiss(None)


def run_provider_manager(
    catalog_path,
    providers: tuple[Provider, ...],
    target_path=None,
) -> Provider | None:
    return ProviderManagerApp(catalog_path, providers, target_path).run()


class ApplyScreen(Screen[bool]):
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    #shell { width: 80%; height: auto; margin: 3 10; padding: 2 3; border: round #3b82f6; background: #131d38; }
    #summary { height: auto; padding: 1 0; color: #c7d2fe; }
    Button { width: 100%; margin-top: 1; background: #2563eb; color: white; }
    #cancel { background: #374151; }
    """
    BINDINGS = [
        ("enter", "apply_profile", "Apply profile"),
        ("escape", "cancel", "Back"),
        ("up", "focus_previous_button", "Previous button"),
        ("down", "focus_next_button", "Next button"),
        ("left", "focus_previous_button", "Previous button"),
        ("right", "focus_next_button", "Next button"),
    ]

    def __init__(self, provider: Provider, settings: CodexSettings, target_path):
        super().__init__()
        self.provider = provider
        self.settings = settings
        self.target_path = target_path

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("APPLY CODEX PROFILE")
            yield Static(
                f"Provider  {self.provider.name}\n"
                f"Base URL  {self.settings.base_url}\n"
                f"API key   {mask_api_key(self.settings.api_key)}\n"
                f"Models    {', '.join(self.settings.models)}\n"
                f"Target    {self.target_path}", id="summary"
            )
            yield Button("Apply configuration  ›", id="apply", variant="primary")
            yield Button("Back to main menu", id="cancel")
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
