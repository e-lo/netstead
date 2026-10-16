# Workbench Plugins, Part 2 (front-end slots): Implementation Plan

Status: **implemented (PR pending)** (open questions decided 2026-10-10) · Date: 2026-10-08 · Owner: Elizabeth Sall · Parent: [Workbench plugins design](2026-10-05-workbench-plugins-design.md)

> **Implementation note (2026-10-10).** Tasks 0–19 are done on `feat/workbench-plugins-p2`, with two review
> rounds folded in (batch 1's fixes after Task 6, batch 2's as "Part R" before Task 19). Where the code differs
> from the snippets below, the [deviations table](#additions-to-and-deviations-from-the-designs-wb-table) says how
> and why; the snippets are kept as written. The largest differences: `commandContext` hands plugin code deep-frozen
> copies (commands, badges and plugin layers alike); each workspace saves its own view (`netstead.views`); the map's
> feature menu never opens at the end of a right-drag; the palette is ⌘K on a Mac and Ctrl+K elsewhere; schema
> forms track entries that didn't parse, send an explicit `null` for a required-but-nullable field, and enforce
> exclusive bounds; registering a command always bumps `commandSeq` (`addCommandTo`). After the final review, plugins
> load in parallel (`pluginload.js`), each under one time limit for import and `activate` together, and a failing
> command toasts on every run.

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to carry out this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give plugins a place in the browser, and put core's own UI on the same footing.
- A plugin's front-end module is imported at startup and its `activate(wb)` called. A failure is contained, rolled
  back, and listed in Settings → Plugins.
- Plugins (and core) register **workspaces**, **dock panels**, **commands** (palette, map feature, table row,
  selection) and **map layers** through one host object, `wb`.
- Every plugin Action gets a generated form (`wb.schemaForm`, the Settings dialog's field renderer made general),
  so a plugin with no JavaScript still has a UI.
- A **Plugins** settings section shows each plugin's id, version, required API and fit, load errors, and an
  enable switch that writes `app.disabled_plugins`.
- **Hard regression rule:** with no plugin installed, the Workbench looks and behaves as it does today. Core's
  Inspect workspace, Details panel and map layers are re-registered through the new slots, and the slots' tab strips
  stay hidden until there is more than one tab.

**Architecture:**
- **Pure registries, DOM renderers.** The extension points live in import-free (or pure-only-importing) modules that
  are unit-tested under node:
  - `slots.js`: workspaces, panels and commands, each owner-tagged and ordered, with namespaced plugin ids;
  - `layers.js`: map layers per component (`roadway`; `transit` reserved);
  - `commands.js`: which commands apply in which context, and the palette's ranked groups;
  - `tabs.js`: tab-strip keys, badges, view-per-workspace, the derived-network mark;
  - `schemaform.js`: JSON Schema → fields, input parsing, the control markup (moved out of `settingsform.js`);
  - `wbhost.js`: builds `wb` from injected parts and rolls a plugin back;
  - `hub.js`: named events; `pluginlist.js`: the Plugins section's rows.
- DOM modules render from them: `workspaces.js` (header workspace tabs and the dock in the right drawer),
  `ctxmenu.js`, `cmdpalette.js`, `formview.js` (`wb.schemaForm`), `actform.js` (an Action's form dialog),
  `plugins.js` (the loader), `pluginspanel.js` (Settings → Plugins), `modal.js` (the Settings dialog's focus
  handling, shared), `corecmds.js` (core's palette commands).
- **The dock is today's right drawer.** `#side` gains a tab strip; its existing content becomes the core *Details*
  panel, unchanged. P2's Issues and Edits tabs register into the same dock (see [What P2 changes](#what-p2-changes)).
- **`map.js` renders from the layer registry.** Core's six layer groups register on it in today's order; plugin
  layers slot in by `order`.
- **Python side (small):** `GET /api/actions` lists every registered Action with its owner and JSON schema (for
  `wb.hasAction`, `wb.actionSchema` and the generated forms). `HOST_API` goes to `"1.1"`: the browser API is new
  plugin API.

**Tech stack:** native ES modules, no build step; MapLibre GL 4.7.1 and deck.gl 9.0.38 (already loaded from the CDN);
FastAPI and pydantic v2 for the one route; node only in tests. **No new dependencies.**

**Spec:** [2026-10-05-workbench-plugins-design.md](2026-10-05-workbench-plugins-design.md): "Front end (no build
step)", the `wb` host object table, UX principles 2 (selection → verb → form → preview → commit), 5 (schema forms),
6 (workspaces as tabs), 7 (dirty state is visible) and 8 (plugins are accountable), and implementation-order items 4
and 6. Scope comes from [Part 1's plan](2026-10-05-workbench-plugins-p1-plan.md) ("Part 2 (separate plan…)").

**Builds on:** Part 1 (`feat/workbench-plugins`, local tip `48305b9` when this was written): `GET /api/plugins`,
`/plugins/<id>/` static files, `PluginStatus.frontend`, `Host.publish` (SSE `plugin` events),
`app.disabled_plugins` (already a restart key), and `examples/workbench-plugin-hello/`.

---

## Open questions (each with a recommendation; all decided 2026-10-10)

1. **Do workspace tabs replace the Map / Split / Table toggle, or sit beside it?**
   - **Recommended: beside it.** A workspace says *which tools* (its dock panels); the toggle says *how the shared map
     and table share the stage*. They are independent, and every workspace uses the same map and table (design:
     "Each workspace arranges the shared map and table alongside a dock").
   - Each workspace remembers the view the user left it in (per tab, in memory). A workspace may declare
     `layout: {view: "split"}` as its first-visit default. Inspect declares none, so today's remembered view mode
     (`netstead.viewmode`) still applies.
   - *Alternative:* fold the toggle into the workspace (Edit is always Split). Rejected: it takes a control away from
     the user, and Inspect would lose its three modes.
   - **Decided 2026-10-10: beside it** (user). Workspace tabs sit beside the Map / Split / Table toggle, and each workspace remembers its own view.
2. **Palette shortcut.**
   - **Recommended: Ctrl+K (⌘K on a Mac), plus a "Commands…" header button.** Ctrl+K is the common web convention
     and a page can claim it; Ctrl+Shift+P opens a private window in Firefox and can't be. It works from text boxes
     too (the utterance box), and does nothing while the Settings or Open/Import dialog is open. The button gives a
     discoverable, pointer-only path and hides at 640 px or narrower (the shortcut still works).
   - **Decided 2026-10-10: Ctrl+K / ⌘K, plus a "Commands…" header button** (the recommendation).
3. **Where does the dock live, relative to today's right drawer?**
   - **Recommended: the dock *is* the right drawer.** `#side` keeps its place and its 330 px width; a tab strip appears
     at its top only when the current workspace has two or more panels. Its current content becomes the core
     **Details** panel (id `details`, title "Details"), which is exactly the pane P2's revised plan names, so P2's
     "Details | Issues | Edits" becomes three panels in the Inspect dock.
   - *Alternatives:* a bottom dock under the table (competes with Split mode for height), or a left dock (moves
     the map). Both change what users see today.
   - **Decided 2026-10-10: the dock is the existing `#side` drawer** (the recommendation); its tab strip shows only with two or more panels.
4. **Do core's Details, Settings and Layers become dock panels now?**
   - **Recommended: Details now; Settings and Layers stay where they are.**
     - **Details** becomes the core dock panel (above). Its markup and ids don't change; the dock adopts it.
     - **Settings** stays a modal dialog. Its `registerSection` is already a slot, and the Plugins section registers
       through it.
     - **Layers** and **Map display** stay as popovers on the map's buttons (they belong to the map, not to a
       workspace). The Layers popover gains an **Overlays** group listing titled plugin layers with a show/hide
       switch; it renders nothing while there are none. This is how "core's layers panel is re-registered through the
       same slots" (design, "Front end") is met: plugin layers appear in it from the registry.
   - **Decided 2026-10-10: only Details moves into the dock now** (user). Settings stays a dialog and gains a Plugins section; Layers and Map display stay popovers, and Layers gains an Overlays group.
5. **Is the workspace strip visible when Inspect is the only workspace?**
   - **Recommended: no.** It appears when a second workspace registers. Same rule for the dock's tab strip. A
     Workbench with no plugins then looks exactly as it does today.
   - **Decided 2026-10-10: no** (the recommendation). The workspace strip is hidden while Inspect is the only workspace.
6. **Which settings layer does the Plugins switch write?**
   - **Recommended: user scope**, like the Language-models section (the Save-to menu is hidden on registered sections).
     When a project file or a `NETSTEAD_*` variable sets `app.disabled_plugins`, the section says so (P1b's
     `scopeNote`), because a user value then doesn't take effect.
   - The switch shows the *saved* setting; the status column shows how *this run* started. When they differ the row
     says "restart to apply" (it is a restart key; there is no hot reload).
   - **Decided 2026-10-10: user scope** (the recommendation). The switch writes user scope, notes any project or `NETSTEAD_*` override, and says "restart to apply".
7. **Sequencing with P2 (Validate + Edit).**
   - **Recommended: Part 1 → Part 2 → P2.** P2's drawer tabs then register through `registerPanel` instead of
     hand-writing a tab bar (see [What P2 changes](#what-p2-changes) for the exact edits to P2's Task 12, 13 and 15).
   - *If P2 must land first:* P2 keeps its Task 12 as written, and this plan's Task 9 then adopts P2's three panes as
     core panels (`details`, `issues`, `edits`) and deletes P2's `#side-tabs` markup, CSS and `showTab`.
   - **Decided 2026-10-10: Part 1 → Part 2 → P2** (user).
8. **Generated forms for plugin Actions ("declarative first").**
   - **Recommended: yes.** Every plugin Action gets one palette command, "‹Plugin›: ‹Action›…", that opens a dialog
     with its schema form and a Run button. A plugin that ships no JavaScript is still usable, as the design promises.
     Core Actions don't get one: they already have UI.
   - **Decided 2026-10-10: yes** (user). Every plugin Action gets an auto-generated palette form.
9. **Does a right-click move the focus?**
   - **Recommended: no.** The menu targets the right-clicked record (`ctx.target`) and leaves focus, details and the
     grid alone. A right-click with no applicable command does nothing new: on the map MapLibre keeps its right-drag
     rotate, and on a grid row the browser's own menu opens as today.
   - **Decided 2026-10-10: no** (the recommendation). A right-click doesn't move focus.
10. **Plugin API version.**
    - **Recommended: bump `HOST_API` to `"1.1"`** in this part (`wb` is new plugin API; a minor bump only adds).
      P2's plan bumps it for its Host additions; it then becomes `"1.2"` (P2's Task 5 changes one string).
    - **Decided 2026-10-10: `HOST_API` goes to `"1.1"` in Part 2** (the recommendation).

## Decisions (technical, made here)

1. **Plugin ids are namespaced everywhere**, like Action types: a plugin's workspace, panel, command and layer ids
   must start with `"<plugin id>."`, and so must the `id` of every deck.gl layer its factory returns (core's ids such
   as `links` drive tooltips, picking and box-select). Core's ids are bare.
2. **Everything a plugin registers is tracked by its `wb`**, so a failed activation is rolled back whole and a
   rolled-back `wb` refuses further registrations.
3. **Activation is sequential, in `/api/plugins` order, with a 5-second limit** per plugin, and runs **after** core
   has rendered (it is not awaited by `boot()`), so a slow plugin never delays the map. The module URL carries
   `?v=<version>` so a reinstalled plugin isn't served from cache.
4. **Errors are contained at every plugin call site:** `activate`, a panel's `render`/`badge`/`onShow`, a command's
   `when`/`run`, a layer factory, a `wb.on`/`wb.store.subscribe` listener. Each is reported once per distinct
   message (`reportPluginError`: console, toast, and the Plugins section) and never propagates into core.
5. **A panel renders lazily**, the first time it shows; `badge(ctx)` is re-evaluated on every server-state change
   and on `panel.refreshBadge()`. A badge is a count, a string, or `{text, dirty, title}`.
6. **`when(ctx)` and `run(ctx)` get one context shape** (`commands.js` `commandContext`): `network`, `selection`,
   `selectionCount`, `focus`, `highlights`, `workspace`, `target` (`{table, id}` for a feature or row), and `state`.
7. **The palette is also the keyboard path to context menus:** with a record focused it lists that record's feature
   and row commands under "For ‹table› ‹id›", and the selection's commands under "Selection (n links)".
8. **Context menus list commands in sections, not nested submenus.** A command's optional `group` renders as
   "Group ▸ Title" ("New change ▸ roadway_property_change"), which is flat, searchable and accessible.
9. **Plugin layers default to order 350**: above core's related-records tint (300), under the user's highlights
   (400), focus (500) and marker (600). A titled layer is listed under Layers → Overlays; hiding it is per tab and
   not recorded (like P2's issue markers, P2 decision 14).
10. **`wb.store` is the server state, read-only by convention**: `get()` returns the live object; `subscribe(keys,
    fn)` fires when any listed key changes (dotted keys allowed: `"plugins.hello"`). Changes go through Actions.
11. **Events:** `wb.on("greeted", fn)` is the plugin's own `host.publish("greeted", …)`; another plugin's is
    `"catalog.added"`; core's are `"core.history"` and `"core.job"`. They come off the existing SSE stream; plugins
    never open their own `EventSource`.
12. **`node_module` follows imports between pure modules.** A pure module may import other pure modules beside it
    (one import per line); the fixture copies them along. `settingsform.js` then imports the shared field code from
    `schemaform.js` instead of duplicating it.

## Additions to, and deviations from, the design's `wb` table

| Design | This plan |
|---|---|
| `wb.api`: scoped fetch + `dispatch` | `wb.api.fetch(path, init)`, plus `get(path)` / `post(path, body)` JSON helpers; all scoped to `/api/plugins/<id>`: the path is resolved with `URL` (so `%2e%2e` and `\` count) and must stay under that prefix; other hosts are refused. `dispatch(action)` posts any Action (cross-plugin and core). (Final review S-1.) |
| `wb.store` with `subscribe(keys, fn)` | As designed; keys may be dotted. `get()` and the listener's argument are a frozen copy (`frozenCopy`, injected). Returns an unsubscribe function. (Final review S-2.) |
| `wb.selection` with a change subscription | `get()` and `subscribe(fn)`; fires when the active network's selection changes. Frozen copies, as `wb.store`. |
| `registerWorkspace({id, title, layout})` | `layout` is `{view?: "map" \| "split" \| "table"}`; optional `badge(ctx)` and `order`. Returns an unregister function. |
| `registerPanel({workspace, id, title, render(el), badge?})` | `workspace` may be an id, a list, or `"*"`; optional `onShow()` and `order`. Returns `{refreshBadge(), dispose()}`. |
| `registerCommand({id, title, when, run, contexts})` | Optional `group`; `contexts` defaults to `["palette"]`. The NL-assistant surface is **deferred** (Actions already reach the assistant through `action_json_schema`). |
| `registerLayer(component, id, factory)` | Optional 4th argument `{order, title}`. `factory(ctx)` returns a layer, a list, or nothing; `ctx` carries `deck`, `net`, `attrs`, `style`, `selection`, `focus`, `highlights`, `related`, `marker`, `zoom`, `server`. |
| `schemaForm(el, schema, value, {onChange})` | Also `omit`; returns `{value(), errors(), set(value), focus()}`. |
| `hasAction(type)`, `on(event, fn)` | As designed (sync: the action list is read once before activation). After a rollback, `on` and both `subscribe`s are refused like the `register*` calls (final review I-1). `host.publish` refuses names outside `[a-z][a-z0-9_]*`, since a dotted name can't be heard through `on` (final review S-3). |
| — | **New:** `wb.id`, `wb.hostApi`, `wb.actionSchema(type)`, `wb.showPanel(id)`, `wb.toast(message)`. |
| Selection commands (`contexts: ["selection"]`) | A selection with no links (an utterance that matched nothing leaves `link_ids: []`) counts as no selection: no "Selection (n links)" section in menus or the palette, and no "Selection actions" button. (Recorded during implementation.) |
| — (palette ranking) | The palette ranks by match **within** each group ("Commands", "For ‹table› ‹id›", "Selection (n links)"); groups keep that order. A command that fits several groups is listed once, in the first it fits. Intended: the group says what the command acts on. |
| — (schema forms) | `wb.schemaForm`'s `value()` includes what a checkbox or a non-nullable menu shows (its default, else unchecked / the first option), so what is sent is what is seen. Fields left blank are left out. |
| Open question 2: "Commands…" hides at 640 px or narrower | It also hides whenever the header's spacer has no room for it (a container query on the spacer), so it never makes the header wrap: the header's layout with no plugins is unchanged at every width. Ctrl+K / ⌘K always works. |
| Task 14 loader | `reportPluginError` is wired once for every plugin call site (dock, menus, palette, map layers); a command's phase names it (`command hello.c`). Passive failures (badge, layer, `when`, listeners) are reported once per (owner, phase, message); a command's `run` is logged and toasted every time, and listed once (final review I-4). The import and `activate` are timed together ("did not load and activate within 5 s"), and plugins load in parallel and all settle before `restoreWorkspace`, so N hung plugins cost one limit (final review I-2). An `activate` that rejects after its limit is reported, not left unhandled. The loader and the reporter live in `pluginload.js` (`activateOne`, `activatePlugins`, `createErrorReporter`, `commandReporters`), injected and node-tested (final review S-4). Consequence of parallel loading: between two plugins' entries with equal `order`, registration order (the registries' tie-break) follows which plugin finished loading first; the guide tells authors to set `order`. Errors from a plugin's own timers or unreturned promises aren't attributed to it (guide, final review S-5); a global `error`/`unhandledrejection` listener could come later. |
| Task 15 Plugins section (final review) | While a project file, `NETSTEAD_APP__DISABLED_PLUGINS` or the session sets `app.disabled_plugins`, every switch is `disabled` with `aria-describedby="plugins-note"`, and the note names the project file (from `payload.paths`) or the variable (`pluginsLock`); when the switches are enabled the effective value is the user value, so they never copy a higher layer's ids into the user file (I-3). The row markup moved to `pluginlist.js` (`pluginRowHTML`, using `schemaform.js`'s `esc`, now exported). `.sw` is `inline-block`, so the switch keeps its size inside the table cell. |
| Task 8 / 16 derived mark | The tooltip names the base network's label while it is open, else `derived from <id> (closed)` (`derivedTitle`, final review N-1). |
| Task 15 Plugins section | The section re-renders when a browser-side plugin error arrives while it is open; the override note hides when empty; focus stays on the switch after the list redraws. |
| Decision 6 / Task 9 badges (`badge(ctx)`) | A badge gets the full `commandContext`, not `{state}`. Everything in that context is a frozen copy; the server state and selection are deep-frozen, made once per server object (`frozenCopy`). A plugin layer factory gets its own frozen ctx (`highlights` a Set copy; `net` and `attrs` shared and read-only, documented). (Review S-1.) |
| Task 6 `layers.build` | Takes `{isLayer, pluginCtx}`: map.js passes `instanceof deck.Layer`, so a factory returning a non-layer is reported and never reaches deck. (Review S-6.) |
| Task 5 `addCommand` | `addCommandTo(slots, store, owner, spec)` in `commands.js` registers core's and plugins' commands and bumps `commandSeq` on add and on an actual removal. (Review I-2.) |
| Open question 1 (the remembered view) | Saved per workspace in `netstead.views`; Inspect restores its own entry (`netstead.viewmode` only as a fallback), so a plugin workspace's layout never leaks into Inspect after a reload. (Review I-4.) |
| Open question 9 (right-drag) | `gesture.js`'s click guard: a `contextmenu` while the button is down (macOS) waits for mouseup; one after a >4 px move is dropped. (Review I-3.) |
| Open question 2 (shortcut) | ⌘K on a Mac only and Ctrl+K elsewhere only, so Ctrl+K keeps its text-box meaning on a Mac; the button's title and `aria-keyshortcuts` follow the platform. (Review S-2.) |
| Task 2 / 12 schema forms | Entries that didn't parse are tracked and named by `errors()`; a required nullable field sends `null` ("None" in its menu); a nullable bool is a three-way menu; a JSON field with no value is empty with its default as placeholder; `exclusiveMinimum`/`exclusiveMaximum` are exclusive; generated ids keep `-` and `.` apart; Enter runs the Action form. The Settings markup is unchanged. A `list` kind for arrays of numbers was not added (it would change Settings' fields). (Review I-1, S-3, S-4, N-4–N-7.) |
| Task 9 / 10 / 11 accessibility | `tabpanel` roles only while a strip shows; workspace tabs use manual activation; menu and palette sections are `role="group"`; Selection actions toggles `aria-expanded`. (Review S-5, N-1–N-3.) |
| Task 18 order | Docs were written after Part R, so they describe the final behaviour. |

## Scope notes

- **In Part 2:** `GET /api/actions` and `HOST_API` 1.1; the pure modules above; workspace tabs and the dock; core
  Inspect + Details re-registered; the layer registry with core layers on it and the Overlays group; context menus
  (feature, row, selection) and the "Selection actions" button; the command palette with core commands; `schemaForm`
  and the Action form dialog with generated plugin-Action commands; the loader; the Plugins settings section; dirty
  badges on panels and workspace tabs and the derived mark in the network switcher; the hello example's front end;
  docs.
- **Deferred:**
  - a plugin's own settings form in the Plugins section (needs its `settings_model` schema on `/api/plugins`);
  - commands surfaced in the NL assistant;
  - opening a row's context menu from the keyboard on the row itself (the palette's "For …" group covers it);
  - workspaces with a main area other than the shared map and table;
  - drag-to-reorder tabs; plugin-provided CSS beyond what a plugin injects itself (the guide says how to scope it);
  - drawing `transit` layers (the key is accepted; P6 draws them).
- **Unchanged:** the Settings dialog's behaviour (its form markup is byte-identical, pinned by a test); P1b linking,
  focus, highlights, scopes and related records; the wizard, jobs and history strip; every existing test.

## File structure

All front-end paths are under `packages/netstead/netstead/workbench/static/`.

| Path | Responsibility |
|---|---|
| `packages/netstead/tests/conftest.py` (modify) | `node_module` follows sibling imports; accepts an absolute path |
| `js/schemaform.js` (new, pure) | `fieldKind`, `parseInput`, `parseControl` (moved), `fieldsFrom`, `getPath`, `setPath`, `formErrors`, `inputHTML` |
| `js/settingsform.js` (modify) | Imports and re-exports the moved helpers |
| `js/settings.js` (modify) | Uses `inputHTML` from `schemaform.js` and `trapTab` from `modal.js` |
| `packages/netstead/netstead/workbench/plugins/spec.py` (modify) | `HOST_API = "1.1"` |
| `packages/netstead/netstead/workbench/session.py` (modify) | `Session.action_catalog()` |
| `packages/netstead/netstead/workbench/routes/core.py` (modify) | `GET /api/actions` |
| `js/slots.js` (new, pure) | `createRegistry`, `checkId`, `workspaceSpec`, `panelSpec`, `commandSpec`, `panelsFor`, `createSlots`, `slots` |
| `js/commands.js` (new, pure) | `commandContext`, `applicable`, `menuSections`, `selectionSections`, `paletteGroups`, `matchScore`, `runCommand`, `isPaletteShortcut`, `actionCommands`, `commandLabel` |
| `js/layers.js` (new, pure) | `createLayerRegistry`, `layerRegistry`, `COMPONENTS`, `CORE_ORDER` |
| `js/tabs.js` (new, pure) | `nextIndex`, `normalizeBadge`, `workspaceBadge`, `tabLabel`, `viewFor`, `networkBadge`, `derivedTitle` |
| `js/hub.js` (new, pure) | `createHub`, `hub` |
| `js/wbhost.js` (new, pure) | `createWb`, `pluginPath`, `eventName`, `keysChanged` |
| `js/pluginlist.js` (new, pure) | `pluginRows`, `withPluginEnabled`, `pluginsLock`, `pluginRowHTML` (final review) |
| `js/pluginload.js` (new, pure; final review) | `activateOne`, `activatePlugins`, `createErrorReporter`, `commandReporters` |
| `js/workspaces.js` (new) | Workspace strip and dock: `registerCoreSlots`, `addWorkspace`, `addPanel`, `showWorkspace`, `showPanel`, `restoreWorkspace`, `renderStrips`, `wireWorkspaces` |
| `js/ctxmenu.js` (new) | Feature/row/selection menus: `openContextMenu`, `wireContextMenus` |
| `js/modal.js` (new) | `trapTab` (moved out of `settings.js`) |
| `js/cmdpalette.js` (new) | The palette: `openPalette`, `closePalette`, `wirePalette` |
| `js/corecmds.js` (new) | `registerCoreCommands` |
| `js/formview.js` (new) | `schemaForm` (the DOM side of `wb.schemaForm`) |
| `js/actform.js` (new) | `openActionForm`, `wireActionForm` |
| `js/plugins.js` (new) | `loadPlugins`, `addCommand`, `reportPluginError` |
| `js/pluginspanel.js` (new) | `renderPluginsPanel`, `wirePluginsPanel` |
| `js/{main,map,panels,table,header,store}.js` (modify) | Wiring; layer registry; Overlays; row menu; derived mark; store keys |
| `index.html`, `app.css` (modify) | Workspace strip, dock strip, Details pane wrapper, selection button, menu, palette, Action form, Plugins section, Overlays |
| `examples/workbench-plugin-hello/netstead_hello/static/main.js`, `README.md` (modify) | Panel, commands and badge through `wb` |
| `packages/netstead/tests/test_workbench_slots_js.py` (new) | Node tests for every new pure module and the fixture |
| `packages/netstead/tests/test_workbench_hello_frontend.py` (new) | The hello front end against the real `createWb` |
| `packages/netstead/tests/test_workbench_static.py`, `test_workbench_plugin_routes.py` (modify) | Static wiring tests; `/api/actions` |
| `packages/netstead/docs/cookbook/workbench-plugins.md`, `workbench.md` (modify); `docs/design/2026-10-05-workbench-plugins-design.md`, `docs/design/README.md` (modify) | Docs |

## Conventions

- Run commands from the repo root.
- **Tiered tests** (as in P1b and P2):
  - while iterating, the task's own paths: `uv run --all-extras pytest <paths> -q`;
  - before every commit: `uv run --all-extras pytest packages -n auto -q`;
  - once, in Task 19: `uv run --all-extras pytest packages -n auto -q -m ""` (everything).
- Lint: `uv run ruff check packages && uv run ruff format --check packages`. Task 19 also runs `uv run lint-imports`.
- Ruff enforces Google-style docstrings outside `tests/`; line length 120. Not every snippet is pre-wrapped: run
  `uv run ruff format <files>` after pasting Python.
- **Pure front-end logic goes in pure modules** (no DOM, no network, no deck.gl; imports only other pure modules
  beside them). They are tested with `node_module`. DOM wiring is covered by the static tests
  (`test_relative_imports_resolve_to_real_exports`, `test_every_element_id_used_by_js_exists_in_index`,
  `test_js_syntax`), the new static tests below, and the browser walk-through in Task 19.
- **Static-test constraints to respect in new JS:** export with `export function` / `export const` (the export
  scanner doesn't read `export { … }` lists, so nothing may import a name a module only re-exports that way), never
  `import { a as b }`, and look up fixed elements as `$("literal-id")` so the id check sees them.
- **Regression rule.** No existing test is edited. With no plugin installed, every screenshot in Task 0's baseline
  must match after Task 16 (Task 19 compares them).
- **Honesty note.** Written against `feat/workbench-plugins` at `48305b9` (Part 1 done, not merged). The eight pure
  modules (`schemaform`, `slots`, `commands`, `layers`, `tabs`, `hub`, `wbhost`, `pluginlist`), the reworked
  `settingsform.js`, the hello `main.js` and the `node_module` copy logic were **run under node 22** in a scratch copy;
  the expected values in Tasks 1, 2, 4, 5, 6, 8, 13, 15 and 17 are their real output. The DOM modules, the Python
  route and the wiring were not run. Where a step fails, fix the plan's code; don't weaken the test.

---

### Task 0: Branch and baseline

**Files:** none.

- [ ] **Step 1: Branch**

Part 1 must be on `main` first (or, until it merges, branch from `feat/workbench-plugins` and rebase later).

```bash
git fetch origin
git checkout -b feat/workbench-plugins-p2 origin/main   # or feat/workbench-plugins
uv sync --all-packages --all-extras
```

- [ ] **Step 2: Baseline the tests**

Run: `uv run --all-extras pytest packages -n auto -q`
Expected: all pass. Record the count in the PR description.

- [ ] **Step 3: Baseline screenshots (the no-visual-change reference)**

With the hello example **not** installed (`uv pip uninstall netstead-hello` if it is), run `uv run netstead app`,
open the Leavenworth fixture (`packages/netstead/netstead/fixtures/leavenworth/parquet`) through Open / Import,
and take these screenshots at 1280×800 into the scratchpad (not the repo):

1. Map mode, nothing selected.
2. Highlight mode on, three links highlighted, then **Set as selection**: Map mode with the selection drawn.
3. Three more links highlighted, one link clicked (focus): Split mode, Link table (related tint visible).
4. Table mode, Node table, a node row clicked (marker on the map is not visible; the grid row is focused).
5. The Layers popover open; the Map display popover open.
6. Settings → Map, Settings → App server, Settings → Language models.
7. 640 px wide, Map mode.

---

### Task 1: `node_module` follows imports between pure modules

**Files:**
- Modify: `packages/netstead/tests/conftest.py`
- Test: `packages/netstead/tests/test_workbench_slots_js.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `packages/netstead/tests/test_workbench_slots_js.py`:

```python
"""Node unit tests for the plugin front end's pure modules (Workbench plugins, Part 2)."""

import json
import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_node_module_follows_sibling_imports(node_module, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "b.js").write_text("export const two = () => 2;\n")
    (src / "a.js").write_text('import { two } from "./b.js";\nexport const four = () => two() * 2;\n')
    assert node_module(src / "a.js", ["four"], "four()") == 4


def test_node_module_refuses_a_non_sibling_import(node_module, tmp_path):
    (tmp_path / "c.js").write_text('import x from "https://cdn.example/x.js";\nexport const y = 1;\n')
    with pytest.raises(AssertionError, match="sibling pure modules"):
        node_module(tmp_path / "c.js", ["y"], "y")
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q`
Expected: FAIL. The first test trips the fixture's "must stay import-free" assertion.

- [ ] **Step 3: Replace the fixture**

In `packages/netstead/tests/conftest.py`, replace the whole `node_module` fixture (keep `WORKBENCH_JS` above it) with:

```python
#: A line that imports something. Pure modules may import only sibling pure modules (``from "./x.js"``).
_IMPORT_LINE = re.compile(r"^\s*import\s.*$", re.M)
_SIBLING = re.compile(r'from\s*"\./([\w-]+)\.js"')


def _copy_pure(path: Path, dest: Path, copied: set[str]) -> str:
    """Copy ``path`` into ``dest`` as ``.mjs`` with the sibling modules it imports (recursively); return its name.

    Node then treats them as ES modules without a package.json. A pure module may import only other pure
    modules beside it, one import per line; any other import (a bare or remote one) fails loudly.
    """
    name = f"{path.stem}.mjs"
    if name in copied:
        return name
    copied.add(name)
    source = path.read_text(encoding="utf-8")
    for line in _IMPORT_LINE.findall(source):
        sibling = _SIBLING.search(line)
        assert sibling, f"{path.name}: a pure module may import only sibling pure modules, not: {line.strip()}"
        _copy_pure(path.with_name(f"{sibling.group(1)}.js"), dest, copied)
    (dest / name).write_text(_SIBLING.sub(r'from "./\1.mjs"', source), encoding="utf-8")
    return name


@pytest.fixture
def node_module(tmp_path: Path, run_node):
    """Evaluate a JS expression against a pure workbench module under node; return its JSON value.

    ``node_module("linking.js", ["pageOffset"], "pageOffset(250, 100)")`` -> ``200``. ``module`` is a file
    in the workbench's ``static/js``, or an absolute path (a test's own harness). Pure modules may import
    sibling pure modules, which are copied along; ``expr`` may use top-level ``await``.
    """
    counter = iter(range(1_000_000))

    def run(module: str | Path, names: list[str], expr: str) -> Any:
        root = tmp_path / f"node{next(counter)}"
        root.mkdir()
        path = Path(module) if Path(module).is_absolute() else WORKBENCH_JS / module
        entry = _copy_pure(path, root, set())
        script = root / "__probe__.mjs"
        script.write_text(f'import {{ {", ".join(names)} }} from "./{entry}";\nconsole.log(JSON.stringify({expr}));\n')
        proc = run_node([str(script)])
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)

    return run
```

- [ ] **Step 4: Run the new and the existing JS tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass (the two new tests, and every existing `linking.js` / `settingsform.js` test unchanged).

- [ ] **Step 5: Before commit, then commit**

Run the fast tier and the ruff pair. Expected: all pass.

```bash
git add packages/netstead/tests/conftest.py packages/netstead/tests/test_workbench_slots_js.py
git commit -m "test(workbench): node_module follows imports between pure modules"
```

---

### Task 2: `schemaform.js`: one field model and renderer for Settings and plugin forms

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/schemaform.js`
- Modify: `packages/netstead/netstead/workbench/static/js/settingsform.js`, `settings.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`, `test_workbench_static.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_workbench_slots_js.py`:

```python
GREET_SCHEMA = {
    "title": "Greet",
    "type": "object",
    "properties": {
        "type": {"const": "hello.greet", "default": "hello.greet", "title": "Type", "type": "string"},
        "name": {"title": "Name", "type": "string"},
        "times": {"default": 1, "minimum": 1, "title": "Times", "type": "integer"},
        "where": {"$ref": "#/$defs/Where"},
        "loud": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": None, "title": "Loud"},
    },
    "required": ["name", "where"],
    "$defs": {
        "Where": {
            "title": "Where",
            "type": "object",
            "properties": {"city": {"title": "City", "type": "string"}},
            "required": ["city"],
        }
    },
}


def test_fields_skip_constants_and_group_one_level_of_nesting(node_module):
    fields = node_module(
        "schemaform.js", ["fieldsFrom"], f"fieldsFrom({json.dumps(GREET_SCHEMA)}, {{name: 'Ada', where: {{city: 'Paris'}}}})"
    )
    assert [(f["key"], f["kind"], f["required"], f["group"], f["value"]) for f in fields] == [
        ("name", "text", True, None, "Ada"),
        ("times", "int", False, None, None),
        ("where.city", "text", True, "Where", "Paris"),
        ("loud", "bool", False, None, None),
    ]


def test_set_path_nests_and_null_removes(node_module):
    got = node_module(
        "schemaform.js", ["setPath"], "[setPath({}, 'where.city', 'Paris'), setPath({a: 1, b: 2}, 'a', null), setPath({w: {c: 1}}, 'w.d', 2)]"
    )
    assert got == [{"where": {"city": "Paris"}}, {"b": 2}, {"w": {"c": 1, "d": 2}}]


def test_form_errors_name_missing_required_fields_and_bounds(node_module):
    schema = json.dumps(GREET_SCHEMA)
    expr = (
        f"(() => {{ const fs = fieldsFrom({schema}); return [formErrors(fs, {{}}), "
        "formErrors(fs, {name: 'A', where: {city: 'P'}, times: 0}), formErrors(fs, {name: 'A', where: {city: 'P'}})]; })()"
    )
    assert node_module("schemaform.js", ["fieldsFrom", "formErrors"], expr) == [
        ["Name is required", "City is required"],
        ["Times must be at least 1"],
        [],
    ]


def test_input_html_is_what_the_settings_dialog_always_rendered(node_module):
    """The Settings dialog's markup is pinned: moving inputHTML must not change a byte of it."""
    fields = [
        {"key": "viz.show_legend", "kind": "bool", "value": True, "readonly": None},
        {"key": "io.default_format", "kind": "choice", "options": ["parquet", "csv"], "nullable": True, "value": "csv"},
        {"key": "app.port", "kind": "int", "min": 1, "max": 65535, "value": 8850, "default": 8850, "readonly": "r"},
        {"key": "io.allowed_roots", "kind": "list", "value": ["/a", "/b"]},
        {"key": "viz.basemap", "kind": "text", "value": None, "default": None},
        {"key": "validation.rules", "kind": "json", "value": {"a": 1}},
    ]
    got = node_module("schemaform.js", ["inputHTML"], f"{json.dumps(fields)}.map(f => inputHTML(f))")
    assert got == [
        '<input type="checkbox" id="set-viz-show_legend" data-key="viz.show_legend" checked>',
        '<select id="set-io-default_format" data-key="io.default_format"><option value="">(default)</option>'
        '<option value="parquet">parquet</option><option value="csv" selected>csv</option></select>',
        '<input type="number" id="set-app-port" data-key="app.port" disabled step="1" min="1" max="65535" '
        'value="8850" placeholder="8850">',
        '<input id="set-io-allowed_roots" data-key="io.allowed_roots" value="/a, /b" placeholder="comma-separated" '
        'spellcheck="false">',
        '<input id="set-viz-basemap" data-key="viz.basemap" value="" placeholder="default" spellcheck="false">',
        '<textarea id="set-validation-rules" data-key="validation.rules" rows="3" spellcheck="false">'
        "{\n &quot;a&quot;: 1\n}</textarea>",
    ]


def test_input_html_marks_required_fields_under_another_prefix(node_module):
    got = node_module(
        "schemaform.js", ["inputHTML"], 'inputHTML({key: "name", kind: "text", value: "", default: null, required: true}, "sf1-")'
    )
    assert got == '<input id="sf1-name" data-key="name" aria-required="true" value="" placeholder="default" spellcheck="false">'
```

Append to `test_workbench_static.py`:

```python
def test_settings_and_schema_forms_share_one_field_model():
    settings = (JS_DIR / "settings.js").read_text()
    assert "function inputHTML" not in settings and 'from "./schemaform.js"' in settings
    settingsform = (JS_DIR / "settingsform.js").read_text()
    assert "function fieldKind" not in settingsform and 'from "./schemaform.js"' in settingsform
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: FAIL (`schemaform.js` does not exist; the static test finds `function inputHTML` in `settings.js`).

- [ ] **Step 3: Create `schemaform.js`**

`fieldKind`, `parseInput` and `parseControl` move here verbatim from `settingsform.js`; `inputHTML` moves from
`settings.js` with an `idPrefix` parameter (default `"set-"`) and an `aria-required` attribute that only a
`required` field gets (Settings fields have no `required`, so their markup is unchanged).

```js
// JSON Schema -> form fields, and form input -> values. The Settings dialog (settingsform.js, settings.js) and
// wb.schemaForm (formview.js) share it, so every form looks and parses alike (plugins design, UX principle 5).
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
// dom.js's `esc`, repeated because a pure module can't import dom.js.
const esc = v => String(v).replace(/[&<>"']/g, c => ESC[c]);

export const resolveRef = (schema, node) => (node && node.$ref ? schema.$defs[node.$ref.split("/").pop()] : node);

export function fieldKind(prop) {
  const options = prop.anyOf ? prop.anyOf.filter(o => o.type !== "null") : [prop];
  const nullable = Boolean(prop.anyOf && prop.anyOf.some(o => o.type === "null"));
  const p = options.length === 1 ? options[0] : null;
  if (!p) return { kind: "json", nullable };
  if (p.enum) return { kind: "choice", options: p.enum, nullable };
  if (p.type === "boolean") return { kind: "bool", nullable };
  if (p.type === "integer" || p.type === "number") {
    return { kind: p.type === "integer" ? "int" : "float", nullable,
      min: p.minimum ?? p.exclusiveMinimum ?? null, max: p.maximum ?? p.exclusiveMaximum ?? null };
  }
  if (p.type === "string") return { kind: "text", nullable };
  if (p.type === "array" && p.items && p.items.type === "string") return { kind: "list", nullable };
  return { kind: "json", nullable };
}

// A JSON Schema object (an Action's, a plugin's) -> one field per property:
// {key, label, kind, options, nullable, min, max, value, default, required, description, group}.
// A `const` property (an Action's `type`) is fixed, so it gets no field; `omit` drops others by name. One level of
// nested object becomes a group of dotted keys ("where.city"); anything deeper is a JSON field.
export function fieldsFrom(schema, value = {}, { omit = [] } = {}) {
  const required = new Set(schema.required || []);
  const fields = [];
  for (const [name, raw] of Object.entries(schema.properties || {})) {
    const prop = resolveRef(schema, raw);
    if (omit.includes(name) || prop.const !== undefined) continue;
    if (prop.type === "object" && prop.properties) {
      const inner = new Set(prop.required || []);
      for (const [sub, subRaw] of Object.entries(prop.properties)) {
        const key = `${name}.${sub}`;
        fields.push(describe(key, resolveRef(schema, subRaw), getPath(value, key),
          required.has(name) && inner.has(sub), prop.title || name));
      }
    } else {
      fields.push(describe(name, prop, getPath(value, name), required.has(name), null));
    }
  }
  return fields;
}

function describe(key, prop, value, required, group) {
  return { key, label: prop.title || key.split(".").pop(), ...fieldKind(prop), value: value ?? null,
    default: prop.default ?? null, required, description: prop.description || "", group };
}

export const getPath = (obj, key) => key.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

// A copy of `obj` with dotted `key` set to `v`; `null` removes the key (an empty input means "not given").
export function setPath(obj, key, v) {
  const out = JSON.parse(JSON.stringify(obj || {}));
  const parts = key.split(".");
  let o = out;
  for (const p of parts.slice(0, -1)) o = o[p] && typeof o[p] === "object" ? o[p] : (o[p] = {});
  if (v === null) delete o[parts[parts.length - 1]];
  else o[parts[parts.length - 1]] = v;
  return out;
}

// What a submit would trip on: required fields left empty, numbers out of bounds ([] when the form can be sent).
// The server validates again; this only saves a round trip.
export function formErrors(fields, value) {
  const errors = [];
  for (const f of fields) {
    const v = getPath(value, f.key);
    if (v === undefined || v === null || v === "") {
      if (f.required && f.default === null) errors.push(`${f.label} is required`);
      continue;
    }
    if (typeof v === "number" && f.min != null && v < f.min) errors.push(`${f.label} must be at least ${f.min}`);
    if (typeof v === "number" && f.max != null && v > f.max) errors.push(`${f.label} must be at most ${f.max}`);
  }
  return errors;
}

// An input's raw value -> {ok, value} or {ok: false, error}. Empty means "reset": null removes the key from that layer.
export function parseInput(field, raw) {
  if (field.kind === "bool") return { ok: true, value: Boolean(raw) };
  if (raw === "" || raw === null || raw === undefined) return { ok: true, value: null };
  if (field.kind === "choice" || field.kind === "text") return { ok: true, value: String(raw) };
  if (field.kind === "int" || field.kind === "float") {
    const n = Number(raw);
    if (!Number.isFinite(n) || (field.kind === "int" && !Number.isInteger(n))) {
      return { ok: false, error: `${field.label}: enter a ${field.kind === "int" ? "whole " : ""}number` };
    }
    return { ok: true, value: n };
  }
  if (field.kind === "list") return { ok: true, value: String(raw).split(",").map(s => s.trim()).filter(Boolean) };
  try { return { ok: true, value: JSON.parse(raw) }; } catch (e) { return { ok: false, error: `${field.label}: not valid JSON` }; }
}

// A form control's state ({type, value, checked, badInput}) -> parseInput's answer. A number input the browser
// cannot parse ("-", "1e") reports an empty value; that must not read as "reset" and delete the saved value.
export function parseControl(field, { type, value, checked, badInput }) {
  if (badInput) return { ok: false, error: `${field.label}: enter a ${field.kind === "int" ? "whole " : ""}number` };
  return parseInput(field, type === "checkbox" ? checked : value);
}

// One form control for a field. The Settings dialog uses idPrefix "set-"; `data-key` names the field for parseControl.
export function inputHTML(f, idPrefix = "set-") {
  const attrs = `id="${idPrefix}${esc(f.key.replace(/\./g, "-"))}" data-key="${esc(f.key)}"${f.readonly ? " disabled" : ""}` +
    (f.required ? ' aria-required="true"' : "");
  if (f.kind === "bool") return `<input type="checkbox" ${attrs}${f.value ? " checked" : ""}>`;
  if (f.kind === "choice") {
    const opts = (f.nullable ? [""] : []).concat(f.options);
    return `<select ${attrs}>${opts.map(o => `<option value="${esc(o)}"${o === (f.value ?? "") ? " selected" : ""}>` +
      `${esc(o === "" ? "(default)" : o)}</option>`).join("")}</select>`;
  }
  if (f.kind === "int" || f.kind === "float") {
    const bounds = (f.min != null ? ` min="${f.min}"` : "") + (f.max != null ? ` max="${f.max}"` : "");
    return `<input type="number" ${attrs} step="${f.kind === "int" ? 1 : "any"}"${bounds} value="${esc(f.value ?? "")}" ` +
      `placeholder="${esc(f.default ?? "default")}">`;
  }
  if (f.kind === "json") return `<textarea ${attrs} rows="3" spellcheck="false">${esc(JSON.stringify(f.value ?? null, null, 1))}</textarea>`;
  const text = f.kind === "list" ? (f.value || []).join(", ") : f.value ?? "";
  const hint = f.kind === "list" ? "comma-separated" : f.default ?? "default";
  return `<input ${attrs} value="${esc(text)}" placeholder="${esc(hint)}" spellcheck="false">`;
}
```

- [ ] **Step 4: `settingsform.js` imports them**

Replace the module's first two comment lines with the block below, then delete the `fieldKind`, `parseInput` and
`parseControl` functions (and the comment line above each):

```js
// Settings form model: /api/settings (JSON schema + values + sources + readonly/restart/notes) -> field
// descriptors. Field kinds and input parsing live in schemaform.js (shared with wb.schemaForm). DOM-free: unit-tested
// under node (tests/test_workbench_js.py).
import { fieldKind, parseControl, parseInput } from "./schemaform.js";

export { fieldKind, parseControl, parseInput }; // their old home: callers and tests import them from here too
```

(Browser code must import them from `schemaform.js`; the static test's export scanner can't see an `export { … }`
list. Only node tests read them through `settingsform.js`.)

- [ ] **Step 5: `settings.js` uses the shared renderer**

Change its imports to:

```js
import { clearHint, resetScope, scopeNote, sectionsFrom } from "./settingsform.js";
import { inputHTML, parseControl } from "./schemaform.js";
```

and delete its local `function inputHTML(f) { … }`. `fieldHTML` keeps calling `inputHTML(f)`; the default prefix is
`"set-"`.

- [ ] **Step 6: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass, including every existing `settingsform.js` test.

- [ ] **Step 7: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static/js packages/netstead/tests
git commit -m "refactor(workbench): schemaform.js, one field model for Settings and plugin forms"
```

---

### Task 3: `GET /api/actions`, and plugin API 1.1

**Files:**
- Modify: `packages/netstead/netstead/workbench/plugins/spec.py`, `session.py`, `routes/core.py`
- Test: `packages/netstead/tests/test_workbench_plugin_routes.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_workbench_plugin_routes.py` (its `client` fixture installs a `hello` plugin with `Greet`):

```python
def test_actions_lists_core_and_plugin_actions_with_owner_and_schema(client):
    actions = {a["type"]: a for a in client.get("/api/actions").json()["actions"]}
    core = actions["open_network"]
    assert core["plugin"] is None and core["name"] == "OpenNetwork" and core["description"]
    greet = actions["hello.greet"]
    assert greet["plugin"] == "hello" and greet["name"] == "Greet" and greet["mutates"] is False
    assert greet["schema"]["required"] == ["name"] and "name" in greet["schema"]["properties"]


def test_plugins_report_host_api_1_1(client):
    assert client.get("/api/plugins").json()["host_api"] == "1.1"
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_plugin_routes.py -q`
Expected: FAIL (`GET /api/actions` answers 405; `host_api` is `"1.0"`).

- [ ] **Step 3: Bump the API**

In `plugins/spec.py`:

```python
#: The plugin API this netstead provides: ``major.minor``. A minor bump only adds; a major bump breaks.
#: Provisional until netstead v1.0 (it may change without a major bump before then).
#: 1.1: the browser host object ``wb`` (workspaces, dock panels, commands, map layers, schema forms).
HOST_API = "1.1"
```

- [ ] **Step 4: `Session.action_catalog`**

Add to `Session` (after `state()`):

```python
    def action_catalog(self) -> list[dict[str, Any]]:
        """Each registered Action: ``type``, class ``name``, ``description``, owning ``plugin`` (``None`` for core),
        ``mutates`` and its JSON ``schema`` (the browser's generated forms and ``wb.hasAction``)."""
        owner = {spec.model.action_type(): plugin_id for plugin_id, p in self.plugins.items() for spec in p.actions}
        catalog = []
        for action_type in self.actions.types():
            model = self.actions.model(action_type)
            doc = (model.__doc__ or "").strip().splitlines()
            catalog.append(
                {
                    "type": action_type,
                    "name": model.__name__,
                    "description": doc[0] if doc else "",
                    "plugin": owner.get(action_type),
                    "mutates": model.mutates,
                    "schema": model.model_json_schema(),
                }
            )
        return catalog
```

- [ ] **Step 5: The route**

In `routes/core.py`, inside `core_router`, before the `@router.post("/actions")` handler:

```python
    @router.get("/actions")
    def action_catalog() -> dict[str, Any]:
        """Every Action this session accepts, with its owner and JSON schema. Read-only; nothing is recorded."""
        return {"actions": session.action_catalog()}
```

- [ ] **Step 6: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests -k "workbench" -q`
Expected: all pass.

- [ ] **Step 7: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench packages/netstead/tests/test_workbench_plugin_routes.py
git commit -m "feat(workbench): GET /api/actions (owner + JSON schema); plugin API 1.1"
```

---

### Task 4: `slots.js`: workspaces, panels and commands

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/slots.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_registry_orders_entries_and_namespaces_plugin_ids(node_module):
    expr = """(() => {
      const r = createRegistry("panel");
      r.add("core", {id: "details", title: "Details", order: 0});
      r.add("hello", {id: "hello.b", title: "B", order: 50});
      r.add("hello", {id: "hello.a", title: "A", order: 50});
      r.add("cards", {id: "cards.x", title: "X", order: 10});
      const errors = [];
      for (const [owner, id] of [["hello", "panel"], ["hello", "cards.y"], ["core", "details"]])
        try { r.add(owner, {id, title: "?"}); } catch (e) { errors.push(e.message); }
      r.removeOwner("cards");
      return [r.list().map(p => p.id), errors];
    })()"""
    ids, errors = node_module("slots.js", ["createRegistry"], expr)
    assert ids == ["details", "hello.b", "hello.a"]
    assert errors == [
        'hello: id "panel" must start with "hello."',
        'hello: id "cards.y" must start with "hello."',
        'panel "details" is already registered',
    ]


def test_command_specs_default_to_the_palette_and_reject_bad_shapes(node_module):
    expr = """(() => {
      const errors = [];
      for (const s of [{id: "hello.x", title: "X", run() {}, contexts: ["menu"]}, {id: "hello.x", title: "X"},
                       {id: "hello.x", title: "X", run() {}, when: true}])
        try { commandSpec(s); } catch (e) { errors.push(e.message); }
      return [commandSpec({id: "hello.x", title: "X", run() {}}).contexts, errors];
    })()"""
    contexts, errors = node_module("slots.js", ["commandSpec"], expr)
    assert contexts == ["palette"]
    assert errors == [
        "command hello.x: unknown context menu (use palette, feature, row, selection)",
        'command "hello.x": run required',
        "command hello.x: when must be a function",
    ]


def test_panels_for_a_workspace_include_shared_ones_and_disposers_undo(node_module):
    expr = """(() => {
      const sl = createSlots();
      sl.addPanel("core", {id: "details", title: "Details", workspace: "*"});
      const off = sl.addPanel("hello", {id: "hello.p", title: "P", workspace: "inspect", order: 5});
      sl.addPanel("cards", {id: "cards.d", title: "Draft", workspace: ["cards.edit"]});
      const before = [panelsFor(sl.panels.list(), "inspect").map(p => p.id), panelsFor(sl.panels.list(), "cards.edit").map(p => p.id)];
      off();
      let bad = null;
      try { workspaceSpec({id: "cards.edit", title: "Edit", layout: {view: "globe"}}); } catch (e) { bad = e.message; }
      return [before, panelsFor(sl.panels.list(), "inspect").map(p => p.id), bad];
    })()"""
    before, after, bad = node_module("slots.js", ["createSlots", "panelsFor", "workspaceSpec"], expr)
    assert before == [["details", "hello.p"], ["details", "cards.d"]]
    assert after == ["details"]
    assert bad == "workspace cards.edit: layout.view must be one of map, split, table"
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q -k "registry or command_specs or panels_for"`
Expected: FAIL (no `slots.js`).

- [ ] **Step 3: Create `slots.js`**

```js
// The front end's extension points: workspaces, dock panels and commands. Each is keyed by id and tagged with the
// owner that registered it ("core" or a plugin id), so a plugin's whole contribution can be found or removed.
// Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

export const CORE = "core";
export const ALL_WORKSPACES = "*";
export const CONTEXTS = ["palette", "feature", "row", "selection"];
export const VIEWS = ["map", "split", "table"];

// A plugin's ids are namespaced like its Action types ("hello.panel"); core's are bare ("inspect", "details").
export function checkId(owner, id) {
  if (typeof id !== "string" || !id) throw new Error(`${owner}: an id is required`);
  if (owner !== CORE && !id.startsWith(`${owner}.`)) {
    throw new Error(`${owner}: id ${JSON.stringify(id)} must start with "${owner}."`);
  }
}

// One ordered, owner-tagged collection: lower `order` first, then registration order.
export function createRegistry(kind) {
  const items = new Map();
  let seq = 0;
  return {
    add(owner, spec) {
      checkId(owner, spec.id);
      if (items.has(spec.id)) throw new Error(`${kind} ${JSON.stringify(spec.id)} is already registered`);
      const item = { order: 0, ...spec, owner, seq: seq++ };
      items.set(spec.id, item);
      return item;
    },
    get: id => items.get(id) || null,
    remove: id => items.delete(id),
    removeOwner(owner) { for (const [id, item] of items) if (item.owner === owner) items.delete(id); },
    list: () => [...items.values()].sort((a, b) => a.order - b.order || a.seq - b.seq),
  };
}

function need(spec, kind, fields) {
  if (!spec || typeof spec !== "object") throw new Error(`${kind}: expected an object`);
  const missing = fields.filter(f => spec[f] === undefined || spec[f] === null || spec[f] === "");
  if (missing.length) throw new Error(`${kind} ${JSON.stringify(spec.id ?? "?")}: ${missing.join(", ")} required`);
}

function functions(spec, kind, names) {
  for (const name of names) {
    if (spec[name] !== undefined && typeof spec[name] !== "function") throw new Error(`${kind} ${spec.id}: ${name} must be a function`);
  }
}

// {id, title, layout?: {view?: "map" | "split" | "table"}, badge?(ctx), order?}
export function workspaceSpec(spec) {
  need(spec, "workspace", ["id", "title"]);
  functions(spec, "workspace", ["badge"]);
  const view = spec.layout && spec.layout.view;
  if (view !== undefined && !VIEWS.includes(view)) throw new Error(`workspace ${spec.id}: layout.view must be one of ${VIEWS.join(", ")}`);
  return { ...spec, layout: { ...(spec.layout || {}) } };
}

// {workspace: id | "*" | [ids], id, title, render?(el), badge?(ctx), onShow?(), order?}
export function panelSpec(spec) {
  need(spec, "panel", ["id", "title", "workspace"]);
  functions(spec, "panel", ["render", "badge", "onShow"]);
  return { ...spec };
}

// {id, title, run(ctx), contexts?: ["palette" | "feature" | "row" | "selection"], when?(ctx), group?, order?}
export function commandSpec(spec) {
  need(spec, "command", ["id", "title", "run"]);
  functions(spec, "command", ["run", "when"]);
  const contexts = spec.contexts || ["palette"];
  const unknown = contexts.filter(c => !CONTEXTS.includes(c));
  if (unknown.length) throw new Error(`command ${spec.id}: unknown context ${unknown.join(", ")} (use ${CONTEXTS.join(", ")})`);
  return { ...spec, contexts: [...contexts] };
}

// The dock's panels in workspace `id`: its own plus the shared ("*") ones, in order.
export const panelsFor = (panels, id) => panels.filter(p => [].concat(p.workspace).some(w => w === id || w === ALL_WORKSPACES));

// The three registries, with checked adders that return a function undoing the registration.
export function createSlots() {
  const workspaces = createRegistry("workspace"), panels = createRegistry("panel"), commands = createRegistry("command");
  const adder = (registry, check) => (owner, spec) => {
    const item = registry.add(owner, check(spec));
    return () => registry.remove(item.id);
  };
  return {
    workspaces, panels, commands,
    addWorkspace: adder(workspaces, workspaceSpec),
    addPanel: adder(panels, panelSpec),
    addCommand: adder(commands, commandSpec),
    removeOwner(owner) { for (const r of [workspaces, panels, commands]) r.removeOwner(owner); },
  };
}

// The page's one set of slots.
export const slots = createSlots();
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass.

- [ ] **Step 5: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static/js/slots.js packages/netstead/tests/test_workbench_slots_js.py
git commit -m "feat(workbench): slots.js, owner-tagged registries for workspaces, panels and commands"
```

---
### Task 5: `commands.js`: what applies where, and the palette's list

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/commands.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`

- [ ] **Step 1: Write the failing tests**

```python
COMMANDS_JS = """
const cmds = [
  {id: "open", title: "Open / Import…", contexts: ["palette"], run() {}},
  {id: "zoom_selection", title: "Zoom to selection", contexts: ["palette"], when: c => c.selectionCount > 0, run() {}},
  {id: "hello.greet", title: "Say hello", contexts: ["palette"], run() {}},
  {id: "hello.rec", title: "Say hello to this record", contexts: ["feature", "row"], run() {}},
  {id: "hello.lane", title: "Lane only", contexts: ["row"], when: c => c.target.table === "lane", run() {}},
  {id: "hello.sel", title: "Hello selection", contexts: ["selection"], run() {}},
  {id: "hello.both", title: "Both", contexts: ["feature", "selection"], run() {}},
  {id: "bad.when", title: "Broken", contexts: ["palette"], when: () => { throw new Error("boom"); }, run() {}},
];
const server = {networks: [{id: "n1", version: 3, derived_from: null}], active: "n1",
                selection: {net_id: "n1", link_ids: [1, 2]}};
const shape = groups => groups.map(g => [g.title, g.items.map(i => (i.command || i).id)]);
"""


def _commands(node_module, body: str, names: list[str]):
    return node_module("commands.js", names, f"(() => {{ {COMMANDS_JS} {body} }})()")


def test_command_context_describes_the_network_selection_and_view(node_module):
    got = _commands(
        node_module,
        'const c = commandContext({server, focus: {table: "link", id: 7, from: "map"}, highlights: new Set([5])});'
        "return [c.network, c.selectionCount, c.highlights, c.workspace, c.target];",
        ["commandContext"],
    )
    assert got == [{"id": "n1", "version": 3, "derived_from": None}, 2, [5], "inspect", None]


def test_menu_sections_for_a_feature_then_the_selection_without_repeats(node_module):
    got = _commands(
        node_module,
        'return [shape(menuSections(cmds, "feature", commandContext({server}, {table: "link", id: 7}))),'
        ' shape(menuSections(cmds, "row", commandContext({server: {...server, selection: null}}, {table: "lane", id: 3})))];',
        ["commandContext", "menuSections"],
    )
    assert got == [
        [["link 7", ["hello.rec", "hello.both"]], ["Selection (2 links)", ["hello.sel"]]],
        [["lane 3", ["hello.rec", "hello.lane"]]],
    ]


def test_palette_groups_reach_the_focused_record_and_contain_a_broken_when(node_module):
    got = _commands(
        node_module,
        'const ctx = commandContext({server, focus: {table: "link", id: 7, from: "map"}});'
        "const errors = []; const groups = paletteGroups(cmds, ctx, '', (c, e) => errors.push([c.id, e.message]));"
        "return [shape(groups), errors, groups[1].items[0].ctx.target, shape(paletteGroups(cmds, ctx, 'hello'))];",
        ["commandContext", "paletteGroups"],
    )
    everything, errors, target, hello = got
    assert everything == [
        ["Commands", ["open", "zoom_selection", "hello.greet"]],
        ["For link 7", ["hello.rec", "hello.both"]],
        ["Selection (2 links)", ["hello.sel"]],
    ]
    assert errors == [["bad.when", "boom"]]
    assert target == {"table": "link", "id": 7}
    assert hello == [["Commands", ["hello.greet"]], ["For link 7", ["hello.rec"]], ["Selection (2 links)", ["hello.sel"]]]


def test_match_score_and_the_palette_shortcut(node_module):
    got = node_module(
        "commands.js",
        ["matchScore", "isPaletteShortcut"],
        '[[matchScore("Say hello", "hello"), matchScore("Hello selection", "hello"), matchScore("Othello", "hello"),'
        ' matchScore("Zoom to selection", "zts"), matchScore("Open", "x"), matchScore("Open", "")],'
        ' [isPaletteShortcut({key: "k", ctrlKey: true}), isPaletteShortcut({key: "K", metaKey: true}),'
        ' isPaletteShortcut({key: "k"}), isPaletteShortcut({key: "k", ctrlKey: true, shiftKey: true})]]',
    )
    assert got == [[2, 3, 1, 0.5, -1, 0], [True, True, False, False]]


def test_run_command_reports_sync_and_async_failures(node_module):
    expr = """await (async () => {
      const errors = [], report = (c, e) => errors.push([c.id, e.message]);
      await runCommand({id: "x", run: () => { throw new Error("bad"); }}, {}, report);
      await runCommand({id: "y", run: async () => { throw new Error("later"); }}, {}, report);
      return errors;
    })()"""
    assert node_module("commands.js", ["runCommand"], expr) == [["x", "bad"], ["y", "later"]]


def test_every_plugin_action_gets_a_form_command(node_module):
    catalog = [
        {"type": "open_network", "name": "OpenNetwork", "description": "Open", "plugin": None, "mutates": False, "schema": {}},
        {"type": "hello.greet", "name": "Greet", "description": "Greet someone.", "plugin": "hello", "mutates": False,
         "schema": {"properties": {}}},
    ]
    got = node_module(
        "commands.js", ["actionCommands"], f'actionCommands({json.dumps(catalog)}, [{{id: "hello", name: "Hello"}}])'
    )
    assert got == [
        {
            "owner": "hello",
            "id": "hello.greet:form",
            "title": "Hello: Greet…",
            "contexts": ["palette"],
            "entry": {"type": "hello.greet", "label": "Hello: Greet", "description": "Greet someone.",
                      "schema": {"properties": {}}},
        }
    ]
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q`
Expected: the new tests FAIL (no `commands.js`).

- [ ] **Step 3: Create `commands.js`**

```js
// Which commands apply where (the palette, a map feature, a table row, the selection), and the palette's list.
// DOM-free (imports only store.js): unit-tested under node (tests/test_workbench_slots_js.py).
import { activeSelection } from "./store.js";

// Tables drawn on the map: a focused record from one of them is a "feature" as well as a "row".
const MAP_TABLES = new Set(["link", "node"]);

// What a command's `when(ctx)` and `run(ctx)` see. `target` is the right-clicked map feature or grid row
// ({table, id}), or the focused record for the palette's "For …" group; null otherwise.
export function commandContext({ server, focus = null, highlights = new Set(), workspace = "inspect" }, target = null) {
  const net = server ? server.networks.find(n => n.id === server.active) : null;
  const selection = activeSelection({ server }) || null;
  return {
    network: net ? { id: net.id, version: net.version, derived_from: net.derived_from ?? null } : null,
    selection, selectionCount: selection ? selection.link_ids.length : 0,
    focus, highlights: [...highlights], workspace, target, state: server,
  };
}

export const commandLabel = c => (c.group ? `${c.group} ▸ ${c.title}` : c.title);

// Commands registered for `context` whose `when(ctx)` holds. A `when` that throws counts as false and is reported
// through `onError(command, error)`: one plugin's bug must not break a menu.
export function applicable(commands, context, ctx, onError = () => {}) {
  return commands.filter(c => c.contexts.includes(context) && holds(c, ctx, onError));
}

function holds(c, ctx, onError) {
  if (!c.when) return true;
  try { return Boolean(c.when(ctx)); } catch (e) { onError(c, e); return false; }
}

const selectionTitle = ctx => `Selection (${ctx.selectionCount} link${ctx.selectionCount === 1 ? "" : "s"})`;

// The context menu for a right-clicked record: its own commands, then the selection's (when there is one).
export function menuSections(commands, context, ctx, onError) {
  const own = applicable(commands, context, ctx, onError);
  const sections = own.length ? [{ title: `${ctx.target.table} ${ctx.target.id}`, items: own }] : [];
  if (ctx.selection) {
    const shown = new Set(own.map(c => c.id));
    const sel = applicable(commands, "selection", ctx, onError).filter(c => !shown.has(c.id));
    if (sel.length) sections.push({ title: selectionTitle(ctx), items: sel });
  }
  return sections;
}

// The "Selection actions" menu in the Details panel.
export function selectionSections(commands, ctx, onError) {
  const items = ctx.selection ? applicable(commands, "selection", ctx, onError) : [];
  return items.length ? [{ title: selectionTitle(ctx), items }] : [];
}

// How well `query` matches `text`: 3 prefix, 2 word start, 1 substring, 0.5 in order, -1 not at all (0: no query).
export function matchScore(text, query) {
  const t = String(text).toLowerCase(), q = String(query || "").trim().toLowerCase();
  if (!q) return 0;
  const at = t.indexOf(q);
  if (at === 0) return 3;
  if (at > 0) return /[\s:▸./-]/.test(t[at - 1]) ? 2 : 1;
  let i = 0;
  for (const ch of t) if (i < q.length && ch === q[i]) i++;
  return i === q.length ? 0.5 : -1;
}

function rank(list, query) {
  return list.map((c, i) => ({ c, i, s: matchScore(commandLabel(c), query) })).filter(x => x.s >= 0)
    .sort((a, b) => b.s - a.s || a.i - b.i).map(x => x.c);
}

// The palette: groups of {command, ctx}, best match first. "Commands" (palette context), then "For <table> <id>"
// (the focused record's feature and row commands: the keyboard way to reach a context menu), then the selection's.
// A command shows once, in the first group it fits.
export function paletteGroups(commands, ctx, query, onError) {
  const groups = [], seen = new Set();
  const add = (title, list, itemCtx) => {
    const fresh = list.filter(c => !seen.has(c.id));
    for (const c of fresh) seen.add(c.id);
    const items = rank(fresh, query).map(command => ({ command, ctx: itemCtx }));
    if (items.length) groups.push({ title, items });
  };
  add("Commands", applicable(commands, "palette", ctx, onError), ctx);
  if (ctx.focus) {
    const fctx = { ...ctx, target: { table: ctx.focus.table, id: ctx.focus.id } };
    const list = [];
    for (const k of MAP_TABLES.has(ctx.focus.table) ? ["feature", "row"] : ["row"]) {
      for (const c of applicable(commands, k, fctx, onError)) if (!list.includes(c)) list.push(c);
    }
    add(`For ${ctx.focus.table} ${ctx.focus.id}`, list, fctx);
  }
  if (ctx.selection) add(selectionTitle(ctx), applicable(commands, "selection", ctx, onError), ctx);
  return groups;
}

// Run a command; a throw or a rejected promise goes to `onError(command, error)`.
export async function runCommand(command, ctx, onError) {
  try { await command.run(ctx); } catch (e) { onError(command, e); }
}

// Ctrl+K (Cmd+K on a Mac) opens the palette, even from a text box.
export const isPaletteShortcut = e =>
  Boolean(e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && String(e.key).toLowerCase() === "k";

// One palette command per plugin Action, opening its schema form ("Hello: Greet…"): a plugin with no front end
// still has a UI (plugins design, "declarative first").
export function actionCommands(catalog, statuses) {
  const names = new Map(statuses.map(s => [s.id, s.name || s.id]));
  return catalog.filter(a => a.plugin && names.has(a.plugin)).map(a => {
    const label = `${names.get(a.plugin)}: ${a.name}`;
    return { owner: a.plugin, id: `${a.type}:form`, title: `${label}…`, contexts: ["palette"],
      entry: { type: a.type, label, description: a.description, schema: a.schema } };
  });
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass.

- [ ] **Step 5: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static/js/commands.js packages/netstead/tests/test_workbench_slots_js.py
git commit -m "feat(workbench): commands.js, command contexts, menus and the ranked palette"
```

---

### Task 6: `layers.js`: the map layer registry

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/layers.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`

- [ ] **Step 1: Write the failing test**

```python
def test_layer_registry_orders_core_and_plugin_layers_and_contains_failures(node_module):
    expr = """(() => {
      const reg = createLayerRegistry(), L = id => ({id});
      for (const id of ["marker", "focus", "highlighted", "related", "selection", "base"])
        reg.register("core", "roadway", id, () => [L(id + "-deck")]);      // registered out of order on purpose
      reg.register("hello", "roadway", "hello.dots", () => L("hello.dots"), {title: "Hello dots"});
      reg.register("hello", "roadway", "hello.top", () => L("hello.top"), {order: 700});
      reg.register("hello", "roadway", "hello.bad", () => L("links"));
      reg.register("hello", "roadway", "hello.throws", () => { throw new Error("nope"); });
      reg.register("hello", "transit", "hello.stops", () => L("hello.stops"));
      reg.register("hello", "roadway", "hello.none", () => null);
      const all = reg.build("roadway", {}), hidden = reg.build("roadway", {}, new Set(["hello.dots"]));
      const refused = [];
      try { reg.register("hello", "rail", "hello.x", () => null); } catch (e) { refused.push(e.message); }
      try { reg.register("hello", "roadway", "dots", () => null); } catch (e) { refused.push(e.message); }
      return [all.layers.map(l => l.id), all.errors, hidden.layers.map(l => l.id),
              reg.build("transit", {}).layers.map(l => l.id), reg.toggleable().map(l => l.id), refused];
    })()"""
    layers, errors, hidden, transit, toggleable, refused = node_module("layers.js", ["createLayerRegistry"], expr)
    assert layers == [
        "base-deck", "selection-deck", "related-deck", "hello.dots",
        "highlighted-deck", "focus-deck", "marker-deck", "hello.top",
    ]
    assert errors == [
        {"owner": "hello", "id": "hello.bad", "error": 'deck layer id "links" must start with "hello."'},
        {"owner": "hello", "id": "hello.throws", "error": "nope"},
    ]
    assert hidden == [l for l in layers if l != "hello.dots"]
    assert transit == ["hello.stops"] and toggleable == ["hello.dots"]
    assert refused == ['unknown component "rail": use roadway or transit', 'hello: id "dots" must start with "hello."']
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q -k layer_registry`
Expected: FAIL (no `layers.js`).

- [ ] **Step 3: Create `layers.js`**

```js
// Map layers by network component: "roadway" now, "transit" reserved for P6 (accepted, not drawn yet). map.js
// builds its deck.gl layers from this registry on every render, core's own included. DOM-free and deck-free
// (factories close over deck.gl): unit-tested under node (tests/test_workbench_slots_js.py).
import { CORE, checkId } from "./slots.js";

export const COMPONENTS = ["roadway", "transit"];
// Core's layers, bottom to top. A plugin layer defaults to just above related records, under the user's own
// highlights, focus and marker, which stay on top.
export const CORE_ORDER = { base: 100, selection: 200, related: 300, highlighted: 400, focus: 500, marker: 600 };
export const PLUGIN_ORDER = 350;

export function createLayerRegistry() {
  const items = new Map();
  let seq = 0;
  const sorted = () => [...items.values()].sort((a, b) => a.order - b.order || a.seq - b.seq);
  return {
    // factory(ctx) -> a deck.gl layer, an array of them, or nothing. Returns a function that unregisters it.
    register(owner, component, id, factory, { order, title = null } = {}) {
      if (!COMPONENTS.includes(component)) {
        throw new Error(`unknown component ${JSON.stringify(component)}: use ${COMPONENTS.join(" or ")}`);
      }
      checkId(owner, id);
      if (typeof factory !== "function") throw new Error(`layer ${id}: factory must be a function`);
      if (items.has(id)) throw new Error(`layer ${JSON.stringify(id)} is already registered`);
      const fallback = owner === CORE ? CORE_ORDER[id] ?? 0 : PLUGIN_ORDER;
      items.set(id, { owner, component, id, factory, title, order: order ?? fallback, seq: seq++ });
      return () => items.delete(id);
    },
    removeOwner(owner) { for (const [id, l] of items) if (l.owner === owner) items.delete(id); },
    ids: () => sorted().map(l => l.id),
    // Layers with a title: the Layers panel lists them under "Overlays" with a show/hide switch.
    toggleable: () => sorted().filter(l => l.title),
    // Every layer of `component`, in order, skipping `hidden` ids. A factory that throws, or a plugin deck layer
    // whose id isn't namespaced ("hello.…"; core's ids such as "links" drive picking), is left out and reported.
    build(component, ctx, hidden = new Set()) {
      const layers = [], errors = [];
      for (const l of sorted()) {
        if (l.component !== component || hidden.has(l.id)) continue;
        let out;
        try { out = l.factory(ctx); } catch (e) {
          errors.push({ owner: l.owner, id: l.id, error: String((e && e.message) || e) });
          continue;
        }
        for (const layer of [].concat(out ?? [])) {
          if (!layer) continue;
          if (l.owner !== CORE && !(typeof layer.id === "string" && layer.id.startsWith(`${l.owner}.`))) {
            errors.push({ owner: l.owner, id: l.id, error: `deck layer id ${JSON.stringify(layer.id)} must start with "${l.owner}."` });
            continue;
          }
          layers.push(layer);
        }
      }
      return { layers, errors };
    },
  };
}

// The page's one layer registry.
export const layerRegistry = createLayerRegistry();
```

- [ ] **Step 4: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass. Then the fast tier and the ruff pair.

```bash
git add packages/netstead/netstead/workbench/static/js/layers.js packages/netstead/tests/test_workbench_slots_js.py
git commit -m "feat(workbench): layers.js, a map layer registry by component"
```

---

### Task 7: `map.js` renders from the layer registry (no visual change); Layers → Overlays

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/map.js`, `panels.js`, `store.js`, `main.js`, `index.html`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static tests**

Append to `test_workbench_static.py`:

```python
def test_map_draws_every_layer_from_the_registry_in_the_old_order():
    src = (JS_DIR / "map.js").read_text()
    assert 'layerRegistry.build("roadway"' in src
    for core_id in ("base", "selection", "related", "highlighted", "focus", "marker"):
        assert f'reg("{core_id}"' in src, f"core layer group {core_id} must register on the registry"
    render = re.search(r"export function render\(\) \{.*?\n\}", src, re.S).group(0)
    assert "layers.push(" not in render  # nothing bypasses the registry
    order = [src.index(f'reg("{core_id}"') for core_id in ("base", "selection", "related", "highlighted", "focus", "marker")]
    assert order == sorted(order)  # registered in drawing order (CORE_ORDER enforces it anyway)


def test_layers_popover_lists_overlays_from_the_registry():
    html = (STATIC_DIR / "index.html").read_text()
    panel = html[html.index('id="layers-panel"') : html.index('id="settings-panel"')]
    assert 'id="plugin-layers"' in panel
    assert "export function renderPluginLayers(" in (JS_DIR / "panels.js").read_text()
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`
Expected: FAIL.

- [ ] **Step 3: `map.js`: register core's layers, build from the registry**

Add the imports:

```js
import { layerRegistry } from "./layers.js";
import { CORE } from "./slots.js";
```

Give the handlers a layer-error hook (Task 14 points it at the Plugins section):

```js
let map = null, overlay = null, handlers = { onLinkClick() {}, onNodeClick() {}, onBoxSelect() {}, onLayerError(e) { console.error(e); } };
```

Replace `export function render() { … }` with the block below. Each core factory returns exactly what the old
`render()` pushed, under the same conditions, so the deck.gl layer list is unchanged:

```js
// Core's layer groups, bottom to top (layers.js CORE_ORDER). Each returns what render() used to push, or nothing.
function registerCoreLayers() {
  const reg = (id, factory) => layerRegistry.register(CORE, "roadway", id, factory);
  reg("base", c => baseLayers(c.net, c.style, linkColors(c.state)));
  reg("selection", c => (c.style.show.selection && c.selection ? selectionLayers(c.net, c.style, c.selection) : null));
  reg("related", c => (c.related ? relatedLayers(c.net, c.related) : null));
  reg("highlighted", c => (c.highlights.size ? idPathLayer(c.net, "highlighted", c.highlights, [...HIGHLIGHT_COLOR, 255], 2.5) : null));
  reg("focus", c => (c.focus && c.focus.table === "link" ? idPathLayer(c.net, "focus", [c.focus.id], FOCUS_COLOR, 4) : null));
  reg("marker", c => (c.marker ? markerLayer(c.marker) : null));
}

// What every layer factory sees (plugins' too: see the cookbook's registerLayer). `deck` is the global deck.gl.
function layerContext(s) {
  return { state: s, server: s.server, style: s.server.style, net: s.net, attrs: s.attrs, selection: activeSelection(s),
    focus: s.focus, highlights: s.highlights, related: s.related, marker: s.marker, zoom: map.getZoom(), deck };
}

export function render() {
  if (!overlay) return;
  const s = store.get();
  if (!s.net || !s.server) { overlay.setProps({ layers: [] }); return; }
  const { layers, errors } = layerRegistry.build("roadway", layerContext(s), s.hiddenLayers);
  overlay.setProps({ layers });
  setLabels(s.server.style.show.labels);
  for (const e of errors) handlers.onLayerError(e);
}

// A plugin's layer (wb.registerLayer): drawn at once, and listed under Layers → Overlays when it has a title.
export function addLayer(owner, component, id, factory, options) {
  const dispose = layerRegistry.register(owner, component, id, factory, options);
  const changed = () => store.set({ layerSeq: store.get().layerSeq + 1 });
  changed();
  return () => { dispose(); changed(); };
}
```

At the very end of `map.js`, add:

```js
registerCoreLayers();
```

(`baseLayers` still reads `map.getZoom()` for the arrows; `render()` still returns before `initMap` has run.)

- [ ] **Step 4: Store keys**

In `store.js`'s initial state, after `relHops`:

```js
  hiddenLayers: new Set(),    // titled overlay layers switched off in Layers → Overlays (per tab, never recorded)
  layerSeq: 0,                // bumped when a layer registers or goes, so Overlays and the map redraw
```

- [ ] **Step 5: The Overlays group**

In `index.html`, inside `#layers-panel`, after the `ramp-row` div:

```html
        <div id="plugin-layers"></div>
```

In `panels.js`, import the store (`import { store } from "./store.js";`) and add:

```js
// Layers → Overlays: each titled registry layer (a plugin's, or later core's issue markers) with a show/hide switch.
// Hiding is view state, like the focus: it is per tab and never recorded. Nothing renders while there are none.
export function renderPluginLayers(list, hidden) {
  $("plugin-layers").innerHTML = list.length
    ? '<h4 style="margin-top:14px">Overlays</h4>' + list.map(l => `<div class="row"><label class="sw">` +
      `<input type="checkbox" data-layer="${esc(l.id)}"${hidden.has(l.id) ? "" : " checked"}><span></span></label>` +
      `<span class="lbl">${esc(l.title)}</span></div>`).join("")
    : "";
}
```

and at the end of `wirePanels()`:

```js
  $("plugin-layers").onchange = e => {
    const el = e.target.closest("input[data-layer]");
    if (!el) return;
    const hidden = new Set(store.get().hiddenLayers);
    if (el.checked) hidden.delete(el.dataset.layer); else hidden.add(el.dataset.layer);
    store.set({ hiddenLayers: hidden });
  };
```

- [ ] **Step 6: `main.js`**

- import `renderPluginLayers` from `./panels.js` and `layerRegistry` from `./layers.js`;
- add `"hiddenLayers", "layerSeq"` to the keys of the first `store.subscribe([...], () => render())` in `wireStore()`;
- add to `wireStore()`:

```js
  store.subscribe(["layerSeq", "hiddenLayers"], s => renderPluginLayers(layerRegistry.toggleable(), s.hiddenLayers));
```

- [ ] **Step 7: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_slots_js.py -q`
Expected: all pass.

- [ ] **Step 8: Quick visual check**

`uv run netstead app`, open Leavenworth, repeat Task 0's screenshots 1–5. Expected: identical, including arrows at
zoom ≥ 13 with "Show direction" on, the related tint after a focus, and the node marker after a node-row click.

- [ ] **Step 9: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "refactor(workbench): map layers render from the layer registry; Layers → Overlays"
```

---

### Task 8: `tabs.js`: tab keys, badges, view per workspace, the derived mark

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/tabs.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_tab_keys_wrap_and_jump(node_module):
    got = node_module(
        "tabs.js",
        ["nextIndex"],
        '[nextIndex(0, "ArrowRight", 3), nextIndex(2, "ArrowRight", 3), nextIndex(0, "ArrowLeft", 3),'
        ' nextIndex(1, "Home", 3), nextIndex(0, "End", 3), nextIndex(0, "ArrowDown", 3),'
        ' nextIndex(0, "ArrowDown", 3, "vertical"), nextIndex(0, "ArrowUp", 3, "vertical"), nextIndex(0, "a", 3),'
        ' nextIndex(0, "End", 0)]',
    )
    assert got == [1, 0, 2, 0, 2, None, 1, 2, None, None]


def test_badges_normalise_roll_up_and_read_aloud(node_module):
    got = node_module(
        "tabs.js",
        ["normalizeBadge", "workspaceBadge", "tabLabel"],
        '[normalizeBadge(null), normalizeBadge(0), normalizeBadge(3), normalizeBadge({dirty: true, title: "2 unsaved"}),'
        ' normalizeBadge({text: ""}), workspaceBadge(null, [null, {text: "4"}, {dirty: true, title: "Draft card"}]),'
        ' workspaceBadge(null, [{text: "4"}]), workspaceBadge("!", [{dirty: true}]),'
        ' tabLabel("Edits", {text: 2, dirty: true, title: "2 unsaved"}), tabLabel("Issues", 5), tabLabel("Details", null)]',
    )
    assert got == [
        None,
        None,
        {"text": "3", "dirty": False, "title": ""},
        {"text": "", "dirty": True, "title": "2 unsaved"},
        None,
        {"text": "", "dirty": True, "title": "Draft card"},  # a dirty panel marks its workspace tab
        None,  # a count alone doesn't
        {"text": "!", "dirty": False, "title": ""},
        "Edits, 2 unsaved",
        "Issues, 5",
        "Details",
    ]


def test_view_for_a_workspace_and_the_derived_network_mark(node_module):
    got = node_module(
        "tabs.js",
        ["viewFor", "networkBadge"],
        '[viewFor({id: "cards.edit", layout: {view: "split"}}, {}, "map"),'
        ' viewFor({id: "cards.edit", layout: {view: "split"}}, {"cards.edit": "table"}, "map"),'
        ' viewFor({id: "inspect", layout: {}}, {}, "table"),'
        ' networkBadge({derived_from: "n1"}), networkBadge({derived_from: null}), networkBadge(null)]',
    )
    assert got == ["split", "table", "table", "derived", "", ""]
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q -k "tab_keys or badges or view_for"`
Expected: FAIL (no `tabs.js`).

- [ ] **Step 3: Create `tabs.js`**

```js
// Tab strips and menus: keyboard movement, badges, which view a workspace opens in, and the network switcher's
// mark. Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

const STEPS = { horizontal: { ArrowRight: 1, ArrowLeft: -1 }, vertical: { ArrowDown: 1, ArrowUp: -1 } };

// The index a key moves to in a strip or menu of `count` items (arrows wrap; Home and End jump), or null when the
// key doesn't move (the caller then leaves the event alone).
export function nextIndex(index, key, count, axis = "horizontal") {
  if (!count) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  const step = STEPS[axis][key];
  return step ? (index + step + count) % count : null;
}

const blank = v => v === null || v === undefined || v === false || v === "" || v === 0;

// A badge as registered (a count, a string, {text, dirty, title}, or nothing) -> {text, dirty, title} or null.
export function normalizeBadge(b) {
  if (blank(b)) return null;
  if (typeof b === "string" || typeof b === "number") return { text: String(b), dirty: false, title: "" };
  const text = blank(b.text) ? "" : String(b.text);
  if (!text && !b.dirty) return null;
  return { text, dirty: Boolean(b.dirty), title: b.title || "" };
}

// A workspace tab's badge: its own, else a dirty mark when any of its panels is dirty (UX principle 7).
export function workspaceBadge(own, panelBadges) {
  const b = normalizeBadge(own);
  if (b) return b;
  const dirty = panelBadges.map(normalizeBadge).filter(x => x && x.dirty);
  if (!dirty.length) return null;
  return { text: "", dirty: true, title: dirty.map(d => d.title).filter(Boolean).join("; ") || "Unsaved changes" };
}

// What a screen reader hears for a tab: "Edits, 2 unsaved", "Issues, 5", "Details".
export function tabLabel(title, badge) {
  const b = normalizeBadge(badge);
  if (!b) return title;
  const parts = [title];
  if (b.title) parts.push(b.title);
  else {
    if (b.text) parts.push(b.text);
    if (b.dirty) parts.push("unsaved changes");
  }
  return parts.join(", ");
}

// The view (map | split | table) a workspace opens in: where the user left it, else its layout's, else the current.
export const viewFor = (workspace, remembered, current) =>
  remembered[workspace.id] || (workspace.layout && workspace.layout.view) || current;

// The network switcher's mark for a derived (non-base) network, such as a plugin's preview: "" for a base network.
export const networkBadge = summary => (summary && summary.derived_from ? "derived" : "");
```

- [ ] **Step 4: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q`, then the fast tier and ruff.

```bash
git add packages/netstead/netstead/workbench/static/js/tabs.js packages/netstead/tests/test_workbench_slots_js.py
git commit -m "feat(workbench): tabs.js, tab keys, badges and view-per-workspace rules"
```

---

### Task 9: Workspace tabs and the dock; core Inspect and Details through the slots

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/workspaces.js`
- Modify: `index.html`, `app.css`, `js/store.js`, `js/table.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static tests**

```python
def test_the_drawer_is_the_dock_and_keeps_the_details_content():
    html = (STATIC_DIR / "index.html").read_text()
    side = html[html.index('<aside id="side">') : html.index("</aside>")]
    assert 'id="dock-tabs" role="tablist"' in side and side.index('id="dock-tabs"') < side.index('id="dock-details"')
    pane = side[side.index('id="dock-details"') :]
    assert 'data-panel="details"' in pane and 'role="tabpanel"' in pane
    for element_id in ("status-wrap", "hl-count", "hl-set", "hl-clear", "details", "anchors", "fragment", "diag"):
        assert f'id="{element_id}"' in pane


def test_the_workspace_strip_is_in_the_first_header_row_and_starts_hidden():
    html = (STATIC_DIR / "index.html").read_text()
    row = html[html.index("<header>") : html.index('id="nl-row"')]
    assert '<nav id="ws-tabs" role="tablist" aria-label="Workspaces" hidden>' in row
    assert '<div id="dock-tabs" role="tablist" aria-label="Panels" hidden>' in html


def test_core_registers_inspect_and_details_through_the_slots():
    src = (JS_DIR / "workspaces.js").read_text()
    assert 'slots.addWorkspace(CORE, { id: "inspect"' in src and 'slots.addPanel(CORE, { id: "details"' in src
    main = (JS_DIR / "main.js").read_text()
    assert "registerCoreSlots()" in main and "wireWorkspaces(" in main
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`
Expected: FAIL.

- [ ] **Step 3: Markup**

In `index.html`, in the first header row, directly after `<h1>Netstead Workbench</h1>`:

```html
      <nav id="ws-tabs" role="tablist" aria-label="Workspaces" hidden></nav>
```

Replace the opening `<aside id="side">` line with the lines below, and put `    </section>` immediately before
`</aside>`. Everything between them (status, highlights, details, anchors, fragment, diagnostics) is unchanged:

```html
  <aside id="side">
    <div id="dock-tabs" role="tablist" aria-label="Panels" hidden></div>
    <section class="dock-pane" id="dock-details" data-panel="details" role="tabpanel" aria-labelledby="dtab-details">
```

Append to `app.css`:

```css
  /* ---- plugins part 2: workspace tabs and the dock (strips show with two or more tabs) ---- */
  #ws-tabs, #dock-tabs { display:flex; gap:2px; flex-wrap:wrap; }
  #ws-tabs button, #dock-tabs button { background:none; color:var(--muted); border:0; border-radius:6px 6px 0 0;
    padding:6px 10px; font-size:12.5px; font-weight:600; }
  #ws-tabs button.on, #dock-tabs button.on { color:var(--ink); box-shadow:inset 0 -2px 0 var(--accent); }
  #ws-tabs button:focus-visible, #dock-tabs button:focus-visible { outline:2px solid var(--hl); outline-offset:-2px; }
  #dock-tabs { margin:-4px 0 12px; border-bottom:1px solid var(--edge); }
  .dirty { color:var(--accent); font-size:10px; margin-left:3px; }
```

- [ ] **Step 4: Store keys and the view mode getter**

In `store.js`, after `layerSeq`:

```js
  workspace: "inspect",       // the active workspace tab (per tab; remembered in localStorage)
  dockPanel: {},              // workspace id -> the dock panel showing in it
```

In `table.js`, after `tableShowing`:

```js
export const currentViewMode = () => $("stage").dataset.mode;
```

- [ ] **Step 5: Create `workspaces.js`**

```js
// Workspace tabs (header) and the dock (the right drawer), rendered from the slots registry. Core's Inspect workspace
// and Details panel register here exactly as a plugin's would. Each strip shows only with two or more tabs, so a
// Workbench with no plugins looks as it always has. Both strips follow the WAI-ARIA tabs pattern (arrows, Home, End).
import { $, esc, toast } from "./dom.js";
import { ALL_WORKSPACES, CORE, panelsFor, slots } from "./slots.js";
import { store } from "./store.js";
import { currentViewMode, setViewMode } from "./table.js";
import { nextIndex, normalizeBadge, tabLabel, viewFor, workspaceBadge } from "./tabs.js";

const WS_KEY = "netstead.workspace";
const rendered = new Set(); // panels whose render(el) has run (once, the first time each shows)
const views = {};           // workspace id -> the view mode the user left it in
let lastShown = null;
let report = (owner, phase, error) => toast(`${owner}: ${(error && error.message) || error}`);

const paneFor = id => document.getElementById(`dock-${id}`);

export function registerCoreSlots() {
  slots.addWorkspace(CORE, { id: "inspect", title: "Inspect", order: 0 });
  slots.addPanel(CORE, { id: "details", title: "Details", workspace: ALL_WORKSPACES, order: 0 }); // adopts #dock-details
}

export function addWorkspace(owner, spec) {
  const dispose = slots.addWorkspace(owner, spec);
  renderStrips();
  return () => {
    dispose();
    if (store.get().workspace === spec.id) showWorkspace("inspect"); else renderStrips();
  };
}

// A panel's pane is created now (hidden) unless the markup already has one (core's Details); render(el) runs the
// first time it shows.
export function addPanel(owner, spec) {
  const dispose = slots.addPanel(owner, spec);
  if (!paneFor(spec.id)) {
    const pane = document.createElement("section");
    pane.id = `dock-${spec.id}`;
    pane.className = "dock-pane";
    pane.hidden = true;
    pane.dataset.panel = spec.id;
    pane.setAttribute("role", "tabpanel");
    pane.setAttribute("aria-labelledby", `dtab-${spec.id}`);
    $("side").appendChild(pane);
  }
  renderStrips();
  return {
    refreshBadge: () => renderStrips(),
    dispose() {
      dispose();
      const pane = paneFor(spec.id);
      if (pane) pane.remove();
      rendered.delete(spec.id);
      renderStrips();
    },
  };
}

export function showWorkspace(id) {
  const s = store.get();
  const target = slots.workspaces.get(id) ? id : "inspect";
  if (target === s.workspace) { renderStrips(); return; }
  views[s.workspace] = currentViewMode();
  const mode = viewFor(slots.workspaces.get(target), views, currentViewMode());
  store.set({ workspace: target });
  if (mode !== currentViewMode()) setViewMode(mode);
  try { localStorage.setItem(WS_KEY, target); } catch (e) { /* storage unavailable: the workspace isn't remembered */ }
}

// Show panel `id`, switching to a workspace that has it when the current one doesn't.
export function showPanel(id) {
  const p = slots.panels.get(id);
  if (!p) return;
  if (!panelsFor([p], store.get().workspace).length) showWorkspace([].concat(p.workspace)[0]);
  const s = store.get();
  store.set({ dockPanel: { ...s.dockPanel, [s.workspace]: id } });
}

// After plugins load: reopen the workspace this browser last used, if it still exists.
export function restoreWorkspace() {
  let id = null;
  try { id = localStorage.getItem(WS_KEY); } catch (e) { /* storage unavailable */ }
  if (id && slots.workspaces.get(id)) showWorkspace(id);
}

const currentPanels = () => panelsFor(slots.panels.list(), store.get().workspace);
const selectedPanel = panels => panels.find(p => p.id === store.get().dockPanel[store.get().workspace]) || panels[0] || null;

function badgeOf(item) {
  if (!item.badge) return null;
  try { return normalizeBadge(item.badge({ state: store.get().server })); } catch (e) { report(item.owner, "badge", e); return null; }
}

const badgeHTML = b => (b && b.text ? ` <span class="pcount">${esc(b.text)}</span>` : "") +
  (b && b.dirty ? ' <span class="dirty" aria-hidden="true">●</span>' : "");

function tabHTML(prefix, item, selected, badge, controls) {
  return `<button role="tab" id="${prefix}-${esc(item.id)}" data-id="${esc(item.id)}" aria-selected="${selected}" ` +
    `aria-controls="${esc(controls)}" tabindex="${selected ? 0 : -1}" aria-label="${esc(tabLabel(item.title, badge))}"` +
    `${selected ? ' class="on"' : ""}>${esc(item.title)}${badgeHTML(badge)}</button>`;
}

// Redraw a strip without losing keyboard focus from the tab that had it (state arrives while a user tabs around).
function redraw(el, html) {
  const focused = el.contains(document.activeElement) ? document.activeElement.id : null;
  el.innerHTML = html;
  if (focused && document.getElementById(focused)) document.getElementById(focused).focus();
}

export function renderStrips() {
  const s = store.get(), workspaces = slots.workspaces.list();
  $("ws-tabs").hidden = workspaces.length < 2;
  redraw($("ws-tabs"), $("ws-tabs").hidden ? "" : workspaces.map(w => tabHTML("wtab", w, w.id === s.workspace,
    workspaceBadge(badgeOf(w), panelsFor(slots.panels.list(), w.id).map(badgeOf)), "stage")).join(""));
  const panels = currentPanels(), current = selectedPanel(panels);
  $("dock-tabs").hidden = panels.length < 2;
  redraw($("dock-tabs"), $("dock-tabs").hidden ? "" : panels.map(p => tabHTML("dtab", p, p === current, badgeOf(p), `dock-${p.id}`)).join(""));
  for (const pane of $("side").querySelectorAll(".dock-pane")) pane.hidden = !current || pane.dataset.panel !== current.id;
  if (current) showPane(current);
}

function showPane(p) {
  const pane = paneFor(p.id);
  if (!pane) return;
  if (!rendered.has(p.id)) {
    rendered.add(p.id);
    if (p.render) {
      try {
        const out = p.render(pane);
        if (out && typeof out.catch === "function") out.catch(e => failed(p, pane, e));
      } catch (e) { failed(p, pane, e); }
    }
  }
  if (lastShown !== p.id && p.onShow) { try { p.onShow(); } catch (e) { report(p.owner, "panel", e); } }
  lastShown = p.id;
}

function failed(p, pane, e) {
  report(p.owner, "panel", e);
  pane.innerHTML = `<p class="empty">This panel failed to load: ${esc((e && e.message) || e)}</p>`;
}

function onStripKey(e, items, currentId, activate, prefix) {
  const n = nextIndex(items.findIndex(x => x.id === currentId), e.key, items.length);
  if (n === null) return;
  e.preventDefault();
  activate(items[n].id);
  const tab = document.getElementById(`${prefix}-${items[n].id}`);
  if (tab) tab.focus();
}

export function wireWorkspaces({ onError } = {}) {
  if (onError) report = onError;
  $("ws-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) showWorkspace(b.dataset.id); };
  $("dock-tabs").onclick = e => { const b = e.target.closest('[role="tab"]'); if (b) showPanel(b.dataset.id); };
  $("ws-tabs").onkeydown = e => onStripKey(e, slots.workspaces.list(), store.get().workspace, showWorkspace, "wtab");
  $("dock-tabs").onkeydown = e => onStripKey(e, currentPanels(), (selectedPanel(currentPanels()) || {}).id, showPanel, "dtab");
  store.subscribe(["workspace", "dockPanel", "server"], () => renderStrips());
  renderStrips();
}
```

- [ ] **Step 6: `main.js`**

Import `registerCoreSlots` and `wireWorkspaces` from `./workspaces.js`. In `boot()`, before the existing `wire*()`
line, add:

```js
  registerCoreSlots();
  wireWorkspaces();
```

- [ ] **Step 7: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass.

- [ ] **Step 8: Quick visual check**

Task 0 screenshots 1–4 and 7: identical (no strip in the header, none in the drawer). Then, in the page console,
check the slots took effect: `document.querySelector('#dock-details').hidden === false`.

- [ ] **Step 9: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): workspace tabs and the dock; core Inspect and Details register through them"
```

---

### Task 10: Context menus: map feature, table row, and the selection

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/ctxmenu.js`
- Modify: `index.html`, `app.css`, `js/map.js`, `js/table.js`, `js/store.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static tests**

```python
def test_context_menus_are_wired_on_the_map_the_grid_and_the_selection():
    html = (STATIC_DIR / "index.html").read_text()
    assert '<div id="ctxmenu" role="menu" aria-label="Commands" hidden></div>' in html
    pane = html[html.index('id="dock-details"') : html.index("</aside>")]
    assert 'id="sel-cmds-wrap" hidden' in pane and 'aria-haspopup="menu"' in pane
    map_js = (JS_DIR / "map.js").read_text()
    assert 'map.on("contextmenu"' in map_js and "handlers.onContextMenu(" in map_js
    table = (JS_DIR / "table.js").read_text()
    assert 'openContextMenu("row"' in table and "oncontextmenu" in table
    main = (JS_DIR / "main.js").read_text()
    assert 'openContextMenu("feature"' in main and "wireContextMenus(" in main
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k context_menus`
Expected: FAIL.

- [ ] **Step 3: Markup and CSS**

In `index.html`, inside `#dock-details`, directly after `<div id="status-wrap"></div>`:

```html
      <div id="sel-cmds-wrap" hidden><button class="mini ghost" id="sel-cmds" aria-haspopup="menu">Selection actions &#9662;</button></div>
```

Before `<div id="toast" role="status"></div>`:

```html
<div id="ctxmenu" role="menu" aria-label="Commands" hidden></div>
```

Append to `app.css`:

```css
  #sel-cmds-wrap { margin:8px 0 0; }
  #ctxmenu { position:fixed; z-index:30; min-width:200px; max-width:320px; padding:4px; background:var(--panel);
             border:1px solid var(--edge); border-radius:8px; box-shadow:0 6px 24px rgba(0,0,0,.45); }
  #ctxmenu .ctx-head { padding:6px 10px 2px; font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  #ctxmenu button { display:block; width:100%; text-align:left; background:none; color:var(--ink); border:0; border-radius:6px;
                    padding:6px 10px; font-weight:500; font-size:12.5px; }
  #ctxmenu button:hover, #ctxmenu button:focus { background:#20242e; outline:none; }
```

- [ ] **Step 4: Store key**

In `store.js`, after `dockPanel`:

```js
  commandSeq: 0,              // bumped when a command registers or goes, so menus re-check what applies
```

- [ ] **Step 5: Create `ctxmenu.js`**

```js
// Context menus: commands for a right-clicked map feature or grid row (plus the selection's), and the "Selection
// actions" menu in the Details panel. A menu opens only when a command applies, so with none registered the map and
// the grid behave as before (MapLibre's right-drag; the browser's own menu on a row). WAI-ARIA menu keys.
import { $, esc } from "./dom.js";
import { applicable, commandContext, commandLabel, menuSections, runCommand, selectionSections } from "./commands.js";
import { slots } from "./slots.js";
import { store } from "./store.js";
import { nextIndex } from "./tabs.js";

let entries = [], menuCtx = null, opener = null;
let report = () => {};

const contextNow = target => {
  const s = store.get();
  return commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace }, target);
};
const items = () => [...$("ctxmenu").querySelectorAll('[role="menuitem"]')];

export const menuOpen = () => !$("ctxmenu").hidden;

// The menu for a right-clicked map feature ("feature") or grid row ("row") at viewport point `at`; false when no
// command applies (the caller then leaves the event alone).
export function openContextMenu(context, target, at) {
  const ctx = contextNow(target);
  return show(menuSections(slots.commands.list(), context, ctx, report), ctx, at);
}

function openSelectionMenu() {
  const ctx = contextNow(null), r = $("sel-cmds").getBoundingClientRect();
  show(selectionSections(slots.commands.list(), ctx, report), ctx, { x: r.left, y: r.bottom + 4 });
}

function show(sections, ctx, at) {
  if (!sections.length) return false;
  opener = document.activeElement;
  entries = sections.flatMap(sec => sec.items);
  menuCtx = ctx;
  let i = 0;
  const menu = $("ctxmenu");
  menu.innerHTML = sections.map(sec => `<div class="ctx-head" role="presentation">${esc(sec.title)}</div>` +
    sec.items.map(c => `<button role="menuitem" tabindex="-1" data-i="${i++}">${esc(commandLabel(c))}</button>`).join("")).join("");
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(at.x, innerWidth - box.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(at.y, innerHeight - box.height - 4))}px`;
  items()[0].focus();
  return true;
}

export function closeMenu({ restore = true } = {}) {
  if (!menuOpen()) return;
  $("ctxmenu").hidden = true;
  if (restore && opener && opener.isConnected) opener.focus();
  opener = null;
}

function choose(i) {
  const command = entries[i], ctx = menuCtx;
  closeMenu();
  if (command) runCommand(command, ctx, report);
}

// "Selection actions" shows only while a selection command applies (core registers none).
function syncSelectionButton() {
  const ctx = contextNow(null);
  $("sel-cmds-wrap").hidden = !(ctx.selection && applicable(slots.commands.list(), "selection", ctx).length);
}

export function wireContextMenus({ onError }) {
  report = onError;
  const menu = $("ctxmenu");
  menu.onclick = e => { const b = e.target.closest('[role="menuitem"]'); if (b) choose(Number(b.dataset.i)); };
  menu.onkeydown = e => {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); return; }
    if (e.key === "Tab") { e.preventDefault(); closeMenu(); return; }
    const list = items(), n = nextIndex(list.indexOf(document.activeElement), e.key, list.length, "vertical");
    if (n !== null) { e.preventDefault(); list[n].focus(); }
  };
  document.addEventListener("pointerdown", e => { if (menuOpen() && !menu.contains(e.target)) closeMenu({ restore: false }); }, true);
  window.addEventListener("blur", () => closeMenu({ restore: false }));
  $("sel-cmds").onclick = () => openSelectionMenu();
  store.subscribe(["server", "commandSeq"], syncSelectionButton);
}
```

(Enter and Space on a focused menu item are a native button click, so they reach `menu.onclick`.)

- [ ] **Step 6: The map: a right-click picks a record**

In `map.js`, add `onContextMenu() {}` to the default `handlers`, add this function after `getTooltip`:

```js
// The record under a right-click: a link or node feature ({table, id}), or null.
function recordAt(point) {
  const s = store.get();
  const info = overlay.pickObject({ x: point.x, y: point.y, radius: 4, layerIds: ["nodes", "links"] });
  if (!info || info.index == null || info.index < 0 || !s.net) return null;
  if (info.layer.id === "links") return { table: "link", id: s.attrs.link_id[info.index] };
  if (info.layer.id === "nodes") return { table: "node", id: s.net.nodeIds[info.index] };
  return null;
}
```

and in `initMap`, after `map.addControl(overlay);`:

```js
  map.on("contextmenu", e => {
    const target = recordAt(e.point);
    if (target) handlers.onContextMenu(target, { x: e.originalEvent.clientX, y: e.originalEvent.clientY });
  });
```

- [ ] **Step 7: The grid: a right-click on a row**

In `table.js`, import `openContextMenu` from `./ctxmenu.js`, and in `renderRows`, after the `tr.onclick` loop:

```js
  for (const tr of body.querySelectorAll("tr.data")) tr.oncontextmenu = e => {
    if (tr.dataset.pk === "" || !TBL.schema.primary_key) return;
    const target = { table: TBL.name, id: coerceId(tr.dataset.pk, pkNumeric()) };
    if (openContextMenu("row", target, { x: e.clientX, y: e.clientY })) e.preventDefault();
  };
```

- [ ] **Step 8: `main.js`**

Import `openContextMenu` and `wireContextMenus` from `./ctxmenu.js`. In `boot()`, after `wireWorkspaces();`:

```js
  wireContextMenus({ onError: (command, e) => toast(`${command.title}: ${e.message}`) }); // Task 14 routes this to the Plugins section
```

and add to the `initMap(cfg.style, { … })` hooks:

```js
    onContextMenu: (target, at) => openContextMenu("feature", target, at),
```

- [ ] **Step 9: Run the tests, check by hand, commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass. By hand: right-click a link and a grid row with no plugin installed. Expected: nothing new on
the map (right-drag still rotates), the browser's own menu on the row, and no "Selection actions" button.

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): context menus for map features, grid rows and the selection"
```

---
### Task 11: The command palette, core's palette commands, and shared dialog focus

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/modal.js`, `cmdpalette.js`, `corecmds.js`
- Modify: `index.html`, `app.css`, `js/settings.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static tests**

```python
def test_the_palette_is_an_accessible_combobox_dialog():
    html = (STATIC_DIR / "index.html").read_text()
    palette = html[html.index('id="cmdk"') : html.index("<!-- /cmdk -->")]
    assert 'role="dialog" aria-modal="true"' in palette
    assert 'id="cmdk-input" role="combobox"' in palette and 'aria-controls="cmdk-list"' in palette
    assert 'id="cmdk-list" role="listbox"' in palette
    row = html[html.index("<header>") : html.index('id="nl-row"')]
    assert 'id="cmd-btn"' in row and 'aria-keyshortcuts="Control+K Meta+K"' in row


def test_dialogs_share_one_focus_trap():
    settings = (JS_DIR / "settings.js").read_text()
    assert "function trapTab" not in settings and 'from "./modal.js"' in settings
    assert 'from "./modal.js"' in (JS_DIR / "cmdpalette.js").read_text()
    main = (JS_DIR / "main.js").read_text()
    assert "wirePalette(" in main and "registerCoreCommands()" in main
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k "palette or focus_trap"`
Expected: FAIL.

- [ ] **Step 3: `modal.js`, and `settings.js` uses it**

Create `modal.js` (the two functions move verbatim from `settings.js`, generalised to any dialog):

```js
// Focus handling shared by the Workbench's dialogs (Settings, the command palette, an Action's form): Tab cycles
// inside the dialog. Each dialog also remembers what opened it and returns focus there on close.
export const focusables = root => [...root.querySelectorAll("button, input, select, textarea, a[href], [tabindex]")]
  .filter(el => !el.disabled && el.tabIndex >= 0 && el.getClientRects().length);

export function trapTab(root, e) {
  if (e.key !== "Tab") return;
  const els = focusables(root);
  if (!els.length) return;
  const first = els[0], last = els[els.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}
```

In `settings.js`: delete its local `focusables` and `trapTab`, add `import { trapTab } from "./modal.js";`, and in
`wireSettings()` change the keydown line to:

```js
  $("settings").addEventListener("keydown", e => trapTab($("settings"), e));
```

- [ ] **Step 4: Markup and CSS**

In the first header row, directly before the `jobs-btn` button:

```html
      <button id="cmd-btn" class="mini ghost" title="Command palette (Ctrl+K, ⌘K on a Mac)" aria-keyshortcuts="Control+K Meta+K">Commands…</button>
```

After the `<!-- /settings -->` comment:

```html
<div id="cmdk" class="modal" role="dialog" aria-modal="true" aria-label="Command palette" hidden>
  <div class="modal-card cmdk-card">
    <input id="cmdk-input" role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-autocomplete="list"
           aria-label="Command" placeholder="Type a command…" autocomplete="off" spellcheck="false" />
    <ul id="cmdk-list" role="listbox" aria-label="Commands"></ul>
  </div>
</div>
<!-- /cmdk -->
```

Append to `app.css`:

```css
  .cmdk-card { width:min(560px, calc(100vw - 32px)); align-self:flex-start; margin-top:12vh; }
  #cmdk-input { margin:12px; padding:9px 12px; font-size:14px; }
  #cmdk-list { list-style:none; margin:0; padding:0 6px 8px; max-height:50vh; overflow:auto; }
  #cmdk-list .cmdk-head { padding:8px 10px 2px; font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  #cmdk-list [role="option"] { padding:7px 10px; border-radius:6px; cursor:pointer; font-size:13px; }
  #cmdk-list [role="option"].on { background:#20242e; color:var(--accent); }
  @media (max-width: 640px) { #cmd-btn { display:none; } } /* Ctrl+K still opens it */
```

- [ ] **Step 5: Create `cmdpalette.js`**

```js
// The command palette (Ctrl+K / ⌘K, or "Commands…"): every applicable command, searchable, keyboard first. The input
// is a combobox over a listbox (aria-activedescendant); Enter runs, Escape closes, focus returns to the opener.
import { $, esc } from "./dom.js";
import { commandContext, commandLabel, isPaletteShortcut, paletteGroups, runCommand } from "./commands.js";
import { trapTab } from "./modal.js";
import { slots } from "./slots.js";
import { store } from "./store.js";
import { nextIndex } from "./tabs.js";

let opener = null, items = [], active = 0;
let report = () => {};

export const paletteOpen = () => !$("cmdk").hidden;

export function openPalette() {
  if (paletteOpen()) return;
  opener = document.activeElement;
  $("cmdk").hidden = false;
  $("cmdk-input").value = "";
  refresh();
  $("cmdk-input").focus();
}

export function closePalette() {
  $("cmdk").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

function refresh() {
  const s = store.get();
  const ctx = commandContext({ server: s.server, focus: s.focus, highlights: s.highlights, workspace: s.workspace });
  const groups = paletteGroups(slots.commands.list(), ctx, $("cmdk-input").value, report);
  items = groups.flatMap(g => g.items);
  active = 0;
  let i = 0;
  $("cmdk-list").innerHTML = groups.length
    ? groups.map(g => `<li role="presentation" class="cmdk-head">${esc(g.title)}</li>` + g.items.map(it =>
      `<li role="option" id="cmdk-opt-${i}" data-i="${i++}" aria-selected="false">${esc(commandLabel(it.command))}</li>`).join("")).join("")
    : '<li role="presentation" class="empty">No matching commands.</li>';
  mark();
}

function mark() {
  for (const li of $("cmdk-list").querySelectorAll('[role="option"]')) {
    const on = Number(li.dataset.i) === active;
    li.setAttribute("aria-selected", String(on));
    li.classList.toggle("on", on);
    if (on) li.scrollIntoView({ block: "nearest" });
  }
  if (items.length) $("cmdk-input").setAttribute("aria-activedescendant", `cmdk-opt-${active}`);
  else $("cmdk-input").removeAttribute("aria-activedescendant");
}

function choose(i) {
  const it = items[i];
  if (!it) return;
  closePalette();
  runCommand(it.command, it.ctx, report);
}

export function wirePalette({ onError }) {
  report = onError;
  $("cmd-btn").onclick = () => openPalette();
  $("cmdk-input").oninput = refresh;
  $("cmdk-input").onkeydown = e => {
    if (e.key === "Enter") { e.preventDefault(); choose(active); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePalette(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return; // Home/End keep moving the caret
    const n = nextIndex(active, e.key, items.length, "vertical");
    if (n !== null) { e.preventDefault(); active = n; mark(); }
  };
  $("cmdk-list").onclick = e => { const li = e.target.closest('[role="option"]'); if (li) choose(Number(li.dataset.i)); };
  $("cmdk").addEventListener("keydown", e => trapTab($("cmdk"), e));
  $("cmdk").addEventListener("pointerdown", e => { if (e.target === $("cmdk")) closePalette(); }); // the backdrop
  document.addEventListener("keydown", e => {
    if (!isPaletteShortcut(e) || !$("settings").hidden || !$("wizard").hidden) return; // one dialog at a time
    e.preventDefault();
    if (paletteOpen()) closePalette(); else openPalette();
  });
}
```

- [ ] **Step 6: Create `corecmds.js`**

```js
// Core's palette commands: the header's and the map's buttons, reachable from the keyboard.
import { fitLinks, fitNetwork } from "./map.js";
import { openSettings } from "./settings.js";
import { CORE, slots } from "./slots.js";
import { store } from "./store.js";
import { setViewMode } from "./table.js";
import { openWizard } from "./wizard.js";

export function registerCoreCommands() {
  const add = spec => slots.addCommand(CORE, { contexts: ["palette"], ...spec });
  add({ id: "open", title: "Open / Import…", run: () => openWizard() });
  add({ id: "settings", title: "Settings…", run: () => openSettings() });
  add({ id: "zoom_network", title: "Zoom to full network", when: c => Boolean(c.network), run: () => fitNetwork() });
  add({ id: "zoom_selection", title: "Zoom to selection", when: c => c.selectionCount > 0, run: c => fitLinks(c.selection.link_ids) });
  add({ id: "highlight_mode", title: "Highlight links (on / off)", when: c => Boolean(c.network),
    run: () => store.set({ highlightMode: !store.get().highlightMode }) });
  add({ id: "clear_highlights", title: "Clear highlights", when: c => c.highlights.length > 0, run: () => store.set({ highlights: new Set() }) });
  for (const [mode, title] of [["map", "Map"], ["split", "Split"], ["table", "Table"]]) {
    add({ id: `view_${mode}`, group: "View", title, run: () => setViewMode(mode) });
  }
}
```

- [ ] **Step 7: `main.js`**

Import `wirePalette` from `./cmdpalette.js` and `registerCoreCommands` from `./corecmds.js`. In `boot()`, after
`wireContextMenus(…)`:

```js
  registerCoreCommands();
  wirePalette({ onError: (command, e) => toast(`${command.title}: ${e.message}`) }); // Task 14 routes this too
```

- [ ] **Step 8: Run the tests, check by hand, commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q`
Expected: all pass. By hand: Ctrl+K from the map and from the utterance box opens the palette; typing "zo" ranks
the zoom commands; ↓/↑ move; Enter runs; Escape closes and focus returns to where it was. Settings: Tab still cycles
inside the dialog (the trap moved).

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): command palette (Ctrl+K) with core commands; dialogs share one focus trap"
```

---

### Task 12: `wb.schemaForm` in the DOM, and the Action form dialog

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/formview.js`, `actform.js`
- Modify: `index.html`, `app.css`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static test**

```python
def test_schema_forms_reuse_the_shared_field_model_and_the_action_dialog_exists():
    formview = (JS_DIR / "formview.js").read_text()
    for name in ("fieldsFrom", "inputHTML", "parseControl", "setPath", "formErrors"):
        assert name in formview
    html = (STATIC_DIR / "index.html").read_text()
    dialog = html[html.index('id="actform"') : html.index("<!-- /actform -->")]
    for element_id in ("actform-title", "actform-desc", "actform-body", "actform-result", "actform-run", "actform-close"):
        assert f'id="{element_id}"' in dialog
    assert "wireActionForm()" in (JS_DIR / "main.js").read_text()
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k schema_forms`
Expected: FAIL.

- [ ] **Step 3: Create `formview.js`**

```js
// wb.schemaForm: a form generated from a JSON Schema, with the Settings dialog's field kinds, controls and parsing
// (schemaform.js). Labels are real <label for>; required fields carry aria-required; a bad entry is reported on its
// control (setCustomValidity), and the form's value is never changed by it.
import { esc } from "./dom.js";
import { fieldsFrom, formErrors, inputHTML, parseControl, setPath } from "./schemaform.js";

let forms = 0;

// Render a form for `schema` into `el`, prefilled from `value`. `onChange(value, {errors})` runs after each edit.
// Returns {value(), errors(), set(value), focus()}.
export function schemaForm(el, schema, value = {}, { onChange = () => {}, omit = [] } = {}) {
  const prefix = `sf${++forms}-`;
  let current = JSON.parse(JSON.stringify(value || {}));
  let fields = [];
  const draw = () => {
    fields = fieldsFrom(schema, current, { omit });
    let group = null;
    el.innerHTML = fields.map(f => {
      const head = f.group && f.group !== group ? `<div class="sf-group">${esc(f.group)}</div>` : "";
      group = f.group;
      return `${head}<div class="sf-field"><label for="${prefix}${esc(f.key.replace(/\./g, "-"))}">${esc(f.label)}` +
        `${f.required ? ' <span class="sf-req" aria-hidden="true">*</span>' : ""}</label>${inputHTML(f, prefix)}` +
        (f.description ? `<div class="sf-help">${esc(f.description)}</div>` : "") + "</div>";
    }).join("") || '<p class="empty">Nothing to fill in.</p>';
  };
  const api = {
    value: () => JSON.parse(JSON.stringify(current)),
    errors: () => formErrors(fields, current),
    set(next) { current = JSON.parse(JSON.stringify(next || {})); draw(); },
    focus() { const c = el.querySelector("[data-key]:not([disabled])"); if (c) c.focus(); return Boolean(c); },
  };
  el.classList.add("sf");
  el.onchange = e => {
    const control = e.target.closest("[data-key]");
    if (!control) return;
    const field = fields.find(f => f.key === control.dataset.key);
    const parsed = parseControl(field, { type: control.type, value: control.value, checked: control.checked,
      badInput: Boolean(control.validity && control.validity.badInput) });
    control.setCustomValidity(parsed.ok ? "" : parsed.error);
    if (!parsed.ok) { control.reportValidity(); return; }
    current = setPath(current, field.key, parsed.value);
    onChange(api.value(), { errors: api.errors() });
  };
  draw();
  return api;
}
```

- [ ] **Step 4: Create `actform.js`**

```js
// One Action's form, generated from its JSON Schema: how a plugin Action runs with no plugin UI (plugins design,
// "declarative first"). Each plugin Action's palette command opens it (plugins.js). Run dispatches the Action, so it
// is recorded in history like any other.
import { dispatch } from "./api.js";
import { $ } from "./dom.js";
import { schemaForm } from "./formview.js";
import { trapTab } from "./modal.js";

let opener = null, form = null, current = null;

export function openActionForm(entry, prefill = {}) {
  opener = document.activeElement;
  current = entry;
  $("actform-title").textContent = entry.label;
  $("actform-desc").textContent = entry.description || "";
  $("actform-result").textContent = "";
  form = schemaForm($("actform-body"), entry.schema, prefill, { omit: ["type"], onChange: () => { $("actform-result").textContent = ""; } });
  $("actform").hidden = false;
  if (!form.focus()) $("actform-run").focus();
}

export function closeActionForm() {
  $("actform").hidden = true;
  if (opener && opener.isConnected) opener.focus();
  opener = null;
}

async function run() {
  const errors = form.errors();
  if (errors.length) { $("actform-result").textContent = errors.join("; "); return; }
  $("actform-run").disabled = true;
  try {
    const result = await dispatch({ type: current.type, ...form.value() });
    $("actform-result").textContent = result == null ? "Done." : `Done: ${typeof result === "string" ? result : JSON.stringify(result)}`;
  } catch (e) {
    $("actform-result").textContent = e.message;
  } finally {
    $("actform-run").disabled = false;
  }
}

export function wireActionForm() {
  $("actform-close").onclick = closeActionForm;
  $("actform-run").onclick = () => run();
  $("actform").addEventListener("keydown", e => {
    trapTab($("actform"), e);
    if (e.key === "Escape") { e.stopPropagation(); closeActionForm(); }
  });
}
```

- [ ] **Step 5: Markup and CSS**

After `<!-- /cmdk -->`:

```html
<div id="actform" class="modal" role="dialog" aria-modal="true" aria-labelledby="actform-title" hidden>
  <div class="modal-card">
    <div class="modal-head">
      <h3 id="actform-title">Action</h3>
      <button class="mini ghost" id="actform-close" aria-label="Close">&#10005;</button>
    </div>
    <div class="modal-body">
      <p class="llm-note" id="actform-desc"></p>
      <div id="actform-body"></div>
      <p class="wz-note" id="actform-result" role="status"></p>
    </div>
    <div class="modal-foot"><span></span><button class="mini" id="actform-run">Run</button></div>
  </div>
</div>
<!-- /actform -->
```

Append to `app.css`:

```css
  /* ---- schema forms (wb.schemaForm, Action forms) ---- */
  .sf-field { display:grid; grid-template-columns:minmax(110px, 200px) 1fr; gap:4px 10px; align-items:center; padding:5px 0; }
  .sf-field input:not([type=checkbox]), .sf-field select, .sf-field textarea { background:#0c0e12; color:var(--ink);
    border:1px solid var(--edge); border-radius:6px; padding:5px 8px; min-width:0; font:inherit; }
  .sf-help { grid-column:2; color:var(--muted); font-size:12px; }
  .sf-group { margin:10px 0 2px; font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }
  .sf-req { color:var(--accent); }
```

- [ ] **Step 6: `main.js`**

Import `wireActionForm` from `./actform.js` and call `wireActionForm();` in `boot()` after `wirePalette(…)`.

- [ ] **Step 7: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`, then the fast tier and ruff.

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): wb.schemaForm and the Action form dialog"
```

---

### Task 13: `hub.js` and `wbhost.js`: the `wb` object

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/hub.js`, `wbhost.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`

- [ ] **Step 1: Write the failing test**

The harness imports several pure modules, so the test copies them beside it and points `node_module` at the harness.
Add `from netstead.workbench.server import STATIC_DIR` to the imports at the top of `test_workbench_slots_js.py`,
then append:

```python
JS_DIR = STATIC_DIR / "js"
HOST_MODULES = ("wbhost.js", "slots.js", "layers.js", "store.js", "hub.js")

WB_HARNESS = """
import { createWb, eventName, keysChanged, pluginPath } from "./wbhost.js";
import { createSlots } from "./slots.js";
import { createLayerRegistry } from "./layers.js";
import { activeSelection, createStore } from "./store.js";
import { createHub } from "./hub.js";

export async function run() {
  const slots = createSlots(), layers = createLayerRegistry(), hub = createHub(), calls = [];
  const server = s => ({ networks: [{ id: "n1", version: 1 }], active: "n1", selection: s, plugins: { hello: { n: 0 } } });
  const store = createStore({ server: server(null) });
  const deps = {
    hostApi: "1.1", store, activeSelection, events: hub, actionTypes: new Set(["hello.greet"]), actionSchema: () => null,
    getJSON: async p => (calls.push(["get", p]), {}), postJSON: async (p, b) => (calls.push(["post", p, b]), {}),
    fetch: async p => (calls.push(["fetch", p]), {}), dispatch: async a => (calls.push(["dispatch", a.type]), "ok"),
    addCommand: (o, s) => slots.addCommand(o, s), layers,
    dock: { addWorkspace: (o, s) => slots.addWorkspace(o, s),
            addPanel: (o, s) => ({ dispose: slots.addPanel(o, s), refreshBadge() {} }), showPanel() {} },
    schemaForm: () => null, toast: m => calls.push(["toast", m]),
    onError: (id, phase, e) => calls.push(["error", id, phase, e.message]),
  };
  const { wb, rollback } = createWb("hello", deps);
  const seen = [];
  wb.registerWorkspace({ id: "hello.ws", title: "Hello" });
  wb.registerPanel({ id: "hello.p", title: "P", workspace: "hello.ws", render() {} });
  wb.registerCommand({ id: "hello.c", title: "C", run() {} });
  wb.registerLayer("roadway", "hello.l", () => null);
  wb.store.subscribe(["plugins.hello"], s => seen.push(["store", s.plugins.hello.n]));
  wb.selection.subscribe(sel => seen.push(["selection", sel && sel.link_ids]));
  wb.on("greeted", p => seen.push(["event", p]));
  wb.on("other.thing", () => { throw new Error("listener bug"); });
  let refused = null;
  try { wb.registerCommand({ id: "c2", title: "x", run() {} }); } catch (e) { refused = e.message; }
  await wb.api.get("/count"); await wb.api.post("/echo", { a: 1 }); await wb.api.dispatch({ type: "hello.greet" });
  let badPath = null;
  try { wb.api.get("/../state"); } catch (e) { badPath = e.message; }
  store.set({ server: server(null) });                                                   // nothing watched changed
  store.set({ server: { ...server({ net_id: "n1", link_ids: [4] }), plugins: { hello: { n: 1 } } } });
  hub.emit("hello.greeted", { name: "Ada" }); hub.emit("other.thing", 1); hub.emit("greeted", "not mine");
  const before = [slots.workspaces.list().map(x => x.id), slots.panels.list().map(x => x.id),
                  slots.commands.list().map(x => x.id), layers.ids()];
  rollback();
  const after = [slots.workspaces.list().length, slots.panels.list().length, slots.commands.list().length, layers.ids().length];
  let late = null;
  try { wb.registerCommand({ id: "hello.late", title: "L", run() {} }); } catch (e) { late = e.message; }
  hub.emit("hello.greeted", "after rollback");
  return { before, after, refused, badPath, late, seen, calls, has: [wb.hasAction("hello.greet"), wb.hasAction("cards.x")],
    pure: [pluginPath("hello", "/count?x=1"), eventName("hello", "greeted"), eventName("hello", "core.history"),
           keysChanged({ a: { b: 1 } }, { a: { b: 1 }, c: 2 }, ["a.b"]), keysChanged({ a: { b: 1 } }, { a: { b: 2 } }, ["a.b"])] };
}
"""


def _harness(tmp_path, source: str):
    root = tmp_path / "harness"
    root.mkdir()
    for name in HOST_MODULES:
        shutil.copy(JS_DIR / name, root / name)
    (root / "harness.js").write_text(source, encoding="utf-8")
    return root / "harness.js"


def test_wb_registers_namespaced_tracks_and_rolls_back(node_module, tmp_path):
    got = node_module(_harness(tmp_path, WB_HARNESS), ["run"], "await run()")
    assert got["before"] == [["hello.ws"], ["hello.p"], ["hello.c"], ["hello.l"]]
    assert got["after"] == [0, 0, 0, 0]
    assert got["refused"] == 'hello: id "c2" must start with "hello."'
    assert got["badPath"] == 'hello: wb.api paths start with "/" and stay under /api/plugins/hello'
    assert got["late"] == "hello: activation was rolled back; nothing more can be registered"
    assert got["seen"] == [["store", 1], ["selection", [4]], ["event", {"name": "Ada"}]]
    assert got["calls"] == [
        ["get", "/api/plugins/hello/count"],
        ["post", "/api/plugins/hello/echo", {"a": 1}],
        ["dispatch", "hello.greet"],
        ["error", "hello", "event other.thing", "listener bug"],
    ]
    assert got["has"] == [True, False]
    assert got["pure"] == ["/api/plugins/hello/count?x=1", "hello.greeted", "core.history", False, True]
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py -q -k wb_registers`
Expected: FAIL (`wbhost.js` doesn't exist, so the copy fails).

- [ ] **Step 3: Create `hub.js`**

```js
// Named events for plugins: core's ("core.history", "core.job") and each plugin's own ("hello.greeted", sent by
// host.publish). main.js feeds it from the SSE stream; wb.on listens. Import-free and DOM-free.
export function createHub() {
  const listeners = new Map();
  return {
    on(name, fn) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add(fn);
      return () => listeners.get(name).delete(fn);
    },
    emit(name, payload) {
      for (const fn of [...(listeners.get(name) || [])]) {
        try { fn(payload); } catch (e) { console.error(`event ${name}:`, e); }
      }
    },
  };
}

// The page's one hub.
export const hub = createHub();
```

- [ ] **Step 4: Create `wbhost.js`**

```js
// The `wb` object a plugin's activate(wb) receives (plugins design, "The wb host object"). It is built from
// injected parts, so it is import-free and DOM-free and unit-tested under node; plugins.js passes the real ones.
// Everything a plugin registers through it is tracked, so a failed activation is rolled back whole.

// "/count" -> "/api/plugins/hello/count". A plugin's fetches stay under its own routes.
export function pluginPath(pluginId, path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.split(/[/?#]/).includes("..")) {
    throw new Error(`${pluginId}: wb.api paths start with "/" and stay under /api/plugins/${pluginId}`);
  }
  return `/api/plugins/${pluginId}${path}`;
}

// "greeted" -> "hello.greeted" (the plugin's own event); "catalog.added" and "core.history" stay as they are.
export const eventName = (pluginId, name) => (name.includes(".") ? name : `${pluginId}.${name}`);

const pick = (obj, key) => key.split(".").reduce((v, k) => (v == null ? undefined : v[k]), obj);

// Whether any of `keys` (top-level, or dotted such as "plugins.hello") differs between two server states.
export const keysChanged = (prev, next, keys) => keys.some(k => JSON.stringify(pick(prev, k)) !== JSON.stringify(pick(next, k)));

// deps: {hostApi, store, activeSelection, getJSON, postJSON, fetch, dispatch, events: {on}, addCommand,
//        layers: {register}, dock: {addWorkspace, addPanel, showPanel}, schemaForm, actionTypes, actionSchema,
//        toast, onError(pluginId, phase, error)}
export function createWb(pluginId, deps) {
  const disposers = [];
  let closed = false;
  const track = off => { disposers.push(off); return off; };
  const open = () => {
    if (closed) throw new Error(`${pluginId}: activation was rolled back; nothing more can be registered`);
  };
  // A plugin callback that throws (or rejects) is reported, never passed on to core.
  const safe = (fn, phase) => (...args) => {
    try {
      const out = fn(...args);
      if (out && typeof out.catch === "function") out.catch(e => deps.onError(pluginId, phase, e));
      return out;
    } catch (e) {
      deps.onError(pluginId, phase, e);
      return undefined;
    }
  };
  const serverNow = () => deps.store.get().server;
  const selectionOf = server => deps.activeSelection({ server }) || null;
  const watch = (changed, fn, phase) => {
    let prev = serverNow();
    const cb = safe(fn, phase);
    return track(deps.store.subscribe(["server"], s => {
      const before = prev;
      prev = s.server;
      if (changed(before, s.server)) cb(s.server);
    }));
  };

  const wb = {
    id: pluginId,
    hostApi: deps.hostApi,
    api: {
      fetch: (path, init) => deps.fetch(pluginPath(pluginId, path), init),
      get: path => deps.getJSON(pluginPath(pluginId, path)),
      post: (path, body) => deps.postJSON(pluginPath(pluginId, path), body),
      dispatch: action => deps.dispatch(action),
    },
    store: {
      get: serverNow,
      subscribe: (keys, fn) => watch((a, b) => keysChanged(a, b, keys), fn, "store"),
    },
    selection: {
      get: () => selectionOf(serverNow()),
      subscribe: fn => watch((a, b) => JSON.stringify(selectionOf(a)) !== JSON.stringify(selectionOf(b)),
        server => fn(selectionOf(server)), "selection"),
    },
    registerWorkspace(spec) { open(); return track(deps.dock.addWorkspace(pluginId, spec)); },
    registerPanel(spec) { open(); const panel = deps.dock.addPanel(pluginId, spec); track(panel.dispose); return panel; },
    registerCommand(spec) { open(); return track(deps.addCommand(pluginId, spec)); },
    registerLayer(component, id, factory, options) {
      open();
      return track(deps.layers.register(pluginId, component, id, factory, options));
    },
    schemaForm: (el, schema, value, options) => deps.schemaForm(el, schema, value, options),
    actionSchema: type => deps.actionSchema(type),
    hasAction: type => deps.actionTypes.has(type),
    on(name, fn) { return track(deps.events.on(eventName(pluginId, name), safe(fn, `event ${name}`))); },
    showPanel: id => deps.dock.showPanel(id),
    toast: message => deps.toast(`${pluginId}: ${message}`),
  };
  const rollback = () => {
    closed = true;
    for (const off of disposers.splice(0).reverse()) {
      try { off(); } catch (e) { /* already removed */ }
    }
  };
  return { wb, rollback };
}
```

- [ ] **Step 5: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass. Then the fast tier and ruff.

```bash
git add packages/netstead/netstead/workbench/static/js/hub.js packages/netstead/netstead/workbench/static/js/wbhost.js packages/netstead/tests/test_workbench_slots_js.py
git commit -m "feat(workbench): the wb host object, with tracked registrations and rollback"
```

---

### Task 14: The plugin loader, and errors routed to the Plugins section

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/plugins.js`
- Modify: `js/store.js`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static tests**

```python
def test_main_loads_plugins_after_core_and_forwards_their_events():
    main = (JS_DIR / "main.js").read_text()
    assert 'from "./plugins.js"' in main and "loadPlugins()" in main
    assert main.index("registerCoreSlots()") < main.index("loadPlugins()")
    assert "await loadPlugins()" not in main  # never delays the map
    assert re.search(r"plugin:\s*e\s*=>\s*hub\.emit\(", main)
    assert 'hub.emit("core.history"' in main and 'hub.emit("core.job"' in main
    assert "onLayerError:" in main


def test_the_loader_isolates_each_plugin():
    src = (JS_DIR / "plugins.js").read_text()
    assert "rollback()" in src and 'reportPluginError(status.id, "activate"' in src
    assert "ACTIVATE_TIMEOUT_MS" in src and "typeof mod.activate" in src
    assert "actionCommands(" in src and "openActionForm(" in src
    assert "status.id === CORE" in src  # "core" is reserved (review I-2): never activated as a plugin
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k "loads_plugins or isolates"`
Expected: FAIL.

- [ ] **Step 3: Store keys**

In `store.js`, after `commandSeq`:

```js
  pluginStatus: [],           // GET /api/plugins: each plugin's startup outcome
  hostApi: null,              // the plugin API this netstead provides ("1.1")
  pluginErrors: {},           // plugin id -> [{phase, message}] from the browser (activate, panel, command, layer…)
```

- [ ] **Step 4: Create `plugins.js`**

```js
// The browser side of plugins: import each loaded plugin's ES module and call its activate(wb) (plugins design,
// "Front end"). One plugin's failure (a module that won't import, no activate export, activate throwing or not
// finishing) rolls back what it registered, is listed in Settings → Plugins, and never stops the others.
import { openActionForm } from "./actform.js";
import { dispatch, getJSON, postJSON } from "./api.js";
import { actionCommands } from "./commands.js";
import { toast } from "./dom.js";
import { schemaForm } from "./formview.js";
import { hub } from "./hub.js";
import { addLayer } from "./map.js";
import { CORE, slots } from "./slots.js";
import { activeSelection, store } from "./store.js";
import { createWb } from "./wbhost.js";
import { addPanel, addWorkspace, restoreWorkspace, showPanel } from "./workspaces.js";

export const ACTIVATE_TIMEOUT_MS = 5000;
const reported = new Set();

// A failure in plugin (or core) front-end code: logged, toasted, and kept for Settings → Plugins. Each distinct
// message is reported once (a broken badge would otherwise repeat on every state change).
export function reportPluginError(owner, phase, error) {
  const message = String((error && error.message) || error);
  const key = `${owner}|${phase}|${message}`;
  if (reported.has(key)) return;
  reported.add(key);
  console.error(`[${owner}] ${phase}:`, error);
  toast(owner === "core" ? message : `Plugin ${owner}: ${message}`);
  if (owner === "core") return;
  const all = store.get().pluginErrors;
  store.set({ pluginErrors: { ...all, [owner]: [...(all[owner] || []), { phase, message }] } });
}

// A command registered by anyone; menus and the "Selection actions" button re-check what applies.
export function addCommand(owner, spec) {
  const off = slots.addCommand(owner, spec);
  const changed = () => store.set({ commandSeq: store.get().commandSeq + 1 });
  changed();
  return () => { off(); changed(); };
}

export async function loadPlugins() {
  const [{ host_api: hostApi, plugins }, { actions }] = await Promise.all([getJSON("/api/plugins"), getJSON("/api/actions")]);
  store.set({ pluginStatus: plugins, hostApi });
  // Declarative first: every plugin Action has a form, whether or not its plugin ships JavaScript.
  for (const spec of actionCommands(actions, plugins)) addCommand(spec.owner, { ...spec, run: () => openActionForm(spec.entry) });
  const shared = { hostApi, actionTypes: new Set(actions.map(a => a.type)), schemas: new Map(actions.map(a => [a.type, a.schema])) };
  for (const status of plugins) {
    // "core" is reserved (spec.problems refuses it too): its owner tag would skip the id prefix check, and a
    // rollback's removeOwner("core") would remove core's own registrations.
    if (status.id === CORE) { reportPluginError(status.id, "activate", 'the plugin id "core" is reserved'); continue; }
    if (status.state === "loaded" && status.frontend) await activatePlugin(status, shared);
  }
  restoreWorkspace();
}

async function activatePlugin(status, shared) {
  const { wb, rollback } = createWb(status.id, hostDeps(shared));
  try {
    const mod = await import(`${status.frontend}?v=${encodeURIComponent(status.version)}`);
    if (typeof mod.activate !== "function") throw new Error(`${status.frontend} exports no activate(wb)`);
    await withTimeout(Promise.resolve(mod.activate(wb)), ACTIVATE_TIMEOUT_MS,
      `activate(wb) did not finish within ${ACTIVATE_TIMEOUT_MS / 1000} s`);
  } catch (e) {
    rollback();
    reportPluginError(status.id, "activate", e);
  }
}

function withTimeout(promise, ms, message) {
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

function hostDeps({ hostApi, actionTypes, schemas }) {
  return {
    hostApi, store, activeSelection, getJSON, postJSON, dispatch, fetch: (url, init) => fetch(url, init),
    events: hub, addCommand, layers: { register: addLayer }, dock: { addWorkspace, addPanel, showPanel },
    schemaForm, actionTypes, actionSchema: type => schemas.get(type) || null, toast, onError: reportPluginError,
  };
}
```

- [ ] **Step 5: `main.js`**

- import `{ hub }` from `./hub.js` and `{ loadPlugins, reportPluginError }` from `./plugins.js`;
- route every plugin-code error through `reportPluginError`. In `boot()` the calls become:

```js
  const commandError = (command, e) => reportPluginError(command.owner, "command", e);
  registerCoreSlots();
  wireWorkspaces({ onError: reportPluginError });
  wireContextMenus({ onError: commandError });
  registerCoreCommands();
  wirePalette({ onError: commandError });
  wireActionForm();
```

- in the `subscribe({ … })` handlers, add the plugin events and forward core's:

```js
    history: e => { showEntry(e.entry); rememberRecent(e.entry); onHistoryEntry(e.entry); onSettingsHistory(e.entry);
      onSettingChanged(e.entry).catch(err => toast(err.message)); hub.emit("core.history", e.entry);
    },
    job: e => { onJob(e.job); onWizardJob(e.job); onLLMJob(e.job); hub.emit("core.job", e.job); },
    plugin: e => hub.emit(`${e.plugin}.${e.name}`, e.payload),
```

- add `onLayerError: e => reportPluginError(e.owner, "layer", e.error),` to the `initMap` hooks;
- right after `restoreViewMode();` in `boot()`:

```js
  loadPlugins().catch(e => toast(`Plugins: ${e.message}`)); // not awaited: a slow plugin never delays the map
```

- [ ] **Step 6: Run the tests**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_plugin_routes.py -q`
Expected: all pass (including `test_pull_button_clears_only_its_own_model_on_job_events`: `onLLMJob(e.job)` still
comes before any `}` in the `job:` handler).

- [ ] **Step 7: Check by hand with the current hello example**

`uv pip install -e examples/workbench-plugin-hello`, `uv run netstead app`. Expected: no error toast; the palette
shows "Say hello" (the old `main.js` registers it with `wb.registerCommand?.`) and "Hello: Greet…" (generated).
Running "Hello: Greet…" with name "Ada" shows "Done: Hello, Ada!" and the history strip shows
`app.do(Greet(name='Ada'))`.

- [ ] **Step 8: Before commit, then commit**

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): load plugin front ends with activate(wb); contain and report failures"
```

---

### Task 15: Settings → Plugins

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/pluginlist.js`, `pluginspanel.js`
- Modify: `index.html`, `app.css`, `js/main.js`
- Test: `packages/netstead/tests/test_workbench_slots_js.py`, `test_workbench_static.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_workbench_slots_js.py`:

```python
STATUSES = [
    {"id": "hello", "name": "Hello", "version": "0.1.0", "requires_api": "1.1", "state": "loaded", "error": None,
     "frontend": "/plugins/hello/main.js"},
    {"id": "cards", "name": "cards", "version": "", "requires_api": None, "state": "disabled", "error": None, "frontend": None},
    {"id": "old", "name": "Old", "version": "2.0.0", "requires_api": "2.0", "state": "incompatible",
     "error": "needs plugin API 2.0; this netstead provides 1.1", "frontend": None},
    {"id": "boom", "name": "boom", "version": "", "requires_api": None, "state": "error", "error": "RuntimeError: nope",
     "frontend": None},
]


def test_plugin_rows_show_fit_errors_and_pending_restarts(node_module):
    args = {"statuses": STATUSES, "hostApi": "1.1", "disabled": ["cards", "hello", "gone"],
            "browserErrors": {"hello": [{"phase": "activate", "message": "x"}]}}
    rows = node_module("pluginlist.js", ["pluginRows"], f"pluginRows({json.dumps(args)})")
    assert [(r["id"], r["version"], r["compat"], r["stateText"], r["enabled"], r["restart"], r["errors"]) for r in rows] == [
        ("hello", "0.1.0", "1.1 (compatible)", "Loaded", False, True, ["activate: x"]),  # switched off: restart to apply
        ("cards", "—", "—", "Disabled", False, False, []),
        ("old", "2.0.0", "2.0 (this netstead provides 1.1)", "Incompatible", True, False,
         ["needs plugin API 2.0; this netstead provides 1.1"]),
        ("boom", "—", "—", "Failed to load", True, False, ["RuntimeError: nope"]),
        ("gone", "—", "—", "Not installed", False, False, []),  # listed so it can be cleared
    ]


def test_switching_a_plugin_edits_the_disabled_list(node_module):
    got = node_module(
        "pluginlist.js",
        ["withPluginEnabled"],
        '[withPluginEnabled(["a", "b"], "a", true), withPluginEnabled(["a"], "b", false), withPluginEnabled(["a"], "a", false)]',
    )
    assert got == [["b"], ["a", "b"], ["a"]]
```

Append to `test_workbench_static.py`:

```python
def test_settings_has_a_plugins_section_registered_like_language_models():
    html = (STATIC_DIR / "index.html").read_text()
    dialog = html[html.index('id="settings"') : html.index("<!-- /settings -->")]
    for element_id in ("plugins-panel", "plugins-host", "plugins-note", "plugins-list"):
        assert f'id="{element_id}"' in dialog
    main = (JS_DIR / "main.js").read_text()
    assert 'registerSection("plugins", "Plugins", "plugins-panel"' in main and "wirePluginsPanel()" in main
    panel = (JS_DIR / "pluginspanel.js").read_text()
    assert '"app.disabled_plugins"' in panel and 'scope: "user"' in panel
```

- [ ] **Step 2: Run them and check they fail**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q -k "plugin_rows or switching or plugins_section"`
Expected: FAIL.

- [ ] **Step 3: Create `pluginlist.js`**

```js
// Settings → Plugins, as rows: what started (GET /api/plugins), what the browser could not load, and what
// app.disabled_plugins says now. Import-free and DOM-free: unit-tested under node (tests/test_workbench_slots_js.py).

const STATE_TEXT = { loaded: "Loaded", disabled: "Disabled", incompatible: "Incompatible", error: "Failed to load" };

function compatText(requiresApi, compatible, hostApi) {
  if (compatible === null) return "—";
  return compatible ? `${requiresApi} (compatible)` : `${requiresApi} (this netstead provides ${hostApi})`;
}

// One row per installed plugin, plus one per id in app.disabled_plugins that isn't installed (so it can be cleared).
// `enabled` is the saved setting; `restart` is true when it differs from how this run started.
export function pluginRows({ statuses, hostApi, disabled, browserErrors = {} }) {
  const rows = statuses.map(s => {
    const compatible = s.requires_api == null ? null : s.state !== "incompatible";
    const enabled = !disabled.includes(s.id);
    return {
      id: s.id, name: s.name || s.id, version: s.version || "—", compat: compatText(s.requires_api, compatible, hostApi),
      state: s.state, stateText: STATE_TEXT[s.state] || s.state,
      errors: [s.error, ...(browserErrors[s.id] || []).map(e => `${e.phase}: ${e.message}`)].filter(Boolean),
      enabled, restart: enabled !== (s.state !== "disabled"), installed: true,
    };
  });
  for (const id of disabled) {
    if (statuses.some(s => s.id === id)) continue;
    rows.push({ id, name: id, version: "—", compat: "—", state: "missing", stateText: "Not installed", errors: [],
      enabled: false, restart: false, installed: false });
  }
  return rows;
}

// app.disabled_plugins after switching `id` on or off (order kept, no duplicates).
export function withPluginEnabled(disabled, id, enabled) {
  const rest = disabled.filter(x => x !== id);
  return enabled ? rest : [...rest, id];
}
```

- [ ] **Step 4: Create `pluginspanel.js`**

```js
// Settings → Plugins (UX principle 8): each plugin's id, version, required API and whether it fits, its load errors,
// and an on/off switch. The switch saves app.disabled_plugins at user scope. It is read at launch, so a change
// applies the next time `netstead app` starts; the row says "restart to apply" until then.
import { dispatch, getJSON } from "./api.js";
import { $, esc, toast } from "./dom.js";
import { pluginRows, withPluginEnabled } from "./pluginlist.js";
import { scopeNote } from "./settingsform.js";
import { store } from "./store.js";

const GUIDE = "https://e-lo.github.io/netstead/netstead/cookbook/workbench-plugins/"; // mkdocs site_url + page

function rowHTML(r) {
  return `<tr><td><b>${esc(r.name)}</b><div class="muted">${esc(r.id)}</div></td><td>${esc(r.version)}</td>` +
    `<td>${esc(r.compat)}</td><td>${esc(r.stateText)}${r.restart ? ' <span class="tag">restart to apply</span>' : ""}` +
    r.errors.map(e => `<div class="err">${esc(e)}</div>`).join("") + "</td>" +
    `<td><label class="sw"><input type="checkbox" data-plugin="${esc(r.id)}"${r.enabled ? " checked" : ""} ` +
    `aria-label="Load ${esc(r.name)} at startup"><span></span></label></td></tr>`;
}

export async function renderPluginsPanel() {
  const settings = await getJSON("/api/settings");
  const disabled = settings.values.app.disabled_plugins || [];
  const s = store.get();
  const rows = pluginRows({ statuses: s.pluginStatus, hostApi: s.hostApi, disabled, browserErrors: s.pluginErrors });
  $("plugins-host").textContent = `This netstead provides plugin API ${s.hostApi || "?"}. Switching a plugin on or off ` +
    "applies the next time netstead app starts.";
  $("plugins-note").textContent = scopeNote({ restart: true, source: settings.sources["app.disabled_plugins"] || "default" }, "user") || "";
  $("plugins-list").innerHTML = rows.length
    ? "<table><thead><tr><th>Plugin</th><th>Version</th><th>Plugin API</th><th>Status</th><th>Load</th></tr></thead>" +
      `<tbody>${rows.map(rowHTML).join("")}</tbody></table>`
    : `<p class="empty">No plugins installed. See <a href="${GUIDE}" target="_blank" rel="noopener">Write a Workbench plugin</a>.</p>`;
}

export function wirePluginsPanel() {
  $("plugins-list").onchange = async e => {
    const el = e.target.closest("input[data-plugin]");
    if (!el) return;
    el.disabled = true;
    try {
      const settings = await getJSON("/api/settings");
      const value = withPluginEnabled(settings.values.app.disabled_plugins || [], el.dataset.plugin, el.checked);
      await dispatch({ type: "set_setting", key: "app.disabled_plugins", value, scope: "user" });
    } catch (err) {
      toast(err.message);
    }
    await renderPluginsPanel().catch(err => toast(err.message));
  };
}
```

(`GUIDE` is `site_url` from `packages/netstead/mkdocs.yml` plus the page Task 18 extends, the same way `llm.js`
builds `OLLAMA_GUIDE`.)

- [ ] **Step 5: Markup and CSS**

In `index.html`, inside the Settings dialog's `.modal-body`, directly after the closing `</section>` of
`#llm-panel`:

```html
        <section id="plugins-panel" aria-label="Plugins" hidden>
          <p class="llm-note" id="plugins-host"></p>
          <p class="set-note" id="plugins-note"></p>
          <div id="plugins-list"></div>
        </section>
```

Append to `app.css`:

```css
  #plugins-list table { width:100%; border-collapse:collapse; }
  #plugins-list th { text-align:left; color:var(--muted); font-weight:600; font-size:11px; padding:4px 6px; }
  #plugins-list td { padding:6px; border-bottom:1px solid var(--edge); vertical-align:top; font-size:12.5px; }
  #plugins-list .err { font-size:12px; margin-top:2px; }
```

- [ ] **Step 6: `main.js`**

Import `{ renderPluginsPanel, wirePluginsPanel }` from `./pluginspanel.js`. In `boot()`, after the existing
`registerSection("llm", …)` line:

```js
  registerSection("plugins", "Plugins", "plugins-panel", () => renderPluginsPanel());
  wirePluginsPanel();
```

- [ ] **Step 7: Run the tests, check by hand, commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_slots_js.py packages/netstead/tests/test_workbench_static.py -q`
Expected: all pass. By hand: Settings shows "Plugins" after "Language models"; the Save-to menu is hidden there, as
on Language models. With no plugin installed: "No plugins installed."

```bash
git add packages/netstead/netstead/workbench/static packages/netstead/tests
git commit -m "feat(workbench): Settings → Plugins with status, errors and an enable switch"
```

---

### Task 16: The derived mark in the network switcher

Dirty badges on panels and workspace tabs landed in Task 9 (`badge`, `workspaceBadge`). This task adds the third
place UX principle 7 names: the network switcher marks a derived (non-base) network, such as a plugin's preview or a
materialized scenario.

**Files:**
- Modify: `packages/netstead/netstead/workbench/static/js/header.js`
- Test: `packages/netstead/tests/test_workbench_static.py`

- [ ] **Step 1: Write the failing static test**

```python
def test_the_network_switcher_marks_derived_networks():
    header = (JS_DIR / "header.js").read_text()
    assert 'from "./tabs.js"' in header and "networkBadge(" in header
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q -k derived`
Expected: FAIL.

- [ ] **Step 3: `header.js`**

Import `{ networkBadge }` from `./tabs.js`, and in `renderHeader` replace the `server.networks.map(…)` option
template with:

```js
    ? server.networks.map(n => {
      const mark = networkBadge(n);
      return `<option value="${esc(n.id)}"${n.id === server.active ? " selected" : ""}` +
        `${mark ? ` title="derived from ${esc(n.derived_from)}"` : ""}>${esc(n.label)}${mark ? ` • ${esc(mark)}` : ""}</option>`;
    }).join("")
```

A base network's option is byte-identical to before.

- [ ] **Step 4: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`, then the fast tier and ruff.

```bash
git add packages/netstead/netstead/workbench/static/js/header.js packages/netstead/tests/test_workbench_static.py
git commit -m "feat(workbench): mark derived networks in the network switcher"
```

---
### Task 17: The hello example's front end, end to end

**Files:**
- Modify: `examples/workbench-plugin-hello/netstead_hello/static/main.js`, `examples/workbench-plugin-hello/README.md`
- Test: `packages/netstead/tests/test_workbench_hello_frontend.py` (new)

- [ ] **Step 1: Write the failing test**

The example's `main.js` runs under node against the real `createWb`, with fake parts for the DOM and the server.

```python
"""The hello example's front end, run under node against the real wb factory (Workbench plugins, Part 2)."""

import shutil
from pathlib import Path

import pytest
from netstead.workbench.server import STATIC_DIR

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

HELLO_JS = Path(__file__).resolve().parents[3] / "examples" / "workbench-plugin-hello" / "netstead_hello" / "static" / "main.js"
MODULES = ("wbhost.js", "slots.js", "layers.js", "store.js", "hub.js")

HARNESS = """
import { activate } from "./hello.js";
import { createWb } from "./wbhost.js";
import { createSlots } from "./slots.js";
import { createLayerRegistry } from "./layers.js";
import { activeSelection, createStore } from "./store.js";
import { createHub } from "./hub.js";

export async function run() {
  const slots = createSlots(), hub = createHub(), calls = [], panels = [];
  const store = createStore({ server: { networks: [], active: null, selection: null, plugins: {} } });
  const deps = {
    hostApi: "1.1", store, activeSelection, events: hub, actionTypes: new Set(["hello.greet"]),
    actionSchema: t => ({ properties: { type: { const: t }, name: { type: "string" } }, required: ["name"] }),
    getJSON: async p => { calls.push(["get", p]); return { greeted: 2 }; },
    postJSON: async () => ({}), fetch: async () => ({}),
    dispatch: async a => { calls.push(["dispatch", a.type, a.name]); return `Hello, ${a.name}!`; },
    addCommand: (o, s) => slots.addCommand(o, s), layers: createLayerRegistry(),
    dock: { addWorkspace: (o, s) => slots.addWorkspace(o, s),
            addPanel: (o, s) => { panels.push(s); return { dispose: slots.addPanel(o, s), refreshBadge() {} }; },
            showPanel() {} },
    schemaForm: () => ({ value: () => ({ name: "Ada" }), errors: () => [] }),
    toast: m => calls.push(["toast", m]), onError: (id, phase, e) => calls.push(["error", id, phase, String(e.message || e)]),
  };
  const { wb } = createWb("hello", deps);
  activate(wb);
  await new Promise(r => setTimeout(r, 0));
  const ctx = { target: { table: "link", id: 7 }, selection: { link_ids: [1, 2] }, selectionCount: 2 };
  const whens = slots.commands.list().map(c => (c.when ? c.when(ctx) : true));
  for (const c of slots.commands.list()) await c.run(ctx);
  hub.emit("hello.greeted", { name: "Ada" });
  await new Promise(r => setTimeout(r, 0));
  return { panels: slots.panels.list().map(p => [p.id, p.workspace]), commands: slots.commands.list().map(c => [c.id, c.contexts]),
           whens, badge: panels[0].badge(), calls };
}
"""


def test_hello_registers_a_panel_three_commands_and_a_live_badge(node_module, tmp_path):
    root = tmp_path / "hello"
    root.mkdir()
    for name in MODULES:
        shutil.copy(STATIC_DIR / "js" / name, root / name)
    shutil.copy(HELLO_JS, root / "hello.js")
    (root / "harness.js").write_text(HARNESS, encoding="utf-8")
    got = node_module(root / "harness.js", ["run"], "await run()")
    assert got["panels"] == [["hello.panel", "inspect"]]
    assert got["commands"] == [
        ["hello.greet", ["palette"]],
        ["hello.greet_record", ["feature", "row"]],
        ["hello.greet_selection", ["selection"]],
    ]
    assert got["whens"] == [True, True, True]
    assert got["badge"] == 2  # read from GET /api/plugins/hello/count after the "greeted" event
    assert got["calls"] == [
        ["get", "/api/plugins/hello/count"],
        ["dispatch", "hello.greet", "world"],
        ["dispatch", "hello.greet", "link 7"],
        ["dispatch", "hello.greet", "2 selected links"],
        ["get", "/api/plugins/hello/count"],
    ]
```

- [ ] **Step 2: Run it and check it fails**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_hello_frontend.py -q`
Expected: FAIL (the current `main.js` registers one command and no panel).

- [ ] **Step 3: Rewrite `examples/workbench-plugin-hello/netstead_hello/static/main.js`**

```js
// The hello plugin's front end. The Workbench imports this module once at startup and calls activate(wb) (see the
// cookbook page "Write a Workbench plugin"). Everything goes through `wb`: no imports, no reaching into core's DOM.
export function activate(wb) {
  let greeted = 0;

  // A dock panel in the Inspect workspace: a form generated from the Action's JSON Schema, and a count badge.
  const panel = wb.registerPanel({
    workspace: "inspect",
    id: "hello.panel",
    title: "Hello",
    order: 50,
    badge: () => greeted || null, // a count on the tab; return {dirty: true, title: "…"} for unsaved work
    render(el) {
      el.innerHTML =
        '<p class="muted">Greet someone. Each greeting is a recorded Action: see the history strip.</p>' +
        '<div></div><button class="mini">Greet</button> <span class="muted" role="status"></span>';
      const [, slot, button, status] = el.children;
      const form = wb.schemaForm(slot, wb.actionSchema("hello.greet"), { name: "world" });
      button.onclick = async () => {
        const errors = form.errors();
        if (errors.length) {
          status.textContent = errors.join("; ");
          return;
        }
        try {
          status.textContent = await wb.api.dispatch({ type: "hello.greet", ...form.value() });
        } catch (e) {
          status.textContent = e.message;
        }
      };
    },
  });

  // The count comes from the plugin's own route; host.publish("greeted") says when to re-read it.
  const refresh = async () => {
    greeted = (await wb.api.get("/count")).greeted;
    panel.refreshBadge();
  };
  wb.on("greeted", () => refresh());
  refresh().catch(e => wb.toast(e.message));

  // Commands: one in the palette, one on a right-clicked map feature or table row, one for the selection.
  wb.registerCommand({
    id: "hello.greet",
    title: "Say hello",
    contexts: ["palette"],
    run: () => wb.api.dispatch({ type: "hello.greet", name: "world" }),
  });
  wb.registerCommand({
    id: "hello.greet_record",
    title: "Say hello to this record",
    contexts: ["feature", "row"],
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.target.table} ${ctx.target.id}` }),
  });
  wb.registerCommand({
    id: "hello.greet_selection",
    title: "Say hello to the selection",
    contexts: ["selection"],
    when: ctx => ctx.selectionCount > 0,
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.selectionCount} selected links` }),
  });
}
```

- [ ] **Step 4: README**

In `examples/workbench-plugin-hello/README.md`, replace the front-end bullet with:

```markdown
- a front-end module at `/plugins/hello/main.js`, which the Workbench loads at startup: a **Hello** panel in the
  Inspect dock (a form generated from the Action's schema, with a greeting count on its tab), "Say hello" in the
  command palette (Ctrl+K), "Say hello to this record" on a right-clicked link, node or table row, and "Say hello to
  the selection" under **Selection actions**
```

and add after the install block: "Settings → Plugins lists it, with an on/off switch that applies on restart."

- [ ] **Step 5: Run the tests, before commit, then commit**

Run: `uv run --all-extras pytest packages/netstead/tests/test_workbench_hello_frontend.py packages/netstead/tests/test_workbench_plugin_routes.py -q`
Expected: all pass. Then the fast tier and ruff.

```bash
git add examples/workbench-plugin-hello packages/netstead/tests/test_workbench_hello_frontend.py
git commit -m "feat(examples): hello plugin front end: a dock panel, three commands and a badge"
```

---

### Task 18: Docs

**Files:**
- Modify: `packages/netstead/docs/cookbook/workbench-plugins.md`, `packages/netstead/docs/cookbook/workbench.md`,
  `docs/design/2026-10-05-workbench-plugins-design.md`, `docs/design/README.md`

- [ ] **Step 1: The authoring guide (`workbench-plugins.md`)**

- In "The UX contract", drop the two "(arrives with the plugin front end)" notes: principle 5 now reads "Plugin
  Actions render from their JSON Schema through one core form component (`wb.schemaForm`); every plugin Action also
  gets a generated form in the command palette."
- In "Routes and front end", replace "The browser loader … Until then the file is only served." with: "At startup the
  Workbench imports `frontend` and calls its `activate(wb)`. If the module fails to import, has no `activate`, or
  `activate` throws or takes more than 5 seconds, everything it registered is removed and Settings → Plugins shows
  the error. The rest of the Workbench carries on."
- Add a section **"The front end: `activate(wb)`"** after "The `Host` reference", with:
  - the hello `main.js` from Task 17, annotated;
  - a `wb` reference table: every member from this plan's
    [Additions to, and deviations from, the design's `wb` table](#additions-to-and-deviations-from-the-designs-wb-table)
    (right-hand column), as the stable list;
  - **ids:** every id a plugin registers (workspace, panel, command, layer, and each deck.gl layer's `id`) starts with
    `"<plugin id>."`;
  - **the command context** (`ctx`): `network`, `selection`, `selectionCount`, `focus`, `highlights`, `workspace`,
    `target`, `state`; and where each context shows (palette; right-click on a link or node; right-click on a grid
    row; **Selection actions** in Details; the palette's "For ‹record›" and "Selection" groups);
  - **badges:** a count, a string, or `{text, dirty, title}`; `dirty: true` also marks the workspace tab;
  - **layers:** `registerLayer("roadway", "<id>.<name>", ctx => new ctx.deck.ScatterplotLayer({id: "<id>.…", …}),
    {title, order})`; core's order (`base` 100 … `marker` 600) and the default 350; a `title` lists it under Layers →
    Overlays; `"transit"` is accepted and not drawn yet;
  - **styling:** use core's classes (`mini`, `ghost`, `muted`, `pcount`, `row`, `sw`) and scope your own CSS under
    `[data-panel="<your panel id>"]`; inject a `<link>` from `activate` if you ship a stylesheet;
  - **failure containment:** what is caught where (Decision 4) and where errors show.
- In "Versioning", note that `HOST_API` 1.1 added `wb`.

- [ ] **Step 2: The Workbench page (`workbench.md`)**

Add a short "Workspaces, panels and commands" section: workspace tabs appear when a plugin adds a workspace; the
right drawer is a dock with tabs when it holds more than one panel; **Ctrl+K / ⌘K** (or **Commands…**) opens the
command palette, which also lists commands for the focused record and the selection; right-click a link, node or
grid row for its commands; **Settings → Plugins** lists installed plugins and turns them on or off (applies on
restart). Link the authoring guide.

- [ ] **Step 3: Design records**

- `2026-10-05-workbench-plugins-design.md`: add the Part 2 plan to the status line, next to Part 1's.
- `docs/design/README.md`: set this plan's status in its row (in flight, then "implemented (#PR)" when it merges,
  moving it to the "Implementation plans" table).

- [ ] **Step 4: Build the docs and commit**

Run: `uv run mkdocs build -f packages/netstead/mkdocs.yml --strict`
Expected: builds with no warnings.

```bash
git add packages/netstead/docs docs/design
git commit -m "docs(workbench): plugin front end (activate(wb), slots, palette, Plugins settings)"
```

---

### Task 19: Full suite, lint, and the browser walk-through

**Files:** none (fixes go in the task they belong to).

- [ ] **Step 1: Lint and import contracts**

Run: `uv run ruff check packages && uv run ruff format --check packages && uv run lint-imports`
Expected: clean; all contracts kept.

- [ ] **Step 2: The full suite, once**

Run: `uv run --all-extras pytest packages -n auto -q -m ""`
Expected: all pass. The count is Task 0's plus the new tests only; no existing test was edited.

- [ ] **Step 3: Walk-through A: no plugin installed (the regression check)**

`uv pip uninstall netstead-hello` if needed; `uv run netstead app`; open Leavenworth. Use the run skill or the in-app
browser.

1. Retake Task 0's seven screenshots. Expected: identical to the baseline, apart from the new **Commands…** header
   button (hidden at 640 px).
2. No workspace strip; no dock strip; right-click on a link: no menu (right-drag still rotates); right-click on a grid
   row: the browser's own menu; no **Selection actions** button.
3. P1b behaviour: a link click focuses it (details, row scroll); a row click flies the map; box-select fills
   Highlighted; an FK cell jumps; related tint and rail badges; the scope menu; "Expand a hop".
4. Settings: every section as before, then **Language models**, then **Plugins** ("No plugins installed."). Tab
   cycles inside the dialog; Escape closes it and focus returns to **Settings…**.
5. Ctrl+K (⌘K): the palette lists core commands; "zo" ranks the zoom commands; ↑/↓, Enter and Escape work; focus
   returns to where it was. With a link focused, nothing extra appears (core has no record commands).

- [ ] **Step 4: Walk-through B: the hello example**

`uv pip install -e examples/workbench-plugin-hello`; restart `netstead app`.

1. The drawer shows a tab strip: **Details | Hello**. Arrow keys move between the tabs, Home/End jump, and the screen
   reader name of the Hello tab includes its count once a greeting has been made.
2. **Hello** panel: the generated form (Name, prefilled "world"); **Greet** shows "Hello, world!"; the tab's count goes
   up; the history strip shows `app.do(Greet(name='world'))`.
3. Palette: "Say hello", and "Hello: Greet…", which opens the Action dialog (Name required: clearing it and pressing
   Run says "Name is required"). Run shows "Done: Hello, Ada!".
4. Right-click a link: a menu "link ‹id›" with "Say hello to this record"; arrows, Enter and Escape work; running it
   adds a history entry. Same on a grid row ("lane ‹id›" on the lane table).
5. Highlight three links and **Set as selection**: **Selection actions ▾** appears in Details with "Say hello to the
   selection"; a right-click on a link now also lists a "Selection (3 links)" section.
6. Focus a link, then Ctrl+K: groups "Commands", "For link ‹id›" and "Selection (3 links)".
7. Settings → Plugins: hello, `0.1.0`, `1.1 (compatible)`, Loaded. Switch it off: "restart to apply"; Settings → App
   server shows `disabled_plugins = hello`. Restart: no Hello tab, no hello commands, `/api/plugins` says
   `disabled`, and the Plugins row shows Disabled. Switch it back on and restart.

- [ ] **Step 5: Walk-through C: a broken plugin**

Temporarily add `throw new Error("broken on purpose");` as the last line of `activate` in the installed example's
`main.js` (it is an editable install), and hard-reload the page (Shift+Reload; no server restart is needed, since
the browser re-imports the module on each page load). Expected: one toast "Plugin hello: broken on purpose"; no Hello tab and no hello commands (rolled back),
though "Hello: Greet…" stays (generated from the Action, not from the JS); Settings → Plugins shows
"activate: broken on purpose" under hello; the rest of the Workbench works. Revert the line.

- [ ] **Step 6: Narrow width**

At 640 px: the header wraps as in P1b, **Commands…** is hidden, the dock strip wraps, the palette and the Action
dialog fit the screen.

- [ ] **Step 7: Hand off**

Use superpowers:finishing-a-development-branch. Open the PR against `main` (or against `feat/workbench-plugins` if Part
1 hasn't merged). Say in the PR that P2's Task 12 should be rebased onto it (next section).

---

## What P2 changes

P2 ([`2026-10-07-workbench-p2-plan.md`](2026-10-07-workbench-p2-plan.md) on `feat/workbench-p2`) adds the drawer tabs
**Details | Issues | Edits**. With Part 2 in place they are three panels in the Inspect dock, registered the way a
plugin registers one. Edit P2's plan as follows when it is rebased:

| P2 step | With Part 2 |
|---|---|
| Task 12, Step 1 (`<nav id="side-tabs">`, the three `.side-pane` sections, the `#side-tabs` CSS) | Drop the nav and its CSS. Keep two static panes so the static id test still sees their contents: `<section class="dock-pane" id="dock-issues" data-panel="issues" role="tabpanel" aria-labelledby="dtab-issues" hidden></section>` and the same for `edits`, after `#dock-details`. |
| Task 12, Step 2 (`drawerTab` store key) | Drop it: the dock's `dockPanel` holds the choice. |
| Task 12, Step 3 (`showTab`, `renderTab`) | Drop both. Register the panels instead: `addPanel(CORE, { id: "issues", title: "Issues", workspace: "inspect", order: 10, badge: issuesBadge })` and `addPanel(CORE, { id: "edits", title: "Edits", workspace: "inspect", order: 20, badge: editsBadge })` (from `workspaces.js`; the panes already exist, so no `render`). Every `showTab("issues")` becomes `showPanel("issues")`. |
| The `.pcount` counts in the tab labels | The `badge` functions: `issuesBadge = () => issueCount(store.get()) \|\| null`; `editsBadge = () => { const s = summary(); return s && s.dirty ? { text: s.unsaved \|\| "", dirty: true, title: dirtyBadge(s) } : null; }`. The Inspect workspace tab then shows the dirty mark whenever another workspace is open. Call `renderStrips()` when `store.edits` or `store.issues` changes. |
| Task 13, the issue-marker layer and its "Issues" toggle in Layers (Decision 14) | `addLayer(CORE, "roadway", "issues", factory, { order: 450, title: "Issues" })` (from `map.js`). The Overlays group gives the toggle; hiding is `hiddenLayers`, per tab and unrecorded, as Decision 14 asks. |
| Task 15, Step 5 (dirty badge in the network switcher) | Compose with Part 2's mark: `[dirtyBadge(edits), networkBadge(n)].filter(Boolean).join(" • ")`. |
| Task 5 (`HOST_API` 1.0 → 1.1) | 1.1 → 1.2. |
| Fix editor, cell editing, issue list | Unchanged. They may use `schemaForm` from `formview.js` for the fix editor's fields, but don't have to. |

The cards plugin's *Edit* workspace and draft panel then use `wb.registerWorkspace` and `wb.registerPanel` with a
`dirty` badge, and its "New change ▸ ‹card type›" commands use `contexts: ["selection", "feature"]` and
`group: "New change"`.

---

## Self-review against the brief and the design

| Requirement | Where |
|---|---|
| Loader: fetch `/api/plugins`, `import()` each frontend, `activate(wb)` in try/catch, failures to the Plugins panel | Task 14 (plus rollback and a timeout, Decisions 2–4); shown in Task 15 |
| `wb.api` (scoped fetch + dispatch), `wb.store`, `wb.selection` | Task 13 (`pluginPath`, `keysChanged`, `selection.subscribe`) |
| `registerWorkspace`, `registerPanel` (with `badge`) | Tasks 4, 9, 13 |
| `registerCommand` with `contexts` and `when` | Tasks 4, 5, 10, 11, 13 |
| `registerLayer(component, id, factory)` | Tasks 6, 7, 13 |
| `schemaForm`, `hasAction`, `on` | Tasks 2, 12, 13, 14 (`/api/actions` in Task 3) |
| Core Inspect workspace; core panels re-registered; P1b linking, focus and selection intact | Task 9 (Details adopted, ids unchanged); Task 19 walk-through A3 |
| Command palette; feature, row and selection contexts | Tasks 5, 10, 11 |
| `map.js` renders from the registry; core layers with no visual change; transit as a key | Tasks 6, 7; screenshots in Tasks 7 and 19 |
| `schemaForm` generalised from `settingsform.js` without changing Settings | Task 2 (markup pinned byte for byte), Task 12 |
| Plugins settings section: id, version, required API and fit, error, switch writing `app.disabled_plugins` (restart key) | Task 15 |
| Dirty badges on workspace tabs and panels (and the switcher) | Tasks 8, 9, 16 |
| Hello example: panel, command and Action end to end; its `main.js` updated | Tasks 12, 14, 17; walk-through B |
| Accessibility: keyboard tabs and palette; focus like the Settings dialog | Tasks 8, 9 (tabs pattern, focus kept on redraw), 10 (menu keys), 11 (combobox, shared `trapTab`, opener focus) |
| P2's tabs register through the same dock | [What P2 changes](#what-p2-changes); Open question 7 |
| Pure logic in import-free modules tested with `node_module`; DOM by static tests and a walk-through | Tasks 1–19 (Decision 12 extends the fixture to pure-to-pure imports) |
| No build step, no new dependencies | Native ES modules; no package changes |
| Existing behaviour and tests unchanged | Conventions ("Regression rule"); Tasks 7, 9, 19 |
| Design: "Core's existing settings and layers panels are re-registered through the same slots" | Settings: the Plugins section uses its `registerSection` slot. Layers: Overlays from the registry. Neither becomes a dock panel (Open question 4). |
| Design: commands in the NL assistant | Deferred (Scope notes) |
