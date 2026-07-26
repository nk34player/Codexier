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
    """Find the current user's Windows ChatGPT desktop processes.

    Detection is deliberately limited to the ChatGPT executable and the
    current user. This prevents a provider change from terminating unrelated
    processes or another user's ChatGPT session.
    """
    if (platform or sys.platform) != "win32":
        return ()

    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        "$items = @(Get-Process -Name 'ChatGPT' -IncludeUserName | "
        "Select-Object Id,Path,UserName); "
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

    current_user = getpass.getuser().casefold()
    processes: list[ChatGPTProcess] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item["Id"])
        except (KeyError, TypeError, ValueError):
            continue
        username = str(item.get("UserName") or "")
        owner = username.rsplit("\\", 1)[-1].casefold()
        executable = str(item.get("Path") or "")
        if owner != current_user or not executable:
            continue
        processes.append(ChatGPTProcess(pid, executable, username))
    return tuple(processes)


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
