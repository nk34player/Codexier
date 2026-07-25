from pathlib import Path


def test_launcher_files_exist_and_reference_root_main():
    root = Path(__file__).parents[1]
    command = (root / "codexier.command").read_text()
    batch = (root / "codexier.bat").read_text()
    assert "main.py" in command
    assert "main.py" in batch
    assert "cd" in command
    assert "cd /d" in batch.lower()
