"""Desktop-patch entry points for verified native Electron installations.

macOS and unpackaged Windows archives use source-validated adapters. Microsoft
Store/MSIX packages are never modified because their signatures cover the
package payload.
"""

from __future__ import annotations

import os
import ntpath
import platform as platform_module
import plistlib
import shutil
import subprocess
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ConfigError
from .patch_progress import PatchProgress, report


PATCH_MARKER = b"__codexDesktopModelProvidersPatchV20"
PATCH_MARKER_PREFIX = b"__codexDesktopModelProvidersPatch"


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
    upgrade_required: bool = False


@dataclass(frozen=True)
class MacOSAppInfo:
    app_path: Path
    app_name: str
    bundle_identifier: str
    version: str
    build: str
    archive_path: Path
    archive_size_bytes: int
    archive_modified_at: datetime
    patch_status: DesktopPatchStatus
    sidecar_exists: bool


@dataclass(frozen=True)
class DesktopBackup:
    target: DesktopPatchTarget
    path: Path
    archive_path: Path
    kind: str
    size_bytes: int
    modified_at: datetime
    valid: bool
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
    if PATCH_MARKER_PREFIX in content:
        return DesktopPatchStatus(
            target,
            True,
            True,
            "An older Codexier desktop patch will be upgraded on the next sync.",
            upgrade_required=True,
        )
    if target.platform == "darwin":
        return DesktopPatchStatus(target, False, True, "macOS app is ready for source validation.")
    return DesktopPatchStatus(
        target,
        False,
        True,
        "Unpackaged Windows Electron archive is ready for source validation.",
    )


def macos_app_info(target: DesktopPatchTarget) -> MacOSAppInfo:
    """Return display information for one installed macOS Electron app."""
    if target.platform != "darwin":
        raise ConfigError("macOS app information is available only for macOS targets.")
    archive = target.archive_path
    contents = archive.parent.parent
    app = contents.parent
    info_path = contents / "Info.plist"
    if not app.is_dir():
        raise ConfigError(f"Installed application was not found: {app}")
    try:
        with info_path.open("rb") as handle:
            metadata = plistlib.load(handle)
        stat = archive.stat()
    except (OSError, plistlib.InvalidFileException) as exc:
        raise ConfigError(f"Could not read installed application information: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ConfigError(f"Could not read installed application information: {info_path}")
    return MacOSAppInfo(
        app_path=app,
        app_name=str(
            metadata.get("CFBundleDisplayName")
            or metadata.get("CFBundleName")
            or app.stem
        ),
        bundle_identifier=str(metadata.get("CFBundleIdentifier") or "Unknown"),
        version=str(metadata.get("CFBundleShortVersionString") or "Unknown"),
        build=str(metadata.get("CFBundleVersion") or "Unknown"),
        archive_path=archive,
        archive_size_bytes=stat.st_size,
        archive_modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
        patch_status=patch_status(target),
        sidecar_exists=archive.with_name(f"{archive.name}.bak").is_file(),
    )


def backup_desktop_patch(
    target: DesktopPatchTarget,
    backup_root: Path,
    progress: PatchProgress | None = None,
) -> Path:
    """Create a verified, managed snapshot without changing the installed app."""
    report(progress, "validation", f"validating installed archive {target.archive_path}")
    if not target.archive_path.is_file():
        raise ConfigError(f"Installed archive is unavailable: {target.archive_path}")
    backup_root.mkdir(parents=True, exist_ok=True)
    if target.platform == "darwin":
        try:
            from .desktop_patch_macos import make_backup

            with (
                target.archive_path.parents[2] / "Contents" / "Info.plist"
            ).open("rb") as handle:
                info = plistlib.load(handle)
            report(progress, "backup", "creating a complete macOS app snapshot")
            backup = make_backup(
                target.archive_path.parents[2],
                backup_root,
                str(info.get("CFBundleShortVersionString", "unknown")),
                str(info.get("CFBundleVersion", "unknown")),
            )
        except (OSError, plistlib.InvalidFileException, KeyError) as exc:
            raise ConfigError(f"Could not create macOS application backup: {exc}") from exc
        report(progress, "verification", f"verifying managed snapshot {backup}")
        report(progress, "completion", f"manual backup created: {backup}")
        return backup
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_root / f"{target.platform}-{stamp}"
    backup.mkdir(exist_ok=False)
    try:
        report(progress, "backup", f"copying app.asar to {backup}")
        _copy(target.archive_path, backup / "app.asar")
        if target.metadata_path and target.metadata_path.exists():
            _copy(target.metadata_path, backup / target.metadata_path.name)
        report(progress, "verification", f"verifying managed snapshot {backup}")
    except Exception:
        shutil.rmtree(backup, ignore_errors=True)
        raise
    report(progress, "completion", f"manual backup created: {backup}")
    return backup


def application_targets(platform: str | None = None) -> tuple[DesktopPatchTarget, ...]:
    """Return current app archives that can expose immutable backups."""
    system = (platform or platform_module.system()).lower()
    if system == "darwin":
        return (default_target("darwin"),)
    if system != "windows":
        return ()
    targets = [default_target("windows")]
    try:
        from .windows_portable import portable_status

        portable = portable_status()
        if portable.archive is not None:
            targets.append(
                DesktopPatchTarget("windows", portable.archive, package_type="unpackaged")
            )
    except (ConfigError, OSError):
        pass
    unique: dict[str, DesktopPatchTarget] = {}
    for target in targets:
        unique[str(target.archive_path.resolve()).casefold()] = target
    return tuple(unique.values())


def desktop_backups(target: DesktopPatchTarget, backup_root: Path) -> tuple[DesktopBackup, ...]:
    """List immutable sidecars and legacy managed snapshots for one target."""
    candidates: list[tuple[Path, Path, str]] = []
    sidecar = target.archive_path.with_name(f"{target.archive_path.name}.bak")
    if sidecar.is_file():
        candidates.append((sidecar, sidecar, "Immutable original"))
    if backup_root.is_dir():
        for backup in backup_root.iterdir():
            if (
                backup.is_symlink()
                or not backup.is_dir()
                or not (
                    backup.name.startswith(f"{target.platform}-")
                    or backup.name.startswith(f"automatic-{target.platform}-")
                    or (
                        target.platform == "darwin"
                        and (
                            backup.name.startswith("ChatGPT-")
                            or backup.name.startswith("automatic-ChatGPT-")
                        )
                        and backup.name.endswith(".app")
                    )
                )
            ):
                continue
            archive = _managed_snapshot_archive(target, backup)
            candidates.append((backup, archive, "Managed snapshot"))
    entries: list[DesktopBackup] = []
    for path, archive, kind in candidates:
        try:
            stat = archive.stat()
            patched = PATCH_MARKER_PREFIX in archive.read_bytes()
        except OSError as exc:
            try:
                modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            except OSError:
                modified_at = datetime.fromtimestamp(0, timezone.utc)
            entries.append(
                DesktopBackup(
                    target, path, archive, kind, 0, modified_at, False,
                    f"Cannot read backup: {exc}",
                )
            )
            continue
        created_at = _backup_created_at(archive)
        if path.name.startswith("automatic-"):
            kind = "Automatic snapshot"
        entries.append(
            DesktopBackup(
                target,
                path,
                archive,
                kind,
                stat.st_size,
                created_at,
                not patched,
                "Original archive" if not patched else "Patched archive; restore is blocked",
            )
        )
    return tuple(sorted(entries, key=lambda item: item.modified_at, reverse=True))


def _backup_created_at(path: Path) -> datetime:
    """Use the OS creation timestamp, never the archive modification time."""
    stat = path.stat()
    return datetime.fromtimestamp(
        getattr(stat, "st_birthtime", stat.st_ctime), timezone.utc
    )


def _managed_snapshot_archive(target: DesktopPatchTarget, backup: Path) -> Path:
    """Find current full-app archive layout or legacy archive-only snapshot."""
    if target.platform == "darwin":
        app_archive = backup / "Contents" / "Resources" / "app.asar"
        if app_archive.is_file():
            return app_archive
    return backup / "app.asar"


def delete_desktop_backup(
    target: DesktopPatchTarget,
    backup: Path,
    backup_root: Path,
    progress: PatchProgress | None = None,
) -> None:
    """Permanently delete a verified sidecar or managed snapshot path."""
    report(progress, "validation", f"validating selected backup {backup}")
    sidecar = target.archive_path.with_name(f"{target.archive_path.name}.bak")
    if backup.absolute() == sidecar.absolute():
        if not backup.is_file():
            raise ConfigError(f"Backup file is unavailable: {backup}")
        report(progress, "atomic replacement", f"deleting immutable sidecar {backup}")
        backup.unlink()
    else:
        root = backup_root.resolve()
        if (
            backup.is_symlink()
            or not backup.is_dir()
            or backup.parent.resolve() != root
            or not (
                backup.name.startswith(f"{target.platform}-")
                or (
                    target.platform == "darwin"
                    and backup.name.startswith("ChatGPT-")
                    and backup.name.endswith(".app")
                )
            )
        ):
            raise ConfigError(f"Refusing to delete backup outside the managed backup root: {backup}")
        report(progress, "atomic replacement", f"deleting managed snapshot {backup}")
        shutil.rmtree(backup)
    report(progress, "verification", f"confirming backup was removed: {backup}")
    if backup.exists():
        raise ConfigError(f"Backup could not be deleted: {backup}")
    report(progress, "completion", "selected backup deleted permanently")


def apply_desktop_patch(
    target: DesktopPatchTarget,
    backup_root: Path,
    progress: PatchProgress | None = None,
) -> DesktopPatchStatus:
    """Patch a supported desktop archive without risking a successful sync.

    The caller receives safe skip statuses for signed packages, already-patched
    installs, and running apps. Expected patch failures still raise
    ``ConfigError`` with the failed milestone for clear remediation.
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
    if status.patched and not status.upgrade_required:
        report(emit, "completion", "already patched; app.asar was not modified")
        return status
    if not status.supported:
        raise ConfigError(status.message)
    if target.platform == "darwin":
        from .desktop_patch_macos import (
            PatchSkipped,
            find_target_app_processes,
            patch_app,
        )

        app = target.archive_path.parents[2]
        from .codex_profile import codex_home

        config = codex_home() / "desktop-model-providers.json"
        if not config.is_file():
            raise ConfigError("Apply a provider before installing the desktop patch.")
        try:
            report(emit, "validation", "validated the provider configuration and app archive")
            report(emit, "process stop", "checking that the desktop app is closed")
            if find_target_app_processes(app):
                raise PatchSkipped(
                    "Desktop patch skipped: ChatGPT is running. Close it manually, "
                    "then rerun with --patch-desktop."
                )
            if progress is None:
                patch_app(app, config, backup_root, False, emit)
            else:
                # TUI owns the terminal; legacy installer panels must not leak
                # over it while the worker is running.
                with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    patch_app(app, config, backup_root, False, emit)
            _launch_macos_desktop_app(app, config.parent, emit)
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


def _launch_macos_desktop_app(
    app: Path,
    codex_root: Path,
    progress: PatchProgress | None = None,
) -> Path:
    log_dir = codex_root / "codexier-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"chatgpt-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    executable = app / "Contents" / "MacOS" / "ChatGPT"
    codex = app / "Contents" / "Resources" / "codex"
    if not executable.is_file():
        report(progress, "restart", f"ChatGPT executable not found; runtime log: {log_path}")
        return log_path
    launch_env = os.environ.copy()
    launch_env.update(
        {
            "ELECTRON_ENABLE_LOGGING": "1",
            "RUST_BACKTRACE": "1",
            "CODEXIER_CODEX_LOG_PATH": str(log_path),
        }
    )
    with log_path.open("ab") as log_handle:
        subprocess.Popen(
            [str(executable)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=launch_env,
            start_new_session=True,
        )
    report(progress, "restart", f"started ChatGPT; runtime log: {log_path}")
    if codex.is_file():
        with log_path.open("ab") as log_handle:
            log_handle.write(f"\n=== Codex executable diagnostic: {codex} ===\n".encode())
            diagnostic = subprocess.run(
                [str(codex), "--version"],
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env={**os.environ, "RUST_BACKTRACE": "1"},
                timeout=15,
                check=False,
            )
            log_handle.write(
                f"=== Codex diagnostic exit code: {diagnostic.returncode} ===\n".encode()
            )
        report(progress, "restart", f"logged Codex diagnostic: {log_path}")
    return log_path


def restore_desktop_patch(
    target: DesktopPatchTarget,
    backup: Path,
    progress: PatchProgress | None = None,
) -> None:
    if target.diagnostic:
        raise ConfigError(target.diagnostic)
    if target.platform == "darwin":
        from .desktop_patch_macos import PatchError, restore_archive_backup, restore_original_app

        try:
            if backup.is_file():
                restore_archive_backup(target.archive_path.parents[2], backup, progress)
            else:
                restore_original_app(target.archive_path.parents[2], backup, progress)
        except PatchError as exc:
            raise ConfigError(str(exc)) from exc
        return
    source = backup if backup.is_file() else backup / "app.asar"
    if not source.is_file():
        raise ConfigError(f"Backup does not contain app.asar: {backup}")
    if PATCH_MARKER_PREFIX in source.read_bytes():
        raise ConfigError(f"Backup is patched and cannot be restored: {backup}")
    report(progress, "process stop", "closing the desktop app before restore")
    try:
        from .desktop_patch_windows import _gracefully_close_target_processes, _target_processes

        _gracefully_close_target_processes(_target_processes(target.archive_path))
    except Exception as exc:
        raise ConfigError(f"Could not close the desktop app before restore: {exc}") from exc
    report(progress, "atomic replacement", "restoring app.asar from backup")
    _atomic_replace(target.archive_path, source.read_bytes())
    if target.metadata_path:
        metadata = backup / target.metadata_path.name
        if metadata.is_file():
            _atomic_replace(target.metadata_path, metadata.read_bytes())
    report(progress, "verification", "verifying restored app.asar")
    if target.archive_path.read_bytes() != source.read_bytes():
        raise ConfigError(f"Restored app.asar does not match backup: {backup}")


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
