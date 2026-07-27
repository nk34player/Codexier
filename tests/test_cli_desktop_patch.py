from pathlib import Path

from codexier import cli
from codexier.desktop_patch import DesktopPatchStatus, DesktopPatchTarget
from codexier.errors import ConfigError


def test_post_sync_desktop_patch_reports_visible_progress(monkeypatch, capsys, tmp_path: Path):
    target = DesktopPatchTarget("windows", tmp_path / "app.asar", package_type="unpackaged")
    calls: list[Path] = []

    monkeypatch.setattr(cli, "default_target", lambda: target)

    def fake_apply(received_target, backup_root, progress):
        assert received_target == target
        calls.append(backup_root)
        progress(5, "detection: fake target")
        progress(100, "completion: fake patch complete")
        return DesktopPatchStatus(target, True, True, "Codexier desktop patch is installed.")

    monkeypatch.setattr(cli, "apply_desktop_patch", fake_apply)
    monkeypatch.setattr(cli.Path, "home", classmethod(lambda _cls: tmp_path))
    cli._apply_post_sync_desktop_patch()

    assert calls == [tmp_path / ".codex" / "codexier-desktop-backups"]
    assert "[desktop patch   5%] detection: fake target" in capsys.readouterr().out


def test_post_sync_patch_failure_does_not_undo_provider_sync(monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_target", lambda: DesktopPatchTarget("windows", Path("app.asar")))
    monkeypatch.setattr(
        cli,
        "apply_desktop_patch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConfigError("source patch failed")),
    )

    cli._apply_post_sync_desktop_patch()

    output = capsys.readouterr().out
    assert "Provider sync succeeded" in output
    assert "source patch failed" in output
