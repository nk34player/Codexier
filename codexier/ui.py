from __future__ import annotations

from rich.console import Console
from rich.table import Table

from .models import CodexSettings, Provider, mask_api_key


def render_preview(provider: Provider, settings: CodexSettings, previous: CodexSettings | None = None) -> None:
    console = Console()
    table = Table(title="Codexier configuration preview")
    table.add_column("Setting")
    table.add_column("New value")
    table.add_row("Provider", provider.name)
    table.add_row("Base URL", settings.base_url)
    table.add_row("API key", mask_api_key(settings.api_key))
    for index, model in enumerate(settings.models, start=1):
        table.add_row(f"Model {index}", model)
    console.print(table)
