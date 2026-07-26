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
configuration. On Windows, if the ChatGPT desktop app is already running,
Codexier gracefully closes its current-user ChatGPT processes and launches the
app again so the new provider is loaded. If ChatGPT is not running, it remains
closed. Use `--no-restart` to opt out; `--restart` still explicitly restarts
Codex where supported.

## Options

```text
--providers PATH   Use a specific provider catalog
--config PATH      Use a specific Codex configuration
--dry-run          Preview changes without writing
--yes              Skip confirmation prompts
--no-restart       Do not prompt to restart Codex
--restart          Attempt a safe Codex restart
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
