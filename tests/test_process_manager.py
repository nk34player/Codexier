from codexier.process_manager import (
    ChatGPTProcess,
    CodexProcess,
    gracefully_close_chatgpt_processes,
    restart_codex,
)


def test_no_process():
    result = restart_codex(())
    assert result.detected is False
    assert result.restarted is False


def test_multiple_processes_are_not_restarted():
    process = CodexProcess(1, "/usr/bin/codex", ("/usr/bin/codex",), "me")
    result = restart_codex((process, process))
    assert result.restarted is False


def test_multiple_matching_processes_restart_when_explicitly_requested():
    calls = []
    first = CodexProcess(1, "/usr/bin/codex", ("/usr/bin/codex", "--serve"), "me")
    second = CodexProcess(2, "/usr/bin/codex", ("/usr/bin/codex", "--worker"), "me")
    result = restart_codex(
        (first, second),
        force=True,
        terminate=lambda pid, sig: calls.append((pid, sig)),
        launch=lambda argv: calls.append(tuple(argv)),
    )
    assert result.restarted is True
    assert calls[:2] == [(1, 15), (2, 15)]
    assert calls[2] == ("/usr/bin/codex", "--serve")


def test_unambiguous_process_restarts():
    calls = []
    process = CodexProcess(1, "/usr/bin/codex", ("/usr/bin/codex", "--serve"), "me")
    result = restart_codex(processes=(process,), terminate=lambda pid, sig: calls.append((pid, sig)), launch=lambda argv: calls.append(tuple(argv)))
    assert result.restarted is True
    assert len(calls) == 2


def test_desktop_close_force_terminates_a_process_left_in_the_tray():
    calls = []
    states = iter((True, False))
    result = gracefully_close_chatgpt_processes(
        (ChatGPTProcess(42, r"C:\Codex.exe", "me"),),
        timeout_seconds=0.01,
        request_close=lambda pid: calls.append(("close", pid)),
        terminate=lambda pid: calls.append(("terminate", pid)),
        poll=lambda _pid: next(states),
    )
    assert result.closed
    assert calls == [("close", 42), ("terminate", 42)]
