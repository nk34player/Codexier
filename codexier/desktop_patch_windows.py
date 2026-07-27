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

from .desktop_patch_macos import (
    ASAR_PACKAGE,
    PATCH_MARKER,
    PRETTIER_PACKAGE,
    PatchError,
    apply_supported_patch_variant,
    asar_header_hash,
    contains_marker,
    run,
    unique_candidate,
    validate_provider_config,
)
from .process_manager import ChatGPTProcess, detect_chatgpt_processes


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


def _stop_target_processes(processes: tuple[ChatGPTProcess, ...]) -> str | None:
    if not processes:
        return None
    executable = processes[0].executable
    for process in processes:
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise PatchError(f"Could not stop the target desktop app: {exc}") from exc
    deadline = time.monotonic() + 8.0
    pending = {process.pid for process in processes}
    while pending and time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["tasklist.exe", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise PatchError(f"Could not verify the desktop app stopped: {exc}") from exc
        pending = {pid for pid in pending if str(pid) in result.stdout}
        if pending:
            time.sleep(0.1)
    if pending:
        ids = ", ".join(str(pid) for pid in sorted(pending))
        raise PatchError(f"Target desktop app did not stop (PIDs: {ids}).")
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
    archive: Path, config: Path, backup_root: Path
) -> WindowsPatchResult:
    """Patch a verified unpackaged archive and recover automatically on error."""
    if not archive.is_file():
        raise PatchError(f"Unpackaged Windows app.asar not found: {archive}")
    try:
        provider_config = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"Could not read desktop provider configuration: {exc}") from exc
    validate_provider_config(provider_config)
    if contains_marker(archive):
        return WindowsPatchResult(backup_root, False)
    # Reject truncated or malformed archives before process shutdown or backup.
    asar_header_hash(archive)
    if shutil.which("npx.cmd") is None and shutil.which("npx") is None:
        raise PatchError("npx is required. Install Node.js, then run this command again.")

    executable = _stop_target_processes(_target_processes(archive))
    with tempfile.TemporaryDirectory(prefix="codexier-windows-patch-") as temporary:
        work = Path(temporary)
        extracted = work / "app"
        patched_archive = work / "app.asar"
        npx = _npx_command()
        run(
            [npx, "--yes", ASAR_PACKAGE, "extract", str(archive), str(extracted)],
            label="Extracting Windows application resources",
        )
        assets = extracted / "webview" / "assets"
        if not assets.is_dir():
            raise PatchError("Extracted app has no webview/assets directory.")
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
        patch_targets = list(dict.fromkeys((central, picker)))
        run(
            [npx, "--yes", PRETTIER_PACKAGE, "--write", *(str(path) for path in patch_targets)],
            label="Preparing the JavaScript bundles",
        )
        apply_supported_patch_variant(central, picker)
        if PATCH_MARKER.decode() not in central.read_text(encoding="utf-8"):
            raise PatchError("Routing marker missing after patch.")
        run(
            [npx, "--yes", PRETTIER_PACKAGE, "--write", *(str(path) for path in patch_targets)],
            label="Formatting patched JavaScript",
        )
        run(
            [npx, "--yes", ASAR_PACKAGE, "pack", str(extracted), str(patched_archive)],
            label="Packing patched Windows application resources",
        )
        if not contains_marker(patched_archive):
            raise PatchError("Packed Windows app.asar is missing the patch marker.")

        backup = _make_backup(archive, backup_root)
        try:
            _atomic_replace(patched_archive, archive)
            if not contains_marker(archive):
                raise PatchError("Installed Windows app.asar is missing the patch marker.")
        except Exception:
            _atomic_replace(backup / "app.asar", archive)
            raise

    restarted = False
    if executable:
        try:
            subprocess.Popen([executable])
            restarted = True
        except OSError:
            # The patch is installed and backed up; a manual launch remains safe.
            restarted = False
    return WindowsPatchResult(backup, restarted)
