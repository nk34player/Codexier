from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from .config_manager import detect_config_target, load_target
from .codex_profile import apply_codex_profiles, launch_command
from .errors import CodexierError
from .models import CodexSettings
from .process_manager import (
    detect_chatgpt_processes,
    detect_codex_processes,
    restart_chatgpt,
    restart_codex,
)
from .provider_store import ProviderStore, resolve_provider_path
from .provider_store import create_provider_catalog
from .setup_tui import run_provider_manager
from .settings import load_settings
from .ui import render_preview
from .desktop_patch import apply_desktop_patch, default_target, restore_desktop_patch


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
                        help="Print the selected default provider's Codex CLI command after syncing.")
    parser.add_argument("--patch-desktop", action="store_true",
                        help="Install the supported desktop provider-picker patch.")
    parser.add_argument("--restore-desktop-patch", type=Path, metavar="BACKUP",
                        help="Restore a desktop patch backup and exit.")
    return parser


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
            providers = ProviderStore(catalog_path).load()
            target = detect_config_target(args.config)
            data, mapping = load_target(target)
            previous = target.adapter.read_settings(data, mapping)
            provider = run_provider_manager(
                catalog_path,
                providers,
                None if args.dry_run else target.path,
            )
            if provider is None:
                return 0
            settings = CodexSettings(
                provider.base_url,
                provider.api_key,
                tuple(model.id for model in provider.models),
            )
            if args.dry_run:
                render_preview(provider, settings, previous)
                return 0

            if target.format == "toml" or target.path.name == "config.toml":
                result = apply_codex_profiles(
                    providers, provider, settings=load_settings(catalog_path)
                )
                print(f"Codex profile installed: {result.config_path}")
                print(f"Model catalog installed: {result.catalog_path}")
                if args.print_command or sys.platform.startswith("linux"):
                    print("Run: " + " ".join(launch_command()))
                if args.patch_desktop:
                    desktop_target = default_target()
                    backup_root = Path.home() / ".codex" / "codexier-desktop-backups"
                    print(apply_desktop_patch(desktop_target, backup_root).message)
            else:
                from .backup import atomic_write, backup_config

                backup_config(target.path)
                updated = target.adapter.write_settings(data, settings, mapping)
                atomic_write(target.path, target.adapter.serialize(updated))
                print(f"Configuration updated: {target.path}")

            if sys.platform == "win32":
                if args.no_restart:
                    print("ChatGPT restart skipped by request.")
                else:
                    # ChatGPT is restarted automatically on Windows only when
                    # it was already running; a closed app remains closed.
                    print(restart_chatgpt(detect_chatgpt_processes()).message)
                if args.restart:
                    print(restart_codex(detect_codex_processes(), force=True).message)
            else:
                should_restart = args.restart
                if not args.no_restart and not args.restart and not args.yes:
                    should_restart = _confirm("Restart Codex now?")
                if should_restart:
                    print(restart_codex(detect_codex_processes(), force=True).message)
                else:
                    print("Restart skipped; restart Codex manually to apply changes.")

            # Keep session alive after apply. User exits from provider manager.
            continue
    except KeyboardInterrupt:
        return 1
    except (CodexierError, OSError) as exc:
        print(f"Error: {exc}")
        return 4
