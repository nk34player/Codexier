from codexier.process_manager import CodexProcess, restart_codex


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
