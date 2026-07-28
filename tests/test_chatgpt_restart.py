import json
import subprocess

from codexier.process_manager import (
    ChatGPTProcess,
    detect_chatgpt_processes,
    restart_chatgpt,
)


def test_chatgpt_detection_is_windows_only():
    assert detect_chatgpt_processes(platform="linux") == ()


def test_chatgpt_detection_uses_current_session_without_elevation(monkeypatch):
    monkeypatch.setattr("codexier.process_manager.getpass.getuser", lambda: "me")
    output = json.dumps(
        [
            {"Id": 10, "Path": r"C:\Apps\ChatGPT.exe", "ProcessName": "ChatGPT"},
            {"Id": 12, "Path": r"C:\Apps\Codex.exe", "ProcessName": "Codex"},
            {"Id": 13, "Path": r"C:\Apps\Other.exe", "ProcessName": "Other"},
        ]
    )

    def run(*args, **kwargs):
        command = args[0][-1]
        assert "'ChatGPT','Codex'" in command
        assert "IncludeUserName" not in command
        assert "SessionId" in command
        return subprocess.CompletedProcess(args[0], 0, output, "")

    assert detect_chatgpt_processes(platform="win32", run=run) == (
        ChatGPTProcess(10, r"C:\Apps\ChatGPT.exe", "me"),
        ChatGPTProcess(12, r"C:\Apps\Codex.exe", "me"),
    )


def test_chatgpt_restart_is_noop_when_not_running():
    result = restart_chatgpt(())
    assert result.detected is False
    assert result.restarted is False


def test_chatgpt_restart_gracefully_closes_then_launches():
    calls = []
    process = ChatGPTProcess(10, r"C:\Apps\ChatGPT.exe", r"PC\me")
    result = restart_chatgpt(
        (process,),
        terminate=lambda pid: calls.append(("terminate", pid)),
        launch=lambda argv: calls.append(("launch", tuple(argv))),
    )
    assert result.restarted is True
    assert calls == [
        ("terminate", 10),
        ("launch", (r"C:\Apps\ChatGPT.exe",)),
    ]
