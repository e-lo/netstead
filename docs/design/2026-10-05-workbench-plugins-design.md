# Workbench plugins: design

- **Status:** accepted (decisions recorded 2026-10-05). Part 1 implemented (#222); Part 1 plan: [`2026-10-05-workbench-plugins-p1-plan.md`](2026-10-05-workbench-plugins-p1-plan.md). Part 2 (front-end slots) implemented, PR pending; Part 2 plan: [`2026-10-08-workbench-plugins-p2-plan.md`](2026-10-08-workbench-plugins-p2-plan.md).
- **Amendment (Part 1 implementation):** two behaviour changes this spec didn't anticipate. A failed Action now rolls back every `mutate`/`derive` it (or an Action nested in it) made, not just the ones a plugin undoes itself. And a `mutate` called outside any Action is recorded in history as a non-replayable entry (it has no Action to attach a replayable `python` snippet to), rather than being silently applied.
- **Date:** 2026-10-05.
- **Depends on:** the Workbench (`netstead app`, package `netstead.workbench`), which lands with PR #211 (`feat/nl-providers`). Implementation stacks on that.
- **Supersedes:** part of [`2026-10-02-netstead-workbench-design.md`](2026-10-02-netstead-workbench-design.md), which ships with that PR.
  - The planned in-core `changes/` package (`NetworkChange`, `DraftCard`, `apply_card`, `Scenario`) is **no longer core**.
  - It moves to an external ProjectCard plugin repo (see [Target plugins](#target-plugins-used-as-requirements)).

## Why

The Workbench is becoming the main front end for netstead. The next capabilities are large, domain-specific workflows:

1. **ProjectCard authoring.** Select facilities, choose a change type, fill in the card's fields, preview the result, then commit the card to a file or a catalog.
2. **Card catalog and scenario management.** Browse cards and assemble scenarios (a base network plus an ordered list of cards). Applying a scenario produces a network you can inspect and compare.

These should not grow the Workbench core. The goal is to let **other people and projects build and ship capabilities independently.** The card authoring and recording plugin will live in its own repo, and may later move into the `projectcard` package or sit beside it.

## What exists today

The Workbench has no extension seam. Every contribution point is a closed list:

| Concern | Today |
|---|---|
| Actions | A fixed pydantic discriminated union in `workbench/actions.py`. Handlers are found with `getattr(Session, f"_do_{type}")`. |
| Routers | Four routers mounted by hand in `workbench/server.py:build_app`. |
| Front end | `main.js:boot()` calls a fixed list of `wire*()` functions. Panels are static markup in `index.html`, and `panels.js` toggles exactly two of them. |
| Map layers | A hard-coded deck.gl `layers` array in `map.js:render()`. The design doc already called for a layer registry. |
| "Copy as Python" | The `history.js` import line comes from a hard-coded list of action names. |
| Settings | Every section uses `extra="forbid"`, so a plugin's `[section]` fails validation. |
| Session state | `Session.state()` returns `{networks, active, selection, style}` and has no room for anything else. |

**Precedent to copy.** `corral.quality.registry` discovers rules through the `corral.quality.rules` entry-point group.
- Discovery is lazy and safe to call more than once.
- Each entry point loads inside its own `try`/`except`, with logging.
- netstead registers itself the same way in `packages/netstead/pyproject.toml`.

## Approaches considered

1. **In-process plugins discovered via entry points, using a versioned host API.** *Chosen.*
   - Plugins are ordinary pip packages.
   - Integration is tight: one selection, one map, one history and one natural-language surface.
2. **Out-of-process micro-apps**, each with its own server, embedded in an iframe and talking to the Workbench REST API. *Rejected.*
   - They give strong isolation.
   - But each one duplicates the map and selection, coordination across frames is awkward, and the result feels like two apps.
3. **Declarative-only plugins**, where Python declares Actions and schemas and core renders the UI. *Folded into option 1*, as "declarative first, JS optional".
   - A plugin that ships no JavaScript still gets an auto-generated form, a command entry, history and NL support.
   - JavaScript is needed only for custom panels, map tools and overlays.

## UX principles

These principles are the contract plugins design against. Core enforces them where it can and documents the rest.

### 1. Core owns the shared network nouns; plugins may own domain nouns

**Core nouns** are:
- networks, including derived networks and their lineage
- the **one shared selection**
- the map and tables
- jobs, history and settings

A plugin never forks the selection or brings a second map.

**Plugin nouns.** A plugin may introduce domain nouns of its own. For example, a catalog plugin owns *Card*, *Catalog* and *Scenario*, and an authoring plugin owns *Draft card*. Plugin nouns follow three rules:

- **Namespaced and exposed.**
  - They live in plugin state (`plugins.<id>.*` in `/api/state`).
  - They change only through that plugin's namespaced Actions.
  - So other plugins, Python and NL can see them and act on them, as in "add this card to the 2035 scenario".
- **Projected onto core nouns, never duplicating them.**
  - A card *resolves to* a core selection. Highlighting its facilities is an ordinary `Select`.
  - A scenario *materializes as* a core derived network. The map, table, validation and Compare then work on it unchanged.
- **Referenceable by a stable id** of the form `{plugin}:{kind}/{id}`, for example `catalog:card/I80-hov-2035`. This keeps history and "copy as Python" replayable.

**Cross-plugin interaction is by Action.**
- Example: the authoring plugin's "commit to catalog" dispatches `catalog.add_card` when that Action is registered, and otherwise falls back to writing a file.
- This is a *soft* dependency, discovered through the action registry. Core has no service registry.
- A plugin that needs another plugin's Python API declares a normal pip dependency on it.

**Promotion to core.** A plugin noun moves into core only when several unrelated plugins need it and projecting it onto core nouns can't express it.

### 2. Selection → verb → form → preview → commit

This is the universal editing rhythm.

1. The user selects something, by clicking, an NL search, or a query.
2. They pick a verb from a context command, such as "New change ▸ roadway_property_change".
3. A form opens, prefilled from the selection; for a card, that means its `existing` values.
4. A preview shows the result on the map.
5. The user commits.

Plugins should present their workflows in this shape so that every capability feels the same.

### 3. Nothing mutates silently

- Previews and scenarios are **derived networks**: new registry handles with lineage. The base network is untouched.
- An in-place mutation needs an explicit mutating Action, and every mutation appears in history.

### 4. Everything is an Action, plugins included

- A plugin Action's `type` is namespaced, for example `cards.commit` or `catalog.apply_scenario`.
- Plugin Actions get the same history, "copy as Python", replay and NL tool exposure (via `action_json_schema`) as core Actions.

### 5. Schema-driven forms

- Plugin Actions and card types render from JSON Schema through one core `schemaForm` component.
- A new card type then needs no UI code, and every plugin's forms look alike.

### 6. Workspaces as tabs, with predictable placement

**Workspaces.** The top level is a strip of workspace tabs: core *Inspect*, plugin-contributed *Edit* and *Scenarios*, and later *Compare*. Each workspace arranges the shared map and table alongside a **dock** of tabbed panels.

**What plugins can contribute:**
- workspaces
- dock panels, either in their own workspaces or in existing ones
- commands
- map layers

**Commands** surface in up to three places:
- the command palette
- context menus on a map feature, a table row, or the current selection
- the NL assistant

A command declares `when` it applies, for example "selection is non-empty and on the roadway".

### 7. Dirty state is always visible

Badges mark three kinds of unsaved or non-base state:
- unsaved drafts
- uncommitted cards
- derived (non-base) networks

They appear in the network switcher and on workspace tabs.

### 8. Plugins are accountable

- A *Plugins* settings section lists each installed plugin with its:
  - id
  - version
  - required host API and whether it is compatible
  - load errors, if any
  - an enable/disable switch
- A broken plugin degrades to "disabled, with this error". It never takes down the app.

## Architecture

### Python: `netstead/workbench/plugins/`

```
plugins/
  spec.py        WorkbenchPlugin, ActionSpec
  discovery.py   entry-point discovery, PluginStatus, API compatibility check
  host.py        Host: the stable API surface plugins program against
```

**`WorkbenchPlugin`** (`spec.py`) is a frozen dataclass that an entry point's zero-argument factory returns. Its fields:

| Field | Required | Purpose |
|---|---|---|
| `id` | yes | Namespace for Action types, routes, state, settings and static files. Must match `[a-z][a-z0-9_]*`. |
| `name`, `version` | yes | Display name and version. |
| `api` | yes | Required host API range, for example `">=1.0,<2"`. |
| `actions: list[ActionSpec]` | no | `ActionSpec(model, handler)`. `model` subclasses the public `Action` base, and `type` must be `"{id}.<name>"`. `handler(host, action) -> result`. |
| `router: Callable[[Host], APIRouter]` | no | Mounted at `/api/plugins/{id}`. |
| `static_dir`, `frontend` | no | Served at `/plugins/{id}/`. `frontend` names the ES module entry (default `main.js`). |
| `settings_model: type[BaseModel]` | no | Validates the `[plugins.<id>]` config table. |
| `state: Callable[[Host], dict]` | no | Merged into `/api/state` under `plugins.{id}`. |
| `on_load: Callable[[Host], None]` | no | Runs once after registration. Use it to subscribe to events. |

Registration in a plugin's `pyproject.toml`:

```toml
[project.entry-points."netstead.workbench.plugins"]
catalog = "netstead_catalog.workbench:plugin"
```

**Discovery** (`discovery.py`) follows the `corral.quality.registry` pattern:
- Each entry point is loaded inside its own `try`/`except`.
- Every outcome becomes a `PluginStatus(id, version, api, state: loaded|disabled|incompatible|error, error)`.
- Plugins listed in `[workbench] plugins.disabled` are skipped.
- Discovery runs **once**, in `build_app`, after which the registries are frozen. There is no hot reload.
- **Collisions:**
  - a duplicate plugin `id` marks the second plugin as an error
  - a plugin Action whose `type` is not prefixed with the plugin's id is rejected

**`Host`** (`host.py`) is the stable surface. Plugins import only `Host`, `Action`, `ActionSpec` and `WorkbenchPlugin` from netstead. It is versioned as `HOST_API = "1.0"` and provides:

| Member | Purpose |
|---|---|
| `networks.get(net_id)` | Returns the handle, its `Network` and its version. |
| `selection` | The current selection result. Its query/resolved ProjectCard facility form comes from `select/emit.to_projectcard`. |
| `mutate(net_id, edits, provenance)` | Applies corral `Edit`s, bumps the version, appends lineage and publishes `state`. Only mutating Actions may call it. |
| `derive(net_id, label, lineage_entry) -> net_id` | Creates a copy-on-write derived handle in the registry, for previews and scenarios. |
| `actions.has(type)`, `dispatch(action)` | Cross-plugin calls, recorded in history like any other dispatch. |
| `events.publish(name, payload)` | Plugin events are namespaced `"{id}.<name>"` and flow over the existing SSE `EventBus`. |
| `events.subscribe(name, fn)` | Core emits `network_opened`, `network_mutated` and `selection_changed`. |
| `jobs.submit(...)` | Background work with progress, using the existing `JobRunner`. |
| `paths.writable(path)` | Resolves a path through the existing `io.allowed_roots` sandbox. |
| `settings` | The plugin's own validated settings. |

### Core refactors

**Action registry.** `actions.py` replaces the closed union with a registry.
- The `TypeAdapter` is rebuilt once, after discovery.
- Handlers live on the `ActionSpec`.
- **Core Actions move onto the same registry.** Core is just the first plugin-shaped contributor, so there is one dispatch path. The `_do_<type>` naming-convention lookup goes away.
- `mutates`, `runs_as_job` and `replay_overrides` stay as class variables.

**Copy as Python.**
- `to_python` derives imports from each Action class's module.
- The server renders the complete snippet, imports included.
- The hard-coded name list in `history.js` is removed.
- Output for core Actions must not change.

**Settings.**
- The root `Settings` gains `plugins: dict[str, dict]`, stored raw and validated per plugin by its `settings_model`.
- All other sections keep `extra="forbid"`.
- The `SetSetting` Action and its secret-refusing validator also accept keys under `plugins.<id>.*`.

**State.** `Session.state()` merges each plugin's contributed state under `plugins`.

### Front end (no build step)

- `GET /api/plugins` returns each plugin's `PluginStatus` plus its front-end entry URL.
- `main.js` loads each entry with `import()` and calls `activate(wb)`. Each plugin runs inside its own `try`/`catch`; a failure is reported to the Plugins panel and then skipped.
- `index.html` gains a workspace tab strip and a dock container. Core's existing settings and layers panels are re-registered through the same slots.
- Plugins *may* use a bundler internally. Core only requires the output to be one ES module.

The `wb` host object:

| Member | Purpose |
|---|---|
| `wb.api` | `fetch` scoped to `/api/plugins/{id}`, plus `dispatch(action)`. |
| `wb.store` | A read-only mirror of server state, with `subscribe(keys, fn)`. |
| `wb.selection` | The current shared selection, with a change subscription. |
| `wb.registerWorkspace({id, title, layout})` | Adds a workspace tab. |
| `wb.registerPanel({workspace, id, title, render(el), badge?})` | Adds a dock panel. `badge` supports the dirty-state indicators. |
| `wb.registerCommand({id, title, when, run, contexts})` | `contexts` is any of `palette`, `feature`, `row`, `selection`. |
| `wb.registerLayer(component, id, factory)` | Adds a deck.gl layer factory for `roadway` or `transit`. `map.js` renders from this registry. |
| `wb.schemaForm(el, schema, value, {onChange})` | The shared JSON Schema form. |
| `wb.hasAction(type)`, `wb.on(event, fn)` | Soft-dependency checks and namespaced events. |

### Developer kit

- **`netstead.workbench.testing`:** a `plugin_host` pytest fixture that registers a plugin against an in-memory `Session`. It lets a plugin's own CI test Actions, state and settings without a browser.
- **`examples/workbench-plugin-hello/`:** a minimal external-style package with one Action, one panel and one command. CI installs it to exercise the real entry-point path.
- **Plugin authoring guide (docs):**
  - covers the principles above, the `Host` and `wb` reference, and the stability policy
  - the API is **provisional until netstead v1.0**; after that, `HOST_API` follows semver

### Trust model

- Plugins are full-trust, in-process Python. Installing one is like installing a pytest plugin, and there is no sandboxing.
- The authoring guide says this explicitly, next to the existing caveat that `--host 0.0.0.0` exposes the app on the LAN.

## Target plugins (used as requirements)

These are separate repos with their own specs. They are listed here to check that the host API is sufficient.

**ProjectCard authoring.** For example `cards`, which may later move into or beside `projectcard`.
- **Edit workspace.** It contributes an *Edit* workspace and a context command, "New change ▸ {card type}". The command is enabled when the selection is non-empty, and card types come from the projectcard JSON Schema.
- **Form.** The form is `wb.schemaForm`, prefilled from the selection's `existing` values. Selection facilities come from `host.selection`, in query form for NL or query selections and id form for clicks.
- **Preview.** `host.derive`, then `host.mutate`, shown on the map.
- **Draft card.** It is plugin state, shown in a dock panel with a dirty badge.
- **Commit.**
  - `cards.commit` writes YAML through `host.paths.writable`.
  - If `catalog.add_card` is registered, it dispatches that as well.
- **Owns.**
  - the change model (`NetworkChange`-equivalent), the card applier, and the GMNS ↔ Wrangler field mapping file
  - converting the existing `map/edits` edit-log YAML into cards, as an import path

**Catalog and scenarios.** For example `catalog`.
- **Scenarios workspace.** It contributes a *Scenarios* workspace with a card list that filters by tag, category and status.
- **Selecting a card** resolves the card's facilities through `Select`, so the shared map highlights them. A plugin layer adds card-specific styling.
- **Apply scenario** is a job:
  1. `derive(base)`.
  2. Apply the cards in order. The plugin owns the card applier, or depends on the authoring plugin's Python API.
  3. A new network appears in the switcher with its lineage, ready for Compare.
- **Owns** Card, Catalog and Scenario, exposed as `catalog:*` nouns and Actions.

## Out of scope

- A marketplace, plugin installation from the UI, and hot reload.
- Sandboxing or permissions for plugins.
- A core service registry for plugin-to-plugin calls. Plugins interact through Actions, events, files and pip dependencies.
- The card plugins themselves, and conflict or dependency resolution across cards.

## Implementation order (for the plan)

1. Action registry and dispatch refactor, moving core Actions onto the registry with no behaviour change.
2. `WorkbenchPlugin` / `ActionSpec` / `Host`, plus discovery and `PluginStatus`.
3. Plugin settings and state merging.
4. Front-end slots: workspace tabs, the dock, commands with palette and context menus, the layer registry, `schemaForm`, and the plugin loader.
5. `netstead.workbench.testing`, the hello example plugin, and the authoring guide.
6. The *Plugins* settings UI.

## Verification

**Regression**
- The existing Workbench tests pass unchanged after core Actions move to the registry.
- "Copy as Python" output for core Actions is byte-identical.

**New unit tests**
- Discovery with a fake entry point.
- A plugin that raises on load is isolated into `PluginStatus(error)`.
- A host API mismatch yields `incompatible`.
- A disabled plugin is skipped.
- An un-namespaced Action type is rejected.
- Namespaced dispatch with history and the Python snippet.
- Cross-plugin `dispatch` and `actions.has`.
- Plugin settings validation, including the secret refusal.
- State merging.
- `derive` leaves the base network unchanged.

**End to end**
1. Install the hello example in editable mode (`uv pip install -e examples/workbench-plugin-hello`), then run `netstead app`.
2. Check that the panel, command and Action appear, and that history shows a working Python snippet.
3. Disable the plugin in settings and confirm it disappears.

**Import contracts.** `lint-imports` still passes. Core never imports plugin packages.
