from pathlib import Path


def test_root_launchers_reference_package_launcher():
    root = Path(__file__).parents[1]
    assert not (root / "main.py").exists()
    command = (root / "codexier.command").read_text()
    batch = (root / "codexier.bat").read_text()
    linux = (root / "codexier.sh").read_text()
    for launcher in (command, batch, linux):
        assert "codexier/main.py" in launcher or r"codexier\main.py" in launcher
    assert "cd" in command
    assert "cd /d" in batch.lower()
    assert "python3" in linux
    assert "Press Enter to close" in command
    assert "pause" in batch.lower()
    assert "mode con: cols=160 lines=50" in batch.lower()
    assert "Press Enter to close" in linux
    assert "160t" in command
