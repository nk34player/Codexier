from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config_manager import detect_config_target, load_target
from .codex_profile import (
    apply_codex_profiles,
    legacy_codexier_provider_id,
    launch_command,
)
from .errors import CodexierError, ConfigError
from .models import CodexSettings
from .process_manager import (
    detect_codex_processes,
    restart_codex,
)
from .provider_store import (
    ProviderStore,
    migrate_legacy_enabled_provider,
    resolve_provider_path,
)
from .provider_store import create_provider_catalog
from .setup_tui import ProviderManagerResult, run_provider_manager
from .settings import load_settings
from .ui import render_preview
from .desktop_patch import (
    apply_desktop_patch,
    default_target,
    patch_status,
    restore_desktop_patch,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage OpenAI-compatible providers and sync their models to Codex."
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--print-command", action="store_true",
                        help="Print the normal Codexier CLI command after syncing.")
    parser.add_argument(
        "--patch-desktop",
        action="store_true",
        help=(
            "Install the provider-first desktop picker on macOS. Windows uses "
            "Settings → Official Codex App / Portable App; signed MSIX files "
            "are never modified."
        ),
    )
    parser.add_argument("--restore-desktop-patch", type=Path, metavar="BACKUP",
                        help="Restore a desktop patch backup and exit.")
    return parser


def _desktop_patch_progress(percent: int, detail: str) -> None:
    print(f"[desktop patch {percent:3d}%] {detail}")


def _windows_progress(percent: int, detail: str) -> None:
    print(f"[windows app {percent:3d}%] {detail}")


def _apply_post_sync_desktop_patch() -> None:
    """Patch supported desktop apps without turning a successful sync into failure."""
    backup_root = Path.home() / ".codex" / "codexier-desktop-backups"
    try:
        status = apply_desktop_patch(
            default_target(),
            backup_root,
            progress=_desktop_patch_progress,
        )
        print(status.message)
        if status.skipped:
            print("Provider sync succeeded; the desktop patch was safely skipped.")
    except (ConfigError, OSError) as exc:
        print(
            "Provider sync succeeded, but desktop patching did not complete: "
            f"{exc}. Your Codex configuration and desktop app data were left intact."
        )


def _manage_macos_desktop_app(*, install_patch: bool) -> None:
    """Install only when requested; never close a running desktop app automatically."""
    target = default_target()
    status = patch_status(target)
    if install_patch:
        _apply_post_sync_desktop_patch()
    elif status.upgrade_required:
        print(
            "Desktop patch update available. Close ChatGPT, then rerun with "
            "--patch-desktop."
        )


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.restore_desktop_patch:
            restore_desktop_patch(default_target(), args.restore_desktop_patch)
            print("Desktop patch restored.")
            return 0
        catalog_path = resolve_provider_path(args.providers)
        if not catalog_path.exists():
            create_provider_catalog(catalog_path)
        while True:
            target = detect_config_target(args.config)
            data, mapping = load_target(target)
            previous = target.adapter.read_settings(data, mapping)
            providers = ProviderStore(catalog_path).load()
            migrated = migrate_legacy_enabled_provider(
                catalog_path, legacy_codexier_provider_id(providers, target.path)
            )
            if migrated.enabled_provider_id:
                providers = ProviderStore(catalog_path).load()
            migration_message = (
                "Your previous Codexier fallback was kept enabled; other legacy "
                "providers remain disabled."
                if migrated.enabled_provider_id
                else (
                    "This catalog predates provider toggles. Enable the provider "
                    "you want to sync, then select it."
                    if migrated.had_legacy_entries
                    else None
                )
            )
            app_settings = load_settings(catalog_path)
            applied_id = legacy_codexier_provider_id(providers, target.path)
            if sys.platform == "win32":
                windows = app_settings["windows"]
                applied_id = (
                    windows.get(
                        "official_provider_id"
                        if windows["mode"] == "official"
                        else "portable_default_provider_id"
                    )
                    or applied_id
                )
            provider = run_provider_manager(
                catalog_path,
                providers,
                None if args.dry_run else target.path,
                applied_id=applied_id,
                migration_message=migration_message,
                interactive_sync=not args.dry_run and sys.platform == "darwin",
            )
            if provider is None:
                return 0
            if isinstance(provider, ProviderManagerResult):
                if provider.synced_in_tui:
                    continue
                provider = provider.provider
            # The TUI commits session-only provider toggles when Apply is
            # confirmed. Reload them before building the actual Codex profile.
            providers = ProviderStore(catalog_path).load()
            provider = next(
                (item for item in providers if item.id == provider.id),
                provider,
            )
            settings = CodexSettings(
                provider.base_url,
                provider.api_key,
                tuple(model.id for model in provider.models),
            )
            if args.dry_run:
                render_preview(provider, settings, previous)
                return 0

            if target.format == "toml" or target.path.name == "config.toml":
                if sys.platform == "win32":
                    from .windows_portable import sync_and_launch_windows

                    windows_result = sync_and_launch_windows(
                        catalog_path,
                        providers,
                        provider,
                        app_settings,
                        progress=_windows_progress,
                    )
                    result = windows_result.profile
                    print(windows_result.message)
                else:
                    result = apply_codex_profiles(
                        providers, provider, settings=app_settings
                    )
                print(f"Codex profile installed: {result.config_path}")
                print(f"Model catalog installed: {result.catalog_path}")
                if result.desktop_config_path:
                    print(
                        "Desktop provider configuration installed: "
                        f"{result.desktop_config_path}"
                    )
                if args.print_command or sys.platform.startswith("linux"):
                    print("Run: " + " ".join(launch_command()))
                if sys.platform == "darwin":
                    _manage_macos_desktop_app(install_patch=args.patch_desktop)
                elif args.patch_desktop and sys.platform != "win32":
                    _apply_post_sync_desktop_patch()
            else:
                from .backup import atomic_write, backup_config

                backup_config(target.path)
                updated = target.adapter.write_settings(data, settings, mapping)
                atomic_write(target.path, target.adapter.serialize(updated))
                print(f"Configuration updated: {target.path}")

            if sys.platform == "win32" and (
                target.format == "toml" or target.path.name == "config.toml"
            ):
                pass
            elif sys.platform == "win32":
                if args.restart:
                    print(restart_codex(detect_codex_processes(), force=True).message)
                else:
                    print("Restart Codex manually to apply CLI configuration changes.")
            else:
                should_restart = not args.no_restart
                if should_restart:
                    print(restart_codex(detect_codex_processes(), force=True).message)

            # Keep session alive after apply. User exits from provider manager.
            continue
    except KeyboardInterrupt:
        return 1
    except (CodexierError, OSError) as exc:
        print(f"Error: {exc}")
        return 4
