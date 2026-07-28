"""Verified app.asar patching for unpackaged Windows Electron installs.

The JavaScript patch is shared with the macOS adapter.  This wrapper only
handles the platform-specific lifecycle: stopping the target executable,
backing up the archive, atomically replacing it, and reopening it afterwards.
Microsoft Store/MSIX packages never reach this module.
"""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backup import immutable_file_backup
from .desktop_patch_macos import (
    ASAR_PACKAGE,
    PATCH_MARKER,
    PatchError,
    PatchSkipped,
    apply_supported_patch_variant,
    asar_header_hash,
    contains_marker,
    run,
    unique_candidate,
    validate_provider_config,
)
from .patch_progress import PatchProgress, report
from .process_manager import (
    ChatGPTProcess,
    detect_chatgpt_processes,
    gracefully_close_chatgpt_processes,
)


@dataclass(frozen=True)
class WindowsPatchResult:
    backup_path: Path
    restarted: bool


def _npx_command() -> str:
    return shutil.which("npx.cmd") or shutil.which("npx") or "npx.cmd"


def _installation_root(archive: Path) -> Path:
    # Both supported layouts end in resources/app.asar:
    #   <install>/resources/app.asar
    #   <install>/app/resources/app.asar
    resources = archive.parent
    return resources.parent.parent if resources.parent.name.casefold() == "app" else resources.parent


def _belongs_to_target(process: ChatGPTProcess, root: Path) -> bool:
    executable = Path(process.executable)
    try:
        return executable.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return str(executable).casefold().startswith(
            str(root).rstrip("\\/").casefold() + "\\"
        )


def _target_processes(archive: Path) -> tuple[ChatGPTProcess, ...]:
    root = _installation_root(archive)
    return tuple(
        process
        for process in detect_chatgpt_processes(platform="win32")
        if _belongs_to_target(process, root)
    )


def _gracefully_close_target_processes(
    processes: tuple[ChatGPTProcess, ...]
) -> str | None:
    """Close windows without taskkill; preserve unsaved desktop state on failure."""
    if not processes:
        return None
    executable = processes[0].executable
    result = gracefully_close_chatgpt_processes(processes)
    if not result.closed:
        raise PatchSkipped(
            f"Desktop patch skipped: {result.message}"
        )
    return executable


def _make_backup(archive: Path, backup_root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = backup_root / f"windows-{timestamp}"
    suffix = 1
    while backup.exists():
        backup = backup_root / f"windows-{timestamp}-{suffix}"
        suffix += 1
    backup.mkdir(parents=True)
    destination = backup / "app.asar"
    shutil.copy2(archive, destination)
    if archive.read_bytes() != destination.read_bytes():
        raise PatchError("Windows app.asar backup verification failed.")
    return backup


def _atomic_replace(source: Path, destination: Path) -> None:
    mode = destination.stat().st_mode
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.patch-", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def patch_windows_app(
    archive: Path,
    config: Path,
    backup_root: Path,
    progress: PatchProgress | None = None,
    *,
    require_running: bool = True,
    restart: bool = True,
) -> WindowsPatchResult:
    """Patch a verified unpackaged archive and recover automatically on error."""
    if not archive.is_file():
        raise PatchError(f"Unpackaged Windows app.asar not found: {archive}")
    try:
        provider_config = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"Could not read desktop provider configuration: {exc}") from exc
    validate_provider_config(provider_config)
    report(progress, "validation", "validated the provider configuration and archive")
    if contains_marker(archive):
        return WindowsPatchResult(backup_root, False)
    # Reject truncated or malformed archives before process shutdown or backup.
    asar_header_hash(archive)
    if shutil.which("npx.cmd") is None and shutil.which("npx") is None:
        raise PatchError("npx is required. Install Node.js, then run this command again.")
    node = shutil.which("node.exe") or shutil.which("node")
    if node is None:
        raise PatchError("Node.js is required. Install Node.js, then run this command again.")

    processes = _target_processes(archive)
    if require_running and not processes:
        raise PatchSkipped(
            "Desktop patch skipped: the validated ChatGPT or Codex runtime is no "
            "longer running. Reopen the unpackaged app, then sync enabled providers again."
        )
    executable = None
    if processes:
        report(progress, "process stop", "requesting a graceful close of the desktop app")
        executable = _gracefully_close_target_processes(processes)
    else:
        report(progress, "process stop", "portable app is not running")
    sidecar, created = immutable_file_backup(archive)
    if contains_marker(sidecar):
        raise PatchError(f"Immutable backup is patched and cannot be used: {sidecar}")
    asar_header_hash(sidecar)
    report(
        progress,
        "backup",
        f"{'created' if created else 'reused'} immutable original backup: {sidecar}",
    )
    report(progress, "backup", "creating a verified backup of app.asar")
    backup = _make_backup(archive, backup_root)
    try:
        with tempfile.TemporaryDirectory(prefix="codexier-windows-patch-") as temporary:
            work = Path(temporary)
            extracted = work / "app"
            patched_archive = work / "app.asar"
            npx = _npx_command()
            report(progress, "extraction", "extracting application resources")
            run(
                [npx, "--yes", ASAR_PACKAGE, "extract", str(archive), str(extracted)],
                label="Extracting Windows application resources",
                terminal=False,
            )
            assets = extracted / "webview" / "assets"
            if not assets.is_dir():
                raise PatchError("Extracted app has no webview/assets directory.")
            report(progress, "bundle matching", "matching the supported application bundle layout")
            central = unique_candidate(
                assets,
                ("async prewarmThreadStart(", "async sendConfigReadRequest("),
                "App Server client",
            )
            picker = unique_candidate(
                assets,
                ("composer.intelligenceDropdown.tooltip", "modelOptionsDisabled"),
                "model picker",
            )
            report(progress, "source patch", "applying provider-first model routing")
            apply_supported_patch_variant(central, picker)
            if PATCH_MARKER.decode() not in central.read_text(encoding="utf-8"):
                raise PatchError("Routing marker missing after patch.")
            if "CodexCustomProviderPickerSection" not in picker.read_text(encoding="utf-8"):
                raise PatchError("Provider picker missing after patch.")
            for bundle in {central, picker}:
                run(
                    [node, "--check", str(bundle)],
                    label="Validating patched JavaScript",
                    terminal=False,
                )
            report(progress, "repack", "repacking the patched application archive")
            run(
                [npx, "--yes", ASAR_PACKAGE, "pack", str(extracted), str(patched_archive)],
                label="Packing patched Windows application resources",
                terminal=False,
            )
            if not contains_marker(patched_archive):
                raise PatchError("Packed Windows app.asar is missing the patch marker.")

            report(progress, "atomic replacement", "atomically replacing app.asar")
            _atomic_replace(patched_archive, archive)
            report(progress, "verification", "verifying the installed app.asar")
            if not contains_marker(archive):
                raise PatchError("Installed Windows app.asar is missing the patch marker.")
    except Exception:
        report(progress, "atomic replacement", "rolling back from the verified backup")
        _atomic_replace(backup / "app.asar", archive)
        report(progress, "verification", "verifying the restored original archive")
        if archive.read_bytes() != (backup / "app.asar").read_bytes():
            raise PatchError(f"Rollback verification failed; backup remains at: {backup}")
        raise

    restarted = False
    report(progress, "restart", "reopening the desktop app when it was previously running")
    if restart and executable:
        try:
            subprocess.Popen([executable])
            restarted = True
        except OSError:
            # The patch is installed and backed up; a manual launch remains safe.
            restarted = False
    return WindowsPatchResult(backup, restarted)
