# Security notes

`providers.json` contains API keys in plaintext by design. Protect it with
filesystem permissions (`chmod 600`) and keep it outside source control.

Codex configuration receives the selected API key because Codex must use it.
Codexier masks keys in previews and errors, but cannot protect secrets from
other processes or users that can read the Codex configuration file.

Codexier does not use broad process commands such as `killall codex`. Restart
is skipped when process ownership, process count, or launch arguments are
ambiguous.
