# Changelog — netstead

All notable changes to the `netstead` package. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

This file is for the **netstead** package only. The underlying generic engine `corral` keeps its own CHANGELOG at [`packages/corral/CHANGELOG.md`](../corral/CHANGELOG.md).

## [Unreleased]

### Security

- **`netstead app` refuses to expose your home folder.** A non-loopback host
  (`--host 0.0.0.0`, `NETSTEAD_APP__HOST` or a config file) with an empty
  `io.allowed_roots` (which means the home folder) now exits with code 2 and
  explains how to set the roots. Before, anyone on the network could browse the
  home folder through the Workbench file browser. Sources named on the command
  line are still allowed for the session but don't satisfy the check. Loopback
  spellings the Host-header guard doesn't cover (`127.0.0.2`, `LOCALHOST`,
  `[::1]`) are refused the same way, with their own message. A blank
  `io.allowed_roots` entry (`[""]`, which resolved to the current folder) is now
  a settings error.

### Fixed

- **The Workbench map draws link shapes kept in the `geometry` table.** A
  network whose links carry a `geometry_id` into a separate `geometry` table
  (the bundled Leavenworth network, for one) drew every link as a straight
  node-to-node line. `viz.buffers.pack_network` now takes the optional
  `geometry` frame and resolves shapes in GMNS order: an inline `link.geometry`,
  else the `geometry_id` row, else the straight from/to segment.
- **Workbench map: a legible focus colour, and direction arrows that follow
  their offset line.** The focused link is now a saturated magenta over a dark
  casing (it was white: invisible on the light Positron basemap and over white
  highway casings); highlights keep their cyan and get the same casing. With
  "Offset directions" on, each link row's arrow now sits on that row's own
  offset line instead of the centreline, so a two-way street shows one arrow
  per direction rather than two crossed into an X. Arrows sit at the path's
  midpoint by length (not its middle vertex), have a dark outline, and grow
  from 8 px at zoom 13 to 18 px at zoom 18. The selection colour is still
  yours to set (`style.colors.selection`).
- **A Workbench build's engine failing to close no longer hides the result.**
  Since corral's `IbisEngine.close()` stopped swallowing errors, a failed
  disconnect in the build job's `finally` (or the estimate's) replaced the
  build's real error and its estimate payload, or failed a build whose output
  was already written. `build.close_quietly` now logs it as a warning.
- **Quality rules: review follow-ups for #246.**
  - `quality.high_speed_residential` again ignores every character Python's
    `str.strip()` removes around `facility_type`, including `\v`, NBSP and other
    Unicode spaces. The pushdown regex had narrowed this to ASCII whitespace.
  - Whole-number float ids print as ints again: `link_id=1`, not `1.0`. A NaN
    `node_id` no longer counts as a node in `quality.duplicate_near_nodes`.
  - `quality.overlapping_links` reports each pair once when the `geometry`
    table repeats a `geometry_id` (it uses the first row). Its docs now say it
    matches shared segments exactly, with no tolerance, and can get slow when
    many bounding boxes overlap.
  - `quality.non_unique_combination`:
    - The defaults now include `signal_timing_phase (timing_plan_id,
      signal_phase_num)`.
    - `DEFAULT_UNIQUE_COMBINATIONS` is read-only (a `MappingProxyType` of
      tuples).
    - A user-configured combination naming a missing table or column is logged
      as a warning (logger `netstead.quality.rules`) instead of being skipped
      silently. It is not an issue, so `severity_override=ERROR` never turns a
      missing optional table into a failure. Missing tables in the defaults
      stay silent.
    - A `combinations` value of the wrong shape raises a `ValueError` that
      shows the expected form.
  - `validation.rules` settings reject a negative or non-numeric
    `epsilon_units` and a malformed `combinations` when they load, so
    `SetSetting` refuses the value up front instead of one bad threshold
    breaking the whole validation run. The rules keep the same check at run
    time for configs built in code.
  - `quality.duplicate_near_nodes` raises `ValueError` for a negative
    `epsilon_units`. It used to skip the check silently.
  - A seeded property test checks the grid self-join against a pairwise scan
    near large negative and positive origins.
  - The docs index, quickstart and `gmns-validate` skill list the nine real
    rules. The skill used to name three codes that don't exist.
- **The Leavenworth fixture's `datapackage.json` is self-contained.** Each
  resource now carries its resolved GMNS 0.97 schema inline instead of a
  `../../spec/0.97/...` path, so the descriptor loads wherever it is copied,
  including the copy inside `leavenworth.csv.zip`, which could not resolve
  any schema before (and still described the package as gmnspy's). A test
  fails if an inlined schema drifts from `spec/0.97/`;
  `scripts/descriptor.py` regenerates the descriptor and zip without an
  OpenStreetMap fetch or the `dev-fixtures` extra. (#127)
- **The offline report's "Fix locally" edit log is a schema-valid ProjectCard.**
  `netstead.map.edits.dump_edit_log` and the browser download (`buildYaml` in
  `map_component.js`) put the facility under `links` / `nodes` with
  `ignore_missing: false` (the ids were clicked), and move each change's reason,
  issue id and edit id from a per-change `notes` (which the schema forbids) to a
  `netstead.note[<i>]: ...` line of the card's `notes`, the convention the
  project-cards plugin reads. Both writers fold control characters and line
  breaks in a note the same way. `load_edit_log` / `apply_edits` read both the
  new shape and logs written before it, split a note back into reason, issue id
  and edit id (so a dump → load → dump round trip writes the same file), and
  yield no edits for a selector that is not ids alone (ids narrowed by another
  key, or a `from` / `to` segment). netstead core still does not depend on
  `projectcard`; the tests validate with it when it is installed.
- **Validation and network HTML reports no longer show "API KEY REQUIRED" tiles when
  zoomed in.** CARTO now watermarks its raster basemap without an API key, and a key
  cannot be added: the report is a static, shareable file, so the key would ship inside
  it. The default `tile_provider` is now `"esri"`, the same keyless Esri light-grey
  canvas the Workbench offers (upscaled past its native zoom 16); `"openstreetmap"`
  remains the alternative and `"carto-positron"` is gone.
- **The `netstead` CLI starts without the `[nl]` extra.** `netstead.select.emit`
  imported `jsonschema` at module level and the CLI imports the `select` command
  group eagerly, so on a `netstead[clean,graph]` install every command, including
  `netstead doctor` and `netstead info`, died with `ModuleNotFoundError: jsonschema`.
  The import is now deferred to `validate_fragment`, which raises the standard
  `requires the [nl] extra` message when called without it.
- **Colab quickstart notebook runs again.** Its load cell pointed at a raw GitHub
  *directory* URL, which `Network.from_source` cannot detect a format for; it now
  fetches the fixture's zipped-CSV bundle. Its install cell installs the beta from
  GitHub (nothing is published on PyPI under the new names yet). A contract test
  now executes the notebook's code cells.
- **`netstead.fixtures.leavenworth.summary()` reports the real row counts** (121
  nodes, 339 links, 429 lanes; the module still said 75 / 214 / 280 from an older
  600 m build). A test pins the metadata to the bundled files.

- **`netstead.clean` geometry ops on networks that keep shapes in the `geometry`
  table.** `simplify_geometry` now edits whichever table holds the link shapes
  (`link.geometry` inline, else `geometry` via `link.geometry_id`; inline wins),
  so `netstead clean simplify-geometry` works on the Leavenworth fixture instead
  of failing with "requires an inline 'geometry' column". The edit and its
  rollback target that table; check `result.edit.table`. `recompute_lengths`
  resolves each link's shape through `assemble_link_geometry` (inline → geometry
  table → node endpoints) and still writes `link.length`. A network with shapes in
  neither place raises a `CleanError` naming both locations.

### Added

- **Workbench: Redo, and icon buttons with tooltips.** A new `redo_edit` Action
  (`RedoEdit`, replayed as `app.do(RedoEdit())`) re-applies the edit the last
  `undo_edit` took back; the session state's `edits[<net>]` gains `redo_count`,
  and any other change to the network empties the redo stack. The Edits panel
  has Undo and Redo icon buttons (a disabled one's tooltip says why); the header's
  Commands, Settings and Jobs controls, the Issues panel's Run validation and
  Export report, and the footer's Session as Python are icon buttons with
  tooltips (inline SVG from `static/js/icons.js`); the model picker's tooltip
  shows the full model label; the empty "No edit warnings." text is gone (the
  count says so).
- **Validation output reads at a glance** (2026-10-20 beta feedback).
  `netstead validate` opens with a severity summary line
  (`0 errors · 1 warning · 16 info`), names the source once, and folds the
  sixteen `structural.missing_optional_resource` findings a partial network
  produces into one row (`16 tables` / `optional resources absent: …`) so the
  `fk.unverifiable` WARNING is no longer buried; `--all` expands them. The
  table keeps every code readable at a narrow terminal width. The `--html`
  report gains a "Resources" table (resource · required/optional · present
  ✓/✗ · rows) above the findings, its "Unlocated findings" block is a
  `<details>` collapsed by default with the count in the summary and grouped
  codes on one line, and the `package source '…': ` lead-in is dropped from
  every message. Its page title is now `validation report` with the source in
  the subtitle. `--json` is unchanged.
- **Quality rule `quality.overlapping_links`** ([#5](https://github.com/e-lo/netstead/issues/5)).
  Flags pairs of links whose shapes share a stretch longer than
  `min_overlap_units` (default `0`, in CRS units), such as a link drawn on top of
  part of another. Links that meet at a node, cross, or have the identical shape
  (two-way twins) are not flagged. Reads inline `link.geometry` or the `geometry`
  table via `geometry_id`; needs shapely (`[clean]`). One WARNING per pair with
  `extra` `link_ids`, `rows` and `overlap_length`. Candidate pairs come from an
  STRtree box query cut down in numpy before the exact test: 0.2 s on a 100k-link
  network.
- **Quality rule `quality.non_unique_combination`** ([#7](https://github.com/e-lo/netstead/issues/7)).
  Flags rows that repeat a column combination that should be unique, such as two
  `link_tod` rows for the same `link_id` and `time_day` (GMNS 0.97 gives the
  time-of-day tables a surrogate key, so spec validation lets these through). It
  checks every time-of-day table plus `lane` / `segment_lane` lane numbers by
  default; the `combinations` threshold replaces that list. One WARNING per
  repeated combination, with its row positions in `extra["rows"]`. Grouping runs
  in DuckDB (0.03 s on a 100k-row `link_tod` plus a 200k-row `lane`).
- **`Network.from_source(url, credentials=...)`** forwards explicit cloud
  credentials (fsspec storage options) to `Package.from_source`, as the
  [Read from S3](https://e-lo.github.io/netstead/corral/cookbook/read-from-s3/) cookbook describes.

- **Plugin Host API 1.6** (the cards plugin's follow-ups):
  - Storage refusing an edit (corral's `EditingError`, an Arrow, ibis or
    DuckDB error) while `Session.mutate` applies it to the draft is an
    `ActionError` ("the edit failed: <type>: <message>"), and refusing a
    reverse in `Session.revert` is "the undo failed: ...". The message is
    scrubbed of URL credentials and key-shaped tokens (it reaches history,
    plugin routes and the assistant); the original is logged. A failure after
    the edit was applied is not reported as a failed edit. This covers
    `host.mutate`, `host.undo` and core's edit Actions alike. **Breaking** for
    Python callers of `Session.mutate`, which used to get corral's exception.
  - Stable edit ids: `host.mutate` returns a frozen `Mutation(results, seq)`
    (exported). **Breaking**: it used to return the list of `EditResult`s.
    `host.pending_edits(net_id)` returns a frozen `PendingEdits(issued, edits)`
    (exported), read in one moment: `edits` are the pending edits' views (`seq`,
    `source`, `note`, `version`, `rows`, `saved`) and `issued` the newest seq
    handed out. `host.undo()`'s `seq` names the edit it reversed. A network
    id's seqs are unique for the session: not reused after a rolled-back Action
    spent one (the ledger used to hand it out again), nor after the network is
    closed and reopened. The Edits panel's seqs can have gaps after a rollback.
  - `host.on_rollback(fn)`: a handler registers a callback that core calls when
    its Action is rolled back, including after it succeeded inside an Action
    that failed later, so the plugin can put back its own state and files. A
    callback that raises is logged and the rollback goes on (a
    `BaseException` is re-raised once it has finished); `host.mutate`,
    `derive`, `undo`, `dispatch` and `on_rollback` refuse to run from a
    callback.
  - `assistant_compact: ClassVar[bool] = True` on a `mutates` Action offers it
    in the assistant's compact tool set (a local model's), and `GET
    /api/actions` entries carry it. Core's everyday edits (`edit_cells`,
    `delete_rows`, `undo_edit`) carry the flag, in place of a hard-coded list;
    what core offers is unchanged.
- **Plugin Host API 1.5:** `ActionError` and `NotSupportedYet` are exported from
  `netstead.workbench.plugins`, so a plugin handler can refuse with a message the
  user sees (anything else it raises is still an internal error).
- **Workbench plugins** (#222, #223). Plugins are pip packages discovered through
  the `netstead.workbench.plugins` entry-point group: a `WorkbenchPlugin` with
  namespaced Actions (`ActionSpec`), a router at `/api/plugins/<id>`, a static
  front end, settings under `[plugins.<id>]` and state under `plugins.<id>`,
  programmed against a versioned `Host` (API 1.0 Python core; 1.1 browser slots:
  `activate(wb)`, workspace tabs, dock panels, commands in the palette and
  context menus, a map-layer registry and `schemaForm`). Core Actions moved onto
  the same registry (one dispatch path; "copy as Python" output unchanged).
  **Settings → Plugins** lists each plugin with its state and an on/off switch;
  a plugin that fails to load is isolated, never fatal. Ships the `hello`
  example plugin and the `netstead.workbench.testing` fixture.
  - Cookbook: `workbench-plugins.md`.
- **Workbench: validate and edit** (P2, #225). **Run validation** as a job with
  the rule config from Settings; an **Issues** panel linked to map markers and
  table rows, **Fix** on an issue, and **Export report**. Direct table edits:
  `EditCells` (live warnings re-checked against the spec), `DeleteRows` (with a
  cascade dry run), `AddRows` (Python only), `UndoEdit`, and **Save a copy**
  (`SaveNetwork`, never overwriting the source). Derived networks carry lineage.
  Plugin Host API 1.2: `host.undo`, `host.edit_warnings`, `plan_update` /
  `plan_delete` / `plan_add` and version-checked `host.mutate`.
- **Workbench: verify a selection** (P3a). A **Selection** dock panel in every
  workspace (the selection's status, anchors, diagnostics, export and saved
  selections moved out of Details).
  - **Pinning:** an ambiguous anchor lists its candidate nodes, drawn on the map
    (**Anchor candidates**); `PinAnchor(role, node_id)` pins one (or the
    resolver's own choice) and re-resolves. A pin that resolves nothing is
    refused. Pins are intent fields (`from_node` / `to_node`), never offered to
    the LLM. When an edit takes a pinned node off its facility, the selection
    keeps its previous links and says so (`stale_pins`, a **Stale pin** warning).
  - **Saved selections:** `SaveSelection`, `UseSelection`, `DeleteSelection`
    (session state, replayed with the history; at most 50; tied to the network's
    source locator).
  - **Export** (`GET /api/selection/export`): a resolved selection, current or
    saved, as a GMNS fragment (schema-checked) or a ProjectCard facility object,
    in resolved or query form, with `warnings`.
- **Workbench: Style, Filter, Navigate.** `Style(width_by=…)` sizes links by a
  numeric column (**Map display → Width by**); `Filter(where=[…])` is a recorded
  predicate over link fields (no SQL, no regex) that dims the rest of the map
  and drives the table's new **Filtered** scope (**Layers → Filter links**);
  `Navigate(record={table, id})` flies to one link or node.
- **Plugin Host API 1.3:** `host.saved_selections()`, `host.saved_selection(name)`.
- **Workbench: the natural-language assistant** (P3b). An **Assistant** dock
  panel in every workspace, plus **Ask** (Ctrl+Enter / ⌘Enter) in the header box
  and **Ask the assistant…** in the palette; Enter still selects. One
  conversation per session, never written to disk.
  - **Tools:** the allowed Actions (`assistant.actions`; plugins opt in with
    `assistant.plugins`) as JSON-schema tools, one model call per question.
    Read-only steps run at once; the first step that changes a network and every
    step after it form one **draft** (Approve / Reject / Edit…), re-checked
    against each network's version when approved, with each edit pinned to the
    version the step before left. A read-only Action the assistant runs can't
    change a network through `host.dispatch`, `host.mutate` or `host.undo`, nor
    open one with `host.derive`.
  - **Small local models:** a read-only call's invented or (on `style`)
    malformed keys are left out instead of failing the answer, and listed under
    it; a key another offered read-only tool owns (`color_by` on `select`)
    becomes a call of its own to that tool, listed as split (two owners, or a
    mutating owner, go back for repair); mutating calls are never changed. The grounding carries the GMNS meaning
    of each link column (`free_speed` = the speed limit). With
    `assistant.compact_tools` (`auto`: on for a local endpoint) a model is
    offered only the read-only tools that apply now plus edit, delete and undo.
    qwen2.5:7b on the eval: 27/45 (60%) -> 39/45 (87%).
  - **Privacy tiers** follow the existing gates: structure always; street names,
    selected ids, issue counts by rule and column values only where grounding is
    on (by default a local model); never table rows or Action results. The panel
    lists exactly what is sent.
  - **Budget:** `assistant.remote_token_budget` (200,000 tokens a session across
    every remote provider and model); local models are counted, never refused.
  - An offline **stub planner** (`select.provider = "stub"`) for tests and demos.
  - Every Action it runs is recorded with `via: "assistant"`; "copy as Python"
    replays it with no model.
- **`netstead.llm.structured.request_tool_calls`:** several tools, none forced,
  every call validated, a bounded repair loop, and a JSON-mode envelope for
  models without tool calling.
- **`Select(query=…)`:** a selection intent given directly, replayed exactly with
  no model.
- **`assistant.*` settings:** `actions`, `plugins`, `max_steps`, `history_turns`,
  `max_turns`, `output_token_limit`, `context_max_chars`, `remote_token_budget`,
  `compact_tools`;
  **Settings → Assistant: actions & plugins** switches each Action and plugin.
- **`EditCells.expected_version`** (left out of copied scripts, like
  `DeleteRows`'s).
- **`Session.consistent()`:** hold the session lock for a block of related reads.
- **Plugin Host API 1.4:** `BaseAction.assistant = False` keeps an Action out of
  the assistant.
- **`scripts/eval_nl_assistant.py`:** a hand-run eval of multi-step planning
  accuracy on Leavenworth (nothing dispatched).

### Changed

- **The quality rule pack filters in DuckDB instead of materialising tables**
  ([#181](https://github.com/e-lo/netstead/issues/181)). Each rule now writes its
  predicate as an ibis expression and fetches only the offending rows, so findings
  are unchanged (a golden test pins every field on Leavenworth, RDU I-40 and a
  synthetic edge-case network) but a 100k-link network checks much faster:
  `duplicate_near_nodes` drops from 75 s to 0.04 s (an O(n²) Python scan became a
  grid self-join), and the other table rules are 7–25× faster.
  `sharp_angle_bends` still parses geometries with shapely, now vectorised and
  reading only `link_id` and `geometry`. One visible difference: a message quotes
  a measured value as stored, so a capacity column of whole-number floats shows
  `1000.0` where it used to show `1000`. Ids are the exception: a float
  `link_id` / `node_id` column holding only whole numbers (a CSV id column with a
  blank cell) still prints `link_id=1`, in messages and `extra`, and a NaN id
  prints `None`. The "write your own rule" cookbook example now shows the
  pushdown pattern.
- **`CloseNetwork.mutates` is `True`** (it throws away unsaved edits). It is never
  offered to the assistant either.

### Fixed

- **Map:** the inline JSON payload is escaped so network data cannot close the
  script block (XSS) (#224).
- **`netstead select` on an ambiguous selection** no longer crashes with
  `SelectError: cannot emit a fragment from status 'ambiguous'`. It now prints
  the status, the provisional link ids, each anchor with the other nodes it
  could be (`candidates`), and diagnostics, and exits `3` (`1` stays
  not-found, `2` invalid settings). Without `--json`, every status now renders
  as a table instead of failing on a missing `json_out` argument.
- **Selection anchors on a network without a `ref` (or `name`) column** no
  longer fail with `KeyError: 'ref'` (Leavenworth, for example).
- **Assistant approval on a derived copy:** a draft that touches a derived
  network (`derived_from`, such as a plugin's preview) also checks the copy's
  base, while the base is open. Approving is refused as stale once the base
  moved, so a plugin that applies the change to the base no longer overwrites an
  edit made there since the draft.
- **`netstead clean <op> <src>` without `--dest` no longer destroys the
  source.** Writing the edited network back over its own directory removed
  the old files before the unedited tables (still read lazily from them) were
  written, leaving only the edited table behind and a DuckDB "No files found"
  error. The write now stages beside the target and swaps in once complete
  (fixed in corral's `Package.write`), and an in-place write keeps the
  directory's format: a CSV directory stays CSV instead of becoming parquet.

## [1.0.0-beta.2] — TBD

First release under the **netstead** name. The package was renamed from
`gmnspy` (and its engine from `datagrove` to `corral`, PyPI dist `dbcorral`)
on 2026-10-05; `import gmnspy` → `import netstead`, `gmnspy <cmd>` →
`netstead <cmd>`. The `gmnspy` name on PyPI stays frozen at the v0.3.x line
(the 1.0.0b1 preview below was yanked).

Second public preview. The big theme: the **Workbench** (`netstead app`), one
local front end to open or build a network, select parts of it in plain
language, and inspect it on a linked map and table. Also new: an embeddable
map with an **edit log** for triaging findings and replaying the fixes in Python.

### Added

- **Workbench — `netstead app`** (#211, #212). A local web app over one typed
  Action bus (`Session.dispatch`) with history and "copy as Python", live
  updates over SSE, background jobs and a network registry. Front end is
  no-build ES modules on MapLibre + deck.gl.
  - **Open / Import wizard:** open local or remote networks, or build one from
    OSM or Overture by drawn or typed bbox or place search. A server-side file
    browser is confined to `io.allowed_roots`; builds estimated over 90 s ask
    for approval.
  - **Settings dialog** generated from the settings schema; each value shows
    its source (default / user / project / env / session) and saves to the
    layer you pick. Settings themselves are layered (`netstead.config`).
  - **Linked map and table:** a map click scrolls to the row, a row click
    flies the map, an FK cell jumps to the referenced row. Scope menu: All /
    Selection / Highlighted / Related.
  - **FK related records** from the spec's foreign keys, computed in DuckDB and
    tinted on the map and in other tables.
  - Cookbook: `workbench.md`.
- **Multi-provider natural-language selection** (`[nl]` extra, #211).
  Anthropic, OpenAI, Gemini or a local Ollama model through hand-rolled
  `httpx` adapters (no vendor SDKs). API keys come only from environment
  variables or the OS keyring and never appear in settings, history, logs or
  responses. Network vocabulary goes only to local endpoints unless you opt
  in. Local Ollama works with no configuration. New `netstead llm`
  commands: `status`, `set-key`, `remove-key`, `test`, `models`, `pull`.
  Cookbook: `local-llm-ollama.md`.
- **`netstead.select` — natural-language selection** (#194). Turns an utterance
  like *"I-40 EB between Harrison Ave and NC 54"* into a validated, repeatable
  set of GMNS `link_id`s via in-network topological traversal (no geocoding).
  `netstead select` CLI. Selection falls back between a facility's `ref` and
  `name` when one misses.
- **`netstead.viz` — binary render buffers, styling and paged DuckDB table
  access** (#194) used by the Workbench: deck.gl layers fed from Parquet without
  GeoJSON, attribute styling, and paged / sorted / filtered table reads.
- **`netstead.overture` + `netstead build --source overture`** (#196) — build a
  GMNS network from Overture Maps, mirroring the OSM pipeline. Behind the
  `[overture]` extra.
- **`netstead.bench` benchmark harness** (#197) — time + peak memory for
  network build, selection and viz packing, with a programmatic API alongside
  the CLI.
- **`netstead.map` module** — embeddable interactive network viewer.
  - `NetworkMap` — reusable component class. Any host page (validation
    report, notebook, custom dashboard) can include shared head assets
    (`NetworkMap.head_assets()`) once and drop in one or more per-instance
    body fragments (`instance.body_fragment()`). Each instance carries a
    UID so multiple maps coexist on one page.
  - `NetworkMap.to_html()` — full standalone HTML doc.
  - `NetworkMap._repr_html_()` — iframe-isolated Jupyter render.
  - `netstead.map.render_network_html(net, issues=None, ...)` — thin
    convenience over `NetworkMap.to_html()`.
  - `netstead.map.render_validation_html(net, report, ...)` — full
    validation report page composing a NetworkMap with the findings
    table.
  - Toggleable layers (links, nodes; extensible for zones / segments /
    movements). Hover tooltips on every element show its GMNS row
    columns. Popups on findings carry `Fix locally` / `Fix upstream
    (OSM) →` / `Show error in table`.
- **Edit log Phase 1** — `netstead.map.edits`:
  - `Edit`, `EditLog`, `AppliedEdit`, `SkippedEdit`, `ApplyResult`
    dataclasses.
  - `load_edit_log(path)` / `dump_edit_log(log, path)` — YAML round-trip.
  - `apply_edits(net, log) → ApplyResult` — deterministic replay with
    PK-indexed row lookup, drift check (`from_value` vs current), and
    per-edit `skipped` reasons.
  - **On-disk format is `network-wrangler` ProjectCard.** Each session
    serialises to a single ProjectCard with `changes[]` of
    `roadway_property_change` entries. GMNS `link_id` maps to
    `model_link_id` in the facility selector; per-cell values live under
    `property_changes.<column>.{existing, set}`. Node property changes
    emit with the same shape via `model_node_id` (netstead extension —
    standard ProjectCard has no first-class node-property-change type).
- **CLI output flags** on `netstead validate`:
  - `--html` writes the new map+table viewer (gracefully falls back to
    the corral table-only HTML when `[reports]` is missing, so
    `--html` always produces a file).
  - `--csv` writes a flat findings table.
  - `--xlsx` writes a single-sheet workbook (requires `[reports]`).
- **`netstead.osm` deep-link helpers** — `osm_edit_url(osm_id, *, kind,
  editor)`, `issue_osm_edit_url(issue, network, *, editor)`. Now
  detection is column-based (`osm_way_id` on link, `osm_node_id` on
  node), so ANY network carrying those columns gets Edit-in-OSM
  links — regardless of whether it was built via `netstead.osm.build`.
- **Bundled Leavenworth fixture rebuilt.** Built from the full OSM city
  polygon via `osmnx.graph_from_place("Leavenworth, Washington, USA")`
  instead of a 600m centroid buffer. Every link row now carries
  `osm_way_id`, every node row `osm_node_id`, so Edit-in-OSM works on
  the bundled fixture out of the box.
  - Row counts: node 75 → 121, link 214 → 339, lane 280 → 429.
  - Total fixture size ~700KB → ~2.7MB (still under the 5MB wheel cap).
- **New cookbook recipes**:
  - `view-your-network.md` — the standalone map, no validation.
  - `fix-findings.md` — the full triage → YAML → apply → save flow.

### Changed

- **`netstead viz` and `netstead select-serve` open the Workbench** (#212). The
  standalone viewer and selection prototype apps they used to start are removed.
- **`[server]` requires `fastapi>=0.132`**, whose strict JSON content-type
  handling the Workbench's request guard relies on.
- **Geometry is WKB in memory** (#201). CSV stays WKT on disk; Parquet stores
  WKB with GeoParquet `geo` metadata (#202).
- **DuckDB is the only compute engine** (via corral, #195). `engine="pandas"` /
  `engine="polars"` are gone; pandas / polars / Arrow are I/O formats.
- **`netstead.osm.__init__` is now lazy.** Importing the package no longer
  eagerly requires the `[osm]` extra. `osm_edit_url` /
  `issue_osm_edit_url` are dep-free helpers and load without
  `requests` / `pyyaml`. `build_network_from_osm` /
  `network_from_records` still need `[osm]` and surface the same
  helpful `ImportError`.
- **`[reports]` extra now includes `pyyaml>=6`** for the edit-log
  YAML round-trip.

### Deprecated

- `netstead.reports.render_network_html` / `render_validation_html` —
  moved to `netstead.map`. The old imports still work via re-export but
  emit a `DeprecationWarning`. `netstead.reports.write_findings_csv` /
  `write_findings_xlsx` stay put.

### Security

- **Map and validation report pages keep data values inside their JSON.** The
  per-map payload is embedded in a `<script>` block; a finding message or a
  street name holding `</script>` could end that block and run as markup. The
  payload now escapes `<`, `>`, `&`, and the U+2028/U+2029 line separators
  (`netstead.map`, used by the offline report and the Workbench's report
  download).

### Known limitations

- `netstead app --host 0.0.0.0` with an empty `io.allowed_roots` lets anyone on
  the network browse your home directory. Keep the default loopback host, or
  set `io.allowed_roots`.
- The default local model (`qwen3:4b`) has not been tested live; `qwen2.5:7b`
  has.

### Known limitations (edit log)

- Edit log only supports `kind: fix` (per-cell value changes). Row
  additions, row deletions, and schema changes are deferred. The YAML
  format reserves ProjectCard `kind: modification` for future use.
- No bulk-fix UI in the browser — bulk fixes go through Python
  directly.
- The map's marker layer has no clustering; past ~5k markers Leaflet
  will slow.

## [1.0.0-beta.1] — 2026-06-29

First public preview of GMNSpy v1.0, published to PyPI as `gmnspy==1.0.0b1` (pre-rename, since yanked; names in this section are as shipped). The entire codebase is a rewrite from v0.3.5 — there is no in-place upgrade path; see the [migration guide](docs/migration/v0.3-to-v1.0.md).

This is a **beta**: API surface is stable enough to build against and most user-facing rough edges have been smoothed, but we expect bug reports + small breaking changes before 1.0.0 GA.

### Headline changes vs v0.3.x

- **New architecture.** GMNS-specific code now sits on top of a generic Frictionless engine ([`datagrove`](https://github.com/e-lo/netstead/tree/main/packages/corral) (now `corral`)) shipped as a separate PyPI package. The intent: future spec toolkits (GTFSpy, etc.) reuse the engine.
- **Multi-version GMNS support out of the box.** Spec versions `0.95`, `0.96`, `0.97` ship side-by-side. `DEFAULT_SPEC = "0.97"`. Override per call: `gmnspy.read(..., spec_version="0.96")`.
- **Regional-scale performance.** Lazy ibis + DuckDB by default. Predicates push down to SQL. Validation and scope operations stream rather than materialising the whole network.
- **Three usage modes, one core.** CLI (`gmnspy <command>`), notebook (`Network._repr_html_`), and programmatic (`gmnspy.read`, `gmnspy.validate`) all share the same underlying objects.

### New features

- **`gmnspy.read(source)`** + **`gmnspy.validate(source)`** — the documented I/O front door. Accepts paths / URLs / loaded Networks.
- **`Network.from_source()`** with multi-version spec auto-load.
- **`gmnspy validate`** CLI with `--html <path>` for interactive single-file reports + `--spec` for version override.
- **`gmnspy quality`** — data-quality rule pack beyond spec compliance: high-speed-on-residential, disconnected components, lane-count mismatch, sharp-angle bends, implausible v/c, etc. Plugin-extensible via the `datagrove.quality.rules` entry point.
- **`gmnspy.scope`** — network-aware scope ops: `from_nodes`, `from_node`, `from_link`, `from_point`, `connected_component`, `from_zone`. Chainable. Returns a scoped `Network` with FK chain pre-filtered.
- **`gmnspy.osm` + `gmnspy build` CLI** — build a validated GMNS network from OpenStreetMap for a bounding box, place name (city/county), or a lat/lon point + buffer. Hand-rolled Overpass + Nominatim access (no `osmnx` / `osm2gmns` dependency); maintained tag-mapping data files; produces a self-describing network with a `config` table declaring units. Behind the `gmnspy[osm]` extra (`requests`, `pyyaml`).
- **`gmnspy.graph` + `gmnspy[graph]` extra** — scipy-CSR routing engine: connectivity (weak / strong components), isochrones, shortest paths, multi-source distance buffer, nearest-node snap. Builds from a `Network` or directly from parquet/duckdb/polars/in-memory sources. This is also the unified backend powering `gmnspy.semantics` + `gmnspy.scope` connectivity / network-distance ops (replacing the older `igraph`-based index).
- **`gmnspy[clean]` extra** — network editing with atomic rollback (`simplify_geometry`, `merge_close_nodes`, `remove_orphans`, `recompute_lengths`). Every edit returns an `EditResult` with diff + log entry; sessions stored as sidecar parquet.
- **`gmnspy[server]` extra** — self-hostable FastAPI server (`gmnspy server run`). Pluggable bearer-token auth. Ships `Dockerfile` + `docker-compose.example.yml`.
- **`gmnspy[mcp]` extra** — MCP server (`gmnspy mcp serve`) exposing read / describe / query / scope / validate / quality_check / edit_session tools to Claude Desktop / Claude Code.
- **`gmnspy[notebook]` extra** — interactive scope-builder ipywidget. (Basic `_repr_html_` ships in core.)
- **`gmnspy doctor`** — install diagnostic: Python version, extras installed, vendored specs, fixture loads, env vars.
- **`gmnspy bench`** — read/validate/connectivity timing.
- **Bundled Leavenworth, WA fixture** in four storage variants (CSV / parquet / DuckDB / zip-CSV) — see [`fixtures/leavenworth/README.md`](netstead/fixtures/leavenworth/README.md) for provenance.

### AI surface

- **`--json` on every CLI command** for tool-call loops.
- **`llms.txt` + `llms-full.txt` + `ai/api-index.json`** auto-generated from docs + docstrings.
- **Five Claude Code Skills** in [`skills/`](../../skills/) — `datagrove-validate`, `gmns-author`, `gmns-validate`, `gmns-convert`, `gmns-clean`.

### Internal architecture

- **Single graph backend.** All connectivity + network-aware-scope ops (`gmnspy.semantics.connectivity`, `gmnspy.scope`, `gmnspy.quality.rules` disconnected-components, `Network.build_indexes(graph=True)`) now run on a shared `gmnspy.graph.GMNSGraph` (scipy CSR), cached once per network. The earlier `gmnspy.indexes.GraphIndex` (igraph) wrapper is retired; the `igraph` dependency is dropped (`[clean]` no longer pulls it in). `SpatialIndex` (STRtree) is unaffected.

### Quality + process

- 1285 tests passing; coverage ≥ 85% on each package (gated in CI).
- Two automated contract tests assert every documented `gmnspy.X` / `datagrove.X` symbol AND every documented `gmnspy <cmd> --flag` actually exists — prevents doc-vs-code drift.
- Per-package CI matrix (Python 3.11 / 3.12 / 3.13 × Linux / macOS), import-linter contracts, raw-SQL lint, doctest-modules sweep, htmlproofer.

### Breaking changes from v0.3.x

**Everything.** v1.0 is a clean rewrite — no `_legacy/` shims, no DeprecationWarning bridges. The [migration guide](docs/migration/v0.3-to-v1.0.md) maps the old API surface to the new. The biggest moves:

- `gmnspy.read_gmns_network(path)` → `gmnspy.read(path)` (auto-detects format) or `Network.from_source(path)`.
- `gmnspy.schema.read_schema(path)` → `gmnspy.load_gmns_spec(version="0.97")`.
- All single-file imports under `gmnspy.{schema,validation,utils}` → moved to `datagrove.spec`, `datagrove.validation`, `datagrove.utils.markdown`.
- Pandas DataFrames are no longer the lingua franca — operations are lazy ibis expressions; call `.to_pandas()` / `.to_polars()` at the boundary.

### Known limitations going into beta

- `gmnspy.bench` is CLI-only — no programmatic API yet (tracked for v1.1).
- HTML report doesn't yet embed a Vega-Lite map view for geo-located issues.
- `gmnspy.clean` lacks `split_link_at_node` and `snap_to_reference` (tracked for v1.1).

### Compatibility

- Python 3.11, 3.12, 3.13.
- macOS + Linux (Windows community-supported only; CI doesn't cover it).
- Optional extras: `osm`, `graph`, `clean`, `server`, `mcp`, `notebook`, `all`.

[Unreleased]: https://github.com/e-lo/netstead/compare/gmnspy-v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/e-lo/netstead/releases/tag/gmnspy-v1.0.0-beta.1
