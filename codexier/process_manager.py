from __future__ import annotations

import getpass
import os
import signal
import subprocess
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
    timeout_seconds: float = 5.0,
    terminate: Callable[[int, int], None] = os.kill,
    poll: Callable[[int], bool] | None = None,
    launch: Callable[[Sequence[str]], object] = subprocess.Popen,
) -> RestartResult:
    if not processes:
        return RestartResult(False, False, "No current-user Codex process detected.")
    if len(processes) != 1:
        return RestartResult(True, False, "Multiple Codex processes detected; restart skipped for safety.")
    process = processes[0]
    if not process.executable or not process.argv:
        return RestartResult(True, False, "Codex launch command unavailable; restart skipped.")
    try:
        terminate(process.pid, signal.SIGTERM)
        if poll:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline and poll(process.pid):
                time.sleep(0.05)
        launch(process.argv)
    except (OSError, ValueError) as exc:
        return RestartResult(True, False, f"Codex restart failed: {type(exc).__name__}.")
    return RestartResult(True, True, "Codex restarted successfully.")
