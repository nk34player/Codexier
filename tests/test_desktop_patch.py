from pathlib import Path

import pytest

from codexier.desktop_patch import (
    PATCH_MARKER,
    DesktopPatchTarget,
    apply_desktop_patch,
    backup_desktop_patch,
    default_target,
    patch_status,
    restore_desktop_patch,
)
from codexier.errors import ConfigError


def target(tmp_path: Path, content: bytes = b"original") -> DesktopPatchTarget:
    archive = tmp_path / "app.asar"
    archive.write_bytes(content)
    return DesktopPatchTarget("windows", archive)


def test_status_detects_installed_patch(tmp_path: Path):
    app = target(tmp_path, b"before" + PATCH_MARKER + b"after")
    assert patch_status(app).patched


def test_restore_uses_exact_backup(tmp_path: Path):
    app = target(tmp_path)
    backup = backup_desktop_patch(app, tmp_path / "backups")
    app.archive_path.write_bytes(b"patched")
    restore_desktop_patch(app, backup)
    assert app.archive_path.read_bytes() == b"original"
    assert not patch_status(app).patched


def test_windows_unpacked_override_is_discovered_without_posix_path_assumptions(
    tmp_path: Path, monkeypatch
):
    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir()
    archive.write_bytes(b"original")
    monkeypatch.setenv("CODEXIER_WINDOWS_APP_ASAR", str(archive))
    app = default_target("windows")
    assert app.archive_path == archive
    assert app.package_type == "unpackaged"
    assert patch_status(app).supported is False


def test_windows_msix_target_refuses_mutation_with_clear_message(tmp_path: Path):
    archive = tmp_path / "WindowsApps" / "OpenAI.Codex" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    app = DesktopPatchTarget("windows", archive, package_type="msix")
    assert "MSIX" in patch_status(app).message
    with pytest.raises(ConfigError, match="MSIX"):
        apply_desktop_patch(app, tmp_path / "backups")
