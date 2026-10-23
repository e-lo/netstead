---
title: Explore networks in the Netstead Workbench
audience: users
kind: howto
summary: Open one or more GMNS networks in a local browser app. The map and tables are linked, natural-language selection is built in, and every action can be replayed as Python.
---

# Explore networks in the Netstead Workbench

## When to use this

You want to look around a network interactively. That means panning the map, browsing tables, checking
what a selection phrase resolves to, and copying the exact Python that reproduces what you did.
For a single self-contained HTML file to send someone, see [View a network on a map](view-your-network.md).

## Quick start

New to the Workbench? [Quickstart: the Workbench](../quickstart-workbench.md) walks through open, select,
validate, fix, replay and save on the bundled Leavenworth network in ten minutes.

```bash
uv run netstead app ./my-network
```

Open <http://127.0.0.1:8850>. Open more networks with the **Open** (folder) icon in the header: it opens a menu with up to
three recent networks, then **Browse / import…** for the full wizard (see below). Switch between open
networks with the network picker. `netstead viz` and `netstead select-serve`
are aliases of `netstead app` (the old standalone apps they used to start are gone).

```bash
uv run netstead app ./base ./build --port 8900 --basemap esri --provider anthropic
```

The server binds `127.0.0.1` by default. While bound to a local host (`127.0.0.1`, `localhost`, or `[::1]`),
it only accepts requests whose `Host` header names that same local address, and it rejects any cross-origin
write (a POST from a page with a different origin, or flagged `Sec-Fetch-Site: cross-site`). `--host 0.0.0.0`
exposes it to the rest of the network — there's no authentication in front of it, so don't do that on a
shared network.

> **Exposing the Workbench needs explicit roots.** `netstead app` refuses a non-loopback host (from
> `--host`, `NETSTEAD_APP__HOST` or a config file) while `io.allowed_roots` is empty, because empty means your
> home folder and the server-side file browser described below would show it to anyone on the network. It
> exits with code 2 and says how to fix it. List the folders you want to share first, in `./netstead.toml`
> or your user `config.toml` (see [Which folders the app can read and write](#which-folders-the-app-can-read-and-write)), or
> for one run with `NETSTEAD_IO__ALLOWED_ROOTS='["/data/networks"]'`. Paths you name on the command line
> are allowed for that session, but they don't count as explicit roots. Blank entries such as `[""]` are
> rejected (a blank path would mean the current folder). Other loopback spellings, such as `127.0.0.2` or
> `LOCALHOST`, are refused too while the roots are empty: the Host-header check above only covers
> `127.0.0.1`, `localhost` and `::1`, so use one of those.

> **Plugins.** Installed [Workbench plugins](workbench-plugins.md) are full-trust Python running inside
> this server, and an exposed bind exposes their routes and Actions too. Install only plugins you trust,
> and turn one off with `[app] disabled_plugins = ["<id>"]`.

## Open or import a network

**Open → Browse / import…** opens a wizard with four sources:

- **GMNS on this machine**: a server-side file browser. Entries are tagged (GMNS folder, `.zip`, `.duckdb`,
  `datapackage.json`); pick one and it opens in place. Nothing is uploaded.
- **GMNS at a URL** (`s3://`, `gs://`, `az://`, `https://`): **Check** says whether the URL is reachable, which
  credential source would be used (`env`, `keyring`, `netrc`, or `none`; never the secret), and which tables it found.
- **Build from OpenStreetMap** or **Build from Overture**: choose an area by drawing a rectangle (drag its corners
  to adjust), typing `W,S,E,N` or a point plus a buffer, or searching for a place and picking one of the
  outlines. You can instead pick a local file: a `.osm` XML file or an Overpass JSON export for OSM, or an
  Overture snapshot folder holding `segment.parquet` + `connector.parquet` (see `netstead.overture.layout`).

Below the four sources, **Included examples** lists the networks that ship with netstead (Leavenworth, WA and an
RDU I-40 interchange stretch), each with its link and node counts. One click opens it, with no path to look up
and no need for it to sit under `io.allowed_roots`. Once you pick a source, the **‹** (Back) button at the
top-left of the dialog returns to this list; **Esc** closes the dialog.

A build always writes to the output folder and format you choose (Parquet, CSV, DuckDB, or Zip) and then opens
the result from disk, so what you see is what was saved. It never overwrites an existing output; a leftover
hidden `.partial-*` staging folder from a crashed build is cleaned up automatically once it's more than 24
hours old. The output `name` must be a plain file name — no path separators, no trailing dot — and if it ends
in a format suffix (`.zip`, `.duckdb`, `.csv`, `.parquet`), that suffix must match the format you chose.

Before a build runs, the wizard shows an estimate of its time and size, from a quick count (Overpass
`out count`, or a DuckDB `COUNT(*)` over Overture) and a simple cost model. When the estimate is over
`app.approve_above_s` (90 s by default), or when the count fails, the button changes to **Run (~N min)** or
**Run anyway**, and the build only starts when you click it. The cost model's numbers are in
`netstead/workbench/data/build_cost.toml`; they are rough, so treat the estimate as a guide.

Opens and builds run as background jobs. The **Jobs** button in the header shows their stage and progress and
lets you cancel one; a cancel takes effect when the job reaches its next stage.

### Which folders the app can read and write

The file browser, opening a local path, and build input and output folders are all limited to
`io.allowed_roots`. When that list is empty (the default) it means your home folder. Paths given to
`netstead app` on the command line are always allowed for that session.

A remote source must look like `scheme://host/...` (for example `s3://bucket/net`) and must not contain any
`..` path segments. `file://` and `duckdb://` sources are treated as local paths in disguise and are checked
against `io.allowed_roots` like any other local path.

Credentials never go in the URL: a source with `user:password@` in it is refused. Put them where the
credential cascade looks instead (environment variables, the system keyring, or `~/.netrc`). A presigned URL's
query string (`?X-Amz-Signature=…`, an Azure SAS token) is accepted and kept in the session history, because
the replay script needs it to open the same object; it is stripped from error messages, job labels, the network
summary, and the **Open** menu's recents list. Treat a copied **Session as Python** script that opened a
signed URL as holding that (expiring) signature.

```toml
# ./netstead.toml
[io]
allowed_roots = ["~/networks", "/data/gmns"]

[app]
approve_above_s = 120
```

### The same thing from Python

A build is an ordinary action, so the history strip shows it as Python you can replay. The replayed call always
passes `approved=True`, because re-running a build you already approved counts as approval.

<!-- doctest: skip -->
```python
from netstead.workbench import BuildNetwork, Session

app = Session()
app.do(
    BuildNetwork(
        source="osm",
        area={"kind": "bbox", "bbox": (-78.91, 35.98, -78.88, 36.01)},
        output_dir="/home/me/networks",
        output_format="parquet",
        name="durham-core",
        approved=True,
    )
)
```

Without `approved=True`, a build over the threshold raises `netstead.workbench.ApprovalRequired`, and its
`.estimate` holds the estimate.

Three things to know before replaying a session's script:

- **Folders trusted on the command line are not in the history.** `netstead app /data/net` allows `/data/net` for
  that session only, without recording an action. A replay in a new `Session()` only gets your configured
  `io.allowed_roots`, so an open or build under such a folder fails with `PathNotAllowed`. Add the folder to
  `io.allowed_roots` (config file or `NETSTEAD_IO__ALLOWED_ROOTS`), or pass it as an override:
  `Session(overrides={"io.allowed_roots": ["/data/net"]})`.
- **Builds never overwrite.** Replaying a `BuildNetwork` whose output already exists fails with
  `… already exists`. Point the replay at a fresh `output_dir`, or move the earlier output away first.
- **Saves never overwrite either.** A replayed `SaveNetwork` writes its copy again, so it fails with
  `… already exists` while the earlier copy is still there; a second replay of the same script always does.
  Its `accept_warnings=True` accepts the edit warnings the network has *when the replay runs*, which need
  not be the ones you reviewed when you saved.

## Settings

Every section, key, type and default is listed in the [Settings reference](../reference/settings.md).

The workbench reads layered settings. From lowest to highest precedence:

1. built-in defaults
2. `~/.config/netstead/config.toml`
3. `./netstead.toml`
4. `NETSTEAD_<SECTION>__<FIELD>` environment variables
5. command-line flags

```toml
# ./netstead.toml
[app]
port = 8900

[viz]
basemap = "esri"

[select]
provider = "anthropic"
```

### The Settings dialog

The gear icon in the header (tooltip "Settings") opens a form built from the same settings model. Each field
has a badge showing where its current value comes from: `default`, `user`, `project`, `env` or `session`.

- **Save to** chooses the layer a change is written to: **User (all projects)** (your user file, the default), **This project**
  (`./netstead.toml`), or **This session only** (nothing is written to disk). Every change is a `SetSetting`
  action, so it appears in the history and replays from Python.
- **Reset** removes the value from the layer it comes from. Emptying a field does the same in the **Save to** layer.
  If the value comes from another layer, the dialog points you to **Reset** instead. A number the browser can't
  read, such as `1e`, is never saved: the dialog says so and keeps the saved value.
- A value set by a `NETSTEAD_*` environment variable outranks both files. The form says so when a saved
  value would not take effect.
- Keys read only at launch, such as the app server's port, are tagged **applies on next launch**. A session value
  has no effect on them.
- `io.allowed_roots` is read-only here. The folders the app may read and write can only be widened in a
  config file, an environment variable, or on the command line.
- A basemap change applies at once, without reloading the page.
- **Language models** is a section of the same dialog. **Models…**, in the model picker (the robot icon in the
  header), opens it too.
- The dialog is keyboard-friendly: focus starts in it, Tab stays inside it, and Esc closes it and returns focus to
  where you were.

## Linked map and table

The map and the table are linked both ways. Three sets drive what you see:

- the **selection**, which is recorded: a natural-language select, or **Set as selection**;
- the **highlights**, links you click or shift-drag a box over in Highlight mode; these are not recorded;
- the **focus**, the one record you last clicked on the map or in the table. It is not recorded either.

Focus is a view. Clicking never changes the selection.

- **Focus.** Click a link or node on the map: its details show and, when the table shows that table, its row is
  outlined and scrolled into view (on whichever page it sits). Click a row: it is focused, and the map flies to it.
- **Show** in the table bar picks the rows:
  - **All rows**;
  - **Selection**: the selected links, or the rows of another table that point at them (for example their lanes);
  - **Highlighted**: the same for the highlighted links. A shift-drag box in Highlight mode switches to it.
  - **Related**: the records related to the highlighted links. A focused record only tints rows; it does not
    change this list. So clicking a row here focuses it without changing the list.
- **Foreign keys.** A key column such as `from_node_id` is a link. Click it to open the `node` table at that row; the
  map marks the node and flies to it.
- **Related records.** Rows a foreign key away from what is focused or highlighted are tinted in every table and on
  the map. Examples are the lanes of a link, a link's end nodes, and the links at a node.
  - The keys come from the GMNS spec, not a hard-coded list.
  - The table list shows how many records are related in each table (`N related`; a `+` means the count stopped
    early). Hover a tinted row to see which key relates it, for example `related via lane.link_id → link`.
  - With **Show → Related**, the **Related** control chooses how far to follow keys: **1 step** (direct references,
    such as the links at a node) or **2 steps** (references of references, such as then their lanes). It shows only
    for that scope. The **i** button beside it explains Related with an example, using the real record counts when
    something is highlighted or focused. 2 steps reaches new tables only; it never loops back to one already reached.
  - Related records are a read-only view. Nothing is recorded.

## Workspaces, panels and commands

- **Command palette.** Press **Ctrl+K** (**⌘K** on a Mac), or click the `>_` terminal icon in the header
  (tooltip "Commands (⌘K / Ctrl+K)") when it shows, to search every command: open a network, Settings, zoom,
  the view modes. With a record focused it also lists that record's commands ("For link 123"), and the
  selection's ("Selection (3 links)"). Arrow keys pick, Enter runs, Escape closes.
- **Right-click** a link or node on the map, or a table row, for its commands. Core puts one command there:
  **Fix…**, on a record that has an issue or an edit warning (see [Validate and fix](#validate-and-fix)). Edit
  and Delete are in the palette (**Edit record…**, **Delete record…**) and in Inspect, not in the menu. With no
  command for a record (no issue, and no plugin command), right-click opens no menu: the map keeps its right-drag
  rotate and the row the browser's own menu.
- **Workspaces and the dock.** When a plugin adds a workspace, tabs for each appear in the header; each keeps the
  map / split / table view you left it in. The right drawer is a dock, with a tab per panel at its top, in order:
  The tabs are icons (hover for the name; counts stay as a badge on the icon): **Find** (magnifier; the selection box, anchors, diagnostics, export, saved selections and highlighted links), **View** (sliders; the layers, color and width, direction and
  filter controls), **Inspect** (info; the focused record; comes forward
  automatically when you click a map feature or a table row), **Edit** (pencil; the edit list, undo/redo, edit warnings and
  "Save selection as card…"), **Cards** (stacked cards; the project-cards plugin's panel, when it's installed) and **Validate**
  (shield with a check; run / export, status, facets and the grouped findings list), then **Assistant** (chat bubble), then any panel another plugin
  adds. The map itself keeps only its zoom/highlight/fit buttons and the legend; the old Layers and Map display
  popovers are gone, and their controls live in the **View** tab. A tab's badge is one pill at its icon's corner (orange while
  edits are unsaved) and the tooltip says what it counts. The Edit tab's help paragraph hides behind its **×**; the **ⓘ** beside the heading brings it back.
  Draggable handles between the map and the table, and between the map/table and the side panel, resize them
  (double-click a handle to reset; sizes are remembered per browser).
- **Settings → Plugins** lists the installed [Workbench plugins](workbench-plugins.md): version, the plugin API
  each needs and whether it fits, and any error loading it. Its switch turns a plugin on or off; that applies the
  next time `netstead app` starts.

## Validate and fix

- **Run validation**: the play icon in the **Validate** tab (tooltip "Run validation"), or in the command
  palette, Ctrl+K / ⌘K, checks the spec, the keys and the quality rules as a background job; the Jobs badge
  counts it while it runs. The quality rules read **Settings → Validation** (`validation.rules`, keyed by rule
  code, such as `quality.high_speed_residential`).
- When it finishes, the panel reads `Validated v0: 271 warnings, 16 info` (for the Leavenworth fixture), the
  header's validity light turns green / amber / red with the counts on hover, and each located issue is a dot
  on the map. The **Validate** tab badge counts what needs attention: a red pill with the number of errors when
  there are any, else an amber pill with the number of warnings (open edit warnings included), else no pill.
  Info findings are never counted; hover for the whole breakdown ("Validate — 3 errors, 1 warning, 16 info",
  "Validate — clean", "Validate — not run yet").
- **Click an issue:** its record is focused, the map flies to it, and when the table shows that table its row is
  scrolled into view and marked. **Click a dot** on the map: the Validate panel opens on **This record**, that
  record's issues. Rows with issues carry a mark in the table. A located finding also carries a crosshair
  "zoom to" icon as an explicit affordance.
- **Findings are grouped by type.** One collapsible row per rule code and severity ("▸ quality.high_speed_residential
  warning 271"), with the rule's fix hint shown once, ordered errors first, then warnings, then info, largest
  group first. Open a group to see its findings (50 at a time, with **Show more**), each with its crosshair
  "zoom to" and **Fix**. Groups are built from every finding, not from one page. For structural findings
  (such as the sixteen `structural.missing_optional_resource` lines a partial GMNS package produces) the row
  names the resources instead of a hint.
- Filter with the **Severity**, **Table** and **Type** menus (each starts with an "All …" choice; Type lists
  the finding types with their counts). **This record** lists only the focused record's issues, with its
  groups already open. Issues with no place on the map (a table-level check, a row whose node has no
  coordinates) are also listed under **Unlocated**, where repeated structural findings fold into one row.
- **Fix** on an issue opens the record editor on that issue's record, on the column the issue names (or the
  whole record when it names none), prefilled with the current value and with the issue as the note. Apply sends
  one edit with only the values you changed.
- **Stale results.** An edit makes the stored validation stale: the panel says
  `the network changed since: re-run`, the button's tooltip becomes **Re-run validation**, and the validity
  light turns amber-outlined. The list and the dots stay until you re-run, so you can work down them. If two
  runs overlap, the newer one wins; the older one finishing later says so and leaves the panel alone.
- **Export report**: the download icon next to the run icon (tooltip "Export report (HTML)") downloads the
  offline HTML validation report (`<network>-validation.html`) for a current (not stale) validation. It needs
  the reports extra. The report itself opens with a severity summary line, the source path once, a
  **Resources** table of every spec resource (required/optional, present, row count), and the grouped
  structural findings collapsed under **Unlocated findings**.

The same from Python:

<!-- doctest: skip -->
```python
from netstead.workbench import RunValidation

app.do(RunValidation())  # a job; the result has counts, version, stale and superseded
```

## Edit tables

Edits change the network you opened **in memory**. Nothing is written to disk until you
[save a copy](#save-a-copy), and the source is never overwritten.

- **A cell.** Click a row to focus it, then click one of its cells, type, and press Enter (Escape cancels and
  discards; an empty value clears the cell). Leaving the cell — clicking elsewhere, tabbing away — applies the
  edit too, as in a spreadsheet. A text column completes from the spec's allowed values plus the column's
  existing values (a `<datalist>` under the cell); a comma-list column such as `allowed_uses` completes one
  token at a time and says "comma-separated". The key column and geometry are not edited in place. A value the
  column cannot hold (`1.5` lanes) is refused with a toast, and nothing changes; a value outside the spec's
  allowed list (an out-of-enum categorical, such as `facility_type = "boulevard"`) is **kept**, not dropped,
  and the cell is outlined in red with a warning icon, the reason as its tooltip.
- **A record.** **Edit…** in Inspect, or **Edit record…** in the palette for the focused record, opens a form
  for every editable column. When the record is a link in a multi-link selection, a checkbox applies the same
  values to every selected link. The note you type is kept with the edit.
- **Fix…** opens the same editor from an issue, or from right-clicking a record that has one.
- **Delete…** (Inspect, the record editor, or **Delete record…** in the palette) first runs a dry run and says
  what goes with the record: a link takes its lanes and time-of-day rows (`Delete 1 link row? This also deletes
  2 lane rows, 1 link_tod row. Undo is in the Edit tab.`). Confirm to delete. A node a link still uses cannot
  be deleted: the refusal names the links. The delete runs against the network version the dry run planned on:
  if another Workbench action (or a plugin's or your Python's `mutate`) changed the network in between, it is
  refused and you confirm again. A replayed delete has no dry run: it deletes the ids and whatever depends on
  them in the network it runs against.
- **Adding rows** is Python or the API only: `app.do(AddRows(table="node", rows=[{...}]))`.
- Each edit is one action in the session history (so "copy as Python" replays it), bumps the network's version,
  and is listed in the **Edit** tab with what it changed.

### Undo and redo

The undo icon in the **Edit** tab (tooltip "Undo last edit"; also in Inspect, or **Undo last edit** in the
palette) reverses your newest edit. Undo is last-in, first-out across everything that changed the network, and
the Workbench only undoes **its own** edits:

- If the newest change came from Python (`session.mutate(...)` outside an action), Undo is unavailable and
  says `The last change was made from Python; undo it there.` Undo it with
  `session.undo_last(net_id, source="python")`.
- If a plugin made it, Undo names the plugin: undo it in that plugin.
- Undo puts the rows it restores **at the end of their table**, not where they were: a deleted row, and also the
  earlier version of a row you changed. Row order carries no meaning in GMNS, but an unsorted table shows those
  rows last. (An edit itself keeps the row where it is.)

The redo icon next to it (tooltip "Redo") re-applies the edit `undo_edit` most recently took back —
`app.do(RedoEdit())` — and is disabled ("nothing to redo") until there is an undone edit waiting, or whenever
any other change to the network clears it.

### Live warnings

An edit the spec disagrees with is still applied, and warned about at once: a foreign key that is not in the
referenced table, a required field left empty, a value of the wrong type or outside the allowed list, or a
duplicate key. The cell is outlined (hover for why), the row is marked, and the warning is listed under
**Edit warnings** in the **Edit** tab, with **Fix**. A later edit that puts it right clears the
warning. The live check covers the rows an edit touched; after a very large edit it stops early and says so,
and **Run validation** checks everything.

### What the selection does after an edit

A selection follows an edit of links or nodes:

- one made by a query (a natural-language select) is resolved again against the edited network, so it may gain
  or lose links;
- one made by clicking or by ids keeps its links, minus any you deleted (and is cleared when none is left).

An edit of lanes or time-of-day rows leaves the selection alone. Undo restores the links, but an id selection
does not take back a link it lost.

A query selection with a [pinned anchor](#verify-a-selection) whose node the edit takes off the facility is not
lost: it keeps the links it had (minus any you deleted) and its `resolved` status, and keeps the pin. The Find
tab says **Stale pin** at its top: those links are no longer what the query gives. Re-pin the anchor, or Unpin it.

### Save a copy

The **Save** icon in the header (tooltip "Save a copy…"; or **Save a copy…** in the palette) opens the save
dialog: name the copy, pick a format, then **Choose folder…** inside your allowed folders, and **Save**. The
save runs as a job and writes a **new** folder or file; the network you opened, local or remote, is never
written to, and a copy inside the source folder is refused.

- **It never overwrites.** If the name already exists in that folder, the save fails with `… already exists`.
- **Warnings are confirmed.** With edit warnings open, the dialog lists them and a checkbox asks whether to
  save anyway. If the live check failed or stopped early, it says that instead (the warning count may be 0, and
  proves nothing) and asks the same. Saving anyway is recorded as `SaveNetwork(..., accept_warnings=True)`. The
  server checks again when the job runs, and asks again if something changed.
- When the job ends, a toast says where the copy went and which tables differ from the source. The badge on the
  Edit tab and in the network switcher (`• 1 unsaved`) counts edits no saved copy holds yet, and clears. If
  the network was edited while saving, the copy holds the version the save started from and the later edits stay
  unsaved; if it was closed while saving, nothing is marked saved.
- **Replay.** A replayed `SaveNetwork` writes the copy again (and fails if it is still there), and its
  `accept_warnings=True` accepts whatever warnings the network has when the replay runs. See
  [Every action is replayable](#every-action-is-replayable).

## Verify a selection

- The **Find** tab shows the selection's status, its anchors and diagnostics.
- An **ambiguous** selection lists the other nodes each anchor could be, also drawn on the map as rings
  (the anchor-candidates overlay draws automatically; there is no separate toggle). To pin the anchor to one,
  click one in the Find tab, or **Pin node N** to keep the resolver's own choice; the selection is resolved
  again. **Unpin** goes back to the resolver's choice. In Python: `app.do(PinAnchor(role="to", node_id=60))`.
- A pin that resolves nothing (a node off the facility, or one with no path to the other anchor) is refused, and
  the selection you were checking stays as it was.
- A pin is part of the selection's query, so it survives an edit and a saved selection's reuse. When an edit takes
  the pinned node off the facility, see [What the selection does after an edit](#what-the-selection-does-after-an-edit).
- **Export** shows a resolved selection in the **Fragment** box, as a GMNS fragment (checked against the
  selection schema) or a ProjectCard facility object, one form at a time: **Segment** (the facility's name plus the
  `from`/`to` anchors, which a tool can resolve again on another version of the network) or **Link list** (only
  the resolved link ids). The box changes as soon as you change an option; the utterance is a caption under it,
  not part of the fragment. **IDs** picks the id system: GMNS (`link_id`, `node_id`), OpenStreetMap
  (`osm_way_id`, `osm_node_id`) or Overture GERS (`overture_segment_id`, `overture_connector_id`). A system the
  network has no columns for is disabled, with the reason as its tooltip (the Leavenworth example has OSM ids,
  not GERS). Several links can share one OSM way, so the list is deduplicated and a caption says so ("3 links
  share OSM way 5847010"), and when a way also covers links outside the selection ("OSM way 5846369 also covers
  11 links outside this selection"). ProjectCard has no key for GERS ids, so that choice is disabled for
  ProjectCard objects. A selection made by clicking links has no named segment: **Segment** is disabled, with the
  tooltip "This selection has no named segment; use Link list". The copy button at the top right of the box
  copies the fragment to the clipboard. A saved selection's **Export** still downloads a file, in the form and ID
  system chosen here. With a stale pin, Segment is refused (the query no longer gives those links), and Link
  list exports with a warning.
- **Save selection…** keeps the selection under a name; **Use** brings it back, resolved on the network as it is
  now. `app.do(SaveSelection(name="ski hill"))`, `app.do(UseSelection(name="ski hill"))`. **Export** on a saved
  selection resolves it the same way without making it current, and warns when its network is not open (it then
  exports it as saved). Saved selections last for the session, up to 50 (a replayed history saves them again).
- A save belongs to the network it was made on: its id and its source's location (scheme, host and path, never a
  URL's query string, so a re-signed link to the same file still matches). Networks added from Python all have the
  source `<python>`, so two of those under the same id are not told apart.

## Style, filter and navigate

The map's style and link filter are set from the **View** tab (its layer toggles, color
and width pickers, direction controls and **Filter links** replace the old map popovers).
Nodes colored by `ctrl_type` use a fixed style where size carries meaning too (signals large and red, stop signs orange or amber, yield yellow, uncontrolled nodes small and grey) and signals draw on top; other columns use the generic palette.

- **Color links by** / **Color nodes by** pick a categorical or numeric column for links and nodes
  independently (`app.do(Style(color_by="facility_type"))`, `app.do(Style(node_color_by="ctrl_type"))`). A
  fresh network defaults to coloring links by `facility_type` and nodes by `ctrl_type` when those columns
  exist, falling back to a single color otherwise.
- The color swatches beside **Links**, **Nodes** and **Selection** set a single color. Picking the Links or Nodes
  swatch while that layer is colored by a column turns **Color links by** / **Color nodes by** off in the same
  recorded action (`Style(colors=..., node_color_by="none")`), since a column coloring would hide the swatch.
- Each section of the View tab (Layers, Overlays, Color, Width, Direction, **Show only links where...**) folds open and
  closed; the tab remembers which are folded in this browser. **Overlays** lists a plugin's or core's map layers
  (issue markers, anchor candidates) with a switch and an info tooltip saying what each draws.
- On the map, a click, right-click or hover picks only layers that are switched on, and when a node and a link are
  both under the pointer the node wins (so nodes can be inspected where links run through them).
- **Width by** sizes links by a numeric column (`app.do(Style(width_by="lanes"))`).
- **Show only links where...** (the View tab's filter; it hides the other links on the map and in the table's Filtered view and never changes the data) dims every link that doesn't match
  (`app.do(Filter(where=[{"col": "lanes", "op": "gte", "val": 2}]))`); the table's **Filtered** scope lists the
  matching links (the filter applies to links: on the node table it lists every node). `Filter(where=None)`
  clears it. Filters are fields and operators only, never free text or SQL: `contains` matches a plain
  substring, `between` takes an inclusive `[low, high]` range, and `any_of` / `all_of` / `none_of` match a list
  of values against a comma-separated cell such as `allowed_uses` (so "auto" matches "auto,truck,walk"); each
  value is read as its column's type (`"2"` on `lanes` is 2; a value the column can't hold is refused). The
  `in` operator (a list of values) is available from Python and the API only.
- `app.do(Navigate(record={"table": "link", "id": 170}))` flies the map to one link or node.

## Column filters in the table

Each table column header carries a funnel icon instead of a text box. Click it to open a popover whose body
depends on the column's kind:

- a **numeric** column offers `= ≠ < ≤ > ≥ between is empty is not empty`, with one or two number inputs;
- a **text** or **boolean** column offers a checklist of its distinct values (searchable past 12), under
  **any of / all of / none of** (**all of** only for a comma-list column such as `allowed_uses`), plus a
  **contains** text tab and **is empty / is not empty**.

**Apply** turns the popover into a removable chip in the strip above the grid (for example `free_speed > 40` or
`allowed_uses any of auto, truck`); a **Clear all** chip appears once two or more are active. The strip is
hidden when there are no filters, so it takes no space. Column filters and the **Filter links** map filter are
independent: the grid's filters only narrow what the table shows.

## Ask the assistant

- Type a request in the **Find** tab's box and press **Ask** (or Ctrl+Enter / ⌘Enter); Enter alone still selects. The
  **Assistant** panel (every workspace, or **Ask the assistant…** in the palette) keeps the conversation, for this
  session only: **Clear** empties it, and it is gone when the Workbench stops.
- It uses the provider and model chosen next to the Find tab's selection box (hover the model to see its full
  name when it's truncated). With **Offline (pattern)** it understands a few fixed commands
  (`color by lanes`, `width by lanes`, `filter lanes >= 2`, `clear the filter`, `zoom to the selection`,
  `zoom to link 170`, `select <street>`, `save the selection as <name>`, `set lanes to 3 on the selection`).
- Steps that only change the view or the selection run at once, and each is listed with its `app.do(...)` line.
  Steps that change a network or something you made (edits, deletions, undo, saving a copy, and, once you allow
  them, changing a setting, building a network or deleting a saved selection) wait in one **draft**: **Approve** runs them all
  in order and stops at the first failure, **Reject** drops them, and a step's **Edit…** opens its form prefilled
  (running it from there is an ordinary action; the draft stays pending). A draft is refused once a network it
  touches has changed, and a new question replaces it.
- Everything it runs is in the session history (the strip shows `#12 · assistant`) and in "Session as Python", which
  replays it with no model.
- **What it sends** is listed at the top of the panel. To a remote provider: your question, the recent conversation
  (your words, and its steps as action names and counts), the tool schemas, the networks' names and sizes, column
  names and what the GMNS spec says they mean, the selection's status and size, how many selections are saved, the
  filter's size, the map style's columns, the number of pending edits, whether validation is out of date, and
  validation counts, plus the shipped guide. Street names, the selection's utterance, selected ids, saved selection
  names, issue counts by rule, column values and project notes go only to a local model unless you opt in
  (`llm.quality.grounding`, `project_context`). Network tables and what actions return are never sent, and a local
  model's answer that used them is not passed on to a remote one.
- **Small local models** get help, and the panel says so. A read-only step with a field its tool doesn't have (or,
  for a style, a malformed one) runs without it, and the answer lists what was left out ("Left out: …"); a step that
  changes a network is never changed this way, it is asked for again. A field that belongs to another read-only tool
  becomes a step of its own: "select Ski Hill Drive and color by lanes" folded into one select is run as a select
  and a style ("Split: …"); if the field could belong to more than one tool, or to one that changes a network, the
  model is asked again. The model sees what each GMNS column means
  (`free_speed` is the speed limit). With `assistant.compact_tools = "auto"` (the default) a model on this machine is
  offered fewer tools: the read-only ones that apply now (no network switch with one network open, no saved selection
  to reuse when none is saved) plus edit, delete, undo and the plugin Actions marked `assistant_compact`. `"on"` does this for
  any model, `"off"` never; it never offers more than `assistant.actions` / `assistant.plugins` allow. Measured with `scripts/eval_nl_assistant.py` on
  `qwen2.5:7b` (15 requests, 3 runs): 39/45 correct (87%), every answer valid. It still drops the second half of
  "select …, then save it as …": ask in two steps.
- **Limits:** `assistant.remote_token_budget` (200,000 tokens a session, shared by every remote provider and model;
  a local model is counted, never stopped), `output_token_limit`, `context_max_chars`, `max_steps`, `history_turns`
  (6 turns sent back to the model) and `max_turns` (50 kept).
  **Settings → Assistant: actions & plugins** shows the use so far, chooses which actions it may call and which
  plugins it may use (none by default), and lists names in those settings that this Workbench doesn't know; the
  numbers are under **Settings → Assistant**.

## Language models (natural-language selection)

The utterance box can be read by:

- an offline pattern parser (`stub`, the default — nothing leaves this machine);
- a local model through [Ollama](https://ollama.com), for example Qwen;
- Anthropic, OpenAI or Gemini, with your own API key.

To set up a local model (install Ollama, pull `qwen3:4b`, troubleshoot), see
[Run language models locally with Ollama](local-llm-ollama.md).

`select.provider` accepts `anthropic`, `openai`, `gemini`, `ollama` or `stub`. `claude` is still
accepted as an alias for `anthropic` (existing config files keep working).

Pick a provider and model with the picker under the robot icon in the header. The choice is
`SetSetting(scope="session")` — it applies to this session only; **Make default** saves the same
pair with `scope="user"`, and warns first if a project setting or a `NETSTEAD_SELECT__*` /
`NETSTEAD_SELECT__MODEL` environment variable would still override it. **Models…** opens the
Language models panel: set, replace, remove and test keys, point at another Ollama server, pull an
Ollama model, tune quality and context, and browse the model catalog.

The robot itself shows whether the choice can answer, by color and by the shape of its small dot:
**green** (circle) when the provider and model are ready (a configured key, or a reachable Ollama with
the model pulled), **yellow** (diamond) when only the offline pattern parser is in use, and **red**
(square) when the chosen provider is not available (missing key, Ollama not running, model not
pulled). Hover for the state in words, for example "Language model: Ollama · qwen3:4b — ready"
or "… — not available: Ollama is not running", followed by what each selection sends; whether data
leaves the machine is only ever said in that text, never by the color.

The defaults are each provider's small, fast model — enough for one selection at a time — and are
maintained in `netstead/llm/models.toml`:

| Provider | Default model |
|---|---|
| `anthropic` | Haiku 4.5 (`claude-haiku-4-5-20251001`) |
| `openai` | small/fast tier |
| `gemini` | small/fast tier |
| `ollama` | `qwen3:4b`, or an installed tool-capable model such as `qwen2.5:7b` when `qwen3:4b` isn't installed |

Pick a larger model from the picker if parses go wrong.

### Keys are write-only

- A key is looked up in an environment variable first (`NETSTEAD_<PROVIDER>_API_KEY`, or the
  provider's own variable — `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`), then in the
  OS keyring (macOS Keychain, Windows Credential Manager, or Secret Service on Linux). There is no
  plaintext file fallback.
- Keys are never written to `config.toml`, shown in the browser, recorded in the session history,
  returned by any API response, or logged.
- Set a key with `uv run netstead llm set-key <provider>` (a hidden prompt, or `--stdin` to pipe one
  in — never a command-line argument, so it never reaches shell history or `ps`), or from the
  Workbench's Language models panel.
- **Keys are bound to their endpoint.** If you point a provider at a different `base_url` (for
  example an OpenAI-compatible server), it needs a new key entered for that endpoint; an existing
  key is never sent there.
- The Workbench's key routes (`/api/llm/keys/*`, `/api/llm/test`, `/api/llm/ollama/pull`) are
  loopback-only (refused unless the server is bound to `127.0.0.1`/`localhost`/`[::1]`) and every
  request must also carry an `X-Netstead-Secrets: 1` header, which a cross-origin page cannot add
  without a CORS preflight this server never approves. When the Workbench is bound elsewhere,
  manage keys from a terminal with `netstead llm set-key`/`remove-key` instead, and pull models with
  `netstead llm pull`.
- **Known limitation:** a key written by the CLI (or another process) while the Workbench is
  already running may take up to about 5 seconds to be seen by that running Workbench.

### Errors are explicit

A missing or rejected key, a rate limit or a timeout shows as an error. The Workbench never
quietly switches to another provider.

### What is sent, and quality settings

Every selection sends your utterance and the selection tool's schema (GMNS field names such as
`lanes`), never your network tables or files. The `[llm.quality]` settings add more, and the
privacy note in the picker's tooltip always lists exactly what the chosen provider receives. Every part
of the prompt that is network- or project-sourced data (not an instruction) is fenced in its own
tagged block (for example `<network_vocabulary>…</network_vocabulary>`) so a street name or
project note can never be mistaken for a new instruction to the model.

`"auto"` means on for a model running on this machine (Ollama, or any loopback `base_url`) and off
for a remote provider.

| Setting | Default | What it does |
|---|---|---|
| `assistant_context` | `true` | Sends the GMNS assistant guide that ships with netstead (data model, examples) |
| `assistant_context_max_chars` | `16000` | Cap on that guide, in characters |
| `project_context` | `"auto"` | Sends your project notes — see below — when allowed for the provider in use |
| `project_context_max_chars` | `4000` | Cap on those notes, in characters |
| `grounding` | `"auto"` | Sends the network's most common street names and route numbers, so the model spells them as the network does |
| `grounding_max_names` | `200` | Cap on those names |
| `few_shot` | `false` | Shows the model this session's earlier selections that resolved. Remembered only for this session, on this network — never persisted |
| `few_shot_max` | `3` | How many of those earlier selections are shown |
| `match_retry` | `"auto"` | If nothing matches, asks the model once more with the closest real names. `"auto"` is on for a local endpoint and off for a remote one |
| `match_candidates` | `5` | How many close names that retry offers |
| `max_repairs` | `1` | Re-prompts after an invalid reply |
| `temperature` | `0.0` | Sampling temperature. A blank field restores the default; it cannot be unset from the dialog |

```toml
# ~/.config/netstead/config.toml: endpoints, choices and quality only, never keys
[select]
provider = "anthropic"
model = "claude-haiku-4-5-20251001"

[llm.ollama]
base_url = "http://localhost:11434"

[llm.quality]
grounding = "on"        # also send street names to remote providers
few_shot = true
```

### Project notes

A project's notes are discovered next to the active network's folder first, then in the project
directory — never the process's current working directory if neither of those applies. netstead
looks, in order:

1. A dedicated `NETSTEAD.md`, sent whole when found.
2. Otherwise, `AGENTS.md` — and failing that, `CLAUDE.md` — but **only** its `## netstead` section
   (any heading level `##`-`####`, matched case-insensitively, up to the next heading at the same
   or a shallower level). A file without that section is treated as if it were absent: the rest of
   an `AGENTS.md`/`CLAUDE.md` holds instructions for a coding agent, not notes for the network
   assistant, and is never read or sent.
3. If neither is found, no project notes are sent.

Every candidate must resolve inside `io.allowed_roots` (symlinks included), so a notes file can't
be used to exfiltrate an arbitrary file on disk.

A `NETSTEAD.md` might read:

```markdown
- "the Beltline" is I 440.
- SR-520 is the Evergreen Point Bridge.
- facility_type 7 means HOV lanes.
```

Or, inside an existing `AGENTS.md`:

```markdown
## netstead
- "the Beltline" is I 440.
- SR-520 is the Evergreen Point Bridge.
```

### From a terminal

Also the way to manage keys when the Workbench is bound to a non-local address:

```bash
uv run netstead llm status
uv run netstead llm set-key anthropic
uv run netstead llm test anthropic
uv run netstead llm models ollama
uv run netstead llm pull qwen3:4b
uv run netstead select "I-40 EB between South Miami Boulevard and Airport Boulevard" ./my-network --provider ollama --model qwen3:4b
```

`netstead select` prints the selection fragment and exits 0 when the utterance resolves. Its other exit codes:

| Exit code | Meaning | What it prints |
|---|---|---|
| `1` | Nothing matched, or the parser failed | `status` and `diagnostics` |
| `2` | Invalid settings (an unknown provider, for example) | the error |
| `3` | Ambiguous: an anchor could be more than one node | `status`, the provisional `link_ids`, each anchor with its `candidates`, and `diagnostics` |

An ambiguous selection is the same one the Workbench's Find tab shows with its candidate rings
(see [Verify a selection](#verify-a-selection)); the CLI has no pin, so narrow the utterance or pin the
anchor in the Workbench and export from there. `--json` puts the same fields on stdout as one JSON object.

## Every action is replayable

Everything you do in the browser is a typed action: open, select, style, navigate, change a setting.
The strip at the bottom shows the last action as Python. The code icon in the footer (tooltip "Copy session
as Python") shows the whole session as a script you can copy. Networks added from Python via `Session.add_network` aren't recorded as actions
and so aren't replayed in that script. A replayed delete has no dry run: it deletes the ids and whatever depends on
them in the network it runs against, which may be more (or fewer) rows than you confirmed in the browser.

## Other tools

The Workbench is a network session, not a GIS: it owns the tables, the selection, validation and the edit
history, and leaves cartography, context layers, printing and model runs to tools built for them. Save a copy
as Parquet and the result opens directly in GeoLibre, QGIS or ArcGIS Pro, and feeds AequilibraE, DTALite and
Path4GMNS. [The Workbench and other tools](../workbench-and-other-tools.md) says what each does best and how to
move between them.
