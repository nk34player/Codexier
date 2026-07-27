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
from codexier.desktop_patch_macos import PatchSkipped
from codexier.patch_progress import MILESTONES


def target(tmp_path: Path, content: bytes = b"original") -> DesktopPatchTarget:
    archive = tmp_path / "app.asar"
    archive.write_bytes(content)
    return DesktopPatchTarget("windows", archive)


def test_status_detects_installed_patch(tmp_path: Path):
    app = target(tmp_path, b"before" + PATCH_MARKER + b"after")
    assert patch_status(app).patched


def test_already_patched_archive_reports_completion_without_mutation(tmp_path: Path):
    app = target(tmp_path, b"before" + PATCH_MARKER + b"after")
    original = app.archive_path.read_bytes()
    events: list[tuple[int, str]] = []
    status = apply_desktop_patch(app, tmp_path / "backups", lambda *event: events.append(event))

    assert status.patched
    assert app.archive_path.read_bytes() == original
    assert events == [
        (MILESTONES["detection"], f"detection: inspecting {app.archive_path}"),
        (MILESTONES["completion"], "completion: already patched; app.asar was not modified"),
    ]


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
    assert patch_status(app).supported is True


def test_windows_unpacked_dispatches_verified_adapter(tmp_path: Path, monkeypatch):
    archive = tmp_path / "ChatGPT" / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "desktop-model-providers.json").write_text(
        '{"version": 2, "default_provider": "openai", "providers": ['
        '{"id": "openai", "label": "OpenAI", "description": "", "models": []}'
        ']}'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))

    def fake_patch(path, config, backup_root, progress=None):
        assert path == archive
        assert config == home / "desktop-model-providers.json"
        assert backup_root == tmp_path / "backups"
        assert progress is not None
        progress(MILESTONES["validation"], "validation: fake adapter validation")
        progress(MILESTONES["backup"], "backup: fake adapter backup")
        path.write_bytes(PATCH_MARKER)

    import codexier.desktop_patch_windows as windows

    monkeypatch.setattr(windows, "patch_windows_app", fake_patch)
    status = apply_desktop_patch(
        DesktopPatchTarget("windows", archive, package_type="unpackaged"),
        tmp_path / "backups",
    )
    assert status.patched


def test_windows_msix_target_is_safely_skipped_without_mutation(tmp_path: Path):
    archive = tmp_path / "WindowsApps" / "OpenAI.Codex" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    app = DesktopPatchTarget("windows", archive, package_type="msix")
    assert "MSIX" in patch_status(app).message
    events: list[tuple[int, str]] = []
    status = apply_desktop_patch(app, tmp_path / "backups", lambda *event: events.append(event))
    assert status.skipped
    assert "MSIX" in status.message
    assert archive.read_bytes() == b"original"
    assert [percent for percent, _detail in events] == [5, 100]


def test_windows_graceful_close_skip_leaves_archive_untouched(tmp_path: Path, monkeypatch):
    archive = tmp_path / "ChatGPT" / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "desktop-model-providers.json").write_text(
        '{"version": 2, "default_provider": "openai", "providers": '
        '[{"id": "openai", "label": "OpenAI", "description": "", "models": []}]}'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    import codexier.desktop_patch_windows as windows

    def skip_patch(*_args, **_kwargs):
        raise PatchSkipped("Desktop patch skipped: close ChatGPT normally.")

    monkeypatch.setattr(windows, "patch_windows_app", skip_patch)
    status = apply_desktop_patch(
        DesktopPatchTarget("windows", archive, package_type="unpackaged"),
        tmp_path / "backups",
    )
    assert status.skipped
    assert archive.read_bytes() == b"original"
    assert not (tmp_path / "backups").exists()


def test_macos_dispatches_verified_adapter_with_progress(tmp_path: Path, monkeypatch):
    archive = tmp_path / "ChatGPT.app" / "Contents" / "Resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"original")
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "desktop-model-providers.json").write_text("{}")
    monkeypatch.setenv("CODEX_HOME", str(home))
    import codexier.desktop_patch_macos as macos

    monkeypatch.setattr(macos, "find_target_app_processes", lambda _app: [])
    monkeypatch.setattr(macos, "gracefully_close_target_app_processes", lambda _app: False)

    def fake_patch(app, config, backup_root, overwrite_config, progress=None):
        assert config == home / "desktop-model-providers.json"
        assert progress is not None
        progress(MILESTONES["source patch"], "source patch: fake adapter patch")
        app.joinpath("Contents", "Resources", "app.asar").write_bytes(PATCH_MARKER)

    monkeypatch.setattr(macos, "patch_app", fake_patch)
    events: list[tuple[int, str]] = []
    status = apply_desktop_patch(
        DesktopPatchTarget("darwin", archive),
        tmp_path / "backups",
        lambda *event: events.append(event),
    )
    assert status.patched
    assert [percent for percent, _detail in events] == [5, 12, 20, 55, 93, 100]
