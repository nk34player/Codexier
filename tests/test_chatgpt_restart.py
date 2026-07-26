import json
import subprocess

from codexier.process_manager import (
    ChatGPTProcess,
    detect_chatgpt_processes,
    restart_chatgpt,
)


def test_chatgpt_detection_is_windows_only():
    assert detect_chatgpt_processes(platform="linux") == ()


def test_chatgpt_detection_filters_to_current_user(monkeypatch):
    monkeypatch.setattr("codexier.process_manager.getpass.getuser", lambda: "me")
    output = json.dumps(
        [
            {"Id": 10, "Path": r"C:\Apps\ChatGPT.exe", "UserName": r"PC\me"},
            {"Id": 11, "Path": r"C:\Apps\ChatGPT.exe", "UserName": r"PC\other"},
        ]
    )

    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, output, "")

    assert detect_chatgpt_processes(platform="win32", run=run) == (
        ChatGPTProcess(10, r"C:\Apps\ChatGPT.exe", r"PC\me"),
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
