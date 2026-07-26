"""Single-command codexier launcher.

Run through one of root launchers. It creates local virtual environment when
missing, installs project dependencies, then re-executes Codexier using that
environment's interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
RUNTIME_IMPORTS = "import httpx, rich, textual, tomli_w"
CACHE_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "cache",
    }
)


def environment_python(root: Path = ROOT) -> Path:
    """Return platform-specific Python path inside project .venv."""
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def needs_bootstrap(root: Path = ROOT) -> bool:
    return not environment_python(root).is_file()


def dependency_check_command() -> list[str]:
    return ["-c", RUNTIME_IMPORTS]


def dependencies_ready(python: Path) -> bool:
    result = subprocess.run(
        [str(python), *dependency_check_command()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def clear_caches(root: Path = ROOT) -> None:
    """Remove generated Python and tool caches below the project root.

    Cache cleanup is best-effort: a locked cache file must not prevent
    Codexier from starting. The project root is the cleanup boundary, so
    unrelated files outside this checkout are never touched.
    """
    root = root.resolve()
    try:
        candidates = sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_dir() and path.name in CACHE_DIRECTORY_NAMES
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
    except OSError:
        return
    for cache_dir in candidates:
        try:
            cache_dir.relative_to(root)
            shutil.rmtree(cache_dir, ignore_errors=True)
        except (OSError, ValueError):
            continue


def script_command(python: Path, script: Path, argv: Sequence[str]) -> list[str]:
    return [str(python), str(script), *argv]


def bootstrap(root: Path = ROOT) -> Path:
    python = environment_python(root)
    if not python.is_file():
        print("codexier: creating .venv …")
        venv.EnvBuilder(with_pip=True, clear=False).create(root / ".venv")
    python = environment_python(root)
    if not dependencies_ready(python):
        print("codexier: installing dependencies …")
        subprocess.run([str(python), "-m", "pip", "install", "-e", str(root)], check=True)
    return python


def run_codexier(argv: Sequence[str], root: Path = ROOT) -> int:
    python = environment_python(root)
    if needs_bootstrap(root) or not dependencies_ready(python):
        python = bootstrap(root)
    command = [str(python), "-m", "codexier", *argv]
    return subprocess.run(command, cwd=root).returncode


def run_script(script: Path, argv: Sequence[str], root: Path = ROOT) -> int:
    python = environment_python(root)
    if needs_bootstrap(root) or not dependencies_ready(python):
        python = bootstrap(root)
    return subprocess.run(script_command(python, script, argv), cwd=root).returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--clean-caches":
        clear_caches()
        return 0
    if args and args[0] == "--script":
        if len(args) < 2:
            print("Usage: launcher --script SCRIPT [ARGS ...]")
            return 2
        return run_script((ROOT / args[1]).resolve(), args[2:])
    return run_codexier(args)


if __name__ == "__main__":
    raise SystemExit(main())
