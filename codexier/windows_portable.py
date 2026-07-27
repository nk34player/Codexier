"""Safe Official/Portable Windows Codex support.

The Microsoft Store package is a read-only source. Codexier copies its
executable payload to LocalAppData and patches only that private copy.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup import atomic_write
from .codex_profile import CodexProfileResult, apply_codex_profiles
from .desktop_patch import PATCH_MARKER
from .desktop_patch_macos import PatchError, asar_header_hash, contains_marker
from .desktop_patch_windows import patch_windows_app
from .errors import ConfigError
from .models import Provider
from .patch_progress import PatchProgress
from .process_manager import (
    ChatGPTProcess,
    detect_chatgpt_processes,
    gracefully_close_chatgpt_processes,
)
from .settings import save_settings


PORTABLE_PATCH_VERSION = PATCH_MARKER.decode().rsplit("V", 1)[-1]
PORTABLE_PROGRESS = {
    "detection": 5,
    "validation": 12,
    "process shutdown": 20,
    "staging": 28,
    "cloning": 38,
    "verification": 50,
    "patching": 62,
    "atomic replacement": 75,
    "install verification": 82,
    "health check": 88,
    "launch": 94,
    "rollback": 96,
    "rollback verification": 98,
    "completion": 100,
}


@dataclass(frozen=True)
class OfficialPackage:
    package_full_name: str
    package_family_name: str
    version: str
    install_location: Path
    application_id: str
    aumid: str
    executable: Path
    archive: Path


@dataclass(frozen=True)
class PortableStatus:
    root: Path
    installed: bool
    patched: bool
    installed_version: str | None
    source_version: str | None
    update_available: bool
    executable: Path | None
    archive: Path | None
    message: str


@dataclass(frozen=True)
class WindowsModeResult:
    profile: CodexProfileResult
    mode: str
    launched: bool
    message: str


def _emit(progress: PatchProgress | None, stage: str, detail: str) -> None:
    if progress is not None:
        progress(PORTABLE_PROGRESS[stage], f"{stage}: {detail}")


def _patch_log(progress: PatchProgress | None) -> PatchProgress | None:
    if progress is None:
        return None
    return lambda _percent, detail: progress(
        PORTABLE_PROGRESS["patching"], f"patching: {detail}"
    )


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _joined(root: Path, manifest_path: str) -> Path:
    return root.joinpath(*manifest_path.replace("\\", "/").split("/"))


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        path_key = ntpath.normcase(ntpath.normpath(str(path))).casefold()
        root_key = ntpath.normcase(ntpath.normpath(str(root))).casefold().rstrip("\\")
        return path_key == root_key or path_key.startswith(root_key + "\\")


def _resolve_manifest_entry(
    install_location: Path,
    package_family_name: str,
) -> tuple[str, str, Path]:
    manifest = install_location / "AppxManifest.xml"
    try:
        root = ET.parse(manifest).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ConfigError(f"Could not read the installed Codex package manifest: {exc}") from exc
    applications = [
        item for item in root.iter() if _local_name(item.tag) == "Application"
    ]
    matches: list[tuple[str, str, Path]] = []
    for application in applications:
        application_id = application.attrib.get("Id", "").strip()
        executable_value = application.attrib.get("Executable", "").strip()
        executable = _joined(install_location, executable_value)
        if (
            application_id
            and executable_value
            and executable.name.casefold() in {"chatgpt.exe", "codex.exe"}
            and executable.is_file()
        ):
            matches.append(
                (
                    application_id,
                    f"{package_family_name}!{application_id}",
                    executable,
                )
            )
    if len(matches) != 1:
        raise ConfigError(
            "The installed OpenAI.Codex package does not expose exactly one "
            "supported ChatGPT.exe or Codex.exe manifest entry."
        )
    return matches[0]


def _find_archive(executable: Path) -> Path:
    candidates = (
        executable.parent / "resources" / "app.asar",
        executable.parent / "app" / "resources" / "app.asar",
    )
    found = [candidate for candidate in candidates if candidate.is_file()]
    if len(found) != 1:
        raise ConfigError(
            "The installed Codex package does not contain exactly one supported "
            "resources/app.asar payload."
        )
    try:
        asar_header_hash(found[0])
    except PatchError as exc:
        raise ConfigError(f"The installed Codex app.asar is invalid: {exc}") from exc
    return found[0]


def discover_official_package(
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> OfficialPackage:
    """Discover the current user's installed OpenAI.Codex Store package."""
    script = (
        "$ErrorActionPreference='Stop'; "
        "$p = Get-AppxPackage -Name 'OpenAI.Codex' | "
        "Sort-Object Version -Descending | Select-Object -First 1; "
        "if ($null -eq $p) { exit 3 }; "
        "[pscustomobject]@{"
        "PackageFullName=$p.PackageFullName;"
        "PackageFamilyName=$p.PackageFamilyName;"
        "Version=$p.Version.ToString();"
        "InstallLocation=$p.InstallLocation"
        "} | ConvertTo-Json -Compress"
    )
    try:
        completed = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
        data = json.loads(completed.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise ConfigError(
            "Could not discover the current-user OpenAI.Codex Store package. "
            "Install it from Microsoft Store, then try again."
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError("Windows package discovery returned an invalid result.")
    try:
        install_location = Path(str(data["InstallLocation"]))
        package_full_name = str(data["PackageFullName"])
        package_family_name = str(data["PackageFamilyName"])
        version = str(data["Version"])
    except KeyError as exc:
        raise ConfigError("Windows package discovery omitted required metadata.") from exc
    application_id, aumid, executable = _resolve_manifest_entry(
        install_location, package_family_name
    )
    archive = _find_archive(executable)
    if not _within(executable, install_location) or not _within(archive, install_location):
        raise ConfigError("The Codex package manifest points outside its install location.")
    return OfficialPackage(
        package_full_name,
        package_family_name,
        version,
        install_location,
        application_id,
        aumid,
        executable,
        archive,
    )


def portable_base(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local = values.get("LOCALAPPDATA")
    if not local:
        raise ConfigError("LOCALAPPDATA is unavailable; the portable app path cannot be derived.")
    return Path(local) / "Codexier"


def portable_root(environ: Mapping[str, str] | None = None) -> Path:
    return portable_base(environ) / "PortableCodex"


def portable_metadata_path(environ: Mapping[str, str] | None = None) -> Path:
    return portable_base(environ) / "portable-codex.json"


def _read_metadata(environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    path = portable_metadata_path(environ)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _portable_paths(
    metadata: Mapping[str, Any], environ: Mapping[str, str] | None = None
) -> tuple[Path, Path]:
    root = portable_root(environ)
    executable = _joined(root, str(metadata.get("executable", "")))
    archive = _joined(root, str(metadata.get("archive", "")))
    if not _within(executable, root) or not _within(archive, root):
        raise ConfigError("Portable Codex metadata points outside its managed directory.")
    return executable, archive


def portable_status(
    package: OfficialPackage | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> PortableStatus:
    root = portable_root(environ)
    metadata = _read_metadata(environ)
    if not root.is_dir() or metadata is None:
        return PortableStatus(
            root, False, False, None, package.version if package else None,
            False, None, None, "Portable Codex is not installed.",
        )
    try:
        executable, archive = _portable_paths(metadata, environ)
    except ConfigError as exc:
        return PortableStatus(root, False, False, None, None, False, None, None, str(exc))
    installed = executable.is_file() and archive.is_file()
    patched = installed and contains_marker(archive)
    installed_version = str(metadata.get("package_version") or "") or None
    source_version = package.version if package else None
    update_available = bool(
        package
        and (
            installed_version != package.version
            or metadata.get("source_app_asar_hash") != _hash(package.archive)
        )
    )
    if not installed:
        message = "Portable Codex metadata exists, but its executable payload is incomplete."
    elif update_available:
        message = "A newer or changed Store payload is available for manual refresh."
    elif not patched:
        message = "Portable Codex is installed, but its provider-first patch needs repair."
    else:
        message = "Portable Codex is installed and patched."
    return PortableStatus(
        root, installed, patched, installed_version, source_version,
        update_available, executable if installed else None, archive if installed else None,
        message,
    )


def _metadata_for(
    package: OfficialPackage,
    payload_root: Path,
    portable_executable: Path,
    portable_archive: Path,
) -> dict[str, Any]:
    return {
        "package_version": package.version,
        "package_full_name": package.package_full_name,
        "package_family_name": package.package_family_name,
        "aumid": package.aumid,
        "source_executable": str(package.executable),
        "source_app_asar_hash": _hash(package.archive),
        "executable": portable_executable.relative_to(payload_root).as_posix(),
        "archive": portable_archive.relative_to(payload_root).as_posix(),
        "portable_patch_version": PORTABLE_PATCH_VERSION,
    }


def _payload_fingerprint(root: Path, executable: Path, archive: Path) -> tuple[str, str]:
    return _hash(executable), _hash(archive)


def _restore_previous(
    root: Path,
    rollback: Path,
    previous_fingerprint: tuple[str, str] | None,
    previous_paths: tuple[Path, Path] | None,
    progress: PatchProgress | None,
) -> None:
    _emit(progress, "rollback", "restoring the previous portable installation")
    if root.exists():
        shutil.rmtree(root)
    if rollback.exists():
        os.replace(rollback, root)
    if previous_fingerprint and previous_paths:
        restored_paths = (
            root / previous_paths[0].relative_to(root),
            root / previous_paths[1].relative_to(root),
        )
        if _payload_fingerprint(root, *restored_paths) != previous_fingerprint:
            raise ConfigError("Portable rollback verification failed.")
    _emit(progress, "rollback verification", "verified the restored portable installation")


def _default_health_check(executable: Path) -> bool:
    try:
        process = subprocess.Popen(
            [str(executable), "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            return process.wait(timeout=5) == 0
        except subprocess.TimeoutExpired:
            launched = (ChatGPTProcess(process.pid, str(executable), ""),)
            return gracefully_close_chatgpt_processes(launched).closed
    except OSError:
        return False


def install_portable(
    config: Path | None,
    *,
    prepare_config: Callable[[], Path] | None = None,
    refresh: bool = False,
    package: OfficialPackage | None = None,
    environ: Mapping[str, str] | None = None,
    progress: PatchProgress | None = None,
    health_check: Callable[[Path], bool] = _default_health_check,
    close_processes: Callable[[Sequence[ChatGPTProcess]], Any] = gracefully_close_chatgpt_processes,
) -> PortableStatus:
    """Create or manually refresh the managed portable application."""
    _emit(progress, "detection", "discovering the installed OpenAI.Codex package")
    package = package or discover_official_package()
    status = portable_status(package, environ=environ)
    if status.installed and status.patched and not refresh:
        _emit(progress, "completion", status.message)
        return status
    if refresh and status.installed and not status.update_available and status.patched:
        _emit(progress, "completion", "portable app already matches the Store payload")
        return status
    _emit(progress, "validation", "validated the signed source payload and manifest entry point")
    processes = detect_chatgpt_processes(platform="win32")
    _emit(progress, "process shutdown", "requesting graceful shutdown of Official and Portable Codex")
    closed = close_processes(processes)
    if not getattr(closed, "closed", False):
        raise ConfigError(getattr(closed, "message", "Codex did not close normally."))
    if prepare_config is not None:
        config = prepare_config()
    if config is None or not config.is_file():
        raise ConfigError("Desktop provider routing configuration is unavailable.")

    base = portable_base(environ)
    root = portable_root(environ)
    metadata_path = portable_metadata_path(environ)
    base.mkdir(parents=True, exist_ok=True)
    rollback = base / "PortableCodex.rollback"
    if rollback.exists():
        shutil.rmtree(rollback)
    previous_metadata = _read_metadata(environ)
    previous_paths = (
        _portable_paths(previous_metadata, environ) if previous_metadata and root.exists() else None
    )
    try:
        previous_metadata_bytes = metadata_path.read_bytes()
    except FileNotFoundError:
        previous_metadata_bytes = None
    except OSError as exc:
        raise ConfigError(f"Could not back up portable metadata: {exc}") from exc
    previous_fingerprint = (
        _payload_fingerprint(root, *previous_paths)
        if previous_paths and all(path.is_file() for path in previous_paths)
        else None
    )
    if root.exists() and previous_fingerprint is None:
        raise ConfigError(
            "The managed PortableCodex directory exists but cannot be verified. "
            "Move it aside, then create the portable app again."
        )
    source_fingerprint = (_hash(package.executable), _hash(package.archive))
    staging = Path(tempfile.mkdtemp(prefix=".PortableCodex-staging-", dir=base))
    shutil.rmtree(staging)
    installed = False
    try:
        _emit(progress, "staging", f"creating staging directory under {base}")
        payload_source = package.install_location
        _emit(progress, "cloning", f"copying the signed package payload from {payload_source}")
        shutil.copytree(payload_source, staging)
        staged_executable = staging / package.executable.relative_to(payload_source)
        staged_archive = staging / package.archive.relative_to(payload_source)
        _emit(progress, "verification", "verifying staged executable and app.asar hashes")
        if _payload_fingerprint(staging, staged_executable, staged_archive) != source_fingerprint:
            raise ConfigError("The staged portable payload does not match the Store source.")

        _emit(progress, "patching", "installing provider-first routing in the staged app.asar")
        patch_windows_app(
            staged_archive,
            config,
            base / "desktop-patch-backups",
            _patch_log(progress),
            require_running=False,
            restart=False,
        )
        if not contains_marker(staged_archive):
            raise ConfigError("The staged portable app is missing the Codexier patch marker.")

        _emit(progress, "atomic replacement", "installing the staged portable payload atomically")
        if root.exists():
            os.replace(root, rollback)
        os.replace(staging, root)
        installed = True
        portable_executable = root / staged_executable.relative_to(staging)
        portable_archive = root / staged_archive.relative_to(staging)
        _emit(progress, "install verification", "verifying the installed portable payload")
        if not portable_executable.is_file() or not contains_marker(portable_archive):
            raise ConfigError("Portable installation verification failed.")
        if (_hash(package.executable), _hash(package.archive)) != source_fingerprint:
            raise ConfigError("The signed Microsoft Store source changed during cloning.")

        _emit(progress, "health check", "checking that the portable executable starts outside package identity")
        if not health_check(portable_executable):
            raise ConfigError(
                "Portable Codex could not run outside package identity. "
                "The previous portable installation will be restored."
            )
        metadata = _metadata_for(package, root, portable_executable, portable_archive)
        atomic_write(
            metadata_path,
            (json.dumps(metadata, indent=2) + "\n").encode(),
            mode=0o600,
        )
        if rollback.exists():
            shutil.rmtree(rollback)
    except Exception:
        try:
            if installed or rollback.exists():
                _restore_previous(
                    root, rollback, previous_fingerprint, previous_paths, progress
                )
        finally:
            if previous_metadata_bytes is None:
                metadata_path.unlink(missing_ok=True)
            else:
                atomic_write(metadata_path, previous_metadata_bytes, mode=0o600)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    final = portable_status(package, environ=environ)
    _emit(progress, "completion", final.message)
    return final


def repair_portable(
    config: Path | None,
    *,
    prepare_config: Callable[[], Path] | None = None,
    environ: Mapping[str, str] | None = None,
    progress: PatchProgress | None = None,
) -> PortableStatus:
    _emit(progress, "detection", "inspecting the managed portable installation")
    status = portable_status(environ=environ)
    if not status.installed or status.archive is None:
        raise ConfigError("Create the portable app before repairing its patch.")
    _emit(progress, "validation", "validated the portable executable and app.asar paths")
    _emit(progress, "process shutdown", "requesting graceful shutdown of Official and Portable Codex")
    closed = gracefully_close_chatgpt_processes(
        detect_chatgpt_processes(platform="win32")
    )
    if not closed.closed:
        raise ConfigError(closed.message)
    if prepare_config is not None:
        config = prepare_config()
    if config is None or not config.is_file():
        raise ConfigError("Desktop provider routing configuration is unavailable.")
    _emit(progress, "patching", "repairing provider-first routing")
    patch_windows_app(
        status.archive,
        config,
        portable_base(environ) / "desktop-patch-backups",
        _patch_log(progress),
        require_running=False,
        restart=False,
    )
    final = portable_status(environ=environ)
    _emit(progress, "completion", final.message)
    return final


def require_shared_codex_home(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
) -> Path:
    values = os.environ if environ is None else environ
    user_profile = values.get("USERPROFILE")
    if not user_profile:
        raise ConfigError(
            "USERPROFILE is unavailable; Codexier cannot verify shared Official/Portable sessions."
        )
    expected = Path(user_profile) / ".codex"
    configured = values.get("CODEX_HOME")
    actual = Path(home).expanduser() if home else Path(configured).expanduser() if configured else expected
    if ntpath.normcase(ntpath.normpath(str(actual))).casefold() != ntpath.normcase(
        ntpath.normpath(str(expected))
    ).casefold():
        raise ConfigError(
            f"Official/Portable sharing requires {expected}. Unset CODEX_HOME or "
            "point it to the standard user .codex directory."
        )
    return actual


def _launch_official(package: OfficialPackage) -> object:
    return subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{package.aumid}"])


def _launch_portable(executable: Path) -> object:
    return subprocess.Popen([str(executable)])


def sync_and_launch_windows(
    catalog_path: Path,
    providers: tuple[Provider, ...],
    selected_provider: Provider,
    settings: dict[str, Any],
    *,
    progress: PatchProgress | None = None,
    environ: Mapping[str, str] | None = None,
    launch_official: Callable[[OfficialPackage], object] = _launch_official,
    launch_portable: Callable[[Path], object] = _launch_portable,
) -> WindowsModeResult:
    """Switch modes only after a verified graceful close, then sync and launch."""
    shared_home = require_shared_codex_home(environ)
    windows = settings["windows"]
    mode = windows["mode"]
    enabled = tuple(provider for provider in providers if provider.enabled)
    if selected_provider.id not in {provider.id for provider in enabled}:
        raise ConfigError("The selected Windows provider must be enabled.")
    package = discover_official_package()
    portable = portable_status(package, environ=environ)
    if mode == "portable" and not portable.installed:
        raise ConfigError("Create the portable app from Settings before launching Portable mode.")

    _emit(progress, "process shutdown", "requesting graceful shutdown before switching modes")
    closed = gracefully_close_chatgpt_processes(
        detect_chatgpt_processes(platform="win32")
    )
    if not closed.closed:
        raise ConfigError(closed.message)

    scope = (selected_provider,) if mode == "official" else enabled
    profile = apply_codex_profiles(
        scope,
        selected_provider,
        home=shared_home,
        settings=settings,
    )
    selection_key = (
        "official_provider_id"
        if mode == "official"
        else "portable_default_provider_id"
    )
    windows[selection_key] = selected_provider.id
    save_settings(catalog_path, settings)

    if mode == "portable":
        assert portable.archive is not None and portable.executable is not None
        try:
            assert profile.desktop_config_path is not None
            patch_windows_app(
                portable.archive,
                profile.desktop_config_path,
                portable_base(environ) / "desktop-patch-backups",
                _patch_log(progress),
                require_running=False,
                restart=False,
            )
        except (PatchError, OSError) as exc:
            raise ConfigError(
                "Provider sync succeeded, but the portable patch failed. "
                f"Use Settings → Portable App → Repair patch: {exc}"
            ) from exc
        if not contains_marker(portable.archive):
            raise ConfigError(
                "Provider sync succeeded, but portable patch verification failed. "
                "Use Settings → Portable App → Repair patch."
            )
        _emit(progress, "launch", f"launching portable Codex from {portable.executable}")
        try:
            launch_portable(portable.executable)
        except OSError as exc:
            raise ConfigError(
                "Provider sync succeeded, but Portable Codex could not launch: "
                f"{exc}"
            ) from exc
    else:
        _emit(progress, "launch", f"launching official Codex package {package.aumid}")
        try:
            launch_official(package)
        except OSError as exc:
            raise ConfigError(
                "Provider sync succeeded, but the Official Codex app could not launch: "
                f"{exc}"
            ) from exc
    _emit(progress, "completion", f"{mode.title()} Codex synced and launched")
    return WindowsModeResult(
        profile,
        mode,
        True,
        f"{mode.title()} Codex synced and launched with the shared codexier profile.",
    )
