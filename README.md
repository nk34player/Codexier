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

The launcher creates `.venv`, installs runtime dependencies when needed, and
starts Codexier with the project environment.

On first run, add a provider by entering its name, OpenAI-compatible base URL,
and API key. Codexier discovers models through `/v1/models` and stores the
catalog locally.

After selecting models, choose **Apply to Codex** to update the detected Codex
configuration. Use `--restart` to restart Codex when supported.

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
