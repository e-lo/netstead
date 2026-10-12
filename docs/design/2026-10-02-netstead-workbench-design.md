# Netstead Workbench — unified front-end UX design

Status: **accepted; P0, P1a, P1b implemented** (#211, #212); P2 implemented (PR pending); P3 next · Date: 2026-10-02 · Owner: netstead

> **Amended by the [Workbench plugins design](2026-10-05-workbench-plugins-design.md) (PR #217):**
> P2's `netstead.changes` package (NetworkChange, DraftCard, ProjectCard export) moves out of core into
> an external ProjectCard plugin. Core keeps `mutate` and `derive`; it stays card-agnostic.

Related: [network-viewer PRD](2026-09-30-network-viewer-prd.md) · [data-table scope](data-table-exploration-scope.md) · [NL selection design](2026-09-23-nl-selection-design.md) · [ProjectCard](https://github.com/network-wrangler/projectcard) · [network_wrangler](https://github.com/network-wrangler/network_wrangler)

## Context

Netstead has grown several separate web surfaces, and none of them covers the whole workflow:

| Surface | Stack | Strength | Gap |
|---|---|---|---|
| `netstead viz` (`netstead/viz/server.py`, `templates/index.html`) | FastAPI, MapLibre + deck.gl, binary buffers | Fast map, colour-by, paged tables, NL select, pick to fragment | Holds one network frozen at startup; one 737-line inline script; map→table link is partial |
| `select-serve` (`select/webapp.py`) | MapLibre + GeoJSON | — | Removed in P1b |
| `NetworkMap` and the validation report (`netstead/map/*`) | Leaflet + Jinja, offline | Issue markers, "fix locally" editor, edit log | Static; no row→map link; 2000-item cap |
| corral `ValidationReport.to_html` | Jinja + Vega-Lite | Generic report | No map link |

Several things are missing entirely:
- Opening or building a network from the UI.
- Comparing two networks.
- A settings or config system: today settings are scattered across kwargs, CLI flags and env vars, with no persisted config.
- Sharing a live session between Python and the UI.

**Goal:** one local app, `netstead app`, that lets a modeller:
- **a.** Open a local or remote GMNS folder, or build one from OSM or Overture using a drawn bbox or a place/entity search.
- **b.** Inspect and validate the network, with map, table and issue list linked both ways.
- **c.** Verify selections.
- **d.** Drive the app from Python.
- **e.** Drive the app in natural language.
- **f.** Compare two networks.
- **g.** Edit every setting and config.

**Decisions already made:**
- A local served app: FastAPI, opened in a browser, one user.
- A no-build ES-module front end.
- A shared live Python session.

## Core idea: one Action bus

Every state change is a typed **Action**: a pydantic discriminated union in `netstead/workbench/actions.py`. Examples: `OpenNetwork`, `BuildNetwork`, `Select`, `Style`, `Filter`, `Navigate`, `RunValidation`, `ApplyEdit`, `Compare`, `SetSetting`.

Three front doors share this one deterministic `Session.dispatch(action)`:
1. **UI clicks**, sent as `POST /api/actions`.
2. **Python**, via `app.do(action)` or sugar such as `app.select(...)`.
3. **The LLM**, whose tools are the Action JSON schemas. This extends PRD §15 (`SelectIntent`, `StyleAction`, `FilterAction`, `NavigateAction`).

Because every change goes through the bus:
- History is a list of Actions, which gives undo, a "copy as Python" snippet for any UI action, and a replayable session log.
- Settings changes are just another Action.
- Natural language never produces ids or code; it produces validated Actions. Anything that mutates is shown as a draft before it is applied.

The server pushes state to the browser over **SSE** at `GET /api/events`. That is plain `StreamingResponse`, with no websocket dependency. When Python edits the network, the UI refreshes.

## Two audit logs: session Actions vs ProjectCard changes

ProjectCard compatibility changes what the logs are organised around. There are two logs, kept deliberately separate.

**1. Session history (Actions).** This records everything: navigate, style, select, validate, settings, and so on.
- It exists for replay, undo and "copy as Python".
- It is internal to netstead, and other tools are not expected to read it.

**2. Change log, made of ProjectCard units.** This is the durable, interoperable record of edits.
- Every *mutating* Action does not touch tables directly. It compiles to a `NetworkChange`, which mirrors a ProjectCard change type:
  - `roadway_property_change`: a facility selection plus `property_changes {prop: {existing, set | change}}`
  - `roadway_addition`
  - `roadway_deletion`
- One applier, `netstead.changes.apply_change(net, change) -> ChangeResult`, executes the change. It lowers to corral `apply_edit` / `reverse_edit`.
- Each applied change is appended to the session's active **draft ProjectCard**, which has project name, tags, dependencies and `changes[]`.
  - The draft can be exported as `.yml`.
  - It is validated against the projectcard JSON schema. This uses the optional `[projectcard]` extra, keeping the base install lean.
- **Selection facility encoding** reuses `select/emit.to_projectcard`, which already maps `link_id` to `model_link_id`:
  - If the edit came from an NL or query selection, the change stores the *query* form (name/ref/from/to), so the card can be re-applied to other network versions.
  - It also records the resolved ids, as provenance.
  - Picks made by clicking store ids only.
- Edits are grouped into cards:
  - Edits accumulate in the active card until you "commit card" (name it and tag it).
  - Then a new draft starts.
  - Undo removes the last change from the draft and reverses it.
- The `map/edits` edit-log YAML from the offline report becomes an import path. Its `Edit`s are converted into `NetworkChange`s.

**Why this matters now (near-term item 1).** The P2 "fix" editor must be built on `NetworkChange` and draft cards from the start. Bolting cards onto raw table edits later would lose the selection and intent.

**Groundwork for far-term items 2 and 3, kept cheap:**
- `apply_change` is already the per-change engine, so **applying a card** later is just: parse the card, check `existing` values, and apply each change in order. That is a `netstead.changes.apply_card` added later.
- `NetworkHandle` carries a `lineage`: the base source plus an ordered list of applied card ids and versions. **Scenario management** later becomes a first-class `Scenario(base, cards[])` that rebuilds that lineage. The registry and the Compare workspace (base vs scenario) don't change.
- Compare can later export a `NetworkDiff` as cards (diff → changes).
- Interoperability is at the card file and schema level, not by depending on network_wrangler. Its pandas/geopandas/pandera stack conflicts with the DuckDB-only engine. The cards we write should load in Wrangler, and the cards Wrangler users write should apply in netstead.
- Field-name mapping (`link_id` ↔ `model_link_id`, `from_node_id` ↔ `A`, etc.) lives in a maintained data file, `changes/mappings/gmns_to_wrangler.yaml`.

**Out of scope for now:**
- The applier for card types other than roadway and transit (`pycode`, managed lanes).
- Conflict and dependency resolution across cards.
- Scenario UI.

## Transit (designed now, built in a later phase)

Transit is part of the data model from P0, even though its features ship later. That avoids a retrofit.

**Bundle model.** A `NetworkHandle` is a bundle of components:
- `roadway`: the GMNS `Network`.
- `transit`: an optional `TransitFeed`, which is GTFS tables loaded as a corral package. That means GTFS has a spec in corral and runs on the same DuckDB engine.

This mirrors Wrangler's `Scenario` of roadway + transit.

**Unions include transit variants from the start.** API routes are `/api/n/{id}/{component}/...`, and the following unions all declare transit variants from P0. Handlers that aren't built yet return a clear "not yet supported".
- **Selection:** `RoadwaySelection | TransitSelection`. A transit selection uses ProjectCard's `service` selector: `route_id`, `route_short_name`, `trip_id`, time window, `agency`.
- **NetworkChange:** `transit_property_change`, `transit_routing_change`, `add_transit_routes`, `transit_service_deletion`.
- **Action:** select, style and filter apply to either component.
- **Layer registry:** map layers come from a generic registry per component (roadway links/nodes; transit shapes/stops/routes), not hard-coded to links.

**What each workspace gets for transit (later phase):**
- **Open/Build:** attach a GTFS zip or URL to a roadway network. Also a list of feeds that cover the area (the Mobility Database catalogue) and OSM route relations. Overture has no transit data.
- **Inspect:**
  - Routes and stops layers.
  - Frequency or headway colour-by.
  - Trip table ↔ shape on the map.
  - Stops on map ↔ stops table.
  - Optional animation with `TripsLayer` (PRD roadmap).
- **Validate:**
  - GTFS integrity.
  - Roadway↔transit consistency:
    - shapes follow existing roadway links or nodes
    - stops are snapped to nodes
    - edits to roadway links that break transit routing are flagged
- **Selection verification:** check that a transit `service` selector resolves to the expected trips and shapes.
- **NL:** e.g. "route 38 between 7–9am"; Wrangler-style "add headway 5 min" becomes a draft transit change.
- **Compare:**
  - Route, trip and frequency diffs.
  - Shape reroutes shown as added/removed segments.
- **Change log:** transit changes go into the same draft ProjectCard.
- **Cross-component rule:** `apply_change` on the roadway checks the transit dependency (shape-on-deleted-link) and warns before applying.

## Architecture

```
netstead/workbench/
  __init__.py      Session/build_app/serve; P4 adds launch(net=None, *, port) -> AppHandle, exposed as netstead.app
  session.py       Session: NetworkRegistry, selections, style spec, history, settings ref
  registry.py      NetworkHandle(id, label, source, Network, version, caches keyed by version)
  actions.py       Action union + handlers; to_python(action) snippet renderer
  jobs.py          background jobs (build/validate/compare) with progress -> SSE
  events.py        SSE broadcaster
  routes/          networks.py, tables.py, validate.py, select.py, compare.py, settings.py, assistant.py, console.py
  static/          index.html + js/{store,api,map,table,issues,select,compare,settings,assistant,console}.js + app.css
netstead/config.py   layered Settings (see below)
netstead/compare/    diff_networks(a, b, *, keys, columns) -> NetworkDiff
netstead/changes/    NetworkChange types, apply_change, DraftCard, to/from ProjectCard YAML, mappings/gmns_to_wrangler.yaml
cli/commands/app.py  `netstead app [SOURCE ...] [--console] [--port] [--provider]`
```

**Multi-network.** All network routes are namespaced as `/api/n/{net_id}/...`. Caches are keyed by `(net_id, version)`, and an edit bumps the version. This fixes viz's frozen `lru_cache` state.

**Front end.** The `index.html` script is split into native ES modules around a ~50-line pub/sub `store.js` (selection, active network, style, view mode). It loads MapLibre 4.7.1 and deck.gl 9.0.38 from a CDN, as today, and adds no build step.

**Map stack.** The app standardises on deck.gl + MapLibre:
- `select-serve` is retired.
- The Leaflet `NetworkMap` stays only for offline static HTML exports. "Export report" in the app calls the existing `render_validation_html`.

**Reuse, don't rewrite:**
- `viz/buffers.pack_network`
- `viz/tables._page_lazy`, and the filter/sort row endpoint
- `select.resolve` / `resolve_frames` / `to_fragment` / `to_projectcard` / `validate_fragment` and `ClaudeParser`
- `osm.build_network_from_osm` and `overture.build_network_from_overture`
- `osm/query.geocode_area`
- `netstead.validate` plus corral `run_quality` with `RuleConfig`
- corral `editing` (`apply_edit`, `reverse_edit`, `Session` edit log), plus `map/edits.apply_edits` for importing an edit log
- the editor logic in `map/templates/map_component.js`, ported to a module
- `io/credentials.resolve_credentials`, for showing credential status only

## Layout

The app is a single page:
- A **left rail** to switch workspaces.
- A **centre** area that shows Map, Split or Table.
- A **right drawer** with tabs for Details, Issues, Selection and Assistant.
- A **bottom strip** showing the history and "copy as Python" for the last action.
- A **network switcher** in the header for the active network, and A/B pickers in Compare.

### a. Open / Build workspace
- **Open:**
  - Local folder, via a server-side file browser limited to allowed roots (from config).
  - Remote URL (`http(s)`, `s3`, `gs`, `az` through fsspec). Shows which credential source resolved the URL, never the secret.
  - Recent networks.
- **Build:**
  - Source toggle: OSM or Overture.
  - Area picker:
    - **bbox:** draw a rectangle on the map.
    - **Search:** Nominatim `geocode_area` returns a list of candidates (name, type, bbox outline previewed on the map), and you pick one.
    - **Point and buffer.**
  - Options form generated from the build kwargs: `network_type`, `buffer_m`, `extra_tags`, `spec_version`, `overture_release`, plus an output destination and format.
  - The build runs as a job with progress, and the result opens automatically.
- Polygon clipping and a lookup in Overture's divisions data come later; builders accept only a bbox today.

#### Open / Import wizard (P1a, agreed 2026-10-02)

- **Entry point:** the header's path box is replaced by an **Open / Import…** button, which opens a modal wizard, and a **Recent** dropdown next to it.
- **Step 1, source:**
  - GMNS on this machine → file browser.
  - GMNS at a URL (`s3/gs/az/https`) → URL field + **Check**. It reports reachable or not, which credential source would be used (never the secret), and the tables found.
  - Build from OSM → Area step, or a local `.osm` XML / Overpass JSON file through the file browser.
  - Build from Overture → Area step, or local Overture GeoParquet (`segment`/`connector`) through the file browser.
- **File browser:**
  - Runs on the server, limited to `io.allowed_roots` (default: the home directory). This setting is now enforced, including in `OpenNetwork`.
  - Recognised entries are tagged (GMNS dir, `.zip`, `.duckdb`, `datapackage.json`, `.osm`, `.json`, Overture parquet).
  - Only valid targets can be chosen. Files are opened in place, never uploaded.
- **Area step:** every tab ends in one bbox, previewed on the map.
  - **Draw** a rectangle, with editable corners.
  - **Coordinates:** `W,S,E,N`, or a point plus buffer in metres.
  - **Place:** a multi-candidate Nominatim search via a new `geocode_candidates(q, limit=8)`, with outlines previewed. For OSM, the place polygon is kept.
- **Options:**
  - Prefilled from Settings: network type, buffer, extra tags, spec version, Overture release.
  - **Output folder + format are required:** builds always write to disk first and then open from disk, so the result is reproducible.
- **Estimate → approve → run:**
  - A cheap pre-query sizes the request: Overpass `out count`, or Overture `COUNT(*)` over the bbox.
  - A calibrated cost model (`latency + rate × elements`, built on corral `OperationCost`/`gate`) produces a time and an output size.
  - Above `app.approve_above_s` (default 90 s), the action needs an explicit **Run (~N min)** click.
  - If the pre-query itself fails, the user is told and asked before running anyway.
- **Jobs:**
  - Builds and large opens run as background jobs. Stages (query → convert → write → open) and progress are pushed over SSE, and each job can be cancelled.
  - A jobs indicator sits in the header.
  - Opening through a job also stops a long open from holding the session lock.
- **Audit:**
  - `OpenNetwork` and `BuildNetwork(source, area, options, output, approved)` are recorded Actions; the estimate and approval are stored with the entry.
  - Python replay counts as approval.
  - Browse, check, place search and estimate are read-only queries and are not recorded.
- **Later:** `.pbf` (via an optional pyosmium extra), upload, polygon clipping for Overture, Overture divisions lookup.

### b. Inspect & Validate workspace
- **Two-way linked selection** (`store.selection`):
  - Clicking a map feature selects and scrolls to the row.
  - Clicking a row selects it and flies the map to it.
  - Box-select fills the table filter "to selection".
- **FK navigation:** a `from_node_id` cell is a link to that node row and its map marker.
- **Related records (P1b):** when links or nodes are highlighted or selected, rows that are related through a foreign key are tinted in a softer colour in every other table and on the map.
  - **Which tables count as related:** FKs pointing in (`lane.link_id`, `segment.link_id`, `link_tod.link_id`, movement in/out links) and FKs pointing out (`link.from_node_id` and `to_node_id` → `node`).
  - **Where the FKs come from:** the spec's foreign keys, the same ones validation uses, so nothing is hard-coded.
  - **Endpoint:** a read-only `GET /api/n/{id}/{component}/related?table=&ids=`. It returns per-table counts, plus the ids on the visible page only, and is not recorded.
  - **Cost:** one DuckDB `fk IN (...)` per related table, one hop by default, with an explicit "expand a hop" option.
  - **UI:** count badges in the table rail, a "Filter to related" mode, and a "via <fk>" hint on related rows.
- **Validation:**
  - Run validation plus quality checks as a job, using the rule config from Settings.
  - The **Issues panel** links issue ↔ map marker ↔ table row (filter by severity, table or code).
  - Unlocated issues are listed separately.
- **Fixing:**
  - "Fix" opens the ported inline editor and dispatches `ApplyEdit`.
  - That becomes a `NetworkChange`, which `apply_change` executes and which is appended to the draft ProjectCard.
  - You get undo, the network version is bumped, and validation can be re-run.
  - A **Changes drawer tab** shows the draft card (its changes, existing → set values, and selection), with commit, export `.yml` and validate actions.
  - Importing a `map/edits` YAML is supported.

### c. Selection verification workspace
- Enter an utterance or pick links (click or box) to get a `SelectionResult` view:
  - The path is highlighted.
  - From/to anchors are shown with gore/merge kind and confidence.
  - Diagnostics are listed.
- **Ambiguous anchors** list their candidates. Choosing one re-resolves with that anchor pinned.
- Export a fragment (resolved or query form), a ProjectCard, or run schema validation.
- Selections can be named and saved in the session ("Save selection…"), so Python and Compare can use them. "Save" is reserved for this; building a selection on the map is "Highlight links" → "Set as selection".

### d. Python: shared live session
- **In a notebook:** `app = netstead.app(net)` starts uvicorn in a background thread, in the same process, on the same `Network` objects. The handle offers:
  - `app.networks["base"]`
  - `app.selection` (the live `SelectionResult` or ids)
  - `app.show(frame_or_ids, style=...)`, which pushes a temporary layer
  - `app.do(action)`
  - `app.history.to_python()`
  - `app.refresh()`
  - `app._repr_html_`, which embeds the app in an iframe
- **From the CLI:** `netstead app` serves the same app.
- **Optional console panel:** `--console` adds a Python console that runs code in the server namespace, with `app`, `net` and `nets` bound. For safety it is only offered on a localhost bind (reusing `is_public_bind`) and needs a per-launch token. Output is text, or `_repr_html_` when an object has one.
- **Pyodide/WASM was rejected.** The DuckDB/ibis stack and the network data live on the server, and Pyodide can't share the live objects.

### e. Natural-language assistant
- A chat drawer plus a command bar.
- The LLM sees the Action schemas as tools and works from scoped grounding: the loaded networks, the GMNS fields, and the current selection.
- Read-only actions apply immediately.
- Mutating actions (`ApplyEdit`, `BuildNetwork`, `SetSetting`) appear as a draft card that you approve.
- Each step echoes what changed.
- If the LLM's output fails schema validation, it is re-prompted with the error.
- The provider and model come from Settings: `stub` or `claude` (the existing `ClaudeParser` pattern, default `claude-sonnet-5`).

### f. Compare workspace
- `netstead.compare.diff_networks(a, b, keys=("link_id", "node_id"), columns=None)` is a DuckDB join that returns a `NetworkDiff`:
  - per-table `status` (added, removed, modified, same)
  - per-column deltas
  - summary counts
- Diffing is a data step on the server, per PRD §5.8.
- **UI:**
  - Pick A and B.
  - Summary chips per table.
  - Map overlay in green, red and amber, with unchanged features muted.
  - Toggle between A, B and diff.
  - A table of changed rows with before/after values.
  - Validation issue deltas: issues new in B, and issues fixed in B.
- **Later:** swipe or side-by-side views, and geometry-based matching for networks with different ids, such as OSM vs Overture. That needs conflation, which v1 does not do; v1 matches on ids.

### g. Settings workspace
`netstead/config.py` is a new layered `Settings` pydantic model. Precedence is:

**defaults < user file < project file < `NETSTEAD_*` env < explicit kwargs**

- The user file is `~/.config/netstead/config.toml`; the project file is `./netstead.toml`.
- Reading uses `tomllib`. Writing uses a small hand-rolled flat TOML writer, so no new dependency.

Sections:
- `io`: `spec_version`, default format, allowed roots
- `engine`: DuckDB threads and memory limit
- `osm`: endpoint, user_agent, timeout, retries, and override paths for the mapping YAMLs
- `overture`: release, data_root
- `validation`: a `RuleConfig` per rule (enabled, severity override, thresholds)
- `select`: provider, model
- `viz`: basemap, palettes
- `app`: bind, port, console
- `credentials`: only the names of hosts that use keyring; secrets are stored with `keyring` and never written to TOML

**UI:**
- The form is generated from the model's JSON schema.
- Each value shows where it came from (default, user, project or env).
- You choose whether to save to the user or project scope, and each change goes through a `SetSetting` Action.
- Credentials can be set in keyring and tested, but are never displayed.

**Wiring:** existing defaults are read from `Settings` instead of being hard-coded in:
- `spec.DEFAULT_SPEC` users
- the build kwargs
- `ClaudeParser`
- `run_quality` config
- the viz basemap

This also fixes the stale `NETSTEAD_AUTO_APPROVE` vs `CORRAL_AUTO_APPROVE` docstrings.

## Phasing (each phase gets its own spec → plan → PR off `main`)

| Phase | Scope | Builds |
|---|---|---|
| P0 | Foundations | `config.py`; the `workbench/` package with Session, Registry, Action bus and SSE (background jobs arrive with Build in P1); split the front end into ES modules; port the viz features; `netstead app` CLI; retire `select-serve` (`netstead viz` becomes an alias) |
| P1a | Open/Import wizard | Wizard (local browser, URL check, OSM/Overture by draw/coords/place, local .osm/Overture files), estimate + approval, background jobs, always-save builds |
| P1b | Inspect + settings |  map↔table linking both ways, FK navigation, Settings UI |
| P2 | Validate + fix + change log | Issues triad; `netstead.changes` (NetworkChange, `apply_change`, DraftCard, ProjectCard export and schema validation); editor built on it; undo; export report |
| P3 | Selection verification + NL assistant | Pinning ambiguous anchors; Style/Filter/Navigate actions |
| P4 | Python live session | `netstead.app()` handle, `show`, history `to_python`, `--console` |
| P5 | Compare | `diff_networks` + Compare workspace |
| P6 | Transit | corral GTFS spec, `TransitFeed` component, transit layers, selection, validation, changes, compare |
| Later | Apply cards; scenarios | `apply_card`, then `Scenario(base, cards[])` built on the lineage, and diff → cards |

P1b plan: [2026-10-05-workbench-p1b-plan.md](2026-10-05-workbench-p1b-plan.md); decisions on click = focus, POST for id lists, and one-hop relations are recorded there.

P2 plan: [2026-10-07-workbench-p2-plan.md](2026-10-07-workbench-p2-plan.md): validation and direct table edits in core
(live warnings, cascading deletes, undo, Save a copy), built on plugins Parts 1 and 2. ProjectCard editing is the cards
plugin's ([scope](2026-10-07-cards-plugin-scope.md)).

Next: the P0 implementation plan (`docs/design/2026-10-02-workbench-p0-plan.md`).

## Verification
- **Unit tests:**
  - `Settings` precedence and round-trip
  - Action schemas and `to_python` snippets
  - `Session.dispatch` per Action
  - `diff_networks` on fixture networks with known adds, removes and modifications
  - Selection anchor pinning
  - `apply_change` round-trip: apply, then reverse, gives the original table
  - Draft card export validates against the projectcard schema
  - Exported cards load with `projectcard.read_card`, as an interop check in the `[projectcard]` extra tests
- **API tests:** FastAPI `TestClient` for `/api/n/{id}/...`, `/api/actions` and the SSE stream (receive an event after a dispatch). A build job runs with mocked OSM/Overture fetchers.
- **End-to-end:**
  - Run `netstead app <fixture network>` through `preview_start`.
  - In the browser pane:
    - open a network
    - check that row click flies the map and map click scrolls the row
    - run validation and click an issue to see its marker and row
    - apply a fix, undo it
    - run an NL select (stub provider) and approve a draft
    - compare the fixture with an edited copy
    - change a setting and confirm the TOML file is written
- **Notebook smoke test:** `app = netstead.app(net)`; `app.show(ids)` updates the open browser over SSE; `app.selection` reflects a UI pick.
