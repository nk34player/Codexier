from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from .config_manager import detect_config_target, load_target
from .codex_profile import apply_codex_profile
from .errors import CodexierError
from .models import CodexSettings
from .process_manager import detect_codex_processes, restart_codex
from .provider_store import ProviderStore, resolve_provider_path
from .provider_store import create_provider_catalog
from .setup_tui import run_provider_manager
from .ui import render_preview


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select Codex provider and live model catalog.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--yes", action="store_true")
    return parser


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} [y/N] ").strip().lower() in {"y", "yes"}
    except EOFError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
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
                result = apply_codex_profile(provider)
                print(f"Codex profile installed: {result.config_path}")
                print(f"Model catalog installed: {result.catalog_path}")
            else:
                from .backup import atomic_write, backup_config

                backup_config(target.path)
                updated = target.adapter.write_settings(data, settings, mapping)
                atomic_write(target.path, target.adapter.serialize(updated))
                print(f"Configuration updated: {target.path}")

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
