"""Desktop-patch entry points.

The macOS implementation is source-validated. Windows discovery, backup, and
restore work with native paths, while mutation deliberately fails closed until
a verified Windows Electron adapter exists. Microsoft Store/MSIX packages are
never modified because their signatures cover the package payload.
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
    package_type: str = "unknown"


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
        return _windows_default_target()
    raise ConfigError("Desktop patching is supported only on Windows and macOS.")


def _windows_default_target() -> DesktopPatchTarget:
    override = os.environ.get("CODEXIER_WINDOWS_APP_ASAR")
    if override:
        archive = Path(override).expanduser()
        return DesktopPatchTarget(
            "windows",
            archive,
            _windows_metadata_path(archive),
            "unpackaged",
        )
    for archive in _windows_unpacked_archives():
        if archive.is_file():
            return DesktopPatchTarget(
                "windows",
                archive,
                _windows_metadata_path(archive),
                "unpackaged",
            )
    package_root = _windows_package_root()
    resources = package_root / "app" / "resources"
    archive = resources / "app.asar"
    return DesktopPatchTarget(
        "windows",
        archive,
        _windows_metadata_path(archive),
        "msix",
    )


def _windows_unpacked_archives() -> tuple[Path, ...]:
    """Return common unpackaged Electron install paths without shell commands."""
    local_app_data = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    program_files = Path(os.environ.get("ProgramW6432", r"C:\Program Files"))
    roots = (
        local_app_data / "Programs" / "ChatGPT",
        local_app_data / "Programs" / "Codex",
        local_app_data / "ChatGPT",
        local_app_data / "Codex",
        program_files / "ChatGPT",
        program_files / "Codex",
    )
    return tuple(
        archive
        for root in roots
        for archive in (root / "resources" / "app.asar", root / "app" / "resources" / "app.asar")
    )


def _windows_metadata_path(archive: Path) -> Path | None:
    for candidate in (
        archive.parent / "owl-electron-app.json",
        archive.parent.parent / "owl-electron-app.json",
    ):
        if candidate.exists():
            return candidate
    return None


def _windows_package_root() -> Path:
    packages = Path(os.environ.get("ProgramW6432", r"C:\Program Files")) / "WindowsApps"
    matches = sorted(
        (
            *packages.glob("OpenAI.Codex_*"),
            *packages.glob("OpenAI.ChatGPT_*"),
        ),
        key=lambda path: path.name,
    )
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
    if target.package_type == "msix":
        return DesktopPatchStatus(
            target,
            False,
            False,
            "Microsoft Store/MSIX package detected. Codexier will not modify "
            "signed package files.",
        )
    return DesktopPatchStatus(
        target,
        False,
        False,
        "Windows target detected, but no verified Windows patch adapter is "
        "available; no changes were made.",
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
        if target.package_type == "msix":
            raise ConfigError(
                "Windows Microsoft Store/MSIX packages are signed. Codexier "
                "refuses to modify app.asar; use an unpackaged install when a "
                "verified Windows patch adapter is available."
            )
        raise ConfigError(
            "A Windows Electron target was found, but Codexier has no verified "
            "Windows source layout/patch adapter yet. No files were changed."
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
