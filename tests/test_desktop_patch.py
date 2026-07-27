from pathlib import Path
import struct

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
from codexier.process_manager import ChatGPTProcess


def target(tmp_path: Path, content: bytes = b"original") -> DesktopPatchTarget:
    archive = tmp_path / "app.asar"
    archive.write_bytes(content)
    return DesktopPatchTarget("windows", archive)


def valid_asar(marker: bytes = b"") -> bytes:
    header = b"{}"
    pickle = struct.pack("<II", 4, len(header)) + header
    return struct.pack("<II", 4, len(pickle)) + pickle + marker


def running_process(root: Path, name: str = "ChatGPT.exe") -> ChatGPTProcess:
    return ChatGPTProcess(10, str(root / name), r"PC\me")


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


@pytest.mark.parametrize("layout", (("resources",), ("app", "resources")))
def test_windows_runtime_archive_is_derived_from_running_process(
    tmp_path: Path, monkeypatch, layout: tuple[str, ...]
):
    root = tmp_path / "Codex"
    archive = root.joinpath(*layout, "app.asar")
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar())
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root, "Codex.exe"),),
    )

    app = default_target("windows")

    assert app.archive_path == archive
    assert app.package_type == "unpackaged"
    assert patch_status(app).supported is True


def test_windows_no_running_runtime_is_safely_skipped(monkeypatch):
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes", lambda **_kwargs: ()
    )

    app = default_target("windows")

    assert app.package_type == "unavailable"
    assert patch_status(app).skipped
    assert "Open the unpackaged desktop app" in patch_status(app).message


def test_windows_multiple_running_roots_are_ambiguous(tmp_path: Path, monkeypatch):
    roots = (tmp_path / "ChatGPT", tmp_path / "Codex")
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: tuple(running_process(root) for root in roots),
    )

    status = patch_status(default_target("windows"))

    assert status.skipped
    assert "multiple running desktop installations" in status.message
    assert all(str(root) in status.message for root in roots)


def test_windows_msix_runtime_is_rejected_before_override_or_write_probe(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "WindowsApps" / "OpenAI.Codex_1.0"
    archive = root / "app" / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar())
    monkeypatch.setenv("CODEXIER_WINDOWS_APP_ASAR", str(archive))
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root, "Codex.exe"),),
    )
    monkeypatch.setattr(
        "codexier.desktop_patch._windows_write_probe",
        lambda _directory: pytest.fail("MSIX must be rejected before the write probe"),
    )

    app = default_target("windows")

    assert app.package_type == "msix"
    assert patch_status(app).skipped
    assert "signed Microsoft Store/MSIX" in patch_status(app).message


def test_windows_override_only_selects_valid_process_archive(tmp_path: Path, monkeypatch):
    root = tmp_path / "ChatGPT"
    archive = root / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar())
    unrelated = tmp_path / "Other" / "resources" / "app.asar"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(valid_asar())
    monkeypatch.setenv("CODEXIER_WINDOWS_APP_ASAR", str(unrelated))
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root),),
    )

    status = patch_status(default_target("windows"))

    assert status.skipped
    assert "does not select a validated archive" in status.message


def test_windows_override_selects_between_valid_process_archives(tmp_path: Path, monkeypatch):
    root = tmp_path / "ChatGPT"
    archives = (
        root / "resources" / "app.asar",
        root / "app" / "resources" / "app.asar",
    )
    for archive in archives:
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(valid_asar())
    monkeypatch.setenv("CODEXIER_WINDOWS_APP_ASAR", str(archives[1]))
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root),),
    )

    app = default_target("windows")

    assert app.archive_path == archives[1]
    assert app.package_type == "unpackaged"


def test_windows_read_only_runtime_is_safely_skipped(tmp_path: Path, monkeypatch):
    root = tmp_path / "ChatGPT"
    archive = root / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar())
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root),),
    )
    monkeypatch.setattr(
        "codexier.desktop_patch._windows_write_probe",
        lambda _directory: "access denied",
    )

    status = patch_status(default_target("windows"))

    assert status.skipped
    assert "not writable (access denied)" in status.message


def test_windows_symlinked_archive_outside_process_root_is_rejected(
    tmp_path: Path, monkeypatch
):
    root = tmp_path / "ChatGPT"
    archive = root / "resources" / "app.asar"
    outside = tmp_path / "outside.asar"
    archive.parent.mkdir(parents=True)
    outside.write_bytes(valid_asar())
    try:
        archive.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root),),
    )

    status = patch_status(default_target("windows"))

    assert status.skipped
    assert "outside the running installation root" in status.message


def test_windows_already_patched_valid_runtime_is_not_modified(tmp_path: Path, monkeypatch):
    root = tmp_path / "ChatGPT"
    archive = root / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar(PATCH_MARKER))
    original = archive.read_bytes()
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(root),),
    )

    app = default_target("windows")
    status = apply_desktop_patch(app, tmp_path / "backups")

    assert status.patched
    assert archive.read_bytes() == original
    assert not (tmp_path / "backups").exists()


def test_windows_unpacked_dispatches_verified_adapter(tmp_path: Path, monkeypatch):
    archive = tmp_path / "ChatGPT" / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(valid_asar())
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "desktop-model-providers.json").write_text(
        '{"version": 2, "default_provider": "openai", "providers": ['
        '{"id": "openai", "label": "OpenAI", "description": "", "models": []}'
        ']}'
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(
        "codexier.process_manager.detect_chatgpt_processes",
        lambda **_kwargs: (running_process(archive.parents[1]),),
    )

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
        default_target("windows"),
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
