"""Desktop-patch entry points for verified native Electron installations.

macOS and unpackaged Windows archives use source-validated adapters. Microsoft
Store/MSIX packages are never modified because their signatures cover the
package payload.
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
from .patch_progress import PatchProgress, report


PATCH_MARKER = b"__codexDesktopModelProvidersPatchV6"


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
    skipped: bool = False


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
        True,
        "Unpackaged Windows Electron archive is ready for source validation.",
    )


def backup_desktop_patch(target: DesktopPatchTarget, backup_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"{target.platform}-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    _copy(target.archive_path, backup / "app.asar")
    if target.metadata_path and target.metadata_path.exists():
        _copy(target.metadata_path, backup / target.metadata_path.name)
    return backup


def apply_desktop_patch(
    target: DesktopPatchTarget,
    backup_root: Path,
    progress: PatchProgress | None = None,
) -> DesktopPatchStatus:
    """Patch a supported desktop archive without risking a successful sync.

    The caller receives safe skip statuses for signed packages, already-patched
    installs, and apps that cannot close gracefully.  Expected patch failures
    still raise ``ConfigError`` with the failed milestone for clear remediation.
    """
    last_stage = "detection"

    def emit(percent: int, detail: str) -> None:
        nonlocal last_stage
        # Recovery emits replacement/verification milestones too; retain the
        # original failing stage so the user receives useful remediation.
        if "rolling back" not in detail and "restored original" not in detail:
            last_stage = detail.split(":", 1)[0]
        if progress is not None:
            progress(percent, detail)

    report(emit, "detection", f"inspecting {target.archive_path}")
    status = patch_status(target)
    if status.patched:
        report(emit, "completion", "already patched; app.asar was not modified")
        return status
    if target.platform == "windows" and target.package_type == "msix":
        skipped = DesktopPatchStatus(
            target,
            False,
            False,
            "Desktop patch skipped: Microsoft Store/MSIX package detected. "
            "Codexier will not modify signed package files.",
            True,
        )
        report(emit, "completion", skipped.message)
        return skipped
    if not status.supported:
        raise ConfigError(status.message)
    if target.platform == "darwin":
        from .desktop_patch_macos import (
            PatchSkipped,
            find_target_app_processes,
            gracefully_close_target_app_processes,
            patch_app,
        )

        app = target.archive_path.parents[2]
        from .codex_profile import codex_home

        config = codex_home() / "desktop-model-providers.json"
        if not config.is_file():
            raise ConfigError("Apply a provider before installing the desktop patch.")
        try:
            report(emit, "validation", "validated the provider configuration and app archive")
            was_running = bool(find_target_app_processes(app))
            report(emit, "process stop", "requesting a graceful close of the desktop app")
            gracefully_close_target_app_processes(app)
            patch_app(app, config, backup_root, False, emit)
            if was_running:
                report(emit, "restart", "reopening the desktop app")
                subprocess.Popen(["open", "-a", "ChatGPT"])
            else:
                report(emit, "restart", "desktop app was already closed; no restart was needed")
        except PatchSkipped as exc:
            skipped = DesktopPatchStatus(target, False, True, str(exc), True)
            report(emit, "completion", skipped.message)
            return skipped
        except Exception as exc:
            raise ConfigError(f"Desktop patch failed during {last_stage}: {exc}") from exc
        final_status = patch_status(target)
        if not final_status.patched:
            raise ConfigError("Desktop patch verification did not find the expected marker.")
        report(emit, "completion", "desktop patch installed successfully")
        return final_status
    if target.platform == "windows":
        from .codex_profile import codex_home
        from .desktop_patch_macos import PatchSkipped
        from .desktop_patch_windows import PatchError, patch_windows_app

        config = codex_home() / "desktop-model-providers.json"
        if not config.is_file():
            raise ConfigError("Apply enabled providers before installing the desktop patch.")
        try:
            patch_windows_app(target.archive_path, config, backup_root, emit)
        except PatchSkipped as exc:
            skipped = DesktopPatchStatus(target, False, True, str(exc), True)
            report(emit, "completion", skipped.message)
            return skipped
        except PatchError as exc:
            raise ConfigError(f"Desktop patch failed during {last_stage}: {exc}") from exc
        final_status = patch_status(target)
        if not final_status.patched:
            raise ConfigError("Desktop patch verification did not find the expected marker.")
        report(emit, "completion", "desktop patch installed successfully")
        return final_status
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
