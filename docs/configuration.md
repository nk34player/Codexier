# Configuration mapping

Codexier auto-detects JSON and TOML targets. It currently recognizes:

```text
provider.base_url
provider.api_key
model_catalog
```

An empty new target is created using that mapping. Existing layouts that do not
match it are rejected instead of guessed. Use `--config` to choose among
multiple Codex configuration files.

Provider catalog entries contain `id`, `name`, `base_url`, `api_key`, `models`,
and optional `presets`. Model IDs are written to Codex; labels are display-only.
Any model returned by an OpenAI-compatible provider may be selected; there is
no model-count limit. Applying syncs every saved provider into one shared model
catalog, creates one Codex profile per provider, and sets the selected provider
as the default. Duplicate model IDs across providers are rejected before files
are written.
