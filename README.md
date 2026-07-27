# Codexier

Codexier is a terminal UI for managing OpenAI-compatible providers and
applying selected models to a Codex configuration.

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

On first run, add a provider by entering its name, OpenAI-compatible base URL,
and API key. Codexier discovers models through `/v1/models` and stores the
catalog locally.

After selecting models, choose **Apply to Codex** to update the detected Codex
configuration. Codexier writes every saved provider into the shared catalog
and gives each provider its own Codex profile. On Linux, run the printed
`codex --profile ...` command for the selected provider.

`--patch-desktop` installs the source-validated native provider picker on
macOS. It backs up `/Applications/ChatGPT.app`, stops it only if it was
running, and reopens it afterwards. ChatGPT updates require re-patching.
Windows Microsoft Store packages are MSIX-signed, so Codexier refuses unsafe
in-place patching rather than damaging the installed app.

## Options

```text
--providers PATH   Use a specific provider catalog
--config PATH      Use a specific Codex configuration
--dry-run          Preview changes without writing
--yes              Skip confirmation prompts
--no-restart       Do not prompt to restart Codex
--restart          Attempt a safe Codex restart
--print-command    Print selected provider's Codex CLI command
--patch-desktop    Install the supported macOS desktop picker patch
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
