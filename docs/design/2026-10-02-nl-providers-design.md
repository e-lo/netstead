# Multi-provider LLM support for the Workbench's natural-language features

Status: **implemented** (#211; accepted with the user's decisions) · Date: 2026-10-02 · Owner: netstead · Phase: after P1a, before P3

Related: [Workbench design](2026-10-02-netstead-workbench-design.md) (core Action bus, §e assistant, §g settings) · [network-viewer PRD §15](2026-09-30-network-viewer-prd.md) (NL action schema) · [NL selection design](2026-09-23-nl-selection-design.md) · [P1a plan](2026-10-02-workbench-p1a-plan.md) · Plan: [2026-10-02-nl-providers-plan.md](2026-10-02-nl-providers-plan.md)

## Context

Today natural language means a single provider:
- `netstead.select.parse.ClaudeParser` wraps the `anthropic` SDK. It forces the `emit_selection_intent` tool, and its model defaults to `claude-sonnet-5`.
- `StubParser` is a regex grammar used offline and in tests.
- `Settings.select.provider` is `Literal["stub", "claude"]`. The key is whatever `anthropic.Anthropic()` finds in `ANTHROPIC_API_KEY`.
- There is no UI for keys, no way to choose a model, and no local option.

The user wants:
- Local models through **Ollama** (Qwen in particular), plus **Gemini**, **Anthropic** and **OpenAI** via API keys.
- Keys entered in the UI but never exposed. They are kept only in a local secret store.
- A per-use choice of provider and model (for example Haiku vs Sonnet), offered only for providers that are actually set up.
- Settings that improve parsing and matching, visible to the user, including system context: a shipped GMNS guide and an optional project `AGENTS.md`.

PRD §15 already requires the NL layer to stay provider-agnostic. Its action schema is plain JSON-schema tool definitions, and the LLM emits validated structured output, never ids or code. This design makes that real for the one NL feature that exists today (utterance → `SelectionIntent`). It also leaves the seam the P3 assistant will reuse.

### Decisions taken with the user (2026-10-02)

1. **Tiny default models.** Each provider defaults to its small, fast tier: Haiku 4.5 for Anthropic, with Sonnet 5 available. See [Tiny models](#tiny-models-where-they-may-struggle-and-what-helps).
2. **Picker scope.** A provider/model choice applies to the session (`SetSetting(scope="session")`). **Make default** saves it to user scope.
3. **No plaintext key file.** Keys come from env vars, then the OS keyring. Without a keyring, the UI says which env var to set.
4. **No per-launch token now.** It is deferred to P4.
5. **AI-quality settings** live in `llm.quality`:
   - grounding vocabulary (on for local, off for remote);
   - the repair budget and temperature;
   - a close-match retry;
   - opt-in few-shot;
   - the context documents.

   All of them are visible and documented, and the privacy note follows them.
6. **Two layers of system context:**
   - **(a)** a shipped, maintained `gmns_assistant.md`, identical for every provider and cached where the adapter supports it;
   - **(b)** an optional project `AGENTS.md`/`CLAUDE.md`. Each has a toggle and a size cap. (b) is off for remote providers unless the user opts in.
7. **Alternatives** (PydanticAI, LiteLLM, instructor; MCP) are recorded, not built.

## Goals

1. **One small provider interface** with four adapters (anthropic, openai, gemini, ollama), plus the stub. The selection tool schema is byte-identical across providers.
2. **Keys are write-only from the browser's point of view.** They are never in TOML, any API response, history, `to_python`, SSE, logs or error messages.
3. **Keys can be configured from the UX** ("Set key / Replace / Remove / Test connection"), and also from the CLI for headless or exposed-bind setups.
4. **Provider + model picker** at every NL entry point. It lists only usable providers and shows a status dot. Changes apply to the session; **Make default** persists them.
5. **Model catalog as maintained data** (`netstead/llm/models.toml`), with a user overlay file. Ollama models are discovered at runtime.
6. **Better parses and matches, under user control:**
   - the shipped guide;
   - project notes;
   - grounding vocabulary;
   - few-shot examples;
   - a close-match retry;
   - repairs and temperature.

   All of them are in `llm.quality`, shown in the UI, and reflected in a generated privacy note.
7. **Explicit, user-facing errors** for missing or invalid keys, rate limits, timeouts and unreachable servers. There is no silent fallback to another provider.
8. **Lean dependencies:** hand-rolled HTTP over `httpx`, no vendor SDKs (justified below).
9. **Offline tests:** mocked HTTP per adapter, recorded-fixture contract tests, and an opt-in live smoke marker.

## Non-goals

- The P3 chat assistant itself: multi-turn chat, tool results, Style/Filter/Navigate tools, draft-before-apply. This design only gives it the provider layer, the prompt-context layer, and the picker.
- Streaming responses. A selection is one short tool call, so latency is dominated by model time, not streaming.
- Cost metering or budgets. Token counts are captured in `Completion` for later use and are not surfaced.
- Azure OpenAI, Bedrock and Vertex auth flows (OAuth, SigV4, service accounts). OpenAI-*compatible* endpoints that use bearer keys (vLLM, LM Studio, OpenRouter, Groq) work through a `base_url` override.
- LLM-side n-best candidate lists. Ambiguity is resolved deterministically: the resolver already ranks anchor candidates (`AnchorMatch.candidates`), and P3 adds pinning. The LLM-side matching knob is the close-match retry.
- Per-launch auth tokens for the server. That belongs to P4's `--console` and will then gate `/api/actions` and `/api/llm/*` writes together.

## Decisions at a glance

| Topic | Decision |
|---|---|
| HTTP | Hand-rolled adapters over `httpx` (in the `[nl]` extra). Drop `anthropic` from `[nl]`. |
| Interface | `LLMProvider.complete(CompletionRequest) -> Completion` and `list_models() -> list[str]`. A request carries a cacheable `context` and a per-call `system`, plus `temperature`. |
| Parser | `LLMParser(provider, model).parse(utterance, *, context=PromptContext)`. `ClaudeParser` is kept as a back-compat subclass (default Haiku). |
| Tool-less models | Automatic JSON mode (same provider, same model), validated with a bounded repair loop |
| Catalog | `netstead/llm/models.toml` plus `<user config dir>/llm_models.toml` overlay. Tiny defaults. Ollama comes from `GET /api/tags`. |
| Key storage | env (`NETSTEAD_<P>_API_KEY`, then the provider's own name) → OS keyring (service `netstead-llm`). **No file.** |
| Key binding | A key slot is bound to the endpoint origin it was entered for. Changing `base_url` never sends an existing key to a new host. |
| Key routes | `/api/llm/*`: not Actions, not recorded. Writes need a loopback bind plus the `X-Netstead-Secrets: 1` header. |
| Settings | `select.provider ∈ {stub, anthropic, openai, gemini, ollama}` (`claude` → `anthropic`); `select.model: str \| None`; `llm.<provider>.{base_url, timeout_s}`; **`llm.quality.*`** |
| Picker choice | A recorded `SetSetting(scope="session")`. **Make default** records the same pair with `scope="user"`. |
| Context | Shipped `netstead/llm/context/gmns_assistant.md` (cached prefix) + optional `AGENTS.md`/`CLAUDE.md` (gated per provider) |
| Errors | `LLMError` subclasses become `ActionError` with an actionable message. An invalid model *output* still becomes a "could not parse" selection after repairs. |

## Architecture

```
netstead/llm/                         (new, provider-neutral; no netstead.select / workbench imports)
  types.py        Tool, Message, ToolCall, CompletionRequest(context, system, temperature, …), Completion, LLMProvider
  errors.py       LLMError → MissingKey, InvalidKey, RateLimited, ProviderTimeout, ProviderUnavailable,
                  ModelNotFound, BadRequest, BadResponse, ToolsUnsupported
  _http.py        request_json(): the ONE network call path (lazy httpx, status→error mapping, scrubbing)
  providers/      _base.HTTPProvider; anthropic.py (prompt caching), openai.py, gemini.py, ollama.py; ADAPTERS
  structured.py   request_tool_call(): forced tool → validate → repair; JSON-mode fallback
  catalog.py      load_catalog(): models.toml + user overlay → Catalog/ProviderInfo/ModelInfo
  models.toml     maintained data (tiny defaults)
  secrets.py      KeySlot, SecretStore (env → keyring), redact(), looks_like_secret()
  registry.py     ProviderRegistry: provider(), status(), models(), test(),
                  is_local(), grounding_on(), project_context_on(), disclosure()
  context/        gmns_assistant.md (shipped guide) + assistant_context(), find_project_context(), read_capped()

netstead/select/prompt.py      PromptContext, render_prompt(), vocabulary_from_links(), close_match_hint()
netstead/select/parse.py       LLMParser, ClaudeParser (alias), make_parser(select, registry), payload_from_intent()
netstead/config.py             SelectSettings widened; LLMSettings (+ LLMQualitySettings)
netstead/workbench/session.py  Session.llm; prompt context; few-shot memory; close-match retry; LLMError → ActionError
netstead/workbench/routes/llm.py   /api/llm/{providers, models, keys/{p}, test}
netstead/workbench/static/js/llm.js  header picker (+ Make default) + "Language models" panel (+ quality & context)
netstead/cli/commands/llm.py   netstead llm {status, set-key, remove-key, test, models}
corral/io/credentials.py  + system_keyring(): shared "is a real OS keyring usable?" check
```

**Dependency direction:**
- `netstead.llm` → `netstead.config` and `corral.io.credentials`.
- `netstead.select` → `netstead.llm`.
- `netstead.workbench` → both.

`netstead.llm` imports nothing optional at module import time: `httpx`, `jsonschema` and `keyring` are imported lazily. So `import netstead.select` still works on a core install, and the `--doctest-modules` sweep stays green.

## Provider interface

```python
@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: tuple[Message, ...]        # plain-text user/assistant turns
    system: str = ""                     # per-call part of the system prompt (examples, hints, JSON-mode text)
    context: str = ""                    # stable, cacheable part (system prompt, guide, notes, vocabulary)
    tools: tuple[Tool, ...] = ()         # Tool(name, description, input_schema): plain JSON schema
    force_tool: str | None = None        # "must call this tool" where the provider supports it
    json_schema: dict | None = None      # JSON mode (native where supported: Ollama `format`)
    max_tokens: int = 1024
    temperature: float | None = None     # None = the provider's default
    def full_system(self) -> str: ...    # context + system, for adapters without prompt caching

@dataclass(frozen=True)
class Completion:
    tool_calls: tuple[ToolCall, ...] = ()   # ToolCall(name, arguments: dict)
    text: str = ""
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

class LLMProvider(Protocol):
    name: str            # "anthropic" | "openai" | "gemini" | "ollama"
    label: str
    def complete(self, request: CompletionRequest) -> Completion: ...
    def list_models(self) -> list[str]: ...  # authenticated, token-free; powers "Test connection" + Ollama discovery
```

The brief sketched `complete_tools(messages, tools, model) -> ToolCalls`. A request object is used instead, so new fields (`context` and `temperature` here, P3's tool results later) don't change every adapter's signature.

**Messages are plain text on purpose.** The repair loop shows the model its previous attempt as an *assistant text* turn, not as a provider-specific tool-use/tool-result pair. That keeps every adapter to one simple mapping.

## Adapters (wire mapping)

All four inherit `HTTPProvider`. It holds the key privately, overrides `__repr__` so the key never appears in tracebacks, and sends every request through `_http.request_json`.

| | Anthropic | OpenAI (+ compatible) | Gemini | Ollama |
|---|---|---|---|---|
| Default base | `https://api.anthropic.com` | `https://api.openai.com/v1` | `https://generativelanguage.googleapis.com/v1beta` | `http://localhost:11434` |
| Call | `POST /v1/messages` | `POST /chat/completions` | `POST /models/{model}:generateContent` | `POST /api/chat` (`stream:false`) |
| Auth header | `x-api-key` + `anthropic-version: 2023-06-01` | `Authorization: Bearer` | `x-goog-api-key` (**never** `?key=`: URLs get logged) | none |
| System prompt | `system: [{text: context, cache_control: {type: ephemeral}}, {text: system}]` | leading `system` message (`full_system()`) | `systemInstruction` (`full_system()`) | leading `system` message (`full_system()`) |
| Temperature | `temperature` | `temperature` | `generationConfig.temperature` | `options.temperature` |
| Tools | `tools[{name,description,input_schema}]` | `tools[{type:function,function:{…,parameters}}]` | `tools[{functionDeclarations[{…,parametersJsonSchema}]}]` | same shape as OpenAI |
| Force tool | `tool_choice{type:tool,name}` | `tool_choice{type:function,function:{name}}` | `toolConfig.functionCallingConfig{mode:ANY,allowedFunctionNames}` | not supported (repair loop covers it) |
| Calls out | `content[].type=="tool_use"` → `input` | `choices[0].message.tool_calls[].function.arguments` (JSON **string**) | `candidates[0].content.parts[].functionCall.args` | `message.tool_calls[].function.arguments` (object) |
| JSON mode | prompt-only | prompt-only | prompt-only | native `format: <schema>` |
| List models | `GET /v1/models` | `GET /models` | `GET /models` (filter `generateContent`) | `GET /api/tags` |

Notes:
- **OpenAI uses Chat Completions, not the Responses API.** Chat Completions is the de facto "OpenAI-compatible" surface (vLLM, LM Studio, OpenRouter, Groq, and both Gemini's and Ollama's compatibility layers), so one adapter serves every compatible endpoint. It sends no `max_tokens`/`max_completion_tokens`, because compatible servers disagree on the name.
- **Gemini `parametersJsonSchema`** accepts full JSON Schema. The older `parameters` field takes an OpenAPI subset that rejects our free-form `conditions` object. *Verify at implementation time* and re-record the contract fixture (plan Task 18). The fallback is a sanitiser that maps `conditions` to `{"type":"object","properties":{}}`.
- **Ollama tool support is detected at use.** A model without tools answers HTTP 400 "… does not support tools". `_http` maps that to `ToolsUnsupported`, and `structured.py` retries the *same model* in JSON mode with `format: <schema>`, recorded as `parsed_by.mode = "json"`.
- **Temperature caveat.** Some OpenAI reasoning-class models accept only their default temperature and answer HTTP 400 otherwise. That shows as a `BadRequest` toast naming the parameter, and the fix is to clear `llm.quality.temperature` (blank in the UI means "provider default"). The tiny defaults all accept 0.

### Prompt caching

`render_prompt` (in `netstead.select.prompt`) puts everything that only changes when the network or the settings change first, in one **stable** block:
- the system prompt;
- the shipped guide;
- the project notes;
- the vocabulary.

Few-shot examples and one-off hints go in a **per-call** block. JSON-mode instructions also go in the per-call block, so a repair or a mode switch doesn't change the prefix.

How each provider caches:
- **Anthropic:** the stable block is sent as the first `system` text block with `cache_control: {"type": "ephemeral"}`. That is one dictionary key, and caching is cheap to support. Below the model's minimum cacheable prompt length the marker is ignored, with no error and no saving. The guide alone is about 1.1k tokens, which may be under that minimum for Haiku. Vocabulary or project notes usually push the prefix over it, and the plan's follow-up measures `cache_read_input_tokens` in the live smoke test.
- **OpenAI and Gemini:** they cache long prefixes automatically on recent models, and putting the stable block first is what makes that work. No flag is needed.
- **Ollama:** it keeps the KV cache for an identical prefix between calls on the same loaded model, so the same ordering helps there too.

## Structured output: forced tool, JSON mode, repair loop

`netstead.llm.structured.request_tool_call(provider, *, model, tool, user, system, context, validate, json_mode=False, max_repairs=1, temperature=None)`:

1. **Tools mode:** send `tools=(tool,)` and `force_tool=tool.name`. On `ToolsUnsupported`, switch to JSON mode.
2. **JSON mode:** append "Reply with ONLY one JSON object … schema: …" to the per-call system text. Set `json_schema` so that Ollama constrains decoding natively.
3. **Extract:** take the matching tool call, or else the first `{…}` object in the text. Drop explicit `null`s, which several providers emit for unused optional fields.
4. **Validate:** check the JSON schema, then the caller's `validate` (`intent_from_payload` → `IntentError`).
5. **Repair:** on a validation failure, append the previous reply as an assistant turn plus "Your previous reply was not usable: {error}. Try again: …". Allow at most `max_repairs` extra calls (`llm.quality.max_repairs`, default 1).
6. **Give up:** raise `StructuredOutputError`. `LLMParser` turns it into `IntentError`, so the Workbench shows a normal `not_found` selection with the diagnostic "could not parse: …".

Transport, auth, rate-limit and timeout errors (`LLMError`) are **never** retried here and never trigger another provider.

## AI quality and context (`llm.quality`)

Every knob is a normal setting, so it shows in the UI, the CLI's `config.toml`, history and replay. `"auto"` means **on for a local endpoint** (Ollama, or any loopback `base_url`) and **off for a remote provider**. `ProviderRegistry.grounding_on(name)` / `project_context_on(name)` hold that rule in one place.

| Setting | Default | Sent where | What it does |
|---|---|---|---|
| `assistant_context`, `assistant_context_max_chars` | `true`, 16000 | every provider | Sends the shipped GMNS guide: data model, field meanings, how to fill the tool, worked examples, the action vocabulary |
| `project_context`, `project_context_max_chars` | `"auto"`, 4000 | local by default | Sends `AGENTS.md` (else `CLAUDE.md`) from next to the active network source, else the project dir. These are local aliases and code meanings |
| `grounding`, `grounding_max_names` | `"auto"`, 200 | local by default | Sends the active network's most frequent route numbers and street names (`ref`, `name`), so the model writes "Airport Boulevard" rather than "Airport Blvd" |
| `match_retry`, `match_candidates` | `true`, 5 | wherever grounding is on | If the selection resolves to `not_found` and a facility or anchor name isn't in the vocabulary, re-prompts **once** with up to N closest real names (stdlib `difflib`). Recorded as `parsed_by.match_retry` |
| `few_shot`, `few_shot_max` | `false`, 3 | every provider when on | Shows the model this session's last N *resolved* selections (utterance → tool input). This is user content, so it is opt-in |
| `max_repairs` | 1 | — | Re-prompts after an invalid reply |
| `temperature` | `0.0` | — | Deterministic parses by default; blank means the provider default |

**The two context layers in detail.**
- **(a) `netstead/llm/context/gmns_assistant.md`** ships with netstead and is maintained like `models.toml`.
  - It covers the link fields and their meanings, how to choose one primary selector, how to map direction words and route numbers, how anchors work, how to use `conditions` and `modes`, seven worked examples, and a note that more action tools will come.
  - It is identical for all providers.
  - A drift test checks that it names every property of the selection tool and that every worked example is valid tool input.
- **(b) The project's `AGENTS.md`** (also `CLAUDE.md`) is discovered next to a *local* network source (a folder source itself, or a file source's parent), else in the project dir. URL sources are never searched.
  - It is capped, truncated with a visible marker, and prefixed "Project notes from AGENTS.md:".
  - It is user content, so for remote providers it is off unless `project_context = "on"`.
- Both layers are listed in the privacy note whenever they would be sent.

**Privacy note.** `ProviderRegistry.disclosure(name)` turns the effective settings into the list of things a selection sends to `name`, for example:
- your utterance;
- the selection tool's schema;
- the GMNS assistant guide;
- up to 200 street names and route numbers from the active network;
- your project notes;
- up to 3 earlier selections.

Every status row carries that list as `sends`. The picker tooltip and the panel render it (`privacyNote`), so the note can never drift from what is actually sent.

### Tiny models: where they may struggle, and what helps

The default models are small. For one forced tool call with a short utterance this is usually enough. These are the places a tiny model is weakest:

| Risk | Why | Mitigation (default) | Recommendation |
|---|---|---|---|
| Repair loop doesn't converge | Small models often repeat the same invalid output after one correction | Schema check + `max_repairs=1`, then an honest "could not parse" | Keep 1 for remote tiny models (cost). For Ollama ≤ 8B, `max_repairs=2` is cheap and helps; for persistent failures switch the picker to Sonnet 5 / the balanced tier |
| No forced tool choice (Ollama) | The model may answer in prose | Text-JSON extraction + repair; JSON mode via native `format` when tools are unsupported | Prefer a Qwen 3 tag with tool support; native `format` JSON is the most reliable path for ≤ 4B models |
| Spelling of street names / refs | The model normalises ("Blvd", "I-40") differently from the network | Guide rules ("I-40" → "I 40"); grounding vocabulary (on for Ollama); `match_retry` | For remote tiny models, opt in to `grounding = "on"` if network names are not sensitive |
| Long system prompt dilutes attention | Guide + notes + vocabulary can reach several thousand tokens | Caps on every part; vocabulary limited to the most frequent 200 names | Lower `grounding_max_names` (for example 80) for ≤ 4B local models; keep `AGENTS.md` short and factual |
| Over-filling optional fields | Small models invent anchors or conditions | Guide: "leave out any field you don't need"; null-dropping; `IntentError` validation | Few-shot (`few_shot = true`) once a few selections have resolved |
| Temperature sensitivity | Sampling noise affects small models most | `temperature = 0.0` | Keep 0 |

## Model catalog (`netstead/llm/models.toml`)

Maintained data, one table per provider (`label`, `kind`, `base_url`, `key_env`, `default_model`, `[[models]]` with `id`, `label`, `tier`, `tools`).

| Provider | Default (fast tier) | Also listed | Status |
|---|---|---|---|
| Anthropic | Haiku 4.5 `claude-haiku-4-5-20251001` | Sonnet 5 `claude-sonnet-5`, Opus 5.5 `claude-opus-5-5` | current ids |
| OpenAI | `gpt-4.1-mini` | `gpt-4.1`, `gpt-5` | **VERIFY** |
| Gemini | `gemini-2.5-flash-lite` | `gemini-2.5-flash`, `gemini-2.5-pro` | **VERIFY** |
| Ollama | `qwen3:4b` | `qwen3:8b` (suggestions only; the picker lists `GET /api/tags`) | **VERIFY** tags |

- `netstead llm test <provider>` reports every catalog id the live endpoint does not serve (`catalog_missing`), so verification is one command per release.
- **User overlay:** `<user config dir>/llm_models.toml` can add or relabel models and change `label`/`default_model`. It **cannot** change `base_url`, `key_env` or `kind`, because those decide where keys are sent.
- `select.model` is a free string. The catalog drives the picker and is never a whitelist.

## Secrets

### Storage and resolution

`SecretStore.lookup(slot)` checks, in order (the same "explicit beats ambient" shape as corral's `resolve_credentials`):
1. **env**, for the official endpoint only: `NETSTEAD_<PROVIDER>_API_KEY`, then the provider-standard name (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`). The names come from `key_env` in the catalog.
2. **OS keyring:** `keyring.get_password("netstead-llm", slot.name)`. This uses Keychain on macOS, Credential Manager on Windows, and Secret Service on Linux.

**There is no plaintext fallback.** When `system_keyring()` finds no usable backend (headless Linux, a container, or WSL without a Secret Service):
- `SecretStore.set` refuses, with the same message `MissingKey` gives: "This machine has no OS keyring, so set NETSTEAD_OPENAI_API_KEY or OPENAI_API_KEY in the environment that starts netstead, then restart it."
- The UI hides **Set key** and shows each provider's env var names (`key_env` in the status row).
- `netstead llm status` prints `key storage: none (no OS keyring): use environment variables`.

Corral gains one public helper, `system_keyring()`. Both packages use it, so "is there a keyring?" has one answer. Corral's `resolve_credentials` cascade for data hosts is unchanged. LLM keys use their own keyring service, so they never leak into fsspec `storage_options`.

**Env keys can't be removed from the UI.** The UI shows "key set · env" with no Remove button.

### Key slots are bound to an origin

`KeySlot(provider, origin)`:
- When `llm.<provider>.base_url` is unset, or equals the catalog's official URL, the slot is the plain provider name (`"openai"`), and env vars apply.
- Any other origin gets its own slot (`"openai@https://llm.example.org"`), with no env fallback.

So **pointing a provider at a new URL never sends an existing key there**.

A consequence of dropping the file: a *custom* endpoint's key can only be stored in a keyring, because env vars apply only to the official endpoint. On a machine without a keyring, custom keyed endpoints aren't available. Keyless local servers such as LM Studio still work. This is open question 1.

### Write-only flow: set → test → use

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant R as /api/llm routes
    participant S as SecretStore
    participant K as OS keyring
    participant P as Provider API
    participant D as Session.dispatch
    U->>R: PUT /api/llm/keys/openai {key} + X-Netstead-Secrets: 1
    Note over R: loopback bind? header? Host/Origin guard (middleware)
    R->>S: set(KeySlot("openai"), key)
    S->>K: set_password("netstead-llm", "openai", key)
    R-->>U: {providers:[{provider:"openai", configured:true, source:"keyring", sends:[…]}…]}  (no key)
    R-->>U: SSE event "llm" (status only); log "llm key set for openai (keyring)"
    Note over U: input cleared immediately; key never enters store.js/DOM/storage
    U->>R: POST /api/llm/test {provider:"openai"}
    R->>S: get(slot) → key
    R->>P: GET /models (Authorization: Bearer …)
    R-->>U: {ok:true, latency_ms, models_served, catalog_missing:[…]}
    U->>D: POST /api/actions set_setting select.provider=openai, select.model=… (scope=session; recorded, no secret)
    U->>D: POST /api/actions select {utterance}
    D->>S: get(slot) (via ProviderRegistry.provider)
    D->>P: POST /chat/completions (forced tool; system = guide [+ notes, vocabulary when allowed])
    D-->>U: selection {status, link_ids, parsed_by:{provider:"openai", model, mode:"tools"}}
```

Setting a key is **not an Action**. The only traces are a status-only SSE `llm` event and one log line, `llm key set for openai (keyring)`.

`SetSetting` rejects values that *look like* an API key before the action is recorded. The `/api/actions` 422 body stops echoing input values.

## Settings changes (`netstead/config.py`)

```python
class SelectSettings(_Section):
    provider: Literal["stub", "anthropic", "openai", "gemini", "ollama"] = "stub"   # "claude" accepted, stored as "anthropic"
    model: str | None = None            # None → the provider's catalog default_model

class LLMEndpointSettings(_Section):
    base_url: str | None = None         # None → official endpoint; http(s) only
    timeout_s: float = 60.0             # 0 < t ≤ 600

class OllamaSettings(LLMEndpointSettings):
    base_url: str | None = "http://localhost:11434"
    timeout_s: float = 120.0

class LLMQualitySettings(_Section):     # see "AI quality and context"
    assistant_context: bool = True;  assistant_context_max_chars: int = 16000
    project_context: Literal["auto", "on", "off"] = "auto";  project_context_max_chars: int = 4000
    grounding: Literal["auto", "on", "off"] = "auto";  grounding_max_names: int = 200
    few_shot: bool = False;  few_shot_max: int = 3
    max_repairs: int = 1;  temperature: float | None = 0.0
    match_retry: bool = True;  match_candidates: int = 5

class LLMSettings(_Section):            # endpoints and quality knobs, never keys
    anthropic, openai, gemini: LLMEndpointSettings
    ollama: OllamaSettings
    quality: LLMQualitySettings
```

- `provider = "claude"` (file, env var or `--provider claude`) validates as `"anthropic"`.
- Changing any `llm.*` or `select.*` key resets the session's cached parser and provider registry.
- The P1b Settings form will show these too. P1b should hide `llm.*` there, because the Language-models panel edits it.

## API routes (`netstead/workbench/routes/llm.py`)

| Route | Recorded? | Guard | Returns |
|---|---|---|---|
| `GET /api/llm/providers` | no | Host guard | `{providers:[{provider,label,kind,base_url,local,default_model,key_env,sends,configured,source,usable,error,models}], keyring, selected:{provider,model}, key_writes}` |
| `GET /api/llm/models?provider=` | no | Host guard | `{provider, models:[{id,label,tier,tools,installed?}]}` |
| `PUT /api/llm/keys/{provider}` body `{key}` | **no** (log line + SSE status) | Host + Origin + loopback bind + `X-Netstead-Secrets: 1` | the providers snapshot |
| `DELETE /api/llm/keys/{provider}` | **no** | same | the providers snapshot |
| `POST /api/llm/test` body `{provider, model?}` | no | same | `{ok, latency_ms, models_served, catalog_missing, message}` or `{ok:false, error_type, message}` |
| `POST /api/llm/ollama/pull` body `{model}` | **no** (job events only; see below) | Host + Origin + loopback bind + `X-Netstead-Secrets: 1` | `202 {job}` |

- `source` is `"env"`, `"keyring"` or `null`. The `PUT` body is validated inside the handler, so a 422 can never echo it.
- Choosing a provider or model is **not** a route. It is a recorded, replayable `set_setting` action.

### Pulling a model (`POST /api/llm/ollama/pull`, `netstead llm pull`)

Downloading a model can be several gigabytes, so it gets its own guards and lifecycle rather than
reusing the key-write or Action machinery above.

- **Guards.** The same loopback bind and `X-Netstead-Secrets: 1` header as a key write (`pull_guard`
  in `routes/llm.py`), plus `llm.ollama.base_url` must resolve to *this* machine
  (`ProviderRegistry.is_local("ollama")`): the browser can only make this process download, never
  point it at another host. The model name itself is validated by
  `valid_model_name` (`netstead.llm.providers.ollama`): a plain `[namespace/]name[:tag]`, never a URL
  or a dotted registry host (which Ollama would read as a different registry).
- **A background job, not an Action.** The route submits an `ollama_pull` job
  (`session.jobs.submit`) and answers `202` with the job's snapshot immediately; progress streams
  as `job` events (Cancel closes the HTTP stream, which stops Ollama's own download) exactly like
  any other job in the Jobs panel. It is deliberately **not recorded** in session history, for the
  same reason a key write isn't: replaying a session must never re-trigger a multi-gigabyte
  download, and the model stays on disk long after any one session ends.
- **409 on a duplicate.** A second `POST` for the same model while its own pull job is still
  `running` is refused with `409` ("… is already being pulled; see Jobs") instead of starting a
  second download of the same model.
- **The CLI's `netstead llm pull MODEL`** runs the same download from a terminal, with a Rich
  progress bar instead of job events, and treats a bare name as `name:latest` when checking
  whether the model is already installed, so `netstead llm pull qwen3` recognizes an installed
  `qwen3:latest` and warns before re-downloading rather than always reporting it as new.

**Catalog `thinking` flag and the reasoning-model token budget.** Two related fixes (commit
`bab6083`) keep a model's hidden reasoning from silently spending the whole output budget before
any visible reply, which otherwise shows up as an empty or truncated selection:
- `ModelInfo.thinking` (`netstead/llm/catalog.py`) marks an Ollama model that emits hidden `thinking`
  output by default (`qwen3:4b` and `qwen3:8b` in `models.toml`). The Ollama adapter sends
  `"think": false` for exactly those models, and omits the `think` key entirely for every other
  model (sending it to a model the catalog doesn't mark errors with "does not support thinking").
- OpenAI's reasoning-tier models (o1/o3/o4-mini, gpt-5, gpt-6) count hidden reasoning tokens
  against `max_completion_tokens`, so the default 1024-token cap could leave nothing for the
  visible reply (HTTP 200, empty content, `finish_reason="length"`). The cap is now floored to
  8192 tokens for those models, and they get `reasoning_effort="low"` to leave more of that budget
  for the reply (`gpt-6-luna` keeps `"none"` when a tool call is forced, per its own docs, and
  otherwise is left alone). An empty reply with `finish_reason="length"` now raises a clear "ran
  out of output tokens" error instead of the generic "reply was not a JSON object" one.

## UX

**Header picker**, next to the utterance box (the P3 assistant drawer reuses the same component):

```
[ I-40 EB between …            ] ● [Anthropic ▾] [Haiku 4.5 · fast ▾] [Make default] [Models…] [Select]
```

- **Providers:** "Offline (pattern)" (always there), plus every provider with `usable: true`. A provider the current setting names but that isn't set up shows as a disabled "(not set up)" entry. The picker never switches silently.
- **Status dot:** green = remote and usable; teal = local; amber = set up but not working (the last **Test** failed, or Ollama is running with no models); grey = not set up.
- **Scope:**
  - A change dispatches `set_setting` with `scope:"session"`. A provider change also sets `select.model` to that provider's `default_model`, so history replays exactly.
  - **Make default** dispatches the current provider/model with `scope:"user"`. It is enabled only while either key is session-only, and it then disables.
- **Tooltip:** the privacy note for the chosen provider, generated from `sends`.

**"Language models" panel**, opened from **Models…**. Until P1b ships the Settings workspace, it floats. `llm.js` renders the whole panel into `#llm-panel`, so P1b can mount it.

- **Privacy note** for the selected provider. An example for Anthropic with defaults:
  > Anthropic is a remote service. Each selection sends: your utterance; the selection tool's schema (GMNS field names such as lanes); the GMNS assistant guide that ships with netstead. Never your network tables or files.

  For Ollama: "Ollama (local) runs on this machine, so nothing leaves it." followed by its list.
- **Storage line:** "Keys are stored in your OS keychain (keyring), or read from environment variables", or the no-keychain instruction, or "key changes are disabled because the Workbench is exposed to the network; use `netstead llm set-key`".
- **One row per provider:** dot, label, `local` tag, status ("key set · keyring", "no key · set OPENAI_API_KEY …", "running · 3 model(s)", or the error text). The actions are **Set key / Replace** (an inline `type=password` field), **Remove** (with confirmation) and **Test**.
- **Quality & context:** a control per `llm.quality` key. Changes are saved with `scope:"user"` and refresh the privacy note.
- **Ollama URL**, and the **model catalog** view with the overlay-file hint.

**Key hygiene in the browser.** The key exists only in the password input until the `PUT`. The input is cleared in the same tick and the form removed from the DOM. Nothing is written to `store.js`, `localStorage` or `sessionStorage`, and a static test asserts it.

**CLI** (for headless use and exposed binds):
- `netstead llm status [--json]`
- `netstead llm set-key PROVIDER [--stdin]`: a hidden prompt, never argv
- `netstead llm remove-key PROVIDER`
- `netstead llm test PROVIDER [--model M]`
- `netstead llm models PROVIDER`

`netstead select` and `netstead app` accept `--provider {stub,anthropic,openai,gemini,ollama,claude}` and `--model`.

## Error handling

| Condition | Raised | User sees (toast + failed history entry) |
|---|---|---|
| No key (keyring available) | `MissingKey` | "OpenAI: no API key is configured. Add one in Settings → Language models, or set NETSTEAD_OPENAI_API_KEY or OPENAI_API_KEY." |
| No key (no keyring) | `MissingKey` | "OpenAI: no API key is configured. This machine has no OS keyring, so set NETSTEAD_OPENAI_API_KEY or OPENAI_API_KEY in the environment that starts netstead, then restart it." |
| 401/403 | `InvalidKey` | "OpenAI rejected the API key (HTTP 401). Replace it in Settings → Language models." (no provider detail) |
| 429 | `RateLimited(retry_after_s)` | "Gemini rate limit or quota reached (HTTP 429); retry in 20 s." |
| Timeout / 408 | `ProviderTimeout` | "Ollama did not answer within 120 s; try again, or raise the timeout in Settings → Language models." |
| Connect error / 5xx / 529 | `ProviderUnavailable` | "could not reach Ollama at http://localhost:11434 (ConnectError)." |
| 404 | `ModelNotFound` | "Ollama: model or endpoint not found (HTTP 404): model "qwen3:4b" not found, try pulling it first" |
| Other 4xx (including an unsupported temperature) | `BadRequest` | provider detail, scrubbed and truncated to 300 characters |
| Unexpected JSON shape, blocked prompt | `BadResponse` | "Gemini blocked the request (SAFETY)." |
| Model output invalid after repairs | `StructuredOutputError` → `IntentError` | a `not_found` selection, "could not parse: …" (not an error) |
| No match after the close-match retry | — | a normal `not_found` selection; `parsed_by.match_retry: true` |

The session turns `LLMError` into `ActionError`, whether it is raised while building the parser or while parsing. Other parse failures become a "could not parse" selection. Nothing ever falls back to another provider. JSON mode on the same model is the only automatic change of strategy, and it is visible in `parsed_by.mode`.

Every message is built by our code. Provider `error.message` text passes through `redact()`, and `httpx` exceptions are raised `from None`.

## Security review

### Assets

- **A-key:** provider API keys.
- **A-data:** utterances, network vocabulary, project notes, earlier selections.
- **A-cfg:** settings integrity, meaning where requests go.

### Adversaries

- **ADV-web:** a malicious web page in the user's browser (cross-origin requests, DNS rebinding).
- **ADV-repo:** a malicious `netstead.toml` or `AGENTS.md` in a cloned project, or a hostile env var.
- **ADV-local:** another OS user or process that can reach `127.0.0.1:8850`.
- **ADV-lan:** a LAN host, when the user binds `--host 0.0.0.0`.
- **ADV-llm:** a provider's or model's output, including prompt injection.
- **ACC:** accidents (logs, history, screenshots, shell history, commits).

### Threat model

| # | Threat | Adversary | Mitigation | Residual |
|---|---|---|---|---|
| T1 | Read a key through the API | web, local, lan | No route, response, SSE event, history entry or `to_python` snippet carries a key. The canary test (plan Task 15) greps every route, SSE event, log record and config file | None via the API |
| T2 | Set, replace or delete a key cross-origin | web | Host allowlist + Origin/`Sec-Fetch-Site` guard. Key routes also need `X-Netstead-Secrets: 1`, which forces a preflight that is never approved | None known |
| T3 | DNS rebinding to read status | web | Host allowlist | Which providers are configured. Low |
| T4 | **Redirect a key to an attacker host** via `llm.<p>.base_url` (project file, env, CSRF'd `SetSetting`, a future assistant) | repo, web, llm | **Origin-bound key slots**; the catalog overlay can't change `base_url`/`key_env`; the assistant refuses every key under `PROTECTED_SETTINGS` (`llm`, `select`, `assistant`, `app`, `io`, `credentials`, `plugins`, `osm`, `overture`, `viz`) inside its repair loop, so no draft ever holds one | The user must type a key for the new origin |
| T5 | Leak through history, `to_python` or SSE (a key pasted into a setting) | acc | `SetSetting` rejects key-shaped values before recording; 422 omits input | A non-key-shaped token pasted into an unrelated setting |
| T6 | Leak through logs | acc | Never logged; header-only Gemini key; `__repr__` overrides; `SecretStr`; scrubbed errors; `from None` | Same-user debugger. Out of scope |
| T7 | Leak through error text | acc, llm | Composed messages; scrubbed, truncated, omitted for 401/403 | None known |
| T8 | Plaintext key at rest | acc, local | **No plaintext storage at all** (env or keyring only) | Env vars are visible to same-user processes, as for any CLI tool |
| T9 | Overwrite or delete a key from another local user or process | local | Writes don't disclose; loopback bind | Integrity/DoS; fixed with P4's token |
| T10 | Exposed bind (`0.0.0.0`) | lan | Key routes refuse writes; P0 warns at startup | Status disclosure; LAN-triggered selections spend quota |
| T11 | Prompt injection through the utterance or model output | llm | Output is only validated Actions; every step that changes a network is a draft the user approves (re-checked against the network's version), and a read-only plugin Action the assistant runs can't mutate; model text is shown as text, never markup | Drafts for every mutation; read-only steps can still change the view, the selection and saved selections without asking (all recorded and reversible by hand) |
| T12 | Same-user malware reads the keychain | — | Out of scope | Accepted |
| T13 | Key in shell history or `ps` | acc | Hidden prompt or `--stdin` | None |
| T14 | Supply chain | — | No vendor SDKs; `httpx`, `keyring` | Those two |
| T15 | **Network vocabulary, project notes or earlier selections sent to a remote provider unexpectedly** | repo, acc | `auto` = local only; remote needs an explicit `on` (few-shot is opt-in everywhere); `disclosure` lists exactly what is sent next to the picker; an effective `base_url` on a remote host makes Ollama "remote" | The user must read the note before opting in |
| T16 | **Injected instructions in a cloned project's `AGENTS.md`/`CLAUDE.md`** | repo | Notes are capped, labelled as project notes, and only shape a validated selection (T11); they are never sent to a remote provider unless opted in | A malicious note can make selections wrong; the result is shown and replayable, and nothing is applied |
| T17 | Coding-agent instructions mistaken for project notes (a repo's `CLAUDE.md` for coding assistants) | acc | Discovery prefers the network's own folder; the cap limits size; the note is local-only by default | Irrelevant text in the prompt (quality, not security). Open question 3 |

### Per-launch token

Not now (decision 4). Key confidentiality doesn't depend on route auth, cross-origin attacks are covered (T2, T3), and the remaining T9 integrity gap is shared with every P0 action. P4's console token will gate `/api/actions` and `/api/llm/*` writes together.

## Dependency choice and alternatives

**Recommendation: hand-roll the four adapters over `httpx`, put `httpx` and `keyring` in `[nl]`, and drop `anthropic`.**

| | Hand-rolled over httpx | Per-provider SDK extras (`anthropic`, `openai`, `google-genai`, `ollama`) |
|---|---|---|
| Surface we use | 1 completion + 1 list-models endpoint per provider, about 60–90 lines each | the same two calls, behind 4 large SDKs |
| Install weight | `httpx` (already in `[server]`) | 4 SDKs with their own `httpx`/`pydantic` pins and `google-genai`'s auth stack |
| Secret hygiene | We own the headers, logging, error text and `repr` | 4 logging, retry and exception formats to audit |
| Errors | One status map → typed `LLMError` | 4 exception hierarchies |
| Testing | `httpx.MockTransport` for all four; plain-dict fixtures | 4 mocking styles |
| Prompt caching / temperature | One field each, per adapter | Per-SDK idioms |
| Hidden behaviour | None (no silent retries) | SDKs retry 429/5xx by default |
| Cost | We own API drift (bounded by contract fixtures, the live smoke test, the data-driven catalog) | SDK churn |

**Later alternatives behind the same `LLMProvider` interface (out of scope here):**
- **LiteLLM** could replace all four adapters with one call that supports 100+ providers. It is heavy and has its own retry, fallback and cost logic, which would have to be configured to keep "no silent fallback".
- **instructor** wraps SDKs to return validated pydantic objects with automatic re-asking. It overlaps `structured.py` and would bring the SDKs back.
- **PydanticAI** provides agents, typed tools and multi-provider models. It is a natural fit for the P3 multi-step assistant, wrapped as one `LLMProvider` or replacing `request_tool_call` there.

Because the parser, the session and the routes only see `LLMProvider` and `request_tool_call`, any of these can replace the adapters without touching the Workbench.

**Complementary direction: MCP.** netstead already ships an MCP server. Exposing Workbench actions (select, style, navigate, and later drafted edits) as MCP tools would let an external agent (Claude Desktop, an IDE) drive the same Action bus, under the same validation and draft-before-apply rules. That is the inverse of this design (an external model calling us, instead of us calling a model), and it is a later phase.

## Testing

- **Unit, per adapter:** `httpx.MockTransport` via `fake_api`. The tests check:
  - the URL and headers (the key in the right header, *not* in the URL);
  - the body shape, including the Anthropic `cache_control` block and the temperature placement;
  - response parsing;
  - the status → error mapping (400 tools-unsupported, 401, 403, 404, 429 with Retry-After, 529, timeout, connect error);
  - scrubbing.
- **No real network or keychain:** `no_network` makes real HTTP fail loudly; an autouse fixture makes `system_keyring()` return `None`; `FakeKeyring` is injected where a keyring is needed.
- **Structured:** forced tool; schema and validator repairs; budget exhaustion; `ToolsUnsupported` → JSON mode with an unchanged `context`; text replies; null-dropping; `LLMError` not retried; `context`/`temperature` on every call.
- **Secrets:** env → keyring order; origin slots ignore env; the no-keyring path names the env vars and refuses to store; `remove`; a broken keyring hides detail; `redact`, `looks_like_secret`.
- **Registry:** status rows (no key values); `auto` resolution for local and remote (including an OpenAI-compatible server on localhost); `disclosure` lists; catalog drift; failures reported, not raised.
- **Context:** the guide names every tool field and its examples are valid tool input; caps; discovery order (network folder before project dir, `AGENTS.md` before `CLAUDE.md`, URL sources skipped).
- **Prompt:** stable/per-call split; vocabulary from the RDU fixture; close-match hints for facilities and anchors; the `payload_from_intent` round trip.
- **Session:**
  - local providers get the guide, vocabulary and notes, and remote providers get only the guide by default;
  - remote opt-in works;
  - few-shot comes from resolved selections only when enabled;
  - the close-match retry turns a typo'd anchor into a resolved selection;
  - provider failures are `ActionError`s; invalid output is "could not parse".
- **Routes:**
  - the status shape (fixed key set, including `key_env`/`sends`);
  - the header and loopback guards;
  - 422 doesn't echo;
  - the middleware rejects a cross-origin PUT;
  - the no-keyring path names the env vars;
  - the **canary test**.
- **Contract (recorded fixtures):** one per provider, replayed through the real adapter and `LLMParser`. Re-recorded by script (headers never written).
- **Live smoke (opt-in):** `@pytest.mark.live_llm`, skipped unless `NETSTEAD_LIVE_LLM=…`.
- **Front end:** the static module graph, ids, `node --check`; "`llm.js` never touches storage or the store"; an end-to-end browser pass.

## Phasing and merge plan

One PR off `feat/workbench-p1a` (the plan runs after P1a), sequenced so that each task is green on its own:
1. `netstead.llm` core: types, errors, catalog, secrets (+ `corral.system_keyring`), `_http`, four adapters, structured output.
2. Settings (`select.*`, `llm.*`, `llm.quality`), the `SetSetting` guard and the 422 hardening.
3. The registry (status, privacy disclosure), the shipped guide and project-note discovery.
4. Prompt assembly, `LLMParser`, the CLI flags and the `[nl]` swap.
5. Session wiring: prompt context, few-shot, close-match retry.
6. `/api/llm` routes and the canary test; the `netstead llm` CLI.
7. Front end: the picker (+ Make default) and the panel (+ quality & context).
8. Contract fixtures, live smoke, docs, end-to-end.

**Builds on P1a.** All of these are resolved in the plan:
- `OpenNetwork` is a 202 job, so tests open networks through `Session.dispatch`, which waits.
- `Session.__init__` gains `llm_transport`/`keyring` after P1a's `http`.
- `routes/core.py` parses before dispatch; only `include_input=False` is added.
- `credentials.py` keeps P1a's `credential_source`.
- The header picker sits between `#utterance` and `#go`, alongside P1a's Open/Import, Recent and Jobs controls.
- `main.js` adds `llm` to P1a's `subscribe`.

**P1b** mounts `#llm-panel` in its Settings workspace and hides `llm.*` from the generic form.

**P3** reuses `ProviderRegistry`, `request_tool_call`, `PromptContext` and the picker. It extends `Message` with tool results and keeps `llm.*`/`select.*` out of the assistant's `SetSetting` vocabulary.

## Open questions

Decided on 2026-10-02 (see the top of this doc): keyring vs file, default models, picker scope, the token, AI-quality settings, context docs, alternatives. Still open:

1. **Custom endpoints without a keyring.** A keyed OpenAI-compatible endpoint needs a keyring, because env vars apply only to official endpoints. Is that acceptable? The alternative is per-origin env names (for example `NETSTEAD_OPENAI_API_KEY__LLM_EXAMPLE_ORG`).
2. **Ollama default tag.** `qwen3:4b` is the proposed tiny default. Which tag do you actually run? It becomes `default_model` and the pull hint.
3. **Project-note file names.** `CLAUDE.md` and `AGENTS.md` are also used by coding assistants. In a code repository, `netstead app` run from the repo root would pick those up as network notes (local providers only, by default). Should discovery prefer a netstead-specific name (for example `NETSTEAD.md`), or read only an `## netstead` section of `AGENTS.md`/`CLAUDE.md`?
4. **Close-match retry on remote providers.** It only runs when grounding is on (off for remote by default). Should remote providers get a narrower opt-in that sends *only* the few close matches after a miss, not the whole vocabulary up front?
5. **Few-shot persistence.** Examples live in session memory only. Should resolved selections persist per network (for example in the project dir) so few-shot works from the first query of a new session?
6. **Catalog verification cadence.** Should `netstead llm test` (`catalog_missing`) be a release-checklist item, or a scheduled CI job that has secrets?
