---
title: Write a Workbench plugin
audience: contributors
kind: howto
summary: Package Actions, settings, state, an API router and a front-end module as a pip-installable plugin that the Netstead Workbench loads at startup, without editing Workbench core.
---

# Write a Workbench plugin

## When to use this

You want the [Workbench](workbench.md) to do something it doesn't do yet, such as authoring project
cards, managing a scenario catalog or running a domain check, and you don't want to fork netstead
to get it. A plugin is a separate pip package. Install it next to netstead and `netstead app`
picks it up.

The plugin API is **provisional until netstead v1.0** (see [Versioning](#versioning)).

## What a plugin is

A plugin is a Python package that declares an entry point in the `netstead.workbench.plugins`
group. The entry-point name must equal the plugin id, and it points at a zero-argument factory that
returns a `netstead.workbench.plugins.WorkbenchPlugin`:

```toml
[project.entry-points."netstead.workbench.plugins"]
hello = "netstead_hello:plugin"
```

At startup each `netstead app` session discovers the installed entry points, checks each plugin,
and installs the ones that pass. A plugin that fails is recorded with its reason and skipped. It
never stops the app (see [Startup outcomes](#startup-outcomes)).

### Trust model

**Plugins are full-trust code running inside the Workbench process**, the same as pytest plugins.
Nothing sandboxes them. A plugin can do anything your user account can do: read and write any
file, call the network, and read the OS keyring. The `Host` API is the *supported* surface, not a
security boundary. The `io.allowed_roots` sandbox that `host.writable` applies only helps plugins
that choose to use it. Install only plugins you would also run as a script.

The `--host 0.0.0.0` warning from [Explore in the Workbench](workbench.md) applies with more force
here. An exposed bind has no authentication, and every loaded plugin's routes
(`/api/plugins/<id>/...`) and Actions (`POST /api/actions`) are exposed with it. While the server
binds to a loopback address, plugin routes sit behind the same `Host`-header and cross-origin
write guards as every core route. Those guards do not run on an exposed bind.

## The UX contract

The Workbench stays coherent only if every plugin follows the same rules. These are condensed from
the design. Core enforces some of them and plugins are expected to follow the rest.

1. **Core owns the shared network nouns; plugins may own domain nouns.** Core owns networks
   (including derived networks and their lineage), the **one shared selection**, the map and
   tables, jobs, history and settings. A plugin never forks the selection or brings a second map.
   A plugin may add domain nouns of its own (a *card*, a *scenario*), and these follow three rules:
    - **Namespaced and exposed.** They live in plugin state (`plugins.<id>.*` in `/api/state`) and
      change only through that plugin's namespaced Actions, so other plugins, Python and NL can see
      and act on them.
    - **Projected onto core nouns, never duplicating them.** A card *resolves to* a core selection
      (highlighting it is an ordinary `Select`). A scenario *materializes as* a core derived
      network, so the map, tables and validation work on it unchanged.
    - **Referenceable by a stable id** of the form `{plugin}:{kind}/{id}`, for example
      `catalog:card/I80-hov-2035`, so history and "copy as Python" stay replayable.

    **Cross-plugin interaction is by Action.** Check for the other plugin's Action with
    `host.has_action("catalog.add_card")` and dispatch it, or fall back. That is a *soft*
    dependency, and core has no service registry. If you need another plugin's Python API, declare
    a normal pip dependency on it.
2. **Selection → verb → form → preview → commit.** The user selects, picks a verb, fills a form
   prefilled from the selection, sees a preview on the map, and commits. Present your workflows in
   this shape.
3. **Nothing mutates silently.** Previews and scenarios are derived networks (`host.derive`) and
   leave the base untouched. Make in-place changes (`host.mutate`) inside an Action handler: the
   Action is recorded in history and replays in "copy as Python", and if the handler fails the
   change is undone. A `mutate` from anywhere else (a `submit_job` function, a plugin route) still
   appears in history, as a `<id>.mutate` entry, but it can't be replayed: the copied script shows
   it only as a comment.
4. **Everything is an Action, plugins included.** Plugin Action types are namespaced
   (`<id>.<name>`) and get the same history, "copy as Python", replay and HTTP dispatch as core
   Actions.
5. **Schema-driven forms.** Plugin Actions render from their JSON Schema through one core form
   component (`wb.schemaForm`); every plugin Action also gets a generated form in the command
   palette.
6. **Workspaces as tabs, with predictable placement.** Plugins contribute workspaces, dock panels,
   commands (palette and context menus; the NL assistant surface comes later) and map layers. They
   don't add ad-hoc UI.
7. **Dirty state is always visible.** Unsaved drafts, uncommitted cards and derived networks are
   badged in the network switcher and on workspace tabs.
8. **Plugins are accountable.** Every installed plugin reports its id, version, required API and
   load error. A broken plugin degrades to "not loaded, with this error" and never takes down the
   app.

## Walk through the `hello` example

[`examples/workbench-plugin-hello`](https://github.com/e-lo/netstead/tree/main/examples/workbench-plugin-hello)
is a complete plugin. Copy it as a starting point:

```bash
uv pip install -e examples/workbench-plugin-hello
uv run netstead app
```

### Actions

An Action is a module-level subclass of `netstead.workbench.plugins.BaseAction`. Its `type`
field is a single-value `Literal` named `"<plugin id>.<name>"`:

<!-- doctest: skip -->
```python
from typing import Literal

from netstead.workbench.plugins import BaseAction


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str
```

Each Action is paired with its handler in a `netstead.workbench.plugins.ActionSpec`. The handler
signature is `handler(host, action) -> result`. It runs under the session lock. Its return value
is what `session.dispatch` returns, and over HTTP it is the `result` field of the `POST /api/actions`
response, next to `ok`, `error` and the recorded history `entry`. Set `mutates: ClassVar[bool] = True`
on an Action that changes a network in place, so the assistant drafts it for the user to confirm
instead of applying it. Plugin Actions can't be `runs_as_job`. For slow work, call `host.submit_job`
from the handler.

Two rules keep "copy session as Python" working:

- **The class must be importable.** Define it at the top level of a module (not in a function and
  not in `__main__`), and give it a class name no core Action or other plugin uses. The copied
  script imports it as `from <its module> import <Class>`. For the example that is
  `from netstead_hello import Greet`.
- **Field values must round-trip through `repr`.** The replay line is
  `app.do(Greet(name='Ada'))`, built from `repr(value)` of each field. Use str, numbers, bools,
  `None`, lists, dicts and tuples of those, or nested pydantic models. A value whose `repr` isn't
  valid Python (a numpy array, a datetime without an import, an open file) makes the copied script
  fail.

The startup checks enforce the namespacing, the single `Literal`, importability and unique class
names. The `repr` rule is yours to keep.

### The plugin object

<!-- doctest: skip -->
```python
from pathlib import Path

from netstead.workbench.plugins import HOST_API, ActionSpec, Host, WorkbenchPlugin
from pydantic import BaseModel, ConfigDict


class HelloSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = "Hello"


def plugin() -> WorkbenchPlugin:
    greeted = {"count": 0}  # per-session state lives in the factory's closure

    def greet(host: Host, action: Greet) -> str:
        greeted["count"] += 1
        host.publish("greeted", {"name": action.name})
        return f"{host.settings.prefix}, {action.name}!"

    return WorkbenchPlugin(
        id="hello",
        name="Hello",
        version="0.1.0",
        requires_api=HOST_API,
        actions=(ActionSpec(Greet, greet),),
        router=router,  # see below
        static_dir=Path(__file__).parent / "static",
        settings_model=HelloSettings,
        state=lambda host: {"greeted": greeted["count"]},
    )
```

| Field | What it does |
|---|---|
| `id` | The namespace for Action types, routes, state, settings and static files. Must match `[a-z][a-z0-9_]*` and equal the entry-point name. |
| `requires_api` | The plugin API version you wrote against. Pass `HOST_API` from the netstead you develop with. |
| `actions` | A tuple of `ActionSpec(model, handler)`. |
| `settings_model` | A pydantic model that validates the `[plugins.<id>]` table. Read the result as `host.settings`. |
| `state(host)` | Returns a JSON-safe dict merged into `/api/state` under `plugins.<id>`. Keep it cheap, because it runs on every state push. |
| `router(host)` | Returns a FastAPI `APIRouter`, mounted at `/api/plugins/<id>`. Called once, at install. |
| `static_dir` / `frontend` | A directory served at `/plugins/<id>/`, and the ES module in it the browser loads (default `main.js`). |
| `on_load(host)` | Runs once at install, before the router is built and the Actions register. If it raises, the plugin isn't installed, but whatever it did before raising (files written, threads started) is not undone. The same holds if the router or static dir then fails. |

The factory runs once per session, so state kept in its closure belongs to that session.

### Settings

Settings come from the same layers as every other setting (user config, project `netstead.toml`,
env, session):

```toml
# ./netstead.toml
[plugins.hello]
prefix = "Howdy"
```

Bad values mark the plugin `error` at startup. Through the Workbench's settings API they are refused
before anything is written. **Credentials are never settings.** A secret-named key (`api_key`,
`token`, `password` and so on), a value shaped like an API key, or a URL carrying a password under
`[plugins.<id>]` is refused in every layer, and the settings fail to load. Read keys from the OS
keyring or the environment in your own code instead.

### Routes and front end

<!-- doctest: skip -->
```python
from fastapi import APIRouter


def router(host: Host) -> APIRouter:
    api = APIRouter()

    @api.get("/count")  # served at GET /api/plugins/hello/count
    def count() -> dict:
        return {"greeted": greeted["count"]}

    return api
```

`static_dir` is served at `/plugins/<id>/`. Requests can't escape it (`..` and its encoded forms
get a 404). At startup the Workbench imports `frontend` and calls its `activate(wb)` (see
[The front end](#the-front-end-activatewb)). If the module fails to import, has no `activate`,
`activate` throws, or importing the module and running `activate` take more than 5 seconds together,
everything it registered is removed and Settings → Plugins shows the error. The rest of the Workbench carries on. `router(host)` is called once,
when the session installs the plugin. If it raises or returns something other than an `APIRouter`,
or `static_dir` isn't a directory, the plugin isn't installed: it is marked `error`, none of its
Actions register, its state isn't merged, and nothing of it is mounted.

## The `Host` reference

A plugin never sees the `Session`. Every handler, `router`, `state` and `on_load` gets a
`netstead.workbench.plugins.Host` bound to that plugin's id.

| Member | What it gives you |
|---|---|
| `host.plugin_id` | This plugin's id. |
| `host.settings` | The validated `[plugins.<id>]` model, or `None` if there is no `settings_model`. Re-read on each access, so it reflects setting changes. |
| `host.active` | The active network's id, or `None`. |
| `host.selection` | A copy of the shared selection payload (the same dict `/api/state` carries), or `None`. |
| `host.network(net_id=None)` | The network handle (default: the active one). Read it, but change it only through `mutate`. |
| `host.mutate(net_id, edits, note=...)` | Applies `corral.editing.Edit`s to a network's roadway **all or nothing**. The edits run on a draft, so a failing edit leaves the network (and its caches) untouched and re-raises. On success it bumps the version, appends `"<id>: <note>"` to the lineage and pushes state. It returns the `EditResult`s (the rollback data). Inside a handler, the change is undone if the handler fails. Outside one (a job, a route), it is recorded as a non-replayable `<id>.mutate` history entry. |
| `host.derive(net_id, label=..., note=...)` | Registers a **copy-on-write** copy of a network (a preview or scenario) and returns its id. Edits to the copy never touch the base. The copy records `derived_from` and inherits the lineage. Inside a handler that fails, the copy is unregistered again. |
| `host.has_action(type)` | Whether core or any loaded plugin registered that Action type. Use it for soft cross-plugin dependencies. |
| `host.dispatch(action)` | Dispatches any Action (a model or a dict). Called from a handler, it is recorded in history as nested in that handler's Action (`parent_seq`): "copy as Python" shows it as a comment, since replaying the outer Action runs it again, and it is undone if the outer handler fails. Job Actions (`open_network`, `build_network`) can't be dispatched from a handler, because waiting for them would deadlock. Use `host.submit_job` for slow work instead. |
| `host.publish(name, payload)` | Sends the browser a `{"type": "plugin", "plugin": <id>, "name": ..., "payload": ...}` event over the session's event stream; `wb.on(name)` hears it. `name` is lowercase letters, digits and underscores, starting with a letter (`[a-z][a-z0-9_]*`), since a dot in `wb.on` names another plugin; anything else raises `ValueError`. |
| `host.submit_job(label, fn)` | Runs `fn(ctx)` on a background job, with progress and cancel in the jobs panel, and returns the job id. `mutate` and `derive` are safe to call from it. |
| `host.writable(path)` | Resolves a path for writing inside `io.allowed_roots`. Raises `PathNotAllowed` outside it. |

A preview, following "nothing mutates silently":

<!-- doctest: skip -->
```python
from corral.editing import Edit


def widen(host: Host, action: Widen) -> str:
    preview = host.derive(None, label="Preview: widen", note="preview")
    payload = {"predicate": lambda t: t.link_id == action.link_id, "set": {"lanes": action.lanes}}
    host.mutate(preview, [Edit(op="update_rows", table="link", payload=payload)], note=f"widen {action.link_id}")
    return preview  # the base network is untouched
```

### Exposing Actions to the NL assistant

Build the assistant's tool vocabulary from the **session's** registry, `session.actions.json_schema()`.
It holds the core Actions plus every loaded plugin's. The module-level
`netstead.workbench.actions.action_json_schema` covers core Actions only, so a tool list built from
it would silently leave out every plugin.

## The front end: `activate(wb)`

A plugin's front end is one native ES module (no build step), served from `static_dir` as
`/plugins/<id>/<frontend>`. After core has drawn the map, the Workbench imports every loaded plugin's
module at once and calls each one's `activate(wb)`. A slow plugin never delays the map or another
plugin. Tabs, panels, commands and layers sort by `order`; between two plugins' entries with the same
`order`, which comes first depends on which plugin finished loading first, so set `order` when placement
matters. `wb` is the only way in: don't import core's modules or reach into its DOM.

The hello example's `main.js`, annotated:

<!-- doctest: skip -->
```js
export function activate(wb) {
  let greeted = 0;

  // A dock panel in the Inspect workspace. render(el) runs the first time the panel shows.
  const panel = wb.registerPanel({
    workspace: "inspect",
    id: "hello.panel",                 // every id starts with "<plugin id>."
    title: "Hello",
    order: 50,                         // after core's Details (0)
    badge: () => greeted || null,      // a count on the tab; {dirty: true, title: "…"} for unsaved work
    render(el) {
      el.innerHTML =
        '<p class="muted">Greet someone. Each greeting is a recorded Action: see the history strip.</p>' +
        '<div></div><button class="mini">Greet</button> <span class="muted" role="status"></span>';
      const [, slot, button, status] = el.children;
      // A form generated from the Action's JSON Schema (from GET /api/actions).
      const form = wb.schemaForm(slot, wb.actionSchema("hello.greet"), { name: "world" });
      button.onclick = async () => {
        const errors = form.errors();  // required fields, bounds, entries that didn't parse
        if (errors.length) { status.textContent = errors.join("; "); return; }
        try {
          // Every change is an Action: recorded in history, replayable in "Copy as Python".
          status.textContent = await wb.api.dispatch({ ...form.value(), type: "hello.greet" });
        } catch (e) {
          status.textContent = e.message;  // text, never markup
        }
      };
    },
  });

  // The count comes from the plugin's own route; host.publish("greeted") says when to re-read it.
  const refresh = async () => {
    greeted = (await wb.api.get("/count")).greeted;  // GET /api/plugins/hello/count
    panel.refreshBadge();
  };
  wb.on("greeted", () => refresh());
  refresh().catch(e => wb.toast(e.message));

  // Commands: one in the palette, one on a right-clicked map feature or table row, one for the selection.
  wb.registerCommand({
    id: "hello.greet", title: "Say hello", contexts: ["palette"],
    run: () => wb.api.dispatch({ type: "hello.greet", name: "world" }),
  });
  wb.registerCommand({
    id: "hello.greet_record", title: "Say hello to this record", contexts: ["feature", "row"],
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.target.table} ${ctx.target.id}` }),
  });
  wb.registerCommand({
    id: "hello.greet_selection", title: "Say hello to the selection", contexts: ["selection"],
    when: ctx => ctx.selectionCount > 0,
    run: ctx => wb.api.dispatch({ type: "hello.greet", name: `${ctx.selectionCount} selected links` }),
  });
}
```

### The `wb` reference

| Member | What it gives you |
|---|---|
| `wb.id`, `wb.hostApi` | This plugin's id, and the plugin API this netstead provides (`"1.1"`). |
| `wb.api.fetch(path, init)`, `wb.api.get(path)`, `wb.api.post(path, body)` | `fetch`, and JSON helpers, scoped to your router: `"/count"` is `/api/plugins/<id>/count`. A path must start with `/` and, once resolved (`%2e%2e` and `\` count as `..` and `/`), stay under that prefix; other hosts are refused. |
| `wb.api.dispatch(action)` | Posts any Action (yours, another plugin's, core's) to `POST /api/actions` and resolves to its result. |
| `wb.store.get()`, `wb.store.subscribe(keys, fn)` | The server state (`/api/state`), and a listener that fires when any of `keys` changes. Keys may be dotted (`"plugins.hello"`). `get()` and the listener's argument are a frozen copy: changing it throws (changes go through Actions). Returns an unsubscribe function. |
| `wb.selection.get()`, `wb.selection.subscribe(fn)` | The active network's shared selection (or `null`), as a frozen copy, and a listener for its changes. Returns an unsubscribe function. |
| `wb.registerWorkspace({id, title, layout?, badge?, order?})` | A workspace tab. `layout: {view: "map" \| "split" \| "table"}` is its first-visit view; after that it opens in the view the user left it in. Returns an unregister function. |
| `wb.registerPanel({workspace, id, title, render?, badge?, onShow?, order?})` | A dock panel. `workspace` is an id, a list, or `"*"` (every workspace). `render(el)` runs once, the first time it shows; `onShow()` each time it is shown. Returns `{refreshBadge(), dispose()}`. |
| `wb.registerCommand({id, title, run, contexts?, when?, group?, order?})` | A command. `contexts` defaults to `["palette"]` (see [Commands](#commands)). Returns an unregister function. |
| `wb.registerLayer(component, id, factory, {order?, title?})` | A map layer (see [Map layers](#map-layers)). Returns an unregister function. |
| `wb.schemaForm(el, schema, value, {onChange?, omit?})` | Renders a form for a JSON Schema into `el` (see [Forms](#forms)). Returns `{value(), errors(), set(value), focus()}`. |
| `wb.actionSchema(type)`, `wb.hasAction(type)` | An Action's JSON Schema (or `null`), and whether it is registered: for soft dependencies on another plugin, as `host.has_action` is in Python. Read from `GET /api/actions` before activation. |
| `wb.on(event, fn)` | Your own `host.publish(event, payload)` events (`"greeted"`), another plugin's (`"catalog.added"`), or core's (`"core.history"`, `"core.job"`). They arrive over the page's one event stream: never open your own `EventSource`. Returns an unsubscribe function. |
| `wb.showPanel(id)` | Shows a panel, switching workspace when the current one doesn't have it. |
| `wb.toast(message)` | A short notice, prefixed with your plugin id. |

`GET /api/actions` lists every registered Action: `type`, class `name`, `description` (its
docstring's first line), owning `plugin` (`null` for core), `mutates`, and its JSON `schema`.

**Ids.** Every id a plugin registers (workspace, panel, command, layer, and the `id` of each deck.gl
layer a factory returns) starts with `"<plugin id>."`. Core's ids are bare (`inspect`, `details`,
`links`), and the plugin id `core` is reserved.

### Commands

`run(ctx)` and `when(ctx)` get one context:

| Key | What it is |
|---|---|
| `network` | `{id, version, derived_from}` of the active network, or `null`. |
| `selection`, `selectionCount` | The active network's shared selection (`{net_id, link_ids, …}`) and its number of links. A selection with no links counts as none. |
| `focus` | The record last clicked, `{table, id}`, or `null`. |
| `highlights` | The highlighted link ids (an array). |
| `workspace` | The active workspace's id. |
| `target` | The right-clicked record, `{table, id}`: a map feature or grid row, or the focused record in the palette's "For …" group. `null` otherwise. |
| `state` | The server state. |

Everything in `ctx` is a frozen copy: changing it throws, and the page's own state is untouched.
Changes go through Actions. A panel's or workspace's `badge(ctx)` gets the same context.

Where each context shows:

| `contexts` entry | Where |
|---|---|
| `"palette"` | The command palette (**Ctrl+K**, **⌘K** on a Mac, or **Commands…** in the header). |
| `"feature"` | Right-click on a link or node on the map. |
| `"row"` | Right-click on a table row. |
| `"selection"` | **Selection actions ▾** in the Details panel, a "Selection (n links)" section in the map and row menus, and in the palette. |

The palette is also the keyboard way to a context menu: with a record focused it lists that record's
feature and row commands under "For ‹table› ‹id›", and the selection's under "Selection (n links)".
A command's `group` shows as "Group ▸ Title" in menus and the palette. A menu opens only when a
command applies; with none, the map keeps its right-drag rotate and a table row the browser's own
menu.

**Declarative first.** Every plugin Action also gets a generated palette command, "‹Plugin›:
‹Action›…" (for hello, "Hello: Greet…"). It opens a dialog with the Action's schema form and a Run
button, so a plugin that ships no JavaScript is still usable.

### Badges

`badge(ctx)` returns a count, a string, or `{text, dirty, title}`, or nothing for no badge. It is
re-evaluated on every server-state change, and when you call `panel.refreshBadge()`. `dirty: true`
shows a dot on the panel's tab and on its workspace tab, so unsaved work is visible from anywhere.

### Map layers

<!-- doctest: skip -->
```js
wb.registerLayer("roadway", "hello.pins", ctx =>
  ctx.focus && ctx.focus.table === "node"
    ? new ctx.deck.ScatterplotLayer({ id: "hello.pins", data: [/* … */], getPosition: d => d.xy, getRadius: 8 })
    : null,
  { title: "Hello pins", order: 350 });
```

- `factory(ctx)` returns a deck.gl layer, a list of them, or nothing, on every map redraw. Anything
  that isn't a deck.gl layer, or a layer whose `id` doesn't start with `"<plugin id>."`, is left
  out and reported (core's ids drive picking, tooltips and box-select).
- `ctx` has `deck` (the page's deck.gl), `server`, `style`, `selection`, `focus`, `highlights` (a
  Set), `related`, `marker`, `zoom`, `net` and `attrs`. `server`, `selection`, `focus`, `related`
  and `marker` are frozen copies and `highlights` is a copy. **`net` and `attrs`** (the decoded
  network and its attributes) are too big to copy on each redraw, so they are the page's own: read
  them, never change them.
- Core's layers, bottom to top: `base` 100, `selection` 200, `related` 300, `highlighted` 400,
  `focus` 500, `marker` 600. A plugin layer defaults to 350: above related records, under the user's
  highlights, focus and marker.
- A `title` lists the layer under Layers → **Overlays**, with a show/hide switch (per browser tab,
  not recorded).
- `"transit"` is accepted as a component and not drawn yet.

### Forms

`wb.schemaForm(el, schema, value, {onChange, omit})` is the Settings dialog's field renderer, made
general: text, numbers (with their bounds), checkboxes, menus for enums and `Literal`s, lists, one
level of nested object as a group, and JSON for anything else. A `const` property (an Action's
`type`) gets no field, and `omit` drops others by name. `value()` is what the form shows: a
checkbox's or menu's shown value is included, a field left blank is left out, and a required field
that may be `None` sends `null` (its blank choice reads "None"). `errors()` lists required fields
left empty, numbers out of bounds, and entries that didn't parse. The server validates again.

### Styling

Use core's classes (`mini`, `ghost`, `muted`, `pcount`, `row`, `sw`) so your panel looks like the
rest of the Workbench. Scope your own CSS under `[data-panel="<your panel id>"]`, the panel's
container. If you ship a stylesheet, add a `<link>` for it from `activate`.

### When plugin code fails

Every call into plugin code is contained, so one plugin's bug never breaks core or another plugin:

| Call | When it throws (or its promise rejects) |
|---|---|
| `activate(wb)` (or the module import, or no `activate`, or the import and `activate` taking more than 5 s together) | Everything it registered is removed, and later registrations and listeners (`wb.on`, `subscribe`) through that `wb` are refused. |
| A panel's `render` | The panel shows "This panel failed to load: …". |
| A `badge` | No badge. |
| `onShow`, a `wb.on` or `wb.store` / `wb.selection` listener | Skipped for that call. |
| A command's `when` | The command doesn't apply. |
| A command's `run` | Nothing else happens. |
| A layer factory | That layer isn't drawn. |

Each failure is logged to the browser console, shown in a notice ("Plugin hello: …"), and listed
under the plugin in **Settings → Plugins**. A failure that would repeat on every redraw (a badge, a
layer, a `when`, a listener) is reported once per message. A command's `run` is reported every time the
user runs it, so they always see why nothing happened, but it is listed once.

Only calls core makes into your code are contained this way. An error thrown from your own timer
(`setTimeout`, `setInterval`), an event handler you attached yourself, or a promise you don't return to
core reaches the browser console but isn't attributed to your plugin or listed in Settings → Plugins.
Catch those yourself (and `wb.toast` if the user should know).

Every string a plugin hands core (titles, badges, menu labels, schema text) is shown as text, never
as markup. Markup you write into your own panel's `el` is yours to escape.

## Test a plugin

There is no special harness. Build a `Session` with your plugin passed in explicitly, which skips
entry-point discovery, and drive it:

<!-- doctest: skip -->
```python
import pytest
from netstead.select.parse import StubParser
from netstead.workbench import Session

from netstead_hello import Greet, plugin


@pytest.fixture
def session(tmp_path):
    environ = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "user")}  # never read your real user config
    return Session(plugins=[plugin()], project_dir=tmp_path, environ=environ, parser=StubParser())


def test_greet(session):
    assert session.dispatch(Greet(name="Ada")) == "Hello, Ada!"
    assert session.dispatch({"type": "hello.greet", "name": "Bo"}) == "Hello, Bo!"  # as the browser sends it
    assert session.state()["plugins"]["hello"] == {"greeted": 2}
    assert session.history[-1].imports == "from netstead_hello import Greet"
    assert next(s for s in session.plugin_status if s.id == "hello").state == "loaded"
```

Pass `overrides={"plugins.hello.prefix": "Howdy"}` to test settings. For the HTTP side, wrap the
session in `TestClient(build_app(session))` from `netstead.workbench`.

## Startup outcomes

Every plugin seen at startup gets a `netstead.workbench.plugins.PluginStatus`, listed by
`GET /api/plugins` together with the host's `host_api`, and shown in the Workbench under
**Settings → Plugins** (with any front-end errors from the browser):

| State | Meaning |
|---|---|
| `loaded` | Installed. Its Actions dispatch, and its routes and static files are mounted. `frontend` is the module URL (`/plugins/<id>/main.js`). |
| `disabled` | Listed in `[app] disabled_plugins`. It is skipped before its package is even imported. |
| `incompatible` | Its `requires_api` doesn't match this netstead's `HOST_API`. |
| `error` | Something failed: importing the entry point, the startup checks (id, namespacing, duplicates, class names), the settings, `on_load`, the router, or the static dir. `error` holds the reason. |

To turn a plugin off without uninstalling it:

```toml
[app]
disabled_plugins = ["hello"]
```

Or switch it off in **Settings → Plugins**, which saves `app.disabled_plugins` in your user config.
When a project file or `NETSTEAD_APP__DISABLED_PLUGINS` sets it instead, that value wins: the switches
are disabled and the section names the file or variable to change. `app.disabled_plugins` is read at
startup, so restart `netstead app` after changing it; the row says "restart to apply" until then.
Dispatching an Action of a plugin that isn't loaded fails like any unknown Action type (HTTP 422).

## Versioning

`HOST_API` (in `netstead.workbench.plugins`) is a `major.minor` string. A plugin's `requires_api`
is compatible when the major versions are equal and the host's minor is at least the plugin's. A
plugin written against `1.0` runs on `1.3`, but not on `2.0`, and a `1.3` plugin doesn't run on `1.0`.
A minor bump only adds to the API and a major bump breaks it.

| `HOST_API` | Added |
|---|---|
| `1.0` | The Python plugin core: Actions, `Host`, settings, state, routes, static files. |
| `1.1` | The browser API: `activate(wb)` and the `wb` object (workspaces, dock panels, commands, map layers, `schemaForm`), and `GET /api/actions`. A plugin with a front end should pass `requires_api="1.1"` (or `HOST_API`); a `1.0` plugin still runs. |

**The plugin API is provisional until netstead v1.0.** Until then it may change without a major
bump. Pin the netstead versions you support in your package's dependencies, and check the changelog
when you upgrade.
