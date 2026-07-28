# Codexier

Codexier is a terminal UI for managing multiple OpenAI-compatible providers
and syncing any models they expose to Codex.

## Quick start

No manual installation is required. From the project directory, run the
launcher for your platform:

```bash
./codexier.command
```

On Windows, double-click `codexier.bat` or run it from Command Prompt:

```bash
codexier.bat
```

On Linux, make launcher executable once, then run it:

```bash
chmod +x codexier.sh
./codexier.sh
```

The launcher creates `.venv`, installs runtime dependencies when needed, and
starts Codexier with the project environment.

On first run, add each provider with its name, OpenAI-compatible base URL, and
API key. Codexier discovers models through `/v1/models`; any returned model ID
can be selected, with no model-count limit.

Toggle the providers that you want to export, select the Codexier fallback,
then sync. Codexier writes one normal `codexier` profile and one shared model
catalog. Only enabled providers are exported to desktop routing; they do not
create separate Codex profiles. Existing catalogs that predate toggles retain
their previous Codexier fallback as enabled when it can be matched safely.
On Linux, run `codex --profile codexier`.

On macOS, every successful sync automatically checks and patches the supported
desktop bundle. Codexier shows percentage milestones and a detailed terminal
log while it closes the app gracefully, backs up `app.asar`, patches, verifies,
and restores on failure. Already-patched installs report success without
rewriting the archive.

# Windows desktop modes

Open **Settings → Windows desktop apps** and choose one of two modes:

- **Official Codex App:** keeps the signed Microsoft Store package untouched
  and exports exactly one selected enabled custom provider and its models.
- **Portable App:** copies the installed `OpenAI.Codex` payload into
  `%LOCALAPPDATA%\Codexier\PortableCodex`, verifies the copy, and patches only
  that managed copy. It exports every enabled provider through the provider-first
  model picker.

**Create portable app** is the only action that copies the installed Store
payload. **Refresh portable status** only re-scans an existing portable copy and
its source version; it never copies, patches, or replaces the app. Codexier
never changes `WindowsApps`, removes package signatures, or registers a modified
AppX/MSIX. It does not require Codex App Manager, a mirror, or another external
project. Use **Repair patch** after a patch problem.

Both modes use the same lowercase `codexier` profile, displayed as `Codexier`,
and the standard `%USERPROFILE%\.codex` home. Portable Codex keeps its Electron
user data in `PortableCodex\user-data`, so it does not take over the Official
app's running instance or session. If `CODEX_HOME` points elsewhere, Windows
dual-app launch is blocked until it is unset or points to the standard `.codex`
directory.

Before switching or updating, Codexier requests a graceful close of all Official
and Portable Codex windows. It never force-kills them. A failed close aborts
before configuration or application files change. Portable install and patch
operations show numbered progress and detailed logs; failures after replacement
restore and verify the previous managed portable payload. The Store source and
user data remain untouched.

## Options

```text
--providers PATH   Use a specific provider catalog
--config PATH      Use a specific Codex configuration
--dry-run          Preview changes without writing
--yes              Skip confirmation prompts
--no-restart       Do not prompt to restart Codex
--restart          Attempt a safe Codex restart
--print-command    Print the normal Codexier CLI command
--patch-desktop    Also request desktop patch handling (automatic on macOS; Windows
                   uses Settings → Official Codex App / Portable App)
--restore-desktop-patch BACKUP
                   Restore a desktop patch backup
```

## Security

Provider credentials are stored in the local `providers.json` file, which is
ignored by Git. Never commit or share this file. Codexier creates it with
private file permissions when required.

## Development

```bash
pip install -e '.[test]'
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q codexier
```
