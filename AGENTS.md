# Repository Guidelines

## Project Structure & Module Organization

- `codexier/` contains the Python package and CLI entry point. Keep provider/config logic in the existing modules; platform-specific desktop behavior belongs in `desktop_patch_macos.py`, `desktop_patch_windows.py`, or `windows_portable.py`.
- `tests/` contains pytest tests, generally organized by module or feature.
- `docs/` contains user-facing configuration notes. Root launchers (`codexier.command`, `codexier.sh`, and `codexier.bat`) support local startup on each platform.
- `catalog.json` is packaged data; local credentials such as `providers.json` and `codexier.settings.json` must remain uncommitted.

## Build, Test, and Development Commands

Install editable development dependencies:

```bash
pip install -e '.[test]'
```

Use the repository's `.venv` folder for Python execution and installed tools.

Run the test suite:

```bash
.venv/bin/python -m pytest
```

Check that all package files compile:

```bash
.venv/bin/python -m compileall -q codexier
```

Run the application through the platform launcher, for example `./codexier.command` on macOS or `./codexier.sh` on Linux.

## Coding Style & Naming Conventions

Use Python 3.11+ with four-space indentation, type hints, and `from __future__ import annotations` for new modules. Prefer small, explicit functions and immutable dataclasses where they match existing code. Use `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Keep platform branching isolated and preserve user-facing validation and error messages.

## Testing Guidelines

Use pytest. Name files `tests/test_<feature>.py` and tests `test_<behavior>`. Add focused coverage for validation, configuration writes, process handling, and platform-specific changes. Run the full suite before submitting; keep tests deterministic and avoid real credentials or external provider calls.

## Commit & Pull Request Guidelines

Follow the history's short imperative style, commonly using prefixes such as `fix:`, `feat:`, or `fix(windows):`. Pull requests should explain user-visible behavior, list verification commands, link related issues when applicable, and call out macOS/Windows effects or screenshots for UI changes.

## Security & Configuration Tips

Never commit API keys. Protect `providers.json` with private filesystem permissions (`chmod 600`) and avoid exposing secrets in logs, previews, or tests.

## Desktop JavaScript / Provider Routing Findings

The desktop UI is not maintained as standalone JavaScript source in this
repository. JavaScript is embedded as source-validated unified-diff payloads
inside `codexier/desktop_patch_macos.py`; the same patch definitions are used
by the Windows desktop patch path. Do not edit an extracted app bundle
directly. Update the embedded JavaScript and its exact source anchors/tests
together.

The patch currently supports two known ChatGPT desktop layouts:

- The legacy/separate-bundle layout uses `CENTRAL_DIFF` and `PICKER_DIFF`.
- The merged `app-initial` layout uses the `CODEX_26721_4979_*` and
  `CODEX_26721_5848_*` anchors plus generated V7 diffs.

The current patch marker is V19:
`__codexDesktopModelProvidersPatchV19`. Any JavaScript behavior change must
bump the marker and add/adjust upgrade coverage so an older installed patch is
replaced safely.

### Provider and model identity

Provider identity and model identity are separate:

- A model ID such as `gpt-5.6-luna` is not globally unique.
- Multiple providers may expose the same model ID.
- The correct identity is the pair `(provider_id, model_id)`.
- Provider model labels are configured in `desktop-model-providers.json`
  (version 2), while the app's upstream model catalog may return a generic or
  missing `name`.

The picker transforms the upstream model list in
`codexUseProviderModels(...)`. It replaces the visible model entries with the
selected provider's configured models and attaches `providerId`, `name`,
`label`, and `displayName` (`Model Label (Provider Label)`). Label lookup is
implemented by `codexPickerModelLabelV4(...)`. Any future label lookup must
use the provider-aware identity; looking up by model ID alone will select the
first catalog entry and can display `5.6 Luna (a6api)` while TongApi is
selected.

The model shown in the composer and the provider shown in the custom-provider
menu are separate UI state. A provider menu checkmark alone does not prove
that an already-created thread or prewarmed thread will route through that
provider. Future fixes must verify both the visible identity and the outgoing
`thread/start` payload.

### Request routing

`codexPatchAppServerParams(...)` is the routing seam:

- `thread/start` is augmented with `modelProvider`.
- `thread/list` deliberately sends `modelProviders: null` so existing chats
  remain visible across provider configurations.
- The prewarm `thread/start` path must also be patched; otherwise the UI can
  display one provider while the prewarmed/default request uses another.
- The provider choice is read from
  `localStorage["codex.customProviderSelection.v2"]`.
- The cached picker configuration is stored in
  `localStorage["codex.customProviderRouting.v4"]`.
- The authoritative config is read from
  `desktop-model-providers.json` under the Codex home directory.

The current V18 routing code chooses the selected provider when that provider
contains the requested model, otherwise it falls back to the sole provider
that contains that model. If the selected provider is absent, stale, or not
matched to the model, routing can fall through to the configured default.
Diagnose these values in order: selected-provider storage, picker provider
state, model entry `providerId`, prewarm payload, then final `thread/start`
payload.

Inspection of the installed ChatGPT app confirmed that the patched
`app-initial-*.js` bundle contains the same V18 logic. The model menu calls the
generic `onSelectModel` callback with only `model` and reasoning effort; it
does not pass `providerId`. Therefore a duplicate model ID can keep the
visible model provider and the request provider out of sync. The V19 patch
adds `codexSyncProviderChoiceForModelV4(...)`, which synchronizes the provider
choice used by request routing with the provider currently used to build the
visible model list. Keep this synchronization in the provider-model hook,
not in individual menu callbacks, because both the compact and advanced model
menus share that hook.

The installed bundle also confirmed that the normal `sendRequest` path and
`prewarmThreadStart` both pass through `codexPatchAppServerParams`; future
routing debugging must inspect both paths before changing the request seam.

Important upgrade finding: changing only the source marker does not upgrade an
already-patched V18 bundle. V19 includes a dedicated V18-to-V19 upgrade
variant; without it, the installer rejects the installed V18 bundle and the
old frontend/backend bugs remain active.
The temporary extraction used for this inspection is `.codexier-app-inspect/`;
it is disposable and must never be committed.

### Known symptom and investigation rule

Observed symptom: choosing `5.6 Luna (a6api)` can still produce a TongApi
request, while choosing `5.6 Luna (TongApi)` can still leave the composer
footer showing `5.6 Luna (a6api)`. This represents two independent bugs. The
frontend and backend fixes are now in the V19 embedded patch:

1. **Backend/request bug:** the actual `thread/start` or prewarm request has
   the wrong `modelProvider` (often the default).
2. **Frontend/label bug:** a duplicate model ID is resolved through the
   upstream catalog or a model-only lookup instead of `(providerId, modelId)`.
   The provider-aware display fields and label helper must remain intact.

Do not infer routing from the footer label, and do not infer label correctness
from the provider menu checkmark. Reproduce each combination and inspect the
actual request payload. Tests must cover at least:

- a6api + shared model;
- TongApi + the same shared model;
- a provider-specific model;
- automatic/default routing;
- prewarm and normal `thread/start`;
- existing-chat visibility through `thread/list`.

When modifying embedded JavaScript, keep the patch source-validated, avoid
provider-specific hardcoded examples, never log API keys, and update focused
tests in `tests/test_desktop_patch_macos.py`.
