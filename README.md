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

Choose a provider as the default, then sync. Codexier writes every saved
provider and selected model to one shared catalog, gives each provider its own
Codex profile, and makes the selected provider the default. Duplicate model
IDs across providers are rejected before anything is written. On Linux, run
the printed `codex --profile ...` command for the selected default provider.

`--patch-desktop` installs the source-validated native provider picker on
macOS. It backs up `/Applications/ChatGPT.app`, stops it only if it was
running, and reopens it afterwards. ChatGPT updates require re-patching.
Windows Microsoft Store packages are MSIX-signed, so Codexier refuses unsafe
in-place patching rather than damaging the installed app. Linux uses generated
Codex CLI profiles rather than a desktop patch.

## Options

```text
--providers PATH   Use a specific provider catalog
--config PATH      Use a specific Codex configuration
--dry-run          Preview changes without writing
--yes              Skip confirmation prompts
--no-restart       Do not prompt to restart Codex
--restart          Attempt a safe Codex restart
--print-command    Print the selected default provider's Codex CLI command
--patch-desktop    Install the supported macOS desktop provider-picker patch
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
