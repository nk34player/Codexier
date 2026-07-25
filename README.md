# codexier

Interactive Codex provider and model catalog manager.

Codexier uses a full-screen Textual TUI: arrow keys move, Enter toggles a
model, and the live counter prevents selecting more than five. Model choices
come only from the provider's OpenAI-compatible `/v1/models` endpoint. If the
endpoint fails, codexier stops safely instead of using stale catalog models.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
```

## Configure providers

Provider credentials are stored in the local, ignored `providers.json` file.
Do not commit this file or share its contents.

## Run

```bash
codexier
```

Root convenience launcher:

```bash
python main.py
```

`main.py` detects `.venv`, creates it when missing, installs the editable
project and dependencies when imports are missing, then runs codexier with
that interpreter. It forwards command-line arguments:

```bash
python main.py --dry-run
python main.py --script tools/example.py --arg value
```

## Run inside Codex

From project root, run:

```bash
python main.py
```

After choosing provider and models, press **Enter** on `Apply to Codex`. This
writes the detected Codex profile configuration. Restart Codex when prompted,
or use:

```bash
python main.py --restart
```

TUI keys are shown in bottom footer bars:

- Provider manager: `↑↓` select, `Enter` use, `A` add, `E` edit, `D` delete.
- Model catalog: `↑↓` move, `Space` toggle, `Enter` apply, `Esc` back.
- Provider form: `Tab` move, `Enter` fetch live models, `Esc` back.
- Main menu: `S` opens settings. Toggle Codex capability flags, saved to
  `codexier.settings.json` beside `providers.json`.

## First run

If `providers.json` does not exist, codexier creates it with private file
permissions and opens an in-app setup screen. Enter provider name, base URL,
and API key. Press Enter on the API-key field. Codexier calls the provider's
`/v1/models` endpoint, saves returned models, and opens the normal provider
switcher. A provider is not saved when live model discovery fails.

Useful options:

```text
--providers PATH   provider catalog override
--config PATH      Codex JSON/TOML config override
--dry-run          preview without writing
--no-restart       write config without restart prompt
--restart          attempt safe restart after writing
--yes              skip write confirmation
```

Codex config detection checks `~/.codex/config.json`, then
`~/.config/codex/config.toml`. If both exist, pass `--config` explicitly. New
configs default to `~/.codex/config.json`.

Codexier recognizes this initial mapping:

```json
{
  "provider": {"base_url": "...", "api_key": "..."},
  "model_catalog": ["model-a", "model-b"]
}
```

Unknown layouts are rejected before writing. This prevents accidental changes
to unrelated Codex settings. Existing files are timestamped beside the target
before atomic replacement.

Restart is conservative. Codexier only restarts one unambiguous current-user
Codex process with a recoverable launch command. Otherwise it reports that
configuration changed and asks you to restart Codex manually.

## Development

```bash
.venv/bin/python -m pytest -v
.venv/bin/python -m compileall -q codexier
```
