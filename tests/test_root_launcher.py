import os
from pathlib import Path

from codexier.main import (
    ROOT,
    VENV,
    clear_caches,
    dependency_check_command,
    environment_python,
    main,
    needs_bootstrap,
    script_command,
)


def test_launcher_uses_root_virtual_environment():
    project_root = Path(__file__).parents[1]
    assert ROOT == project_root
    assert VENV == project_root / ".venv"


def test_environment_python_points_inside_project_venv(tmp_path: Path):
    if os.name == "nt":
        expected = tmp_path / ".venv" / "Scripts" / "python.exe"
    else:
        expected = tmp_path / ".venv" / "bin" / "python"
    assert environment_python(tmp_path) == expected


def test_needs_bootstrap_when_venv_python_missing(tmp_path: Path):
    assert needs_bootstrap(tmp_path) is True


def test_needs_bootstrap_false_when_venv_python_exists(tmp_path: Path):
    python_path = environment_python(tmp_path)
    python_path.parent.mkdir(parents=True)
    python_path.touch()
    assert needs_bootstrap(tmp_path) is False


def test_dependency_check_command_checks_runtime_dependencies():
    command = dependency_check_command()
    assert command[:2] == ["-c", "import httpx, rich, textual, tomli_w"]


def test_script_command_uses_project_python(tmp_path: Path):
    command = script_command(tmp_path / ".venv/bin/python", tmp_path / "tools.py", ("--demo",))
    assert command == [str(tmp_path / ".venv/bin/python"), str(tmp_path / "tools.py"), "--demo"]


def test_clear_caches_removes_python_and_tool_cache_directories(tmp_path: Path):
    keep = tmp_path / "keep.txt"
    keep.write_text("keep")
    cache_dirs = [
        tmp_path / ".venv" / "Lib" / "site-packages" / "pkg" / "__pycache__",
        tmp_path / "nested" / "__pycache__",
        tmp_path / ".pytest_cache",
        tmp_path / ".cache",
        tmp_path / "nested" / "cache",
    ]
    for directory in cache_dirs:
        directory.mkdir(parents=True)
        (directory / "data").write_text("cache")

    clear_caches(tmp_path)

    assert keep.is_file()
    assert all(not directory.exists() for directory in cache_dirs)


def test_clean_caches_requires_an_explicit_launcher_option(monkeypatch):
    calls = []
    monkeypatch.setattr("codexier.main.clear_caches", lambda: calls.append(True))

    assert main(("--clean-caches",)) == 0
    assert calls == [True]


def test_launcher_handles_ctrl_c_without_a_traceback(monkeypatch, capsys):
    def interrupted(_args):
        raise KeyboardInterrupt

    monkeypatch.setattr("codexier.main.run_codexier", interrupted)

    assert main(()) == 130
    assert capsys.readouterr().out.strip() == "codexier: cancelled."
