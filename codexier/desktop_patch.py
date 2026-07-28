"""Desktop-patch entry points for verified native Electron installations.

macOS and unpackaged Windows archives use source-validated adapters. Microsoft
Store/MSIX packages are never modified because their signatures cover the
package payload.
"""

from __future__ import annotations

import os
import ntpath
import platform as platform_module
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConfigError
from .patch_progress import PatchProgress, report


PATCH_MARKER = b"__codexDesktopModelProvidersPatchV7"


@dataclass(frozen=True)
class DesktopPatchTarget:
    platform: str
    archive_path: Path
    metadata_path: Path | None = None
    package_type: str = "unknown"
    diagnostic: str | None = None


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
    from .desktop_patch_macos import PatchError, asar_header_hash
    from .process_manager import detect_chatgpt_processes

    processes = detect_chatgpt_processes(platform="win32")
    if not processes:
        return _windows_skip_target(
            "Desktop patch skipped: no current-user ChatGPT or Codex desktop "
            "process is running. Open the unpackaged desktop app once, then "
            "sync enabled providers again."
        )

    roots = {
        _windows_path_key(_windows_process_root(process.executable)):
        _windows_process_root(process.executable)
        for process in processes
    }
    if len(roots) != 1:
        names = ", ".join(str(root) for root in roots.values())
        return _windows_skip_target(
            "Desktop patch skipped: multiple running desktop installations were "
            f"detected ({names}). Close all but one, then sync enabled providers again."
        )

    root = next(iter(roots.values()))
    candidates = (
        root / "resources" / "app.asar",
        root / "app" / "resources" / "app.asar",
    )
    if _is_windows_msix_path(root):
        return DesktopPatchTarget(
            "windows",
            next((path for path in candidates if path.is_file()), candidates[-1]),
            package_type="msix",
            diagnostic=(
                "Desktop patch skipped: the running app is a signed Microsoft "
                "Store/MSIX package. Install and run the unpackaged desktop "
                "version, then sync enabled providers again."
            ),
        )

    valid: list[Path] = []
    failures: list[str] = []
    for archive in candidates:
        if not archive.is_file():
            continue
        if not _windows_path_is_within(archive, root):
            failures.append(f"{archive} is outside the running installation root")
            continue
        if _is_windows_msix_path(archive):
            failures.append(f"{archive} belongs to a signed Microsoft Store/MSIX package")
            continue
        try:
            asar_header_hash(archive)
        except PatchError as exc:
            failures.append(f"{archive} is not a valid ASAR ({exc})")
            continue
        write_error = _windows_write_probe(archive.parent)
        if write_error:
            failures.append(f"{archive} is not writable ({write_error})")
            continue
        valid.append(archive)

    override = os.environ.get("CODEXIER_WINDOWS_APP_ASAR")
    if override:
        selected = Path(override).expanduser()
        valid = [
            path for path in valid
            if _windows_path_key(path) == _windows_path_key(selected)
        ]
        if not valid:
            return _windows_skip_target(
                "Desktop patch skipped: CODEXIER_WINDOWS_APP_ASAR does not select "
                "a validated archive used by the running unpackaged app. Remove "
                "the override or point it to that runtime's app.asar."
            )

    if len(valid) > 1:
        names = ", ".join(str(path) for path in valid)
        return _windows_skip_target(
            "Desktop patch skipped: multiple valid app.asar archives were found "
            f"in the running installation ({names}). Set CODEXIER_WINDOWS_APP_ASAR "
            "to select one of them."
        )
    if not valid:
        detail = "; ".join(failures) or "no supported app.asar layout was found"
        return _windows_skip_target(
            "Desktop patch skipped: the running unpackaged desktop runtime could "
            f"not be validated: {detail}. Reinstall the unpackaged app or fix its "
            "folder permissions, then sync enabled providers again."
        )

    archive = valid[0]
    return DesktopPatchTarget(
        "windows", archive, _windows_metadata_path(archive), "unpackaged"
    )


def _windows_process_root(executable: str) -> Path:
    return Path(ntpath.dirname(executable) or ".")


def _windows_path_key(path: Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path))).casefold()


def _windows_path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        pass

    path_key = _windows_path_key(path)
    root_key = _windows_path_key(root).rstrip("\\")
    return path_key == root_key or path_key.startswith(root_key + "\\")


def _is_windows_msix_path(path: Path) -> bool:
    parts = ntpath.normpath(str(path)).split("\\")
    if "windowsapps" in {part.casefold() for part in parts}:
        return True
    return any((candidate / "AppxManifest.xml").is_file() for candidate in (path, *path.parents))


def _windows_write_probe(directory: Path) -> str | None:
    name: str | None = None
    try:
        handle, name = tempfile.mkstemp(prefix=".codexier-write-probe-", dir=directory)
        os.close(handle)
        Path(name).unlink()
        return None
    except OSError as exc:
        if name:
            Path(name).unlink(missing_ok=True)
        return str(exc)


def _windows_skip_target(message: str) -> DesktopPatchTarget:
    return DesktopPatchTarget(
        "windows", Path("app.asar"), package_type="unavailable", diagnostic=message
    )


def _windows_metadata_path(archive: Path) -> Path | None:
    for candidate in (
        archive.parent / "owl-electron-app.json",
        archive.parent.parent / "owl-electron-app.json",
    ):
        if candidate.exists():
            return candidate
    return None


def patch_status(target: DesktopPatchTarget) -> DesktopPatchStatus:
    if target.diagnostic:
        return DesktopPatchStatus(target, False, False, target.diagnostic, skipped=True)
    if target.platform == "windows" and target.package_type == "msix":
        return DesktopPatchStatus(
            target,
            False,
            False,
            "Microsoft Store/MSIX package detected. Codexier will not modify "
            "signed package files.",
            True,
        )
    try:
        content = target.archive_path.read_bytes()
    except OSError as exc:
        return DesktopPatchStatus(target, False, False, f"Cannot read app archive: {exc}.")
    if PATCH_MARKER in content:
        return DesktopPatchStatus(target, True, True, "Codexier desktop patch is installed.")
    if target.platform == "darwin":
        return DesktopPatchStatus(target, False, True, "macOS app is ready for source validation.")
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
    if status.skipped:
        report(emit, "completion", status.message)
        return status
    if status.patched:
        report(emit, "completion", "already patched; app.asar was not modified")
        return status
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
    if target.diagnostic:
        raise ConfigError(target.diagnostic)
    if target.platform == "darwin":
        from .desktop_patch_macos import PatchError, restore_original_app

        try:
            restore_original_app(target.archive_path.parents[2], backup)
        except PatchError as exc:
            raise ConfigError(str(exc)) from exc
        return
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
