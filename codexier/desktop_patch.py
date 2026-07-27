"""Desktop-patch entry points.

The macOS implementation is vendored from the user-selected, source-validated
patch project. Windows Store packages are signed MSIX payloads and deliberately
fail closed until a verified Windows-specific source layout exists.
"""

from __future__ import annotations

import os
import platform as platform_module
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConfigError


PATCH_MARKER = b"__codexDesktopModelProvidersPatchV3"


@dataclass(frozen=True)
class DesktopPatchTarget:
    platform: str
    archive_path: Path
    metadata_path: Path | None = None


@dataclass(frozen=True)
class DesktopPatchStatus:
    target: DesktopPatchTarget
    patched: bool
    supported: bool
    message: str


def default_target(platform: str | None = None) -> DesktopPatchTarget:
    system = (platform or platform_module.system()).lower()
    if system == "darwin":
        resources = Path("/Applications/ChatGPT.app/Contents/Resources")
        return DesktopPatchTarget("darwin", resources / "app.asar")
    if system == "windows":
        package_root = _windows_package_root()
        resources = package_root / "app" / "resources"
        return DesktopPatchTarget(
            "windows", resources / "app.asar", resources / "owl-electron-app.json"
        )
    raise ConfigError("Desktop patching is supported only on Windows and macOS.")


def _windows_package_root() -> Path:
    packages = Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "WindowsApps"
    matches = sorted(packages.glob("OpenAI.Codex_*"))
    if matches:
        return matches[-1]
    # The Store package is normally protected; use this only as a clear
    # diagnostic path when package discovery was unavailable.
    return Path(r"C:\Program Files\WindowsApps\OpenAI.Codex")


def patch_status(target: DesktopPatchTarget) -> DesktopPatchStatus:
    try:
        content = target.archive_path.read_bytes()
    except OSError as exc:
        return DesktopPatchStatus(target, False, False, f"Cannot read app archive: {exc}.")
    if PATCH_MARKER in content:
        return DesktopPatchStatus(target, True, True, "Codexier desktop patch is installed.")
    if target.platform == "darwin":
        return DesktopPatchStatus(target, False, True, "macOS app is ready for source validation.")
    return DesktopPatchStatus(
        target, False, False, "Unsupported desktop app version; no changes were made."
    )


def backup_desktop_patch(target: DesktopPatchTarget, backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{target.platform}-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    _copy(target.archive_path, backup / "app.asar")
    if target.metadata_path and target.metadata_path.exists():
        _copy(target.metadata_path, backup / target.metadata_path.name)
    return backup


def apply_desktop_patch(target: DesktopPatchTarget, backup_root: Path) -> DesktopPatchStatus:
    status = patch_status(target)
    if status.patched:
        return status
    if target.platform == "darwin":
        from .desktop_patch_macos import (
            find_target_app_processes,
            patch_app,
            stop_target_app_processes,
        )

        app = target.archive_path.parents[2]
        config = Path.home() / ".codex" / "desktop-model-providers.json"
        if not config.is_file():
            raise ConfigError("Apply a provider before installing the desktop patch.")
        try:
            was_running = bool(find_target_app_processes(app))
            stop_target_app_processes(app, False)
            patch_app(app, config, backup_root, False)
            if was_running:
                subprocess.Popen(["open", "-a", "ChatGPT"])
        except Exception as exc:
            raise ConfigError(str(exc)) from exc
        return patch_status(target)
    if target.platform == "windows":
        raise ConfigError(
            "Windows Microsoft Store packages are MSIX-signed. Refusing to modify "
            "app.asar until a verified Windows patch layout and package-integrity "
            "recovery path exist."
        )
    if not status.supported:
        raise ConfigError(status.message)
    raise ConfigError(status.message)


def restore_desktop_patch(target: DesktopPatchTarget, backup: Path) -> None:
    source = backup / "app.asar"
    if not source.is_file():
        raise ConfigError(f"Backup does not contain app.asar: {backup}")
    _atomic_replace(target.archive_path, source.read_bytes())
    if target.metadata_path:
        metadata = backup / target.metadata_path.name
        if metadata.is_file():
            _atomic_replace(target.metadata_path, metadata.read_bytes())


def _copy(source: Path, destination: Path) -> None:
    shutil.copy2(source, destination)
    if source.read_bytes() != destination.read_bytes():
        raise ConfigError(f"Could not verify desktop backup: {source}")


def _atomic_replace(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".codexier-tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
