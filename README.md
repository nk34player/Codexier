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

`--patch-desktop` installs the source-validated provider-first picker on
macOS and unpackaged Windows Electron installs. It verifies the supported
bundle layout, creates a backup, stops the target app only when needed, and
reopens it after a successful patch. ChatGPT updates require re-patching.
Windows Microsoft Store packages are MSIX-signed, so Codexier refuses unsafe
in-place patching rather than damaging the installed app. Linux uses the
normal Codexier profile rather than a desktop patch.

## Options

```text
--providers PATH   Use a specific provider catalog
--config PATH      Use a specific Codex configuration
--dry-run          Preview changes without writing
--yes              Skip confirmation prompts
--no-restart       Do not prompt to restart Codex
--restart          Attempt a safe Codex restart
--print-command    Print the normal Codexier CLI command
--patch-desktop    Install the supported macOS/Windows desktop provider-picker patch
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
