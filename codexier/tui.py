from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Horizontal, Middle, Vertical
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from .model_client import LiveModel, ModelFetchError, fetch_models
from .models import Provider


def widget_id(prefix: str, value: str) -> str:
    """Encode arbitrary provider/model text into a valid Textual id."""
    # Textual IDs allow only letters, numbers, underscores, and hyphens.
    # Encode punctuation instead of replacing it so ``a.b`` and ``a-b`` do
    # not collide while the original API model ID remains untouched.
    chunks: list[str] = []
    for character in value:
        if re.fullmatch(r"[A-Za-z0-9_-]", character):
            chunks.append(character)
        else:
            chunks.append(f"_u{ord(character):x}_")
    encoded = "".join(chunks).strip("-") or "item"
    if encoded[0].isdigit():
        encoded = f"x-{encoded}"
    return f"{prefix}-{encoded}"


class SelectionState:
    def __init__(self, model_ids: Sequence[str], initial: Sequence[str] = ()):
        self.model_ids = tuple(model_ids)
        known = set(self.model_ids)
        self._selected: list[str] = [model_id for model_id in initial if model_id in known]

    @property
    def selected(self) -> tuple[str, ...]:
        return tuple(self._selected)

    def toggle(self, model_id: str) -> None:
        if model_id in self._selected:
            self._selected.remove(model_id)
            return
        self._selected.append(model_id)

    def confirm(self) -> tuple[str, ...]:
        if not self._selected:
            raise ValueError("Select at least one model.")
        return self.selected


@dataclass(frozen=True)
class TuiResult:
    provider: Provider
    models: tuple[str, ...]


class ProviderApp(App[Provider | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header { background: #111a33; color: #8be9fd; height: 3; }
    Footer { background: #111a33; }
    #shell { width: 94%; height: 90%; margin: 2 3; }
    #brand { height: 5; padding: 1 2; background: #131d38; border: round #3b82f6; color: #8be9fd; }
    #providers { height: 1fr; margin-top: 2; border: round #263b68; background: #0f1730; }
    ListItem { padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    """
    BINDINGS = [Binding("q", "quit", "Quit"), Binding("enter", "choose", "Select")]

    def __init__(self, providers: Sequence[Provider]):
        super().__init__()
        self.providers = tuple(providers)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield Static("CODEXIER  /  PROVIDER SWITCHBOARD", id="brand")
            yield ListView(id="providers")
        yield Footer()

    def on_mount(self) -> None:
        view = self.query_one("#providers", ListView)
        for provider in self.providers:
            view.append(ListItem(Label(f"◆  {provider.name}\n   [dim]{provider.base_url}[/dim]"), id=widget_id("provider", provider.id)))

    def action_choose(self) -> None:
        item = self.query_one("#providers", ListView).highlighted_child
        if item and item.id:
            provider_id = item.id.removeprefix("provider-")
            provider = next((p for p in self.providers if widget_id("provider", p.id).removeprefix("provider-") == provider_id), None)
            if provider:
                self.exit(provider)


def run_provider_tui(providers: Sequence[Provider]) -> Provider | None:
    return ProviderApp(providers).run()


class CodexierApp(App[TuiResult | None]):
    TITLE = "Codexier"
    CSS = """
    Screen { background: #0b1020; color: #e7eefc; }
    Header { background: #111a33; color: #8be9fd; height: 3; }
    Footer { background: #111a33; }
    #shell { width: 94%; height: 90%; margin: 2 3; }
    #brand { height: 5; padding: 1 2; background: #131d38; border: round #3b82f6; color: #8be9fd; }
    #subtitle { color: #9aa9c7; }
    #content { height: 1fr; margin-top: 1; }
    #models { width: 2fr; border: round #263b68; background: #0f1730; }
    #side { width: 1fr; margin-left: 1; padding: 1 2; border: round #263b68; background: #0f1730; }
    ListItem { padding: 1 2; }
    ListItem.--highlight { background: #1d4ed8; color: white; }
    .selected { color: #50fa7b; }
    .muted { color: #8290ad; }
    #error { color: #ff6b8a; height: 3; }
    Button { margin-top: 2; width: 100%; background: #2563eb; color: white; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "toggle_model", "Toggle model"),
        Binding("enter", "apply_models", "Apply profile"),
    ]

    def __init__(self, provider: Provider):
        super().__init__()
        self.provider = provider
        self.live_models: tuple[LiveModel, ...] = ()
        self.state: SelectionState | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="shell"):
            yield Static("CODEXIER  /  MODEL CATALOG", id="brand")
            yield Static(f"[bold]{self.provider.name}[/bold]  ·  {self.provider.base_url}", id="subtitle")
            with Horizontal(id="content"):
                yield ListView(id="models")
                with Vertical(id="side"):
                    yield Label("SELECTED", classes="muted")
                    yield Static("0", id="count")
                    yield Static("Select one or more models.\nModels are fetched live; unavailable providers stop safely.", id="hint", classes="muted")
                    yield Static("", id="error")
                    yield Button("Continue  ›", id="continue", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#continue", Button).disabled = True
        self.run_worker(self._load_models(), exclusive=True)

    async def _load_models(self) -> None:
        try:
            self.live_models = await self._fetch_async()
        except ModelFetchError as exc:
            self.query_one("#error", Static).update(f"LIVE FETCH FAILED\n{exc}")
            return
        self.state = SelectionState(
            [model.id for model in self.live_models],
            initial=self.provider.models and tuple(model.id for model in self.provider.models) or (),
        )
        model_list = self.query_one("#models", ListView)
        for model in self.live_models:
            model_list.append(ListItem(Label(self._label_for(model.id)), id=widget_id("model", model.id)))
        self._refresh_summary()

    async def _fetch_async(self) -> tuple[LiveModel, ...]:
        import asyncio
        return await asyncio.to_thread(fetch_models, self.provider.base_url, self.provider.api_key)

    def action_toggle_model(self) -> None:
        if not self.state or not self.live_models:
            return
        item = self.query_one("#models", ListView).highlighted_child
        if not item or not item.id:
            return
        model_id = next((model.id for model in self.live_models if widget_id("model", model.id) == item.id), None)
        if model_id is None:
            return
        try:
            self.state.toggle(model_id)
        except ValueError as exc:
            self.query_one("#error", Static).update(str(exc))
            return
        self.query_one("#error", Static).update("")
        item.query_one(Label).update(self._label_for(model_id))
        self._refresh_summary()

    def action_apply_models(self) -> None:
        if not self.state:
            return
        try:
            self.exit(TuiResult(self.provider, self.state.confirm()))
        except ValueError as exc:
            self.query_one("#error", Static).update(str(exc))

    def _label_for(self, model_id: str) -> str:
        mark = "●" if model_id in self.state.selected else "○"
        model = next(model for model in self.live_models if model.id == model_id)
        return f"{mark}  {model.label}\n   [dim]{model.id}[/dim]"

    def _refresh_summary(self) -> None:
        selected = self.state.selected if self.state else ()
        self.query_one("#count", Static).update(str(len(selected)))
        self.query_one("#continue", Button).disabled = not selected

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "continue" or not self.state:
            return
        self.action_apply_models()


def run_tui(provider: Provider) -> TuiResult | None:
    return CodexierApp(provider).run()
