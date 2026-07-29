#!/usr/bin/env python3
"""Install the custom model-provider picker patch into ChatGPT.app on macOS.

The patch is intentionally version-sensitive: it only edits JavaScript bundles
whose expected source hunks match exactly. App updates that change those bundles
cause a clean failure before the installed app is modified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import plistlib
import re
import signal
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
from typing import Any, NoReturn

try:
    from .patch_progress import PatchProgress, report
except ImportError:  # Support running this installer directly as a script.
    from patch_progress import PatchProgress, report

try:
    from .backup import immutable_file_backup
except ImportError:  # Support running this installer directly as a script.
    from backup import immutable_file_backup

try:
    import pwd
except ImportError:  # Windows imports the shared source-validation helpers.
    pwd = None


from .desktop_macos_diffs import *  # noqa: F401,F403
from .desktop_macos_diffs import PATCH_MARKER, ASAR_PACKAGE  # noqa: F401


def colors_enabled(stream: Any = sys.stdout) -> bool:
    return "NO_COLOR" not in os.environ and (
        getattr(stream, "isatty", lambda: False)()
        or os.environ.get("FORCE_COLOR") not in (None, "", "0")
    )


def color(text: object, *codes: str, stream: Any = sys.stdout) -> str:
    rendered = str(text)
    if not colors_enabled(stream) or not codes:
        return rendered
    return f"\033[{';'.join(codes)}m{rendered}\033[0m"


def terminal_width() -> int:
    return max(64, min(shutil.get_terminal_size((96, 24)).columns, 110))


def terminal_status(
    label: str,
    message: object,
    code: str,
    *,
    detail: object | None = None,
    stream: Any = sys.stdout,
) -> None:
    badge_width = 10
    plain_badge = f"[{label}]"
    badge = color(plain_badge, "1", code, stream=stream)
    badge_padding = " " * max(1, badge_width - len(plain_badge))
    available = max(30, terminal_width() - badge_width)
    lines = textwrap.wrap(
        str(message),
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    print(f"{badge}{badge_padding}{lines[0]}", file=stream)
    for line in lines[1:]:
        print(f"{'':{badge_width}}{line}", file=stream)
    if detail is not None:
        detail_lines = textwrap.wrap(
            str(detail),
            width=max(30, terminal_width() - badge_width - 2),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        for index, line in enumerate(detail_lines):
            marker = "↳ " if index == 0 else "  "
            print(
                f"{'':{badge_width}}{color(marker + line, '2', stream=stream)}",
                file=stream,
            )
    stream.flush()


def terminal_heading(title: str, code: str = "36") -> None:
    visible_title = f" {title.upper()} "
    rule_length = max(2, terminal_width() - len(visible_title))
    print()
    print(
        color(f"{visible_title}{'━' * rule_length}", "1", code),
    )
    sys.stdout.flush()


def terminal_panel(
    title: str,
    message: object,
    code: str,
    *,
    stream: Any = sys.stderr,
) -> None:
    width = terminal_width()
    title_text = f" {title.upper()} "
    top = f"╭─{title_text}{'─' * max(1, width - len(title_text) - 2)}"
    bottom = f"╰{'─' * (width - 1)}"
    print(file=stream)
    print(color(top, "1", code, stream=stream), file=stream)
    paragraphs = str(message).splitlines() or [""]
    for paragraph in paragraphs:
        wrapped = textwrap.wrap(
            paragraph,
            width=max(30, width - 4),
            break_long_words=False,
            break_on_hyphens=False,
        ) or [""]
        for line in wrapped:
            border = color("│", code, stream=stream)
            print(f"{border} {color(line, '1', stream=stream)}", file=stream)
    print(color(bottom, "1", code, stream=stream), file=stream)
    print(file=stream)
    stream.flush()


def terminal_bullet(label: str, description: str) -> None:
    bullet = color("◆", "1", "36")
    key = color(label, "1", "33")
    prefix_width = 29
    prefix = f"  {bullet} {key}"
    padding = " " * max(1, prefix_width - 4 - len(label))
    available = max(30, terminal_width() - prefix_width)
    lines = textwrap.wrap(
        description,
        width=available,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]
    print(f"{prefix}{padding}{lines[0]}")
    for line in lines[1:]:
        print(f"{'':{prefix_width}}{line}")
    sys.stdout.flush()


def print_completion_summary(
    config: Path,
    *,
    backup: Path | None = None,
    already_installed: bool = False,
    upgraded: bool = False,
) -> None:
    codex_config = config.parent / "config.toml"
    if already_installed:
        terminal_status(
            "READY",
            "Patch already installed; no app files were changed.",
            "32",
        )
    else:
        terminal_status(
            "SUCCESS",
            "Patch upgraded successfully."
            if upgraded
            else "Patch installed successfully.",
            "32",
        )

    terminal_heading("Custom provider config")
    terminal_status("CONFIG", "Codexier manages this provider/model config:", "36", detail=config)
    terminal_bullet("providers", "Enabled providers displayed in the app menu.")
    terminal_bullet(
        "providers[].models",
        "Models shown only after their provider is selected.",
    )
    terminal_status(
        "LINK",
        "Each custom provider ID maps to Codexier's internal route in config.toml.",
        "35",
        detail=codex_config,
    )
    terminal_status(
        "KEYS",
        "Do not put API keys in the provider-routing JSON file.",
        "33",
        detail="Keep credentials in the provider authentication configuration or environment.",
    )

    terminal_heading("After editing", "35")
    terminal_status(
        "RELOAD",
        "Use Codexier to sync changes, then close and reopen the model/provider menu.",
        "35",
        detail="No repatching or app restart is needed.",
    )

    if backup is not None:
        terminal_heading("Recovery", "34")
        terminal_status("BACKUP", "Complete original app backup:", "34", detail=backup)

    terminal_heading("Important", "33")
    terminal_status(
        "NOTICE",
        "The app now has an ad-hoc signature. A ChatGPT update may replace this patch.",
        "33",
    )
    print()


def fail(message: str, exit_code: int = 1) -> NoReturn:
    terminal_panel("Error", message, "31", stream=sys.stderr)
    raise SystemExit(exit_code)


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    label: str | None = None,
    terminal: bool = True,
) -> subprocess.CompletedProcess[str]:
    if terminal:
        terminal_status(
            "STEP",
            label or f"Running {Path(command[0]).name}",
            "36",
            detail=shlex.join(command),
        )
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        output = exc.stdout.strip() if exc.stdout else ""
        if output and terminal:
            terminal_panel("Command output", output, "31", stream=sys.stderr)
        step = label or f"Running {Path(command[0]).name}"
        message = f"{step} failed with exit status {exc.returncode}."
        if output:
            message += f" Diagnostic output: {output[-2000:]}"
        else:
            message += (
                " The command produced no diagnostic output. Verify Node.js/npm, "
                "close Codex/ChatGPT and retry; if it exits unexpectedly on Windows, "
                "check Event Viewer → Windows Logs → Application."
            )
        raise PatchError(message) from exc


class FancyArgumentParser(argparse.ArgumentParser):
    def _print_message(self, message: str, file: Any = None) -> None:
        if not message:
            return
        stream = file or sys.stdout
        width = terminal_width()
        title = " COMMAND HELP "
        top = f"╭─{title}{'─' * max(1, width - len(title) - 2)}"
        bottom = f"╰{'─' * (width - 1)}"
        print(file=stream)
        print(color(top, "1", "36", stream=stream), file=stream)
        for raw_line in message.rstrip().splitlines():
            border = color("│", "36", stream=stream)
            stripped = raw_line.strip()
            if not stripped:
                print(border, file=stream)
                continue
            if raw_line.startswith("usage:"):
                label, remainder = raw_line.split(":", 1)
                rendered = (
                    color(label.upper(), "1", "35", stream=stream)
                    + color(":", "35", stream=stream)
                    + color(remainder, "1", stream=stream)
                )
            elif stripped in {"options:", "optional arguments:"}:
                rendered = color(stripped.upper(), "1", "36", stream=stream)
            elif raw_line.startswith("  -"):
                option_and_help = re.split(r"(\s{2,})", stripped, maxsplit=1)
                option = option_and_help[0]
                remainder = "".join(option_and_help[1:])
                rendered = (
                    "  "
                    + color(option, "1", "33", stream=stream)
                    + color(remainder, stream=stream)
                )
            else:
                rendered = color(raw_line, stream=stream)
            print(f"{border} {rendered}", file=stream)
        print(color(bottom, "1", "36", stream=stream), file=stream)
        print(file=stream)
        stream.flush()

    def error(self, message: str) -> NoReturn:
        terminal_panel("Argument error", message, "31", stream=sys.stderr)
        terminal_status(
            "HELP",
            "Show all installer options with:",
            "33",
            detail=f"{self.prog} --help",
            stream=sys.stderr,
        )
        self.exit(2)


def invoking_user_home() -> Path:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root" and pwd is not None:
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


def parse_args() -> argparse.Namespace:
    home = invoking_user_home()
    configured_codex_home = os.environ.get("CODEX_HOME")
    codex_home = (
        Path(configured_codex_home).expanduser()
        if configured_codex_home
        else home / ".codex"
    )
    parser = FancyArgumentParser(
        description=(
            "Add a dynamic provider selector and per-model provider routing to the "
            "macOS ChatGPT/Codex desktop app."
        )
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=Path("/Applications/ChatGPT.app"),
        help="ChatGPT.app to patch (default: /Applications/ChatGPT.app)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=codex_home / "desktop-model-providers.json",
        help="Provider-routing JSON file in the effective Codex home",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=home / "Applications" / "ChatGPT Patch Backups",
        help="Directory in which a complete app backup is created",
    )
    parser.add_argument(
        "--overwrite-config",
        action="store_true",
        help="Replace the provider-routing JSON with the built-in template",
    )
    return parser.parse_args()


def validate_provider_config(data: Any) -> None:
    if not isinstance(data, dict):
        raise PatchError("Provider config must be a JSON object")
    if data.get("version") != 2:
        raise PatchError("Provider config version must be 2")
    providers = data.get("providers")
    if not isinstance(providers, list) or not providers:
        raise PatchError("Provider config 'providers' must be a non-empty array")

    provider_ids: set[str] = set()
    for provider in providers:
        if not isinstance(provider, dict):
            raise PatchError("Every provider must be an object")
        provider_id = provider.get("id")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise PatchError("Every provider id must be a non-empty string")
        provider_id = provider_id.strip()
        if provider_id in provider_ids:
            raise PatchError(f"Duplicate provider id: {provider_id}")
        provider_ids.add(provider_id)
        label = provider.get("label")
        if not isinstance(label, str) or not label.strip():
            raise PatchError(f"Provider '{provider_id}' needs a non-empty label")
        description = provider.get("description", "")
        if not isinstance(description, str):
            raise PatchError(f"Provider '{provider_id}' description must be a string")
        models = provider.get("models")
        if not isinstance(models, list):
            raise PatchError(f"Provider '{provider_id}' models must be an array")
        model_ids: set[str] = set()
        for model in models:
            if not isinstance(model, dict):
                raise PatchError(
                    f"Every model for provider '{provider_id}' must be an object"
                )
            model_id = model.get("id")
            if not isinstance(model_id, str) or not model_id.strip():
                raise PatchError(
                    f"Every model for provider '{provider_id}' needs a non-empty id"
                )
            model_id = model_id.strip()
            if model_id in model_ids:
                raise PatchError(
                    f"Provider '{provider_id}' has a duplicate model id: {model_id}"
                )
            model_ids.add(model_id)
            label = model.get("label")
            if not isinstance(label, str) or not label.strip():
                raise PatchError(
                    f"Model '{model_id}' for provider '{provider_id}' "
                    "needs a non-empty label"
                )

    default_provider = data.get("default_provider")
    if default_provider not in provider_ids:
        raise PatchError("default_provider must reference a configured provider")

def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def ensure_provider_config(path: Path, overwrite: bool) -> str:
    if not path.exists() or path.stat().st_size == 0:
        raise PatchError(
            f"Provider routing config is missing: {path}. "
            "Sync at least one enabled provider before patching the desktop app."
        )
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PatchError(f"Cannot read valid JSON from {path}: {exc}") from exc
    validate_provider_config(data)
    return "kept"


def asar_header_hash(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            size_pickle = handle.read(8)
            if len(size_pickle) != 8:
                raise PatchError("ASAR archive is too short to contain a header")
            size_payload, header_pickle_size = struct.unpack("<II", size_pickle)
            if size_payload != 4 or header_pickle_size < 8:
                raise PatchError("ASAR archive has an invalid header-size pickle")

            header_pickle = handle.read(header_pickle_size)
            if len(header_pickle) != header_pickle_size:
                raise PatchError("ASAR archive contains a truncated header")
    except OSError as exc:
        raise PatchError(f"Cannot read ASAR header from {path}: {exc}") from exc

    header_payload_size, header_string_size = struct.unpack("<II", header_pickle[:8])
    if header_payload_size > header_pickle_size - 4:
        raise PatchError("ASAR header payload size is invalid")
    header_start = 8
    header_end = header_start + header_string_size
    if header_end > len(header_pickle):
        raise PatchError("ASAR header string is truncated")

    header_json = header_pickle[header_start:header_end]
    try:
        json.loads(header_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PatchError("ASAR header does not contain valid UTF-8 JSON") from exc
    return hashlib.sha256(header_json).hexdigest()


def contains_marker(path: Path, marker: bytes = PATCH_MARKER) -> bool:
    overlap = len(marker) - 1
    previous = b""
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            data = previous + chunk
            if marker in data:
                return True
            previous = data[-overlap:] if overlap else b""
    return False


def contains_legacy_marker(path: Path) -> bool:
    return any(contains_marker(path, marker) for marker in LEGACY_PATCH_MARKERS)


def load_plist(path: Path) -> tuple[dict[str, Any], plistlib.PlistFormat]:
    raw = path.read_bytes()
    plist_format = plistlib.FMT_BINARY if raw.startswith(b"bplist00") else plistlib.FMT_XML
    try:
        data = plistlib.loads(raw)
    except Exception as exc:
        raise PatchError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PatchError(f"Unexpected plist root in {path}")
    return data, plist_format


def asar_integrity_hash(plist: dict[str, Any]) -> str:
    try:
        value = plist["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"]
    except (KeyError, TypeError) as exc:
        raise PatchError("Info.plist has no Electron ASAR integrity entry") from exc
    if not isinstance(value, str):
        raise PatchError("Electron ASAR integrity hash is not a string")
    return value.lower()


def app_path_variants(app: Path) -> set[str]:
    variants = {str(app), str(app.resolve())}
    for value in tuple(variants):
        if value.startswith("/private/tmp/") or value.startswith("/private/var/"):
            variants.add(value[len("/private") :])
        elif value.startswith("/tmp/") or value.startswith("/var/"):
            variants.add(f"/private{value}")
    return variants


def find_target_app_processes(app: Path) -> list[tuple[int, str]]:
    prefixes = tuple(f"{variant.rstrip('/')}/" for variant in app_path_variants(app))
    try:
        result = subprocess.run(
            ["/bin/ps", "-ww", "-axo", "pid=,command="],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PatchError(f"Could not inspect running processes: {exc}") from exc

    matches: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        parsed = re.match(r"\s*(\d+)\s+(.+)", line)
        if parsed is None:
            continue
        pid = int(parsed.group(1))
        command = parsed.group(2)
        if pid != os.getpid() and command.startswith(prefixes):
            matches.append((pid, command))
    return matches


def wait_for_app_processes_to_exit(app: Path, timeout: float) -> list[tuple[int, str]]:
    deadline = time.monotonic() + timeout
    remaining = find_target_app_processes(app)
    while remaining and time.monotonic() < deadline:
        time.sleep(0.2)
        remaining = find_target_app_processes(app)
    return remaining


def stop_target_app_processes(app: Path, allow_running: bool) -> None:
    """Compatibility wrapper that no longer force-closes desktop processes."""
    if allow_running:
        raise PatchSkipped(
            "Patching while ChatGPT is running is no longer supported. "
            "Close it normally, then run the patch again."
        )
    gracefully_close_target_app_processes(app)


def gracefully_close_target_app_processes(app: Path, *, force: bool = False) -> bool:
    """Ask the app to quit and skip safely if it remains open.

    Unlike the standalone legacy installer, automatic sync never sends a
    signal or force-kills a desktop process.  This protects unsaved work and
    keeps the app archive untouched when a graceful close is not possible.
    """
    processes = find_target_app_processes(app)
    if not processes:
        terminal_status("PROCESS", "The target ChatGPT app is not running.", "32", detail=app)
        return False

    pid_summary = ", ".join(str(pid) for pid, _command in processes)
    terminal_status(
        "CLOSE",
        "Requesting a graceful close of the target ChatGPT app.",
        "35",
        detail=f"PIDs: {pid_summary}",
    )
    escaped = str(app).replace("\\", "\\\\").replace('"', '\\"')
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'tell application (POSIX file "{escaped}" as alias) to quit',
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise PatchSkipped(
            f"Desktop patch skipped: could not request a graceful app close ({exc}). "
            "Close ChatGPT manually, then sync enabled providers again."
        ) from exc

    remaining = wait_for_app_processes_to_exit(app, 8.0)
    if remaining:
        details = ", ".join(str(pid) for pid, _command in remaining)
        if force:
            terminal_status(
                "FORCE CLOSE",
                "Forcefully terminating the remaining ChatGPT processes.",
                "31",
                detail=f"PIDs: {details}",
            )
            for pid, _command in remaining:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    continue
                except OSError as exc:
                    raise PatchSkipped(
                        f"Desktop patch skipped: could not force-close PID {pid} ({exc})."
                    ) from exc
            remaining = wait_for_app_processes_to_exit(app, 3.0)
            if not remaining:
                terminal_status("CLOSED", "The target ChatGPT app was forcefully closed.", "32")
                return True
        raise PatchSkipped(
            "Desktop patch skipped: ChatGPT is still running "
            f"(PIDs: {details}). Close it normally, then sync enabled providers again."
        )
    terminal_status("CLOSED", "The target ChatGPT app closed gracefully.", "32")
    return True


def unique_candidate(
    assets: Path,
    content_needles: tuple[str, ...],
    role: str,
) -> Path:
    candidates = sorted(
        path
        for path in assets.glob("*.js")
        if not path.name.endswith(".map.js")
    )
    matches = []
    for path in candidates:
        source = path.read_text(encoding="utf-8")
        if all(needle in source for needle in content_needles):
            matches.append(path)
    if len(matches) != 1:
        raise PatchError(
            f"Expected exactly one {role} JavaScript bundle containing all "
            f"required source markers, found {len(matches)} among "
            f"{len(candidates)} JavaScript bundles"
        )
    return matches[0]

def make_backup(
    app: Path, backup_dir: Path, version: str, build: str, *, automatic: bool = False
) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    if automatic:
        for previous in backup_dir.glob("automatic-ChatGPT-*.app"):
            if previous.is_dir() and not previous.is_symlink():
                shutil.rmtree(previous)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version)
    safe_build = re.sub(r"[^A-Za-z0-9._-]+", "-", build)
    prefix = "automatic-" if automatic else ""
    backup = backup_dir / (
        f"{prefix}ChatGPT-{safe_version}-build-{safe_build}-{timestamp}.app"
    )
    suffix = 1
    while backup.exists():
        backup = backup_dir / (
            f"ChatGPT-{safe_version}-build-{safe_build}-{timestamp}-{suffix}.app"
        )
        suffix += 1
    run(
        ["/usr/bin/ditto", str(app), str(backup)],
        label="Creating a complete app backup",
    )
    if not (backup / "Contents" / "Resources" / "app.asar").is_file():
        raise PatchError(f"Backup verification failed: {backup}")
    return backup


def atomic_replace_file(source: Path, target: Path) -> None:
    original_stat = target.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.patch-", dir=target.parent)
    os.close(fd)
    temporary_path = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary_path)
        os.chmod(temporary_path, original_stat.st_mode)
        if os.geteuid() == 0:
            os.chown(temporary_path, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def restore_backup(app: Path, backup: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    failed_copy = app.with_name(f"{app.stem}.patch-failed-{timestamp}.app")
    suffix = 1
    while failed_copy.exists():
        failed_copy = app.with_name(
            f"{app.stem}.patch-failed-{timestamp}-{suffix}.app"
        )
        suffix += 1
    os.replace(app, failed_copy)
    try:
        run(
            ["/usr/bin/ditto", str(backup), str(app)],
            label="Restoring the original app from backup",
        )
    except Exception:
        os.replace(failed_copy, app)
        raise
    return failed_copy


def latest_original_backup(app: Path, backup_dir: Path) -> Path:
    """Find the newest verified, unpatched backup for this app build."""
    info, _ = load_plist(app / "Contents" / "Info.plist")
    version = re.sub(r"[^A-Za-z0-9._-]+", "-", str(info.get("CFBundleShortVersionString", "unknown")))
    build = re.sub(r"[^A-Za-z0-9._-]+", "-", str(info.get("CFBundleVersion", "unknown")))
    prefix = f"ChatGPT-{version}-build-{build}-"
    backups = sorted(
        (path for path in backup_dir.glob(f"{prefix}*.app") if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for backup in backups:
        archive = backup / "Contents" / "Resources" / "app.asar"
        if archive.is_file() and not contains_marker(archive):
            return backup
    raise PatchError(
        f"No verified original backup for ChatGPT {version}, build {build} was found in {backup_dir}."
    )


def restore_original_app(app: Path, backup: Path, progress: PatchProgress | None = None) -> Path:
    """Restore a full verified app backup without touching Codex configuration or sessions."""
    backup_asar = backup / "Contents" / "Resources" / "app.asar"
    backup_info = backup / "Contents" / "Info.plist"
    if not backup_asar.is_file() or not backup_info.is_file() or contains_marker(backup_asar):
        raise PatchError(f"Backup is not a verified original ChatGPT app: {backup}")
    report(progress, "process stop", "closing the patched desktop app before restore")
    gracefully_close_target_app_processes(app)
    report(progress, "atomic replacement", "restoring the original desktop app from backup")
    failed_copy = restore_backup(app, backup)
    restored_asar = app / "Contents" / "Resources" / "app.asar"
    restored_info = app / "Contents" / "Info.plist"
    report(progress, "verification", "verifying the restored original desktop app")
    if (
        not restored_asar.is_file()
        or restored_asar.read_bytes() != backup_asar.read_bytes()
        or not restored_info.is_file()
        or restored_info.read_bytes() != backup_info.read_bytes()
        or contains_marker(restored_asar)
    ):
        raise PatchError(f"Restored app does not match the verified original backup: {backup}")
    return failed_copy


def restore_archive_backup(
    app: Path, backup: Path, progress: PatchProgress | None = None
) -> None:
    """Restore an immutable app.asar.bak and re-sign its matching app bundle."""
    asar_path = app / "Contents" / "Resources" / "app.asar"
    info_path = app / "Contents" / "Info.plist"
    if not backup.is_file() or contains_marker(backup) or contains_legacy_marker(backup):
        raise PatchError(f"Backup is not an original app.asar archive: {backup}")
    original_asar = asar_path.read_bytes()
    original_info = info_path.read_bytes()
    report(progress, "process stop", "closing the desktop app before restore")
    gracefully_close_target_app_processes(app)
    try:
        report(progress, "atomic replacement", "restoring app.asar from immutable backup")
        atomic_replace_file(backup, asar_path)
        info, plist_format = load_plist(info_path)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = asar_header_hash(backup)
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            plistlib.dump(info, temporary, fmt=plist_format, sort_keys=False)
            plist_path = Path(temporary.name)
        try:
            atomic_replace_file(plist_path, info_path)
        finally:
            plist_path.unlink(missing_ok=True)
        run(
            ["/usr/bin/codesign", "--deep", "--force", "--sign", "-", str(app)],
            label="Applying the ad-hoc app signature",
        )
        report(progress, "verification", "verifying restored archive and application signature")
        final_info, _ = load_plist(info_path)
        if (
            asar_path.read_bytes() != backup.read_bytes()
            or asar_header_hash(asar_path) != asar_integrity_hash(final_info)
            or contains_marker(asar_path)
            or contains_legacy_marker(asar_path)
        ):
            raise PatchError("Restored app does not match its immutable original backup")
    except Exception:
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temporary.write(original_asar)
            asar_restore = Path(temporary.name)
        with tempfile.NamedTemporaryFile(delete=False) as temporary:
            temporary.write(original_info)
            info_restore = Path(temporary.name)
        try:
            atomic_replace_file(asar_restore, asar_path)
            atomic_replace_file(info_restore, info_path)
        finally:
            asar_restore.unlink(missing_ok=True)
            info_restore.unlink(missing_ok=True)
        raise


@contextmanager
def restore_app_after_failure(
    app: Path, backup: Path, progress: PatchProgress | None
) -> Any:
    """Restore and verify the full app bundle after every post-backup failure."""
    try:
        yield
    except Exception:
        report(progress, "atomic replacement", "rolling back from the verified backup")
        terminal_status(
            "RECOVERY",
            "Patching failed after backup. Restoring the original app.",
            "33",
            stream=sys.stderr,
        )
        try:
            failed_copy = restore_backup(app, backup)
            report(progress, "verification", "verifying the restored original application")
            backup_asar = backup / "Contents" / "Resources" / "app.asar"
            restored_asar = app / "Contents" / "Resources" / "app.asar"
            if backup_asar.read_bytes() != restored_asar.read_bytes():
                raise PatchError("Restored app.asar does not match the verified backup")
            terminal_status(
                "RESTORED",
                "The original app was restored. The failed patched copy was retained.",
                "32",
                detail=failed_copy,
                stream=sys.stderr,
            )
        except Exception as restore_exc:
            terminal_panel(
                "Recovery failed",
                f"Automatic restoration failed: {restore_exc}\n"
                f"The full backup remains at: {backup}",
                "31",
                stream=sys.stderr,
            )
            raise PatchError(
                f"Patch failed and automatic restoration failed; backup remains at: {backup}"
            ) from restore_exc
        raise


def patch_app(
    app: Path,
    config: Path,
    backup_dir: Path,
    overwrite_config: bool,
    progress: PatchProgress | None = None,
) -> None:
    info_path = app / "Contents" / "Info.plist"
    resources = app / "Contents" / "Resources"
    asar_path = resources / "app.asar"
    unpacked_path = resources / "app.asar.unpacked"

    if sys.platform != "darwin":
        raise PatchError("This installer only supports macOS")
    if not app.is_dir() or not info_path.is_file() or not asar_path.is_file():
        raise PatchError(f"Not a supported ChatGPT app bundle: {app}")
    if not unpacked_path.is_dir():
        raise PatchError(f"Missing ASAR companion directory: {unpacked_path}")
    if shutil.which("npx") is None:
        raise PatchError("npx is required. Install Node.js, then run this installer again")

    config_action = ensure_provider_config(config, overwrite_config)
    terminal_status(
        "CONFIG",
        "Provider-routing config created."
        if config_action == "written"
        else "Existing provider-routing config validated.",
        "36",
        detail=config,
    )

    info, plist_format = load_plist(info_path)
    version = str(info.get("CFBundleShortVersionString", "unknown"))
    build = str(info.get("CFBundleVersion", "unknown"))
    sidecar = asar_path.with_name(f"{asar_path.name}.bak")
    if contains_marker(asar_path) or contains_legacy_marker(asar_path):
        if not sidecar.is_file() or contains_marker(sidecar) or contains_legacy_marker(sidecar):
            raise PatchError(
                "Cannot apply new patches to an already patched app without a "
                f"verified immutable original backup at: {sidecar}"
            )
        terminal_status(
            "RESTORE",
            "Restoring the immutable original app before applying new patches.",
            "34",
            detail=sidecar,
        )
        restore_archive_backup(app, sidecar, progress)
        info, plist_format = load_plist(info_path)

    sidecar, created = immutable_file_backup(asar_path)
    if contains_marker(sidecar) or contains_legacy_marker(sidecar):
        raise PatchError(f"Immutable backup is patched and cannot be used: {sidecar}")
    asar_header_hash(sidecar)
    report(
        progress,
        "backup",
        f"{'created' if created else 'reused'} immutable original backup: {sidecar}",
    )

    current_header_hash = asar_header_hash(asar_path)
    expected_header_hash = asar_integrity_hash(info)
    if current_header_hash != expected_header_hash:
        raise PatchError(
            "The ASAR header does not match the current app's Info.plist integrity "
            "metadata. The bundle may be incomplete or modified."
        )
    terminal_status(
        "VERIFY",
        "The original app's ASAR header integrity is valid.",
        "32",
        detail=current_header_hash,
    )

    terminal_heading("Installation", "35")
    terminal_status(
        "APP",
        f"Preparing ChatGPT {version}, build {build}.",
        "34",
        detail=app,
    )
    report(progress, "backup", "creating a verified backup of the desktop app")
    backup = make_backup(app, backup_dir, version, build, automatic=True)
    terminal_status("OK", "App backup created.", "32", detail=backup)
    with restore_app_after_failure(
        app, backup, progress
    ), tempfile.TemporaryDirectory(prefix="chatgpt-provider-patch-") as temporary:
        work = Path(temporary)
        extracted = work / "app"
        patched_asar = work / "app.asar"
        patched_plist = work / "Info.plist"

        report(progress, "extraction", "extracting application resources")
        run(
            ["npx", "--yes", ASAR_PACKAGE, "extract", str(asar_path), str(extracted)],
            label="Extracting application resources",
        )
        assets = extracted / "webview" / "assets"
        if not assets.is_dir():
            raise PatchError("Extracted app has no webview/assets directory")

        report(progress, "bundle matching", "matching the supported application bundle layout")
        central = unique_candidate(
            assets,
            ("async prewarmThreadStart(",),
            "App Server client",
        )
        picker = unique_candidate(
            assets,
            ("modelOptionsDisabled:m",),
            "model picker",
        )

        report(progress, "source patch", "applying provider-first model routing")
        patch_layout = apply_supported_patch_variant(central, picker)
        terminal_status(
            "LAYOUT",
            "Matched a supported application bundle layout.",
            "32",
            detail=patch_layout,
        )

        if PATCH_MARKER.decode() not in central.read_text(encoding="utf-8"):
            raise PatchError("Routing marker missing after patch")
        if "CodexCustomProviderPickerSection" not in picker.read_text(encoding="utf-8"):
            raise PatchError("Provider picker missing after patch")

        report(progress, "repack", "repacking the patched application archive")
        run(
            ["npx", "--yes", ASAR_PACKAGE, "pack", str(extracted), str(patched_asar)],
            label="Packing patched application resources",
        )

        if not contains_marker(patched_asar):
            raise PatchError("Packed ASAR does not contain the patch marker")
        patched_header_hash = asar_header_hash(patched_asar)
        info["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"] = patched_header_hash
        with patched_plist.open("wb") as handle:
            plistlib.dump(info, handle, fmt=plist_format, sort_keys=False)

        try:
            report(progress, "atomic replacement", "atomically replacing the application archive")
            atomic_replace_file(patched_asar, asar_path)
            atomic_replace_file(patched_plist, info_path)
            run(
                ["/usr/bin/codesign", "--deep", "--force", "--sign", "-", str(app)],
                label="Applying the ad-hoc app signature",
            )
            run(
                [
                    "/usr/bin/codesign",
                    "--verify",
                    "--deep",
                    "--strict",
                    "--verbose=2",
                    str(app),
                ],
                label="Verifying the app signature",
            )

            report(progress, "verification", "verifying the installed archive and application signature")
            final_info, _ = load_plist(info_path)
            if asar_header_hash(asar_path) != asar_integrity_hash(final_info):
                raise PatchError("Installed ASAR integrity verification failed")
            if not contains_marker(asar_path):
                raise PatchError("Installed ASAR is missing the patch marker")
        except Exception:
            raise

    print_completion_summary(config, backup=backup, upgraded=False)


def main() -> int:
    args = parse_args()
    try:
        app = args.app.expanduser().resolve()
        gracefully_close_target_app_processes(app)
        patch_app(
            app,
            args.config.expanduser().resolve(),
            args.backup_dir.expanduser().resolve(),
            args.overwrite_config,
        )
    except PatchError as exc:
        fail(str(exc))
    except PermissionError as exc:
        fail(f"Permission denied: {exc}")
    except KeyboardInterrupt:
        fail("Interrupted", 130)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
