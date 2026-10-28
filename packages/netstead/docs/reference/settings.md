---
title: Settings reference
audience: both
kind: reference
summary: Every section and key of netstead.config.Settings — type, default, meaning — plus the file locations, precedence, env-var form and the secret-refusal rule.
---

# Settings reference

## Summary

`netstead.config.Settings` is the one model behind the Workbench's Settings dialog, the `netstead app` flags, `SetSetting` actions and the config files. It has twelve typed sections (`io`, `engine`, `osm`, `overture`, `build`, `validation`, `select`, `llm`, `assistant`, `viz`, `app`, `credentials`) plus a free-form `plugins` table. Every section rejects unknown keys (`extra="forbid"`), so a typo in a file is an error, not a silently ignored line.

The tables below are generated from the pydantic model: field names, types and defaults are the model's own. The "Applies" column says when a change takes effect in a running Workbench: **live** means the next action reads the new value; **next launch** means the server reads it only when `netstead app` starts (these four keys are tagged *applies on next launch* in the Settings dialog, and a session-scope change to them does nothing).

## Where settings come from

Precedence, lowest to highest — a later layer overrides the earlier ones key by key:

1. Model defaults (the tables below).
2. User file: `~/.config/netstead/config.toml` (`$XDG_CONFIG_HOME/netstead/config.toml` when set; `%APPDATA%\netstead\config.toml` on Windows; `NETSTEAD_CONFIG_DIR` overrides the directory).
3. Project file: `./netstead.toml` in the current directory (or the `project_dir` passed to `load_settings` / `Session`).
4. Environment variables `NETSTEAD_<SECTION>__<FIELD>`.
5. Session overrides: CLI flags (`netstead app --port 9100`), `Session(overrides={...})`, and `SetSetting(scope="session")`.

`load_settings()` records which layer each leaf value came from; the Settings dialog shows that as a badge (`default`, `user`, `project`, `env`, `session`). Load the layers and read where a value came from:

```python
from netstead.config import get_value, load_settings

loaded = load_settings(environ={"NETSTEAD_VIZ__BASEMAP": "esri"}, overrides={"app.port": 9100})
print(loaded.settings.viz.basemap, loaded.sources["viz.basemap"])
print(get_value(loaded.settings, "app.port"), loaded.sources["app.port"])
print(loaded.sources["io.spec_version"])
```

Expected:

```text
esri env
9100 session
default
```

### Environment variables

The name is `NETSTEAD_` + the dotted key upper-cased with `__` for each dot: `NETSTEAD_APP__PORT=9100`, `NETSTEAD_LLM__OLLAMA__BASE_URL=http://gpu-box:11434`, `NETSTEAD_IO__ALLOWED_ROOTS='["/data/networks"]'`. A value starting with `[` or `{` is parsed as JSON (that is how lists and tables are passed); anything else is a string that pydantic coerces to the field's type. An env value outranks both files, and the Settings dialog says so when a saved value would not take effect.

### TOML files

Both files use the same shape — one table per section, nested tables for `llm.<provider>`, `llm.quality`, `validation.rules.<code>` and `plugins.<id>`:

```toml
# ./netstead.toml
[app]
port = 8900

[viz]
basemap = "esri"

[select]
provider = "ollama"

[llm.ollama]
base_url = "http://localhost:11434"

[validation.rules."quality.high_speed_residential"]
enabled = true
severity_override = "info"
thresholds = { max_free_speed = 45 }
```

`save_setting(key, value, scope="user" | "project")` writes one dotted key into the chosen file (and `value=None` removes it); the Workbench's **Save to** picker calls the same function.

### Secrets are never settings

API keys, tokens and passwords are refused everywhere settings are written:

* `SetSetting` refuses, before anything is recorded, a key or value that looks like an API key, a key whose last segment is secret-shaped (`*_key`, `token`, `password`, …), any URL carrying `user:password@`, and a `base_url` with a query string or fragment.
* `[plugins.<id>]` tables are free-form, so the model applies the same secret-name check to them in every layer — files and env included — and a hit fails validation with the key named, never the value.
* `llm.*` holds endpoints only; keys go through the Workbench's write-only key route or `netstead llm set-key` into the OS keyring. `credentials.keyring_hosts` names hosts, never what is stored for them.
* `io.allowed_roots` is config-only: the action API refuses to change it at any scope (a file, an env var or the command line can widen the sandbox; an action cannot).

## Sections

### `[io]` — reading and writing networks

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `io.spec_version` | `str` | `"0.97"` | GMNS spec version used to open networks and, unless a build names one, to build them. | live |
| `io.default_format` | `"csv" \| "parquet" \| "duckdb" \| "zip"` | `"parquet"` | Output format the Open / Import wizard prefills. | live |
| `io.allowed_roots` | `list[str]` | `[]` | Folders the Workbench may read and write (empty: the home directory). Config-only — see above. | live, config layers only |

### `[engine]` — DuckDB tuning

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `engine.threads` | `int \| None` | `None` | DuckDB thread count (`None`: DuckDB's own default). | not read yet |
| `engine.memory_limit` | `str \| None` | `None` | DuckDB memory limit string, e.g. `"4GB"`. | not read yet |

The Workbench notes this section as "not used yet: networks open with DuckDB's own defaults".

### `[osm]` — OpenStreetMap builds

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `osm.endpoint` | `str \| None` | `None` | Overpass API URL (`None`: the builder's default). | live |
| `osm.user_agent` | `str \| None` | `None` | User-Agent sent to Overpass / Nominatim (`None`: netstead's default). | live |
| `osm.timeout` | `int` | `180` | Per-request timeout in seconds. | live |
| `osm.retries` | `int` | `3` | Retries per request. | live |
| `osm.mapping_path` | `str \| None` | `None` | Path to a custom OSM-tag → GMNS mapping file. Declared in the model; no code reads it today. | not read yet |

### `[overture]` — Overture Maps builds

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `overture.release` | `str \| None` | `None` | Overture release string a build uses when the action does not name one (`None`: the builder's default). | live |
| `overture.data_root` | `str \| None` | `None` | Where Overture data is read from: a remote URL as is, or a local snapshot folder, which must lie inside `io.allowed_roots`. | live |

### `[build]` — Open / Import wizard defaults

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `build.network_type` | `list[str]` | `["roads"]` | Network types the wizard prefills: `roads`, `paths` or both. A single name is accepted; the old `drive` reads as `roads` and `walk`, `bike`, `all` as both. | live |
| `build.buffer_m` | `float` (> 0) | `1000.0` | Buffer in metres the wizard prefills for a point area. | live |
| `build.extra_tags` | `list[str]` | `[]` | Extra OSM tags the wizard prefills. | live |

### `[validation]` — quality rules

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `validation.rules` | `dict[str, RuleSettings]` | `{}` | One table per rule code (e.g. `quality.high_speed_residential`). | live (next validation) |

Each `RuleSettings` value has `enabled: bool = true`, `severity_override: "error" | "warning" | "info" | None = None` and `thresholds: dict = {}` (the rule's own threshold names). A code no installed rule has is reported as unknown by the validation result, not refused.

### `[select]` — natural-language selection

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `select.provider` | `"stub" \| "anthropic" \| "openai" \| "gemini" \| "ollama"` | `"stub"` | Which provider parses utterances (`stub` is the offline pattern parser). The old name `claude` is accepted and stored as `anthropic`. | live |
| `select.model` | `str \| None` | `None` | Model name (`None`: the provider's catalog default; for Ollama, an installed tool-capable model). | live |
| `select.grammar_first` | `bool` | `true` | Read a request with the offline pattern parser first and call the model only when that reading names a street, route number or road type the network doesn't have, or selects nothing. **Ask** does the same with the offline assistant (streets and columns must be in the network). `false` sends every request to the model. | live |

### `[llm]` — language-model endpoints and quality knobs

`llm.anthropic`, `llm.openai` and `llm.gemini` share one shape; `llm.ollama` has local defaults.

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `llm.anthropic.base_url` | `str \| None` | `None` | Endpoint override (`None`: the official endpoint). Must be `http(s)`, no userinfo, query or fragment. | live |
| `llm.anthropic.timeout_s` | `float` (0 < x ≤ 600) | `60.0` | Whole-request timeout. | live |
| `llm.openai.base_url` | `str \| None` | `None` | As above (any OpenAI-compatible server). | live |
| `llm.openai.timeout_s` | `float` (0 < x ≤ 600) | `60.0` | As above. | live |
| `llm.gemini.base_url` | `str \| None` | `None` | As above. | live |
| `llm.gemini.timeout_s` | `float` (0 < x ≤ 600) | `60.0` | As above. | live |
| `llm.ollama.base_url` | `str \| None` | `"http://localhost:11434"` | The Ollama server. A non-local URL means utterances leave this machine. | live |
| `llm.ollama.timeout_s` | `float` (0 < x ≤ 600) | `120.0` | Whole-request timeout. | live |

`llm.quality` controls what the natural-language features send and how hard they try. `"auto"` means on for a local endpoint (Ollama, or any loopback `base_url`) and off for a remote provider.

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `llm.quality.assistant_context` | `bool` | `true` | Send the shipped GMNS assistant notes (`gmns_assistant.md`) as context. | live |
| `llm.quality.assistant_context_max_chars` | `int` (0–100000) | `16000` | Cap on those notes. | live |
| `llm.quality.project_context` | `"auto" \| "on" \| "off"` | `"auto"` | Send the project's `AGENTS.md` / `CLAUDE.md`. | live |
| `llm.quality.project_context_max_chars` | `int` (0–50000) | `4000` | Cap on the project notes. | live |
| `llm.quality.grounding` | `"auto" \| "on" \| "off"` | `"auto"` | Send network vocabulary (street names, route numbers) to the model. | live |
| `llm.quality.grounding_max_names` | `int` (1–2000) | `200` | How many names grounding sends. | live |
| `llm.quality.few_shot` | `bool` | `false` | Add this session's resolved selections as examples (same network only; never persisted). | live |
| `llm.quality.few_shot_max` | `int` (1–10) | `3` | How many examples. | live |
| `llm.quality.max_repairs` | `int` (0–5) | `1` | Re-prompts after an invalid tool call. | live |
| `llm.quality.temperature` | `float \| None` (0–2) | `0.0` | Sampling temperature (`None`: the provider's default). | live |
| `llm.quality.match_retry` | `"auto" \| "on" \| "off"` | `"auto"` | After a miss, re-prompt with only the closest real names (a bool is accepted: `true` → `on`). | live |
| `llm.quality.match_candidates` | `int` (1–20) | `5` | How many candidate names that retry sends. | live |

### `[assistant]` — the natural-language assistant

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `assistant.actions` | `list[str]` | the 14 default action types (see below) | Core Actions the assistant may call. Opt-ins: `add_rows`, `set_setting`, `build_network`, `delete_selection`. `open_network` / `close_network` are never offered. | live |
| `assistant.plugins` | `list[str]` | `[]` | Plugin ids whose Actions it may call. | live |
| `assistant.max_steps` | `int` (1–20) | `6` | Tool calls per question. | live |
| `assistant.history_turns` | `int` (0–20) | `6` | Past turns sent with each question. | live |
| `assistant.max_turns` | `int` (1–500) | `50` | Turns kept in the conversation. | live |
| `assistant.output_token_limit` | `int` (128–8192) | `1024` | Output tokens per model call. | live |
| `assistant.context_max_chars` | `int` (1000–100000) | `12000` | Cap on the session state sent (grounded extras give way first). | live |
| `assistant.remote_token_budget` | `int` (0–10000000) | `200000` | Tokens a remote provider may spend this session before the assistant stops. | live |
| `assistant.compact_tools` | `"auto" \| "on" \| "off"` | `"auto"` | Offer a small model fewer tools per question (`auto`: on for a local endpoint). | live |

The default `assistant.actions` list is `select`, `clear_selection`, `pin_anchor`, `save_selection`, `use_selection`, `style`, `filter`, `navigate`, `set_active_network`, `run_validation`, `edit_cells`, `delete_rows`, `undo_edit`, `save_network` (`netstead.config.DEFAULT_ASSISTANT_ACTIONS`). It uses `select.provider` / `select.model` and the `llm.quality` privacy gates.

### `[viz]` — map rendering

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `viz.basemap` | `"positron" \| "esri"` | `"positron"` | Basemap style; a change applies without reloading the page. | live |

### `[app]` — the Workbench web server

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `app.host` | `str` | `"127.0.0.1"` | Bind address. A non-loopback host prints a warning at launch. | next launch |
| `app.port` | `int` | `8850` | TCP port. | next launch |
| `app.console` | `bool` | `false` | Tagged as a launch-time key in the Settings dialog; no code reads it today. | next launch |
| `app.approve_above_s` | `float` (≥ 0) | `90.0` | A build estimated to take longer than this (or that cannot be estimated) needs `approved=True`. | live |
| `app.disabled_plugins` | `list[str]` | `[]` | Plugin ids not to load. | next launch |

### `[credentials]` — host names only

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `credentials.keyring_hosts` | `list[str]` | `[]` | Hosts whose secrets live in the OS keyring (names only). The Workbench notes this section as not used yet. | not read yet |

### `[plugins.<id>]` — per-plugin tables

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `plugins` | `dict[str, dict]` | `{}` | One free-form table per Workbench plugin id, validated by that plugin's own settings model when it installs. Secret-named keys are refused in every layer. | per plugin |

For example, the bundled project-cards plugin reads `[plugins.project_cards]`:

| Key | Type | Default | Meaning | Applies |
|---|---|---|---|---|
| `plugins.project_cards.catalog_dir` | `str \| None` | `None` | A folder of ProjectCard files to open as the Cards panel's catalog on load (equivalent to calling `project_cards.open_catalog` once at startup). Must resolve inside `io.allowed_roots`. | next open |
| `plugins.project_cards.enable_apply` | `bool` | `false` | Turn on applying catalog cards to a network (`project_cards.apply_card` and the Cards panel's **Apply…**). Off, `apply_card` is refused and the panel offers no Apply. The assistant is never offered `apply_card`. | live |

## See also

* [Explore in the Workbench](../cookbook/workbench.md#settings) — the Settings dialog, scopes and badges.
* [Write a Workbench plugin](../cookbook/workbench-plugins.md) — how a plugin declares its `[plugins.<id>]` model.
* [Local language models (Ollama)](../cookbook/local-llm-ollama.md) — the `select` / `llm` keys in practice.
* [API reference](api.md#settings) — `Settings`, `load_settings`, `save_setting`, `get_value`.
