from pathlib import Path
import subprocess

import pytest

from codexier.desktop_patch_macos import (
    CENTRAL_DIFF,
    CENTRAL_DIFF_V6_TO_V7,
    CENTRAL_DIFF_26721_V7,
    CENTRAL_V7_JAVASCRIPT,
    PICKER_DIFF,
    PICKER_DIFF_26721,
    PICKER_DIFF_V6_TO_V7,
    PICKER_DIFF_26721_V7,
    PICKER_V7_JAVASCRIPT,
    PatchError,
    ensure_provider_config,
    render_unified_diff,
    run,
    validate_provider_config,
)
from codexier.patch_progress import MILESTONES
from codexier.process_manager import ChatGPTProcess


def test_patch_fallbacks_contain_no_example_provider_models():
    patch_source = "\n".join(
        (
            CENTRAL_DIFF_26721_V7,
            PICKER_DIFF_26721_V7,
            CENTRAL_V7_JAVASCRIPT,
            PICKER_V7_JAVASCRIPT,
        )
    ).casefold()

    assert "chatgpt / openai" not in patch_source
    assert "defaultprovider: `openai`" not in patch_source
    assert "id === `openai`" not in patch_source
    assert "openrouter" not in patch_source
    assert "kimi" not in patch_source
    assert "grok" not in patch_source
    assert "claude-fable" not in patch_source
    assert "defaultprovider: null" in patch_source
    assert "providers: []" in patch_source


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
    patch_source = "\n".join((CENTRAL_V7_JAVASCRIPT, PICKER_V7_JAVASCRIPT))
    assert "thread/start" in patch_source
    assert "thread/list" not in patch_source
    assert "thread/read" not in patch_source
    assert "workspace" not in patch_source


def test_v6_upgrade_removes_synthetic_openai_and_installs_v7_marker():
    central = """function codexProviderRoutingFallbackV4() {
  return {
    version: 2,
    defaultProvider: `openai`,
    providers: [{ id: `openai`, label: `ChatGPT / OpenAI`, description: `Uses your signed-in ChatGPT account`, models: [] }],
  };
}
function codexProviderRoutingStateV4() {
  return (window.__codexDesktopModelProvidersPatchV6 ??= {
    config: codexProviderRoutingFallbackV4(), error: null, loaded: !1, promise: null,
  });
}
async function codexPatchAppServerParams(e, t) {
  let n = {}, r = null;
  let i = n.providers.find((e) => e.id === r) ?? n.providers.find((e) => e.id === n.defaultProvider);
  if (i == null) return t;
  if (i.id !== `openai` && !i.models.some((e) => e.id === t.model))
    throw Error(`The selected model is not configured for the selected provider`);
}
"""
    picker = """function codexPickerProviderRoutingFallbackV4() {
  return {
    version: 2,
    defaultProvider: `openai`,
    providers: [{ id: `openai`, label: `ChatGPT / OpenAI`, description: `Uses your signed-in ChatGPT account`, models: [] }],
  };
}
function codexUseProviderModels(e) {
  let r = { config: {} }, t = { providers: [] }, i = null;
  let o = t.providers.find((e) => e.id === i) ?? t.providers.find((e) => e.id === t.defaultProvider);
  if (o == null) return e;
  if (o.id === `openai`) {
    let t = new Set(r.config.providers.flatMap((e) => e.id === `openai` ? [] : e.models.map((e) => e.id)));
    return e?.filter((e) => !t.has(e.model));
  }
  return o.models.flatMap((t) => {
    return [];
  });
}
"""

    upgraded_central = render_unified_diff(central, CENTRAL_DIFF_V6_TO_V7, "central.js")
    upgraded_picker = render_unified_diff(picker, PICKER_DIFF_V6_TO_V7, "picker.js")
    upgraded = (upgraded_central + upgraded_picker).casefold()

    assert "__codexdesktopmodelproviderspatchv7" in upgraded
    assert "chatgpt / openai" not in upgraded
    assert "defaultprovider: `openai`" not in upgraded
    assert "id === `openai`" not in upgraded
    assert "thread/list" not in upgraded
    assert "thread/read" not in upgraded
    assert "workspace" not in upgraded
    assert "defaultprovider: null" in upgraded
    assert "providers: []" in upgraded


def test_missing_provider_config_is_not_replaced_with_examples(tmp_path: Path):
    config = tmp_path / "desktop-model-providers.json"

    with pytest.raises(PatchError, match="Sync at least one enabled provider"):
        ensure_provider_config(config, overwrite=False)

    assert not config.exists()


def test_patch_hunks_match_minified_javascript_without_prettier():
    source = "function increment(e){return e;}"
    diff = """@@ -1,3 +1,3 @@
 function increment(e) {
-  return e;
+  return e + 1;
 }
"""

    patched = render_unified_diff(source, diff, "bundle.js")

    assert "return e + 1;" in patched


def test_failed_patch_command_names_the_stage_when_it_has_no_output(monkeypatch):
    monkeypatch.setattr(
        "codexier.desktop_patch_macos.subprocess.run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            subprocess.CalledProcessError(134, ["npx"], output="")
        ),
    )

    with pytest.raises(PatchError, match=r"Formatting patched JavaScript failed with exit status 134") as error:
        run(["npx", "--version"], label="Formatting patched JavaScript", terminal=False)

    assert "Verify Node.js/npm" in str(error.value)
    assert "Event Viewer" in str(error.value)


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
    process = ChatGPTProcess(10, str(tmp_path / "ChatGPT.exe"), r"PC\me")
    monkeypatch.setattr(windows, "_target_processes", lambda _archive: (process,))
    monkeypatch.setattr(
        windows, "_gracefully_close_target_processes", lambda _processes: process.executable
    )

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
