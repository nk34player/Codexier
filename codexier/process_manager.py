from __future__ import annotations

import getpass
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Sequence


@dataclass(frozen=True)
class CodexProcess:
    pid: int
    executable: str
    argv: tuple[str, ...]
    username: str


@dataclass(frozen=True)
class RestartResult:
    detected: bool
    restarted: bool
    message: str


@dataclass(frozen=True)
class CloseResult:
    closed: bool
    message: str


@dataclass(frozen=True)
class ChatGPTProcess:
    pid: int
    executable: str
    username: str


def detect_codex_processes() -> tuple[CodexProcess, ...]:
    current_user = getpass.getuser()
    try:
        result = subprocess.run(
            ["ps", "-axo", "user=,pid=,command="],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ()
    processes: list[CodexProcess] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        username, pid_text, command = parts
        argv = tuple(command.split())
        if username != current_user or not any("codex" in part.lower() for part in argv):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        processes.append(CodexProcess(pid, argv[0], argv, username))
    return tuple(processes)


def restart_codex(
    processes: Sequence[CodexProcess],
    *,
    force: bool = False,
    timeout_seconds: float = 5.0,
    terminate: Callable[[int, int], None] = os.kill,
    poll: Callable[[int], bool] | None = None,
    launch: Callable[[Sequence[str]], object] = subprocess.Popen,
) -> RestartResult:
    if not processes:
        return RestartResult(False, False, "No current-user Codex process detected.")
    if len(processes) != 1 and not force:
        return RestartResult(True, False, "Multiple Codex processes detected; restart skipped for safety.")
    if any(not process.executable or not process.argv for process in processes):
        return RestartResult(True, False, "Codex launch command unavailable; restart skipped.")
    try:
        for process in processes:
            terminate(process.pid, signal.SIGTERM)
        if poll:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and any(poll(process.pid) for process in processes):
                time.sleep(0.05)
        # Relaunch one canonical Codex command. Multiple processes may be
        # helper/worker instances and launching every argv would duplicate them.
        launch(processes[0].argv)
    except (OSError, ValueError) as exc:
        return RestartResult(True, False, f"Codex restart failed: {type(exc).__name__}.")
    return RestartResult(True, True, "Codex restarted successfully.")


def detect_chatgpt_processes(
    *,
    platform: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[ChatGPTProcess, ...]:
    """Find ChatGPT or Codex desktop processes in this Windows session.

    ``Get-Process -IncludeUserName`` requires elevation on standard Windows
    desktops.  Use the current interactive session instead, so normal users
    can still close their own app windows without touching another session.
    """
    if (platform or sys.platform) != "win32":
        return ()

    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$session = (Get-Process -Id $PID).SessionId; "
        "$items = @(Get-Process -Name 'ChatGPT','Codex' | "
        "Where-Object { $_.SessionId -eq $session -and $null -ne $_.Path } | "
        "Select-Object Id,Path,ProcessName); "
        "$items | ConvertTo-Json -Compress"
    )
    try:
        result = run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = result.stdout.strip()
        if not payload:
            return ()
        items = json.loads(payload)
        if isinstance(items, dict):
            items = [items]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return ()

    current_user = getpass.getuser()
    processes: list[ChatGPTProcess] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item["Id"])
        except (KeyError, TypeError, ValueError):
            continue
        executable = str(item.get("Path") or "")
        executable_name = os.path.basename(executable.replace("\\", os.sep)).casefold()
        if executable_name not in {"chatgpt.exe", "codex.exe"}:
            continue
        processes.append(ChatGPTProcess(pid, executable, current_user))
    return tuple(processes)


def gracefully_close_chatgpt_processes(
    processes: Sequence[ChatGPTProcess],
    *,
    timeout_seconds: float = 8.0,
    request_close: Callable[[int], object] | None = None,
    poll: Callable[[int], bool] | None = None,
) -> CloseResult:
    """Request normal window closure and never force-terminate a desktop app."""
    if not processes:
        return CloseResult(True, "No current-user Codex desktop process is running.")

    def close_window(pid: int) -> object:
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    f"$p = Get-Process -Id {pid} -ErrorAction SilentlyContinue; "
                    "if ($null -ne $p) { [void]$p.CloseMainWindow() }"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def is_running(pid: int) -> bool:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout

    close = request_close or close_window
    check_running = poll or is_running
    try:
        for process in processes:
            close(process.pid)
        deadline = time.monotonic() + timeout_seconds
        pending = {process.pid for process in processes}
        while pending and time.monotonic() < deadline:
            pending = {pid for pid in pending if check_running(pid)}
            if pending:
                time.sleep(0.1)
    except OSError as exc:
        return CloseResult(False, f"Could not verify graceful app shutdown: {exc}")
    if pending:
        ids = ", ".join(str(pid) for pid in sorted(pending))
        return CloseResult(
            False,
            f"Codex is still running (PIDs: {ids}). Close it normally and try again.",
        )
    return CloseResult(True, "Codex desktop app closed normally.")


def restart_chatgpt(
    processes: Sequence[ChatGPTProcess],
    *,
    timeout_seconds: float = 8.0,
    terminate: Callable[[int], object] | None = None,
    poll: Callable[[int], bool] | None = None,
    launch: Callable[[Sequence[str]], object] = subprocess.Popen,
) -> RestartResult:
    """Gracefully close and reopen ChatGPT only when it was already running."""
    if not processes:
        return RestartResult(False, False, "No current-user ChatGPT process detected.")
    if any(not process.executable for process in processes):
        return RestartResult(True, False, "ChatGPT launch path unavailable; restart skipped.")

    def graceful_terminate(pid: int) -> object:
        return subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T"],
            check=False,
            capture_output=True,
            text=True,
        )

    def is_running(pid: int) -> bool:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/NH"],
            check=False,
            capture_output=True,
            text=True,
        )
        return str(pid) in result.stdout

    terminate_process = terminate or graceful_terminate
    # Tests and non-Windows callers may inject their own process callbacks;
    # the real tasklist probe is only meaningful on Windows.
    poll_process = poll or (is_running if os.name == "nt" else lambda _pid: False)
    try:
        for process in processes:
            terminate_process(process.pid)
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and any(
            poll_process(process.pid) for process in processes
        ):
            time.sleep(0.05)
        # Launch one executable even when the app has helper processes.
        launch([processes[0].executable])
    except (OSError, ValueError) as exc:
        return RestartResult(True, False, f"ChatGPT restart failed: {type(exc).__name__}.")
    return RestartResult(True, True, "ChatGPT restarted successfully.")
