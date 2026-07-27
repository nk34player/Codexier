from pathlib import Path

import pytest

from codexier.desktop_patch_macos import (
    CENTRAL_DIFF_V4_TO_V5,
    PICKER_DIFF_V4_TO_V5,
    PATCH_VARIANTS,
    PatchError,
    render_unified_diff,
    validate_provider_config,
)
from codexier.patch_progress import MILESTONES


def test_provider_first_desktop_config_accepts_duplicate_model_ids_across_providers():
    validate_provider_config(
        {
            "version": 2,
            "default_provider": "codexier-one",
            "providers": [
                {
                    "id": "openai",
                    "label": "OpenAI",
                    "description": "",
                    "models": [],
                },
                {
                    "id": "codexier-one",
                    "label": "One",
                    "description": "",
                    "models": [{"id": "shared", "label": "Shared One"}],
                },
                {
                    "id": "codexier-two",
                    "label": "Two",
                    "description": "",
                    "models": [{"id": "shared", "label": "Shared Two"}],
                },
            ],
        }
    )


def test_provider_first_desktop_config_rejects_duplicate_models_within_provider():
    with pytest.raises(PatchError, match="duplicate model id"):
        validate_provider_config(
            {
                "version": 2,
                "default_provider": "one",
                "providers": [
                    {
                        "id": "one",
                        "label": "One",
                        "models": [
                            {"id": "same", "label": "Same"},
                            {"id": "same", "label": "Again"},
                        ],
                    }
                ],
            }
        )


def test_generated_desktop_patch_only_routes_new_thread_starts():
    patch_source = "\n".join(
        source
        for name, central, picker in PATCH_VARIANTS
        if "safety upgrade" not in name
        for source in (central, picker)
    )
    assert "thread/start" in patch_source
    assert "thread/list" not in patch_source
    assert "thread/read" not in patch_source
    assert "workspace" not in patch_source


def test_v4_safety_upgrade_removes_list_filter_and_installs_v5_marker():
    central = """function codexProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatchV4 ??= {
    config: codexProviderRoutingFallbackV4(), error: null, loaded: !1, promise: null,
  });
}
async function codexPatchAppServerParams(e, t) {
  if (e === `thread/list`) {
    let e = t != null && typeof t === `object` ? t : {};
    return e.modelProviders == null ? { ...e, modelProviders: [] } : e;
  }
  if (e !== `thread/start` || t == null || typeof t !== `object`) return t;
}
"""
    picker = (
        "function CodexCustomProviderPickerSection() {\n"
        "  let r = codexPickerProviderRoutingStateV4(),\n"
        "    e = r.config;\n"
        "}\n"
    )

    upgraded_central = render_unified_diff(central, CENTRAL_DIFF_V4_TO_V5, "central.js")
    upgraded_picker = render_unified_diff(picker, PICKER_DIFF_V4_TO_V5, "picker.js")

    assert "__codexDesktopModelProvidersPatchV5" in upgraded_central
    assert "thread/list" not in upgraded_central
    assert "thread/start" in upgraded_central
    assert upgraded_picker == picker


def test_windows_rollback_restores_verified_archive_after_post_backup_failure(
    tmp_path: Path, monkeypatch
):
    import codexier.desktop_patch_windows as windows

    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir()
    archive.write_bytes(b"original")
    config = tmp_path / "desktop-model-providers.json"
    config.write_text(
        '{"version": 2, "default_provider": "openai", "providers": '
        '[{"id": "openai", "label": "OpenAI", "description": "", "models": []}]}'
    )
    monkeypatch.setattr(windows, "asar_header_hash", lambda _archive: "valid")
    monkeypatch.setattr(windows.shutil, "which", lambda _name: "npx")
    monkeypatch.setattr(windows, "_target_processes", lambda _archive: ())

    def fail_extract(*_args, **_kwargs):
        raise PatchError("simulated extraction failure")

    monkeypatch.setattr(windows, "run", fail_extract)
    events: list[tuple[int, str]] = []
    with pytest.raises(PatchError, match="simulated extraction failure"):
        windows.patch_windows_app(
            archive,
            config,
            tmp_path / "backups",
            lambda *event: events.append(event),
        )

    backups = list((tmp_path / "backups").glob("*/app.asar"))
    assert len(backups) == 1
    assert archive.read_bytes() == backups[0].read_bytes() == b"original"
    assert (MILESTONES["atomic replacement"], "atomic replacement: rolling back from the verified backup") in events
    assert (MILESTONES["verification"], "verification: verifying the restored original archive") in events
