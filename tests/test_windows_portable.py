from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import tomllib
from pathlib import Path

import pytest

from codexier.desktop_patch import PATCH_MARKER
from codexier.errors import ConfigError
from codexier.models import ModelDefinition, Provider
from codexier.process_manager import ChatGPTProcess, CloseResult
from codexier.windows_portable import (
    OfficialPackage,
    PORTABLE_PATCH_VERSION,
    _default_health_check,
    _remove_tree,
    _portable_paths,
    create_portable_shortcut,
    discover_official_package,
    install_portable,
    portable_root,
    portable_status,
    refresh_portable,
    repair_portable,
    require_shared_codex_home,
    sync_and_launch_windows,
 )


def valid_asar(marker: bytes = b"") -> bytes:
    header = b"{}"
    pickle = struct.pack("<II", 4, len(header)) + header
    return struct.pack("<II", 4, len(pickle)) + pickle + marker


def provider(name: str, *, enabled: bool = True) -> Provider:
    return Provider(
        name.lower(),
        name,
        f"https://{name.lower()}.example/v1",
        f"{name.lower()}-secret",
        (ModelDefinition(f"{name.lower()}-model", f"{name} Model"),),
        {},
        enabled,
    )


def package_fixture(
    tmp_path: Path,
    *,
    version: str = "1.0.0.0",
    executable_parts: tuple[str, ...] = ("Codex.exe",),
    archive_layout: tuple[str, ...] = ("resources", "app.asar"),
 ) -> OfficialPackage:
    root = tmp_path / f"OpenAI.Codex_{version}"
    executable = root.joinpath(*executable_parts)
    archive = executable.parent.joinpath(*archive_layout)
    executable.parent.mkdir(parents=True, exist_ok=True)
    archive.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(f"exe-{version}".encode())
    archive.write_bytes(valid_asar())
    return OfficialPackage(
        f"OpenAI.Codex_{version}_x64__openai",
        "OpenAI.Codex_openai",
        version,
        root,
        "App",
        "OpenAI.Codex_openai!App",
        executable,
        archive,
    )


def patch_staged(archive: Path, *_args, **_kwargs) -> object:
    archive.write_bytes(archive.read_bytes() + PATCH_MARKER)
    return object()


def test_portable_launch_disables_app_updates(tmp_path: Path, monkeypatch):
    import codexier.windows_portable as portable

    executable = tmp_path / "Codex.exe"
    executable.write_bytes(b"portable-exe")
    launched = {}
    monkeypatch.setattr(
        portable.subprocess,
        "Popen",
        lambda command, **kwargs: launched.update(command=command, env=kwargs["env"]),
    )

    portable._launch_portable(executable)

    assert launched["command"][0] == str(executable)
    assert launched["command"][1].endswith(r"PortableCodex\user-data")
    assert launched["env"]["CODEX_SPARKLE_ENABLED"] == "false"


def test_portable_desktop_shortcut_uses_private_data_and_portable_icon(tmp_path: Path):
    executable = tmp_path / "PortableCodex" / "Codex.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"portable-exe")
    called = {}

    def run(command, **kwargs):
        called.update(command=command, **kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    create_portable_shortcut(
        executable,
        environ={"LOCALAPPDATA": str(tmp_path / "Local")},
        run=run,
    )

    assert called["command"][:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert "Portable Codex.lnk" in called["command"][-1]
    assert "CODEX_SPARKLE_ENABLED" in called["command"][-1]
    assert "EncodedCommand" in called["command"][-1]
    assert called["env"]["CODEXIER_PORTABLE_EXECUTABLE"] == str(executable)
    assert called["env"]["CODEXIER_PORTABLE_ARGUMENTS"].endswith(
        r"PortableCodex\user-data"
    )
    assert called["env"]["CODEXIER_PORTABLE_WORKING_DIRECTORY"] == str(executable.parent)


def test_health_check_closes_only_the_launched_portable_process(tmp_path: Path, monkeypatch):
    executable = tmp_path / "Codex.exe"
    executable.write_bytes(b"exe")

    class LaunchedProcess:
        pid = 4321

        def wait(self, timeout):
            raise subprocess.TimeoutExpired(str(executable), timeout)

    closed = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: LaunchedProcess())
    monkeypatch.setattr(
        "codexier.windows_portable.detect_chatgpt_processes",
        lambda **_kwargs: pytest.fail("global process detection must not run"),
    )
    monkeypatch.setattr(
        "codexier.windows_portable.gracefully_close_chatgpt_processes",
        lambda processes: closed.extend(processes) or CloseResult(True, "closed"),
    )

    assert _default_health_check(executable)
    assert [(item.pid, item.executable) for item in closed] == [
        (4321, str(executable))
    ]


def test_health_check_terminates_only_its_stuck_process_tree(tmp_path: Path, monkeypatch):
    executable = tmp_path / "Codex.exe"
    executable.write_bytes(b"exe")

    class StuckProcess:
        pid = 4321

        def wait(self, timeout):
            raise subprocess.TimeoutExpired(str(executable), timeout)

    commands = []
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: StuckProcess())
    monkeypatch.setattr(
        "codexier.windows_portable.gracefully_close_chatgpt_processes",
        lambda _processes: CloseResult(False, "still running"),
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0),
    )

    assert _default_health_check(executable)
    assert commands == [["taskkill.exe", "/PID", "4321", "/T", "/F"]]


def test_remove_tree_retries_windows_file_locks(tmp_path: Path, monkeypatch):
    directory = tmp_path / "PortableCodex"
    directory.mkdir()
    original_rmtree = shutil.rmtree
    calls = 0

    def locked_once(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("locked")
        original_rmtree(path)

    monkeypatch.setattr("codexier.windows_portable.shutil.rmtree", locked_once)
    monkeypatch.setattr("codexier.windows_portable.time.sleep", lambda _seconds: None)

    _remove_tree(directory)

    assert calls == 2
    assert not directory.exists()


def test_health_check_reports_the_exit_code_and_diagnostics(tmp_path: Path, monkeypatch):
    executable = tmp_path / "Codex.exe"
    executable.write_bytes(b"exe")

    class FailedProcess:
        pid = 4321
        stdout = io.StringIO("fatal startup error")

        def wait(self, timeout):
            return 134

    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: FailedProcess())

    with pytest.raises(ConfigError, match=r"code 134.*fatal startup error"):
        _default_health_check(executable)


@pytest.mark.parametrize(
    ("executable_value", "archive_layout"),
    (
        ("ChatGPT.exe", ("resources", "app.asar")),
        (r"bin\Codex.exe", ("app", "resources", "app.asar")),
    ),
)
def test_package_discovery_resolves_manifest_entry_and_supported_asar_layout(
    tmp_path: Path, executable_value: str, archive_layout: tuple[str, ...]
):
    root = tmp_path / "WindowsApps" / "OpenAI.Codex_1.2.3.4"
    executable = root.joinpath(*executable_value.replace("\\", "/").split("/"))
    archive = executable.parent.joinpath(*archive_layout)
    executable.parent.mkdir(parents=True)
    archive.parent.mkdir(parents=True)
    executable.write_bytes(b"exe")
    archive.write_bytes(valid_asar())
    (root / "AppxManifest.xml").write_text(
        (
            '<Package xmlns="urn:test"><Applications><Application '
            f'Id="Codex" Executable="{executable_value}" />'
            '</Applications></Package>'
        ),
        encoding="utf-8",
    )

    def run(*_args, **_kwargs):
        payload = {
            "PackageFullName": "OpenAI.Codex_1.2.3.4_x64__openai",
            "PackageFamilyName": "OpenAI.Codex_openai",
            "Version": "1.2.3.4",
            "InstallLocation": str(root),
        }
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    package = discover_official_package(run=run)
    assert package.executable == executable
    assert package.archive == archive
    assert package.aumid == "OpenAI.Codex_openai!Codex"


def test_portable_paths_are_fixed_under_local_app_data_and_reject_escape(tmp_path: Path):
    environ = {"LOCALAPPDATA": str(tmp_path / "Local")}
    root = portable_root(environ)
    assert root == tmp_path / "Local" / "Codexier" / "PortableCodex"
    with pytest.raises(ConfigError, match="outside"):
        _portable_paths({"executable": "../outside.exe", "archive": "resources/app.asar"}, environ)


def test_install_clones_full_package_root_patches_copy_and_keeps_source_immutable(
    tmp_path: Path, monkeypatch
):
    package = package_fixture(
        tmp_path / "source",
        executable_parts=("bin", "Codex.exe"),
        archive_layout=("app", "resources", "app.asar"),
    )
    (package.install_location / "root-resource.dat").write_bytes(b"keep-me")
    source_exe = package.executable.read_bytes()
    source_asar = package.archive.read_bytes()
    environ = {"LOCALAPPDATA": str(tmp_path / "Local") }
    config = tmp_path / "desktop-model-providers.json"
    config.write_text('{"version": 2, "providers": []}')
    events: list[tuple[int, str]] = []
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", patch_staged)

    status = install_portable(
        config,
        package=package,
        environ=environ,
        progress=lambda percent, detail: events.append((percent, detail)),
        health_check=lambda _path: True,
        close_processes=lambda _items: CloseResult(True, "closed"),
        detect_processes=lambda **_kwargs: (),
    )

    assert status.installed and status.patched
    assert (status.root / "root-resource.dat").read_bytes() == b"keep-me"
    assert status.executable == status.root / "bin" / "Codex.exe"
    assert package.executable.read_bytes() == source_exe
    assert package.archive.read_bytes() == source_asar
    assert [percent for percent, _ in events] == sorted(percent for percent, _ in events)
    assert events[-1][0] == 100
    metadata = json.loads((tmp_path / "Local/Codexier/portable-codex.json").read_text())
    assert metadata["package_version"] == "1.0.0.0"
    assert metadata["executable"] == "bin/Codex.exe"
    assert metadata["archive"] == "bin/app/resources/app.asar"


def test_install_waits_for_a_fresh_empty_process_scan_before_cloning(
    tmp_path: Path, monkeypatch
):
    package = package_fixture(tmp_path / "source")
    config = tmp_path / "desktop-model-providers.json"
    config.write_text('{"version": 2, "providers": []}')
    process = ChatGPTProcess(42, r"C:\Apps\Codex.exe", r"PC\me")
    scans = [(process,), (process,), ()]
    detector_calls = 0

    def detect(**_kwargs):
        nonlocal detector_calls
        detector_calls += 1
        return scans.pop(0) if scans else ()

    original_copytree = shutil.copytree

    def copytree(source, destination, *args, **kwargs):
        assert detector_calls >= 3, "the fresh scan must be empty before cloning"
        return original_copytree(source, destination, *args, **kwargs)

    closed: list[ChatGPTProcess] = []
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", patch_staged)
    monkeypatch.setattr("codexier.windows_portable.shutil.copytree", copytree)

    status = install_portable(
        config,
        package=package,
        environ={"LOCALAPPDATA": str(tmp_path / "Local")},
        health_check=lambda _path: True,
        close_processes=lambda processes: closed.extend(processes) or CloseResult(True, "closed"),
        detect_processes=detect,
    )

    assert status.installed
    assert closed == [process]


def test_refresh_only_inspects_an_existing_portable_app(tmp_path: Path, monkeypatch):
    environ = {"LOCALAPPDATA": str(tmp_path / "Local") }
    config = tmp_path / "desktop-model-providers.json"
    config.write_text('{"version": 2, "providers": []}')
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", patch_staged)
    close = lambda _items: CloseResult(True, "closed")
    first = package_fixture(tmp_path / "source-one", version="1.0.0.0")
    installed = install_portable(
        config,
        package=first,
        environ=environ,
        health_check=lambda _path: True,
        close_processes=close,
        detect_processes=lambda **_kwargs: (),
    )
    assert installed.executable and installed.archive
    old_exe = installed.executable.read_bytes()
    old_asar = installed.archive.read_bytes()
    metadata_path = tmp_path / "Local/Codexier/portable-codex.json"
    old_metadata = metadata_path.read_bytes()
    second = package_fixture(tmp_path / "source-two", version="2.0.0.0")
    events: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "codexier.windows_portable.detect_chatgpt_processes",
        lambda **_kwargs: pytest.fail("Refresh must not close or inspect processes"),
    )
    monkeypatch.setattr(
        "codexier.windows_portable.patch_windows_app",
        lambda *_args, **_kwargs: pytest.fail("Refresh must not patch or recreate the app"),
    )

    refreshed = refresh_portable(
        package=second,
        environ=environ,
        progress=lambda percent, detail: events.append((percent, detail)),
    )

    assert refreshed.installed and refreshed.update_available
    assert installed.executable and installed.archive
    assert installed.executable.read_bytes() == old_exe
    assert installed.archive.read_bytes() == old_asar
    assert metadata_path.read_bytes() == old_metadata
    assert [percent for percent, _ in events] == [5, 12, 100]


def test_refresh_requires_an_existing_portable_app(tmp_path: Path):
    with pytest.raises(ConfigError, match="Create portable app first"):
        refresh_portable(package=package_fixture(tmp_path / "source"), environ={"LOCALAPPDATA": str(tmp_path / "Local")})


def test_repair_patches_the_existing_portable_archive_without_recloning(tmp_path: Path, monkeypatch):
    environ = {"LOCALAPPDATA": str(tmp_path / "Local")}
    config = tmp_path / "desktop-model-providers.json"
    config.write_text('{"version": 2, "providers": []}')
    package = package_fixture(tmp_path / "source")
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", patch_staged)
    installed = install_portable(
        config,
        package=package,
        environ=environ,
        health_check=lambda _path: True,
        close_processes=lambda _items: CloseResult(True, "closed"),
        detect_processes=lambda **_kwargs: (),
    )
    assert installed.archive is not None
    installed.archive.write_bytes(valid_asar())
    patched: list[Path] = []
    monkeypatch.setattr(
        "codexier.windows_portable.patch_windows_app",
        lambda archive, *_args, **_kwargs: patched.append(archive) or patch_staged(archive),
    )
    monkeypatch.setattr(
        "codexier.windows_portable.shutil.copytree",
        lambda *_args, **_kwargs: pytest.fail("Repair must not clone a new portable app"),
    )
    repaired = repair_portable(config, environ=environ)

    assert repaired.patched
    assert patched == [installed.archive]


def test_portable_status_detects_manual_update_and_already_current_install(
    tmp_path: Path, monkeypatch
):
    environ = {"LOCALAPPDATA": str(tmp_path / "Local") }
    config = tmp_path / "desktop-model-providers.json"
    config.write_text('{"version": 2, "providers": []}')
    package = package_fixture(tmp_path / "source", version="1.0.0.0")
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", patch_staged)
    install_portable(
        config,
        package=package,
        environ=environ,
        health_check=lambda _path: True,
        close_processes=lambda _items: CloseResult(True, "closed"),
        detect_processes=lambda **_kwargs: (),
    )
    assert not portable_status(package, environ=environ).update_available
    changed = package_fixture(tmp_path / "new-source", version="2.0.0.0")
    assert portable_status(changed, environ=environ).update_available


def test_shared_home_requires_standard_user_profile_dot_codex(tmp_path: Path):
    environ = {"USERPROFILE": str(tmp_path / "User") }
    expected = tmp_path / "User" / ".codex"
    assert require_shared_codex_home(environ) == expected
    with pytest.raises(ConfigError, match="Unset CODEX_HOME"):
        require_shared_codex_home({**environ, "CODEX_HOME": str(tmp_path / "Other")})


def test_failed_graceful_close_changes_no_config_settings_or_user_data(
    tmp_path: Path, monkeypatch
):
    user = tmp_path / "User"
    home = user / ".codex"
    home.mkdir(parents=True)
    config = home / "config.toml"
    config.write_bytes(b'[profiles.keep]\nname = "Keep"\n')
    sentinel = home / "sessions" / "thread.jsonl"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"session-bytes")
    catalog = tmp_path / "providers.json"
    settings_path = tmp_path / "codexier.settings.json"
    settings_path.write_bytes(b'{"existing": true}\n')
    package = package_fixture(tmp_path / "source")
    monkeypatch.setattr("codexier.windows_portable.discover_official_package", lambda: package)
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr(
        "codexier.windows_portable.gracefully_close_chatgpt_processes",
        lambda _items: CloseResult(False, "close it normally"),
    )
    selected = provider("One")
    settings = {
        "context_window": 250000,
        "auto_compact_token_limit": 70000,
        "windows": {
            "mode": "official",
            "official_provider_id": None,
            "portable_default_provider_id": None,
        },
    }

    with pytest.raises(ConfigError, match="close it normally"):
        sync_and_launch_windows(
            catalog, (selected,), selected, settings, environ={"USERPROFILE": str(user), "LOCALAPPDATA": str(tmp_path / "Local")},
        )

    assert config.read_bytes() == b'[profiles.keep]\nname = "Keep"\n'
    assert settings_path.read_bytes() == b'{"existing": true}\n'
    assert sentinel.read_bytes() == b"session-bytes"


def test_portable_install_prepares_config_only_after_graceful_close(
    tmp_path: Path, monkeypatch
):
    package = package_fixture(tmp_path / "source")
    environ = {"LOCALAPPDATA": str(tmp_path / "Local")}
    prepared = []
    monkeypatch.setattr(
        "codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ()
    )

    with pytest.raises(ConfigError, match="close it normally"):
        install_portable(
            None,
            package=package,
            environ=environ,
            prepare_config=lambda: prepared.append(True) or tmp_path / "routing.json",
            close_processes=lambda _items: CloseResult(False, "close it normally"),
        )

    assert prepared == []
    assert not (tmp_path / "Local" / "Codexier").exists()


def test_official_sync_exports_one_provider_and_launches_aumid(tmp_path: Path, monkeypatch):
    user = tmp_path / "User"
    home = user / ".codex"
    sentinel = home / "workspaces" / "keep.bin"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"workspace")
    catalog = tmp_path / "providers.json"
    package = package_fixture(tmp_path / "source")
    monkeypatch.setattr("codexier.windows_portable.discover_official_package", lambda: package)
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr(
        "codexier.windows_portable.gracefully_close_chatgpt_processes",
        lambda _items: CloseResult(True, "closed"),
    )
    one, two = provider("One"), provider("Two")
    settings = {
        "context_window": 250000,
        "auto_compact_token_limit": 70000,
        "windows": {
            "mode": "official",
            "official_provider_id": None,
            "portable_default_provider_id": None,
        },
    }
    launched: list[str] = []

    result = sync_and_launch_windows(
        catalog,
        (one, two),
        two,
        settings,
        environ={"USERPROFILE": str(user), "LOCALAPPDATA": str(tmp_path / "Local")},
        launch_official=lambda item: launched.append(item.aumid),
    )

    config = tomllib.loads(result.profile.config_path.read_text())
    desktop = json.loads(result.profile.desktop_config_path.read_text())
    models = json.loads(result.profile.catalog_path.read_text())["models"]
    assert launched == [package.aumid]
    assert config["profiles"]["codexier"]["name"] == "Codexier"
    assert set(key for key in config["model_providers"] if key.startswith("codexier-")) == {"codexier-two"}
    assert [item["id"] for item in desktop["providers"]] == ["codexier-two"]
    assert [item["slug"] for item in models] == ["two-model"]
    assert sentinel.read_bytes() == b"workspace"


def test_portable_sync_exports_all_enabled_providers_and_launches_copy(
    tmp_path: Path, monkeypatch
):
    user = tmp_path / "User"
    home = user / ".codex"
    home.mkdir(parents=True)
    catalog = tmp_path / "providers.json"
    package = package_fixture(tmp_path / "source")
    environ = {"USERPROFILE": str(user), "LOCALAPPDATA": str(tmp_path / "Local") }
    root = portable_root(environ)
    executable = root / "Codex.exe"
    archive = root / "resources" / "app.asar"
    archive.parent.mkdir(parents=True)
    executable.write_bytes(b"portable-exe")
    archive.write_bytes(valid_asar(PATCH_MARKER))
    metadata = {
        "package_version": package.version,
        "source_app_asar_hash": "different-is-an-update-only",
        "executable": "Codex.exe",
        "archive": "resources/app.asar",
        "portable_patch_version": PORTABLE_PATCH_VERSION,
    }
    metadata_path = tmp_path / "Local/Codexier/portable-codex.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata))
    monkeypatch.setattr("codexier.windows_portable.discover_official_package", lambda: package)
    monkeypatch.setattr("codexier.windows_portable.detect_chatgpt_processes", lambda **_kwargs: ())
    monkeypatch.setattr(
        "codexier.windows_portable.gracefully_close_chatgpt_processes",
        lambda _items: CloseResult(True, "closed"),
    )
    monkeypatch.setattr("codexier.windows_portable.patch_windows_app", lambda *_args, **_kwargs: object())
    shortcuts: list[Path] = []
    monkeypatch.setattr(
        "codexier.windows_portable.create_portable_shortcut",
        lambda path, **_kwargs: shortcuts.append(path),
    )
    one, two, off = provider("One"), provider("Two"), provider("Off", enabled=False)
    settings = {
        "context_window": 250000,
        "auto_compact_token_limit": 70000,
        "windows": {
            "mode": "portable",
            "official_provider_id": None,
            "portable_default_provider_id": None,
            "create_desktop_shortcut": True,
        },
    }
    launched: list[Path] = []

    result = sync_and_launch_windows(
        catalog,
        (one, two, off),
        one,
        settings,
        environ=environ,
        launch_portable=lambda path: launched.append(path),
    )

    config = tomllib.loads(result.profile.config_path.read_text())
    desktop = json.loads(result.profile.desktop_config_path.read_text())
    assert launched == [executable]
    assert shortcuts == [executable]
    assert set(key for key in config["model_providers"] if key.startswith("codexier-")) == {"codexier-one", "codexier-two"}
    assert [item["id"] for item in desktop["providers"]] == ["codexier-one", "codexier-two"]
    assert all(item["id"] != "openai" for item in desktop["providers"])
    assert list(config["profiles"]) == ["codexier"]
