import os
from pathlib import Path

from main import environment_python, needs_bootstrap, dependency_check_command, script_command


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
