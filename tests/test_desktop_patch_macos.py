from pathlib import Path
import subprocess
from pathlib import Path

import pytest

from codexier.desktop_patch_macos import (
    CODEX_26721_4979_CENTRAL_ANCHOR,
    CODEX_26721_4979_LAYOUT,
    CODEX_26721_4979_MENU_ANCHOR,
    CODEX_26721_4979_MODEL_CHANGED_ANCHOR,
    CODEX_26721_4979_MODELS_ANCHOR,
    CODEX_26721_4979_PICKER_ANCHOR,
    CODEX_26721_4979_PREWARM_ANCHOR,
    CODEX_26721_4979_REACT_ANCHOR,
    CODEX_26721_4979_REQUEST_ANCHOR,
    CODEX_26721_5848_LAYOUT,
    CODEX_26721_5848_MODEL_LABEL_ANCHOR,
    CODEX_26721_5848_SUBMENU_ANCHOR,
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
    apply_supported_patch_variant,
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

    assert "__codexdesktopmodelproviderspatchv13" in upgraded
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


def test_restore_original_app_leaves_codex_config_and_sessions_untouched(tmp_path: Path, monkeypatch):
    import codexier.desktop_patch_macos as macos

    app = tmp_path / "ChatGPT.app"
    backup = tmp_path / "ChatGPT-backup.app"
    for bundle, archive in ((app, b"patched" + macos.PATCH_MARKER), (backup, b"original")):
        resources = bundle / "Contents" / "Resources"
        resources.mkdir(parents=True)
        (resources / "app.asar").write_bytes(archive)
        (bundle / "Contents" / "Info.plist").write_bytes(b"plist")
    config = tmp_path / ".codex" / "config.toml"
    session = tmp_path / ".codex" / "sessions" / "thread.jsonl"
    config.parent.mkdir()
    session.parent.mkdir()
    config.write_bytes(b"config")
    session.write_bytes(b"session")
    failed = tmp_path / "ChatGPT.patch-failed.app"

    monkeypatch.setattr(macos, "gracefully_close_target_app_processes", lambda _app: False)

    def restore(target: Path, source: Path) -> Path:
        (target / "Contents" / "Resources" / "app.asar").write_bytes(
            (source / "Contents" / "Resources" / "app.asar").read_bytes()
        )
        (target / "Contents" / "Info.plist").write_bytes(
            (source / "Contents" / "Info.plist").read_bytes()
        )
        return failed

    monkeypatch.setattr(macos, "restore_backup", restore)
    assert macos.restore_original_app(app, backup) == failed
    assert (app / "Contents" / "Resources" / "app.asar").read_bytes() == b"original"
    assert config.read_bytes() == b"config"
    assert session.read_bytes() == b"session"


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


def test_26721_4979_merged_bundle_uses_one_source_validated_patch(tmp_path: Path):
    bundle = tmp_path / "app-initial.js"
    bundle.write_text(
        "".join(
            (
                CODEX_26721_4979_CENTRAL_ANCHOR,
                CODEX_26721_4979_REQUEST_ANCHOR,
                CODEX_26721_4979_PREWARM_ANCHOR,
                CODEX_26721_4979_PICKER_ANCHOR,
                CODEX_26721_4979_MODELS_ANCHOR,
                CODEX_26721_4979_MENU_ANCHOR,
                CODEX_26721_4979_MODEL_CHANGED_ANCHOR,
                CODEX_26721_4979_REACT_ANCHOR,
            )
        ),
        encoding="utf-8",
    )

    assert apply_supported_patch_variant(bundle, bundle) == CODEX_26721_4979_LAYOUT
    patched = bundle.read_text(encoding="utf-8")
    assert "__codexDesktopModelProvidersPatchV13" in patched
    assert "CodexCustomProviderPickerSection" in patched
    assert "if (e.providers.length < 2 && a == null) return null;" in patched
    assert "name: t.label" in patched
    assert "label: t.label" in patched
    assert "}=e;p=codexUseProviderModels(p,d,y);let P=m" in patched
    assert "codex.customProviderSelection.v2.change" in patched
    assert "children: e.description || `Custom Provider`" in patched
    assert "Provider config error" in patched
    assert "o=codexPickerModelLabelV4(n,jol(n,a))" in patched
    assert "l=codexPickerModelLabelV4(r,jol(r,c))" in patched
    assert "t=await codexPatchAppServerParams(e,t)" in patched
    assert "e=await codexPatchAppServerParams(`thread/start`,e)" in patched


def test_5848_bundle_shows_provider_config_errors_and_custom_model_labels(tmp_path: Path):
    bundle = tmp_path / "app-initial.js"
    bundle.write_text(
        "".join(
            (
                "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,n)}",
                "async prewarmThreadStart(e,t){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);let n=",
                "function dMs(e){",
                "var TMs,wQ,EMs=e((()=>{TMs=c(),",
                CODEX_26721_5848_SUBMENU_ANCHOR,
                "let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
                "},model:{ariaLabel:U.formatMessage(",
                "children:[m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
                "contentClassName:`w-[280px]`,disabled:P||p==null,children:re",
                "contentClassName:`w-[280px]`,disabled:fe,children:re",
                CODEX_26721_5848_MODEL_LABEL_ANCHOR,
            )
        ),
        encoding="utf-8",
    )

    assert apply_supported_patch_variant(bundle, bundle) == CODEX_26721_5848_LAYOUT
    patched = bundle.read_text(encoding="utf-8")
    assert "__codexDesktopModelProvidersPatchV13" in patched
    assert "p=codexUseProviderModels(p,d,y);" in patched
    assert "codex.customProviderSelection.v2.change" in patched
    assert "codex.customProviderRouting.v4" in patched
    assert "Provider config error" in patched
    assert "t[11]!==n.extras" in patched
    assert "children:[n.extras,l]" in patched
    assert "model:{extras:(0,wQ.jsx)(CodexCustomProviderPickerSection,{})" in patched
    assert "CodexProviderModelLabelV4" in patched
    assert "model:e,fallback:GM(t,e)?.displayName" in patched
    assert "disabled:P||p==null,flyoutHeader:(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),children:re" in patched
    assert "disabled:fe,flyoutHeader:(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),children:re" in patched
    assert "children:[(0,wQ.jsx)(CodexCustomProviderPickerSection,{}),m," not in patched


def test_windows_patches_26721_4979_merged_bundle(tmp_path: Path, monkeypatch):
    import codexier.desktop_patch_windows as windows

    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir()
    archive.write_bytes(b"original")
    config = tmp_path / "desktop-model-providers.json"
    config.write_text(
        '{"version": 2, "default_provider": "openai", "providers": '
        '[{"id": "openai", "label": "OpenAI", "description": "", "models": []}]}'
    )
    source = "".join(
        (
            CODEX_26721_4979_CENTRAL_ANCHOR,
            CODEX_26721_4979_REQUEST_ANCHOR,
            CODEX_26721_4979_PREWARM_ANCHOR,
            CODEX_26721_4979_PICKER_ANCHOR,
            CODEX_26721_4979_MODELS_ANCHOR,
            CODEX_26721_4979_MENU_ANCHOR,
            CODEX_26721_4979_MODEL_CHANGED_ANCHOR,
            CODEX_26721_4979_REACT_ANCHOR,
            "async prewarmThreadStart(async sendConfigReadRequest("
            "composer.intelligenceDropdown.tooltipmodelOptionsDisabled",
        )
    )
    monkeypatch.setattr(windows, "asar_header_hash", lambda _archive: "valid")
    monkeypatch.setattr(windows.shutil, "which", lambda _name: "npx")

    def fake_run(command, **_kwargs):
        if "extract" in command:
            bundle = Path(command[-1]) / "webview" / "assets" / "app-initial.js"
            bundle.parent.mkdir(parents=True)
            bundle.write_text(source, encoding="utf-8")
        elif "--check" not in command:
            Path(command[-1]).write_bytes(
                (Path(command[-2]) / "webview" / "assets" / "app-initial.js").read_bytes()
            )

    monkeypatch.setattr(windows, "run", fake_run)
    windows.patch_windows_app(
        archive, config, tmp_path / "backups", require_running=False, restart=False
    )

    patched = archive.read_text(encoding="utf-8")
    assert "__codexDesktopModelProvidersPatchV13" in patched
    assert "CodexCustomProviderPickerSection" in patched
    assert "if (e.providers.length < 2 && a == null) return null;" in patched
    assert "name: t.label" in patched
    assert "label: t.label" in patched
    assert archive.with_name("app.asar.bak").read_bytes() == b"original"


def test_windows_patches_26721_5848_with_reactive_provider_labels(
    tmp_path: Path, monkeypatch
):
    import codexier.desktop_patch_windows as windows

    archive = tmp_path / "resources" / "app.asar"
    archive.parent.mkdir()
    archive.write_bytes(b"original")
    config = tmp_path / "desktop-model-providers.json"
    config.write_text(
        '{"version": 2, "default_provider": "codexier-test", "providers": '
        '[{"id": "codexier-test", "label": "Test", "description": '
        '"Custom Provider", "models": []}]}'
    )
    source = "".join(
        (
            "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);return e===`config/read`?this.sendConfigReadRequest(t,n):this.enqueueRequest(e,t,n)}",
            "async prewarmThreadStart(e,t){if(this.dispatchMessage==null)throw Error(`AppServerRequestClient is missing a message dispatcher`);let n=",
            "function dMs(e){",
            "var TMs,wQ,EMs=e((()=>{TMs=c(),",
            CODEX_26721_5848_SUBMENU_ANCHOR,
            "let z=R,B=A===void 0?!1:A,V=j===void 0?!0:j,H=M===void 0?!1:M,U=ed(),",
            "},model:{ariaLabel:U.formatMessage(",
            "children:[m,(0,wQ.jsx)(`div`,{className:`vertical-scroll-fade-mask",
            "contentClassName:`w-[280px]`,disabled:P||p==null,children:re",
            "contentClassName:`w-[280px]`,disabled:fe,children:re",
            CODEX_26721_5848_MODEL_LABEL_ANCHOR,
            "async sendConfigReadRequest(",
            "composer.intelligenceDropdown.tooltipmodelOptionsDisabled",
        )
    )
    monkeypatch.setattr(windows, "asar_header_hash", lambda _archive: "valid")
    monkeypatch.setattr(windows.shutil, "which", lambda _name: "npx")

    def fake_run(command, **_kwargs):
        if "extract" in command:
            bundle = Path(command[-1]) / "webview" / "assets" / "app-initial.js"
            bundle.parent.mkdir(parents=True)
            bundle.write_text(source, encoding="utf-8")
        elif "--check" not in command:
            Path(command[-1]).write_bytes(
                (Path(command[-2]) / "webview" / "assets" / "app-initial.js").read_bytes()
            )

    monkeypatch.setattr(windows, "run", fake_run)
    windows.patch_windows_app(
        archive, config, tmp_path / "backups", require_running=False, restart=False
    )

    patched = archive.read_text(encoding="utf-8")
    assert "__codexDesktopModelProvidersPatchV13" in patched
    assert "children: e.description || `Custom Provider`" in patched
    assert "CodexProviderModelLabelV4" in patched
    assert "codex.customProviderSelection.v2.change" in patched


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
    assert archive.with_name("app.asar.bak").read_bytes() == b"original"
    assert (MILESTONES["atomic replacement"], "atomic replacement: rolling back from the verified backup") in events
    assert (MILESTONES["verification"], "verification: verifying the restored original archive") in events
