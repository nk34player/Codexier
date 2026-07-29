from __future__ import annotations

from dataclasses import dataclass

from textual.binding import Binding

from .models import Provider


__all__ = [
    "ProviderManagerResult",
    "_HIDDEN_PROVIDER_MANAGER_BINDINGS",
    "_MACOS_PATCHES",
    "_ProviderManagerShortcutIsolation",
]


@dataclass(frozen=True)
class ProviderManagerResult:
    provider: Provider
    synced_in_tui: bool = False


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
