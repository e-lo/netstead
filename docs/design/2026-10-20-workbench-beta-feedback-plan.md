# Workbench beta feedback: implementation plan

Status: **accepted** · Date: 2026-10-20 · Owner: Elizabeth Sall

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Act on the twenty items of beta feedback collected on 2026-10-20 while walking the Workbench quickstart on the Leavenworth fixture: three rendering bugs, a silent-edit gap, a validation-report layout problem, a batch of UI ergonomics, a side-panel restructure, and a Cards panel with a catalog.

**Architecture:** The Workbench is a FastAPI app (`packages/netstead/netstead/workbench/`) with a vanilla-JS front end (`workbench/static/`: `index.html`, `app.css`, `js/*.js` ES modules, no bundler, no framework). Pure JS modules (import-free, DOM-free) are unit-tested under Node through the `node_module` fixture in `packages/netstead/tests/conftest.py`; DOM wiring is covered by `tests/test_workbench_static.py` (every `$("id")` must exist in `index.html`, every import must resolve, every module must parse). Server state flows to the page over SSE (`/api/events`) into `store.js`; UI actions are `POST /api/actions` with a pydantic `BaseAction` (`workbench/actions.py`) handled in `workbench/session.py`. Plugins reach core only through `netstead.workbench.plugins` (Host API, now 1.6) and the front-end `wb` object (`static/js/wbhost.js`).

**Tech stack:** Python 3.11+, FastAPI, pydantic, ibis + DuckDB, pandas; deck.gl 9 + MapLibre on the map; pytest (+xdist), Node for JS unit tests. Run everything from the repo root with `uv run --all-extras ...`.

**Branch / worktree:** `feat/workbench-beta-feedback` in `.claude/worktrees/workbench-beta-feedback`, based on `origin/main` at `8ac873f` (Host API 1.6 merged). One commit per task; one PR at the end.

**Feedback items → tasks**

| Item | Feedback (short) | Task |
|---|---|---|
| 1 | Validation report repeats INFO findings; no resource inventory | 7 |
| 2 | Map loses link shapes when geometry lives in the `geometry` table | 2 |
| 3 | White focus highlight invisible on the light basemap | 3 |
| 4 | Column filter illegible; wants typed popovers, list-membership, chips | 5 |
| 5 | Categorical edits drop silently; wants allowed values + autocomplete + loud flagging | 6 |
| 6 | Layers popover cut off in Split view | 4 |
| 7 | Undo / redo icons with tooltips | 1 |
| 8 | Resizable table and side panels | 4 |
| 9 | Panel restructure: Find/Inspect, Edit, Cards, Validate; ribbon duplicative | 9, 10 |
| 10 | Side panel too small for validation | 4, 9 |
| 11 | Settings → gear icon, Commands → `>_` icon | 1 |
| 12 | Model name truncated | 1 |
| 13 | Duplicative text; icons with tooltips | 1 |
| 14 | Default colouring: nodes by `ctrl_type`, links by `facility_type` | 9 |
| 15 | Fold Recent into Open | 8 |
| 16 | Direction arrows overlap and ignore the offset | 3 |
| 17 | Save/export as a top-level icon + dialog | 8 |
| 18 | "Session as Python" as an icon | 1 |
| 19 | Top-level validity indicator | 8 |
| 20 | Interactive geometry editing | roadmap only (Task 11 adds it to `roadmap.md`) |

Backlog recorded for later (not in this PR): a global filter expression box compiled to the same predicate as the column popovers.

---

## Conventions every task follows

- **TDD.** Write the failing test first, run it, implement, run it green, commit. Test commands below are exact.
- **Pure-module rule.** Any new front-end logic that can be expressed without the DOM goes in an import-free module under `static/js/` and gets a Node test in `packages/netstead/tests/test_workbench_js.py` (pattern: `node_module("editmodel.js", "<expression>")` returns the JSON of the expression). DOM wiring stays thin.
- **Static contract.** After touching `index.html` or any `js/*.js`, run `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py -q`; it fails on a missing element id, a broken import, or a parse error.
- **Tooltips.** Icon buttons are `<button class="iconbtn" data-tip="..." aria-label="...">`; the tooltip is the CSS rule `.iconbtn[data-tip]:hover::after` in `app.css`. Every icon-only control needs both attributes.
- **Recorded vs local state.** View preferences that should replay from Python (colour-by, filters) are `SetStyle` / `Filter` actions; purely per-browser conveniences (panel sizes, recents) are `localStorage`, wrapped in try/catch.
- **Docs.** Each task that changes user-visible behaviour adds a line to `packages/netstead/CHANGELOG.md` under *Unreleased* (and `plugins/project-cards/CHANGELOG.md` for Task 10). Task 11 refreshes the prose docs.
- **Lint.** `uv run ruff check packages plugins && uv run ruff format --check packages plugins` before each commit. Pyright runs in CI (`uv run pyright packages/netstead/netstead/workbench` is a quick local check for touched Python).
- **Full suite** before the PR: `uv run --all-extras pytest packages plugins -n auto -q` (fast tier) and the Workbench slice with `-m ""`.

---

### Task 1: Inline SVG icon set, icon buttons, undo/redo, header de-cluttering

Items 7, 11, 12, 13, 18.

**Files:**
- Create: `packages/netstead/netstead/workbench/static/js/icons.js` (pure module)
- Modify: `packages/netstead/netstead/workbench/static/index.html` (header rows, `#dock-issues`, `#dock-edits`, footer history button)
- Modify: `packages/netstead/netstead/workbench/static/app.css` (`.iconbtn svg` sizing, `.icon-tip` positioning for header buttons, `#nl-model` tooltip)
- Modify: `packages/netstead/netstead/workbench/static/js/header.js`, `edits.js`, `issues.js`, `history.js`, `llm.js`, `cmdpalette.js`, `settings.js` (wire the renamed/iconified controls; remove duplicate text)
- Modify: `packages/netstead/netstead/workbench/static/js/editmodel.js` (`redoBlocked`)
- Modify: `packages/netstead/netstead/workbench/actions.py` (`RedoEdit`), `workbench/session.py` (redo stack), `workbench/ledger.py` if the undone edit's payload lives there
- Test: `packages/netstead/tests/test_workbench_js.py`, `tests/test_workbench_static.py`, `tests/test_workbench_edits.py`

**Behaviour:**

1. `icons.js` exports `ICONS` (an object of name → SVG path data, 24×24 viewBox, `stroke="currentColor" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"`) and `icon(name, {size=18, label=null})` returning an inline `<svg ...>` string with `aria-hidden="true"` (or `role="img" aria-label` when `label` is given). Names required by this plan: `undo`, `redo`, `gear`, `terminal` (the `>_` glyph), `code` (session as Python), `save`, `folder-open`, `play` (run validation), `download` (export report), `check-circle`, `alert-triangle`, `x-circle`, `circle` (validity light states), `filter`, `x` (chip remove), `chevron-down`, `layers`, `crosshair` (zoom to), `grip` (resize handle). Unknown name throws.
2. Header row 1: `#open-wizard` keeps its text ("Open…") but gains a `folder-open` icon before it; `#recent` is removed in Task 8 (leave it for now); `#cmd-btn` becomes an icon button with `terminal` icon, `data-tip="Commands (⌘K / Ctrl+K)"`; `#settings-btn` becomes an icon button with `gear`, `data-tip="Settings"`; `#jobs-btn` keeps its badge but shows a `play`-style icon plus the count. Keyboard shortcuts and ids are unchanged so `cmdpalette.js` / `settings.js` keep working.
3. `#nl-model`: set `title` (native tooltip) to the full model label whenever `llm.js` repopulates it (both on change and on refresh) so a truncated option is readable on hover. Also give the select `data-full` with the same text (test hook).
4. Edits panel: replace `#ed-undo` ("Undo last") with two icon buttons `#ed-undo` (`undo`, tip "Undo last edit") and `#ed-redo` (`redo`, tip "Redo"). `editmodel.js` gains `redoBlocked(state)` mirroring `undoBlocked`: returns a reason string when `state.edits[active].redo_count` is 0 or missing, else `null`. `edits.js` disables each button from its reason and puts the reason in the tooltip (`data-tip`).
5. Server: new action `RedoEdit(net_id: str | None = None)` with `type = "redo_edit"`, `mutates = True`. `Session` keeps `self._redo: dict[str, list[PendingEdit]]` (or the ledger's equivalent). `undo_edit` pushes the undone pending edit; any other mutation of that network clears its redo list; `redo_edit` pops and re-applies the edit's recorded `edits` through the same path `mutate` uses, with note `"redo: <original note>"`, and refuses with `ActionError("nothing to redo")` when the list is empty. `session.state()["edits"][net_id]` gains `"redo_count": int`. The Python export line for a redo is `app.redo_edit()`.
6. Issues panel: `#iss-run` becomes an icon button (`play`, tip "Run validation"); `#iss-report` becomes an icon button (`download`, tip "Export report (HTML)"); the "No edit warnings." empty text under the `EDIT WARNINGS 0` label is removed (the count is the message; when there are warnings the list renders as today).
7. Footer: `#hist-all` ("Session as Python") becomes an icon button (`code`, tip "Copy session as Python").
8. CSS: `.iconbtn svg { width:18px; height:18px; display:block }`; header icon buttons get the same hover/tooltip rules as the map's (`.iconbtn[data-tip]:hover::after`) — today that rule is scoped to the map; generalise it so it works in the header, the dock and the footer, with the tooltip rendered *below* the button when the button is in `header` (so it is not clipped at the top of the window).

- [ ] **Step 1: Node test for `icons.js`** in `test_workbench_js.py`:

```python
def test_icon_renders_svg_with_aria(node_module):
    svg = node_module("icons.js", 'icon("undo")')
    assert svg.startswith("<svg") and 'aria-hidden="true"' in svg and 'viewBox="0 0 24 24"' in svg

def test_icon_label_makes_it_an_image(node_module):
    svg = node_module("icons.js", 'icon("gear", {label: "Settings"})')
    assert 'role="img"' in svg and 'aria-label="Settings"' in svg

def test_icon_unknown_name_throws(node_module):
    with pytest.raises(Exception):
        node_module("icons.js", 'icon("nope")')

def test_every_planned_icon_exists(node_module):
    names = node_module("icons.js", "Object.keys(ICONS).sort()")
    for n in ["undo","redo","gear","terminal","code","save","folder-open","play","download",
              "check-circle","alert-triangle","x-circle","circle","filter","x","chevron-down","layers","crosshair","grip"]:
        assert n in names
```

- [ ] **Step 2: run, see it fail** — `uv run --all-extras pytest packages/netstead/tests/test_workbench_js.py -k icon -q` → FAIL (module missing).
- [ ] **Step 3: write `icons.js`**, run again → PASS.
- [ ] **Step 4: Python tests for redo** in `tests/test_workbench_edits.py` (follow the file's existing fixtures for a session on the Leavenworth fixture):

```python
def test_redo_reapplies_the_undone_edit(session):
    before = session.registry.get(session.active).links_df().set_index("link_id").loc[17, "free_speed"]
    session.dispatch(EditCells(table="link", ids=[17], values={"free_speed": 42.0}))
    session.dispatch(UndoEdit())
    assert session.state()["edits"][session.active]["redo_count"] == 1
    session.dispatch(RedoEdit())
    after = session.registry.get(session.active).links_df().set_index("link_id").loc[17, "free_speed"]
    assert after == 42.0 and before != 42.0
    assert session.state()["edits"][session.active]["redo_count"] == 0

def test_redo_refused_when_nothing_to_redo(session):
    with pytest.raises(ActionError, match="nothing to redo"):
        session.dispatch(RedoEdit())

def test_new_edit_clears_redo(session):
    session.dispatch(EditCells(table="link", ids=[17], values={"free_speed": 42.0}))
    session.dispatch(UndoEdit())
    session.dispatch(EditCells(table="link", ids=[18], values={"free_speed": 43.0}))
    assert session.state()["edits"][session.active]["redo_count"] == 0
```

- [ ] **Step 5: run, fail; implement `RedoEdit` + session redo stack; run, pass.** Also add the action to the action registry test (`tests/test_workbench_action_registry.py` lists every action type; follow its pattern) and to the `_doc` / Python-export mapping so `script` output reads `app.redo_edit()`.
- [ ] **Step 6: `redoBlocked` Node test** (`test_workbench_js.py`): `node_module("editmodel.js", 'redoBlocked({active:"n", edits:{n:{redo_count:0}}})')` → a non-empty string; with `redo_count:2` → `None`.
- [ ] **Step 7: HTML/CSS/JS wiring** (items 2–8 above). Run `uv run --all-extras pytest packages/netstead/tests/test_workbench_static.py packages/netstead/tests/test_workbench_js.py -q` → PASS.
- [ ] **Step 8: Browser check.** `uv run --all-extras netstead app packages/netstead/netstead/fixtures/leavenworth/csv --provider stub --port 8851`; confirm the header icons render with tooltips, an edit → undo → redo cycle works from the Edits panel, and the model select shows its full name on hover.
- [ ] **Step 9: CHANGELOG + commit** — `git commit -m "feat(workbench): inline SVG icons, undo/redo buttons, iconified header and panel controls"`.

---

### Task 2: Link shapes from the `geometry` table in the map buffer

Item 2. Cause confirmed: `viz/buffers.py:_link_paths` reads only `links["geometry"]`; the Leavenworth fixture (and any GMNS package with `link.geometry_id` → `geometry.geometry`) has no inline column, so every link falls back to a straight segment. The resolution order already exists in `netstead/semantics/geometry.py` (`assemble_link_geometry`, `GeometrySource`) and `clean/clean.py:_shape_table` (PR #232).

**Files:**
- Modify: `packages/netstead/netstead/viz/buffers.py` (`pack_network(links, nodes, geometry=None)`)
- Modify: `packages/netstead/netstead/workbench/registry.py` (`NetworkHandle.links_df()` or a new `shapes_df()` passing the geometry table), `workbench/routes/network.py:network_bin`
- Modify: any other `pack_network` caller (`grep -rn pack_network packages`) — the viewer in `netstead/viz/__init__.py` / `map/` if it uses it
- Test: `packages/netstead/tests/test_viz_buffers.py`

**Behaviour:** `pack_network` accepts an optional `geometry` frame (`geometry_id`, `geometry`). Resolution per link, in this priority: inline `links.geometry` if present and parseable; else `geometry_id` joined to the geometry frame; else straight from/to. Implement by reusing `assemble_link_geometry` if its signature fits (it takes the network or the two frames — read it first); otherwise a small `_resolve_shapes(links, geometry)` in `buffers.py` that returns a `geometry` Series aligned to `links`. The Workbench handle passes `self.roadway.geometry` (may be `None`). Cache key `"network.bin"` is unchanged (it is per version already).

- [ ] **Step 1: failing test** in `test_viz_buffers.py`:

```python
def test_pack_uses_geometry_table_when_links_have_no_inline_shape():
    from netstead.fixtures import leavenworth
    from netstead import Network
    net = Network.from_source(leavenworth.csv_dir())
    links, nodes, geom = net.links.to_pandas(), net.nodes.to_pandas(), net.geometry.to_pandas()
    assert "geometry" not in links.columns
    straight = decode_network(pack_network(links, nodes))          # today's behaviour: 2 points per link
    shaped = decode_network(pack_network(links, nodes, geometry=geom))
    # link 1's shape has 8 vertices in geometry.csv
    assert shaped.start_indices[1] - shaped.start_indices[0] == 8
    assert straight.start_indices[1] - straight.start_indices[0] == 2
```

(Use whatever decode helper the test file already has; if none, assert on `pack_network`'s `startIndices` array directly.)

- [ ] **Step 2: run → FAIL (unexpected kwarg). Implement. Run → PASS.**
- [ ] **Step 3: Workbench route test** in `tests/test_workbench_network_routes.py`: `GET /api/n/{net_id}/network.bin` on the Leavenworth session decodes to more than `2 * n_links` positions.
- [ ] **Step 4: browser check** — the Leavenworth streets curve (Farivar Lane and the US‑2 bend are obvious).
- [ ] **Step 5: CHANGELOG (Fixed) + commit** — `fix(workbench): draw link shapes from the geometry table, not straight node-to-node lines`.

---

### Task 3: Map rendering: focus colour and direction arrows

Items 3 and 16.

**Files:**
- Modify: `static/js/map.js` (`FOCUS_COLOR`, `HIGHLIGHT_COLOR` outline; arrow IconLayer `getPixelOffset`, `getSize`), `static/js/netbuf.js` (`buildArrows`), `app.css` (`--hl`)
- Test: `tests/test_workbench_js.py` (netbuf is pure)

**Behaviour:**

1. Focus colour: `FOCUS_COLOR` becomes a saturated magenta `[255, 0, 200, 240]` drawn **with a dark outline**: render the focused link twice (a 2 px wider dark `[20, 20, 30, 220]` path under the magenta one) so it reads on both the Positron (light) and Esri basemaps and over the white highway casing. `HIGHLIGHT_COLOR` (cyan) keeps its hue but the highlight layer gets the same dark under-stroke. The selection colour stays user-configurable.
2. Arrows: `buildArrows(paths, {offset, directed})` in `netbuf.js` returns, per link, `{position, angle, side}` where `side` is `+1` for a link drawn on the right-hand offset, `0` when offsets are off. Two-way streets in GMNS are two link rows (A→B and B→A); each row gets its own arrow, so the fix is to place the arrow on *that row's* offset line: `pixelOffset = side * OFFSET_PX * [cos(angle+90°), sin(angle+90°)]` computed in `map.js` from the same `OFFSET_AMT` the `PathStyleExtension` uses (convert the extension's units to pixels the same way it does; read `OFFSET_EXT` setup). Arrow size scales with zoom: `size = clamp(8 + 2*(zoom-13), 8, 18)` px; below `ARROW_ZOOM` no arrows (unchanged). Place the arrow at the path's midpoint by *length* (not the middle vertex) — add `midpointByLength(points)` to `netbuf.js`. Arrow colour: white triangle with a 1 px dark outline (update `ARROW_SVG`).

- [ ] **Step 1: Node tests** for `midpointByLength` (`[[0,0],[10,0],[10,10]]` → `[10,0]` exactly? No: half of 20 is 10 along the path → `[10,0]`) and for `buildArrows` with `offset:true` returning `side: 1` for every arrow and `side: 0` with `offset:false`.
- [ ] **Step 2: run → FAIL; implement; run → PASS.**
- [ ] **Step 3: browser check** with "Show direction" + "Offset directions" on Leavenworth downtown: two parallel arrows per two-way street, each pointing with its line; the focused link visibly magenta-with-outline on the light basemap. Take a screenshot into `/tmp` for the PR.
- [ ] **Step 4: CHANGELOG (Fixed) + commit** — `fix(workbench): legible focus colour; direction arrows follow their offset line and scale with zoom`.

---

### Task 4: Layout: popovers that fit, resizable panels

Items 6, 8, 10.

**Files:**
- Create: `static/js/resize.js` (pure: `clampSize`, `readSizes`, `writeSizes`, `applySizes` are split so the math is testable)
- Modify: `index.html` (move `#layers-panel`, `#settings-panel` and `#map-btns` out of `#map` into a sibling `#map-overlay` div inside `#stage`; add `#split-handle` between `#map` and `#tablepane`, `#side-handle` between `#stage` and `#side`), `app.css`, `static/js/panels.js` (popover open/close unchanged; add `max-height` + `overflow:auto`), `static/js/main.js` (wire handles)
- Test: `test_workbench_js.py`, `test_workbench_static.py`

**Behaviour:**

1. Popovers: `#map-overlay { position:absolute; inset:0; pointer-events:none }` over the map, children `pointer-events:auto`; `.panel { max-height: calc(100% - 66px); overflow:auto }` so the Layers panel scrolls instead of being clipped by MapLibre's `overflow:hidden`. The map buttons move with it. `map.js` still passes `container:"map"`; nothing else changes.
2. Resizable: dragging `#split-handle` (horizontal bar, `grip` icon) sets `#map`'s flex-basis as a percentage (20–80 %); dragging `#side-handle` (vertical bar) sets the `#app` grid's side column width (240–720 px). Double-click a handle resets to the default (55 % / 330 px). Sizes persist in `localStorage` key `wb.sizes` as `{split: number, side: number}`; invalid or missing storage → defaults. After a drag `map.resize()` is called so MapLibre re-measures. `resize.js` exports `clampSize(kind, value)`, `DEFAULTS`, `parseSizes(json)`, `serializeSizes(sizes)`; `main.js` owns the pointer events.

- [ ] **Step 1: Node tests** — `clampSize("split", 95)` → 80; `clampSize("side", 100)` → 240; `parseSizes("garbage")` → defaults; `parseSizes('{"split":40,"side":500}')` → same values.
- [ ] **Step 2: run → FAIL; implement `resize.js`; run → PASS.**
- [ ] **Step 3: HTML/CSS/wiring; static contract test → PASS.**
- [ ] **Step 4: browser check** in Split view: the Layers panel's "Filter links" section is reachable (scrolls); dragging each handle resizes, reload keeps the sizes, double-click resets.
- [ ] **Step 5: CHANGELOG + commit** — `feat(workbench): resizable table and side panels; map popovers scroll instead of clipping in Split view`.

---

### Task 5: Column filters: typed popovers and chips

Item 4. Today: a text `<input data-fcol>` in every `<th>` sends `{col, op:"contains", val}` to the rows route (`table.js:127-160`). Server ops available in `viz/tables.py:_OPS`: `eq ne lt lte gt gte in contains isnull notnull`.

**Files:**
- Create: `static/js/gridfilter.js` (pure): the per-column filter model and chip text
- Modify: `static/js/table.js` (header: name + sort caret + `filter` icon button; popover; chip strip above the grid), `index.html` (`#tbl-chips` strip in `#tbl-bar`'s row below, `#col-filter` popover element), `app.css`
- Modify: `workbench/routes/network.py` (new `GET /api/n/{net_id}/{component}/table/{table}/distinct?col=&limit=200` → `{values:[...], truncated: bool}`), `viz/tables.py` (`distinct_values(table, col, limit)`; for a list-valued text column (`allowed_uses`) split on `,` and strip before counting, flagged by `list_like: true` in the response when any value contains a comma)
- Modify: `viz/tables.py:_OPS` — add `"any_of"` (comma-split membership: the cell, split on `,` and stripped, intersects `val`), `"all_of"`, `"none_of"`, `"between"` (`val` is `[lo, hi]`, inclusive). Add them to `FILTER_OPS` and to the `Filter` action's allowed ops if it validates against `FILTER_OPS` (it does: see `test_workbench_filter.py`).
- Test: `tests/test_workbench_js.py`, `tests/test_viz_tables.py` (or wherever `filter_rows` is tested), `tests/test_workbench_network_routes.py`

**Behaviour:**

1. Header `<th>`: `<span class="cn">name</span><span class="ar">▲</span><button class="iconbtn fbtn" data-tip="Filter name">{filter icon}</button>`; the button carries class `on` when that column has an active filter. No input in the header.
2. Clicking the funnel opens `#col-filter` (one shared popover, absolutely positioned under the button, closed on Escape / outside click / Apply) whose body depends on the column kind from `/schema`:
   - `num`: operator select (`= ≠ < ≤ > ≥ between is empty is not empty`) + one or two numeric inputs.
   - `str` and `bool`: a checklist of distinct values (from `/distinct`, with a "search values" box when > 12) under a mode select `any of / all of / none of` (`all of` only shown when `list_like`), plus "contains" text as an alternative tab, plus `is empty / is not empty`.
3. Apply → `gridfilter.setFilter(col, spec)`; the model's `conditions()` yields the `[{col, op, val}]` list the rows request sends (`between` → `{op:"between", val:[lo,hi]}`; checklists → `in` for plain columns, `any_of`/`all_of`/`none_of` for list-like ones).
4. Chip strip `#tbl-chips` above the grid: one chip per column filter, text from `gridfilter.chipText(col, spec)` (e.g. `free_speed > 40`, `allowed_uses any of auto, truck`, `name contains "Pine"`), each with an `x` button that removes it; a "Clear all" chip when ≥ 2. The strip is hidden when empty so it takes no vertical space.
5. The `TBL.filters` object is replaced by the `gridfilter` model instance; `query()` calls `conditions()`.

- [ ] **Step 1: Python tests for the new ops** (`filter_rows` on a small DataFrame): `any_of` on `["auto","auto,truck","walk"]` with `["auto"]` keeps rows 0 and 1; `all_of` with `["auto","truck"]` keeps row 1 only; `none_of` with `["auto"]` keeps row 2; `between` `[35, 45]` on `[30, 40, 50]` keeps the middle. Run → FAIL; implement; → PASS.
- [ ] **Step 2: route test** for `/distinct` on Leavenworth `link.facility_type` → values include `residential`, `primary`; on `allowed_uses` → `list_like` is true and `auto` is a value.
- [ ] **Step 3: Node tests for `gridfilter.js`**: `setFilter`/`conditions`/`chipText`/`clear`/`remove` as specified; `chipText("allowed_uses", {mode:"any_of", values:["auto","truck"]})` → `"allowed_uses any of auto, truck"`.
- [ ] **Step 4: table.js + HTML + CSS; static test → PASS.**
- [ ] **Step 5: browser check** on the `link` table: filter `free_speed > 40`, then `allowed_uses any of bike`, chips appear, row count changes, removing a chip restores rows; the header no longer shows the old inputs.
- [ ] **Step 6: CHANGELOG + commit** — `feat(workbench): typed column filters with popovers and chips; any_of/all_of/none_of/between filter operators`.

---

### Task 6: Categorical edits: allowed values, autocomplete, loud warnings

Item 5. Facts: the server does **not** refuse an out-of-enum value; `editing.plan_update` only checks the storage type, the mutate persists, and `editcheck` adds an `edit.enum` warning returned in the 200 response (`session.py:_apply_plan`) and kept in `state()["edits"]`. The front-end cell editor is a bare `<input>`; `/schema` (`viz/tables.py:table_schema`) does not expose `constraints.enum`.

**Files:**
- Modify: `viz/tables.py:table_schema` (add `enum: list | None` per column from the spec field's `constraints.enum`; `list_like: bool` when the field's description/`categories` say it is a comma list — simplest rule: a text column whose spec field has `constraints.enum` **and** whose name is in a small set `{"allowed_uses"}` or whose current values contain commas; implement as `enum` + `list_like` computed from the data sample in `/distinct` instead if cleaner — pick one and document it in the docstring)
- Modify: `static/js/table.js:startCellEdit` (render `<input list="cell-opts">` + a `<datalist id="cell-opts">` filled from `enum` ∪ distinct values via the Task 5 `/distinct` route; for `list_like` columns the datalist offers single tokens and the input accepts a comma list)
- Modify: `static/js/table.js:renderRows` (a cell with an open warning gets class `warn` and `title=<warning message>`; the rows response already carries `warnings`; if it does not, read them from `store.server.edits[active].warnings` by `(table, key, column)`), `app.css` (`td.warn { box-shadow: inset 0 0 0 2px var(--to) }` + a small `⚠` after the text)
- Modify: `static/js/editmodel.js` (`cellOptions(col, distinct)` pure helper: dedupe `enum` then distinct, sorted, enum first)
- Test: `tests/test_workbench_editing.py` or `test_workbench_edits.py` (regression), `test_workbench_network_routes.py` (`/schema` enum), `test_workbench_js.py`

**Behaviour / regression:** an `EditCells` setting `link.facility_type = "boulevard"` persists (the row reads back `"boulevard"`) and the response contains a warning with `code == "edit.enum"` naming the column; the table shows that cell outlined with the message on hover; the Edits panel count includes it (already does). If, while doing this, the implementer finds a path where a categorical edit is genuinely dropped (the beta report), fix it and add a test for it; report what it was.

- [ ] **Step 1: Python regression test** (as above) → should already PASS for persistence; the `/schema` enum test FAILS until implemented.
- [ ] **Step 2: implement schema enum; → PASS.**
- [ ] **Step 3: Node test** for `cellOptions({enum:["a","b"]}, ["b","c"])` → `["a","b","c"]`.
- [ ] **Step 4: cell editor + warning rendering; static test → PASS.**
- [ ] **Step 5: browser check**: edit `facility_type` on a row: the datalist offers the spec's values; type `boulevard`, Enter: the value stays, the cell is outlined red with the enum message on hover, the Edits badge shows a warning.
- [ ] **Step 6: CHANGELOG + commit** — `feat(workbench): categorical cells offer the spec's allowed values; out-of-enum edits persist and are flagged in the grid`.

---

### Task 7: Validation report: group repeated findings, resource inventory

Item 1. Facts: `structural.missing_optional_resource` carries `table=resource.name`; the CLI table is `corral/cli/render.py:render_issues`; the HTML report is `netstead/map/render.py:render_validation_html` + `templates/validation_report.html.j2` ("Unlocated findings" list); the Workbench Issues tab lists through `routes/edit.py` + `static/js/issues.js`.

**Files:**
- Create: `packages/corral/corral/reports/grouping.py` — `group_issues(issues) -> list[IssueGroup]` where `IssueGroup(code, severity, table_names: list[str], issues: list[Issue], groupable: bool)`; a group is formed when ≥ 2 issues share `code` and have `row is None` (unlocated, structural) — the message differs only by resource; plus `resource_inventory(report, spec) -> list[{name, required, present, rows}]`.
- Modify: `corral/cli/render.py:render_issues` — print a severity summary line first (`2 errors · 1 warning · 16 info`), then the table with grouped rows rendered as one row: code, table column shows `16 tables`, message `optional resources absent: location, movement, ... (+N more)` when over 8 names; a `--all` flag (`netstead validate --all`) expands the groups. The package source path is printed once above the table (`source: /path`), and removed from the grouped message.
- Modify: `netstead/map/render.py` + `templates/validation_report.html.j2` — "Unlocated findings" becomes `<details>` collapsed by default with the count in `<summary>`; inside, grouped codes render as one line with the resource list; add a "Resources" section (table: resource · required/optional · present ✓/✗ · rows) above the findings, built from `resource_inventory`.
- Modify: `workbench/issues.py` / `static/js/issues.js` — the Issues list shows a group as one collapsible row (`▸ structural.missing_optional_resource (16)`) that expands to the individual findings; server returns `group: {code, count}` on each paged issue, or the client groups the page — choose the client (the page is small) and document it.
- Test: `packages/corral/tests/reports/test_grouping.py`, `corral/tests/cli/test_render.py` (snapshot of the summary line and the grouped row), `packages/netstead/tests/test_map_render.py` (or the report's existing test file), `tests/test_workbench_js.py` for the pure grouping helper (`issuegroups.js`).

- [ ] **Step 1: failing `group_issues` tests**: 16 `structural.missing_optional_resource` issues with distinct `table` and `row=None` → one group with `len(table_names)==16`; two `fk.unverifiable` issues with rows → not grouped (groups of size 1, `groupable=False`).
- [ ] **Step 2: implement; → PASS.**
- [ ] **Step 3: CLI render tests** (`rich` console captured at width 120): the first line matches `r"\d+ errors? · \d+ warnings? · \d+ info"`; exactly one row contains `optional resources absent`; with `all=True` sixteen rows do.
- [ ] **Step 4: HTML report test**: the rendered HTML for Leavenworth contains `<details` wrapping "Unlocated findings (17)", the source path appears exactly once, and a `Resources` table lists `zone` as absent and `link` as present.
- [ ] **Step 5: Workbench Issues grouping** (client-side `issuegroups.js` pure helper + `issues.js` rendering) with a Node test.
- [ ] **Step 6: run the docs CLI contract test** since `validate` output is documented: `uv run --all-extras pytest packages/netstead/tests/test_documented_cli_contract.py -m "" -q`; update any documented expected output.
- [ ] **Step 7: CHANGELOG (corral + netstead) + commit** — `feat(reports): severity summary, grouped structural findings, resource inventory in the CLI, HTML report and Workbench`.

---

### Task 8: Top bar: Open with recents, Save dialog, validity light

Items 15, 17, 19.

**Files:**
- Modify: `index.html` (remove `#recent`; `#open-wizard` becomes a split control: button + `#open-menu` dropdown listing recents; add `#save-btn` icon button + `#save-dialog` `<dialog>`; add `#valid-light` icon button in header row 1 before `#count`), `app.css`
- Modify: `static/js/header.js` (recents dropdown: the existing localStorage recents list, max 3, each entry opens the network on click; "Browse…" opens the wizard), `static/js/edits.js` (move the save form logic into `static/js/savedialog.js`; the Edits panel keeps only the edit list + undo/redo), `static/js/issues.js` / new pure `validity.js` (`validityState(server) -> {level: "unknown"|"stale"|"clean"|"warn"|"error", tip: string}` from `server.issues[active]` counts and `stale`)
- Test: `test_workbench_js.py`, `test_workbench_static.py`

**Behaviour:**

1. Open: `#open-wizard` (text "Open…" with folder icon). Clicking shows a small menu: up to 3 recent networks (label = name, tooltip = path) then a divider and "Browse / import…" which opens the existing wizard. Recents are what `header.js:23-39` already stores (secrets stripped).
2. Save: `#save-btn` (`save` icon, tip "Save a copy…") opens `#save-dialog`: name, format select (parquet / csv / duckdb / zip), folder picker (the existing `createFileBrowser`), the warnings confirmation (`savePrompt`) folded into the dialog as a checkbox when warnings exist, Save / Cancel. Dispatches the same `save_network` job as today. The "SAVE A COPY" block leaves the Edits panel.
3. Validity light: `#valid-light` shows `circle` (grey, tip "Not validated yet") / `circle` amber-outlined (tip "Validated vN, the network changed since: re-run") / `check-circle` green ("Valid: 0 errors, 0 warnings") / `alert-triangle` amber ("1 warning, 16 info") / `x-circle` red ("3 errors, 1 warning"). Clicking it opens the Validate panel (Task 9 renames Issues → Validate; until then, the Issues panel). Updates on every `state` SSE event.

- [ ] **Step 1: Node tests for `validityState`** covering all five levels and the tip text (counts phrasing: `1 error, 2 warnings, 16 info`; singular/plural).
- [ ] **Step 2: run → FAIL; implement; → PASS.**
- [ ] **Step 3: markup, CSS, wiring; static test → PASS.** `edits.js` tests (if any Node tests cover `savePrompt`) still pass.
- [ ] **Step 4: browser check**: Open menu lists Leavenworth as a recent after reopening; Save dialog writes `leavenworth-copy` parquet to a chosen folder (job completes); the light turns grey → green/amber after Run validation → amber-outlined after an edit.
- [ ] **Step 5: CHANGELOG + commit** — `feat(workbench): Open menu with recents, top-level Save dialog, validity indicator`.

---

### Task 9: Side panel restructure and default colouring

Items 9 (core part), 10, 14.

**Files:**
- Modify: `index.html` (dock sections), `static/js/workspaces.js` / `slots.js` (panel ids + order), `static/js/selection.js`, `side.js`, `edits.js`, `issues.js`, `assistant.js`, `panels.js`, `main.js`, `app.css`
- Modify: `workbench/session.py` (`DEFAULT_STYLE`: `color_by="facility_type"`, new `node_color_by="ctrl_type"`), `workbench/actions.py:SetStyle` (`node_color_by`), `static/js/map.js` (node colour from a categorical column with the same palette logic links use), `panels.js` (a "Color nodes by" select), `viz/styling.py` if the palette helper lives there
- Modify: `plugins/project-cards/netstead_project_cards/static/main.js` only to move its panel into the new `cards` workspace id (Task 10 does the rest)
- Test: `test_workbench_session.py` (defaults), `test_workbench_js.py`, `test_workbench_static.py`, plugin tests still pass

**Target layout** (dock tabs, in order):

1. **Find** (`id: find`): the utterance box with Select / Ask and the provider/model picker (moved from header row 2, which is removed), the Selection content (anchors, diagnostics, export, saved selections), Highlighted links, and **View** controls: layers toggles, colour links by, colour nodes by, width by, show direction / offset, filter links (moved out of the map popovers; the map keeps only zoom/highlight/fit buttons and the legend). The `#layers-panel` / `#settings-panel` popovers are deleted; `panels.js` wires the same ids inside the Find pane. Header row 1 keeps: Open, Save, view mode, counts, validity light, Jobs, Commands, Settings.
2. **Inspect** (`id: inspect`): Details for the focused feature + related records (today's Details tab minus highlights).
3. **Edit** (`id: edit`): edit list, undo/redo, edit warnings (moved here from Issues), and a "Save selection as card…" button that is only enabled when the project-cards plugin is loaded (it dispatches `showPanel("cards")` and the plugin's new-change command).
4. **Cards** (`id: cards`): the plugin's panel (Task 10).
5. **Validate** (`id: validate`): run / export icons, status line, facets, the grouped findings list (Task 7), and the interactive behaviour: clicking a finding focuses and zooms the map to it (already in `issues.js:selectIssue`; verify it also works for findings anchored on a node and on an unlocated coordinate; add the `crosshair` icon on each located finding as an explicit "zoom to" affordance).
6. **Assistant** stays as is, after Validate.

Defaults: `color_by = "facility_type"` for links and `node_color_by = "ctrl_type"` for nodes when those columns exist in the network (fall back to `"none"` otherwise, and never raise); the legend shows both. `SetStyle` gains `node_color_by` with the same validation as `color_by` (categorical or numeric column of the node table).

- [ ] **Step 1: session tests**: a fresh Leavenworth session's `state()["style"]` has `color_by == "facility_type"` and `node_color_by == "ctrl_type"`; a network without `ctrl_type` (build a tiny one in the test, or drop the column from a copy) gets `"none"`.
- [ ] **Step 2: run → FAIL; implement; → PASS.**
- [ ] **Step 3: dock restructure** (HTML + the panel registrations). Keep element ids where the JS depends on them; `test_workbench_static.py` guards this. Remove header row 2 and the two map popovers. Run the static test and the full Workbench JS tests → PASS. Run `uv run --all-extras pytest plugins/project-cards/tests -q` → PASS (the plugin registers a panel by id; nothing else about it changes here).
- [ ] **Step 4: browser check**: five tabs in the order above; Select from the Find tab resolves "Pine Street"; View controls change the map; nodes are coloured by control type and links by facility type on open; the Validate tab lists grouped findings and clicking a `fk.unverifiable`-style unlocated finding does nothing harmful while a located one zooms.
- [ ] **Step 5: CHANGELOG + commit** — `feat(workbench): Find / Inspect / Edit / Cards / Validate panels; selection and view controls move into the side panel; nodes coloured by ctrl_type and links by facility_type by default`.

---

### Task 10: Cards panel: catalog, applicability, preview, applied record, session summary

Item 9 (cards part). Facts: the plugin already has a draft card, `project_cards.import` (a YAML path inside `io.allowed_roots`, applies the card's changes to the draft), `project_cards.preview` (derived network shown on the map), `CardsState.committed`, and `/api/history` for a session log. There is no directory listing on `Host` and no `catalog` plugin; `catalog.add_card` is an optional hook.

**Files:**
- Modify: `packages/netstead/netstead/workbench/plugins/host.py` (`readable(path) -> Path`, `list_dir(path) -> list[{name, path, kind}]` delegating to `workbench/files.py:list_dir` inside `io.allowed_roots`), `plugins/spec.py` (`HOST_API = "1.7"`, changelog comment), `tests/test_workbench_host_api_1_7.py` (new, patterned on `test_workbench_host_api_1_6.py`)
- Modify: `plugins/project-cards/netstead_project_cards/plugin.py` (settings model gains `catalog_dir: str | None`), `state.py` (`catalog: Catalog | None` with `{path, cards: [CatalogEntry]}`, `applied: list[AppliedCard]` = committed ∪ imported-from-catalog with `source: "committed" | "catalog"`, timestamps), `actions.py` + `handlers.py` (`project_cards.open_catalog(path)`, `project_cards.refresh_catalog`, `project_cards.check_card(card_id)` → applicability, `project_cards.preview_card(card_id)` / reuse `preview`, `project_cards.apply_card(card_id, options)` → import + commit-to-network without writing YAML (it is already a file), `project_cards.session_summary()` → text), `routes.py` (`GET /catalog` listing with applicability cache, `GET /summary.txt`)
- Create: `plugins/project-cards/netstead_project_cards/catalog.py` (pure: scan a folder for `*.yml|*.yaml|*.json` cards, read each with the existing tolerant reader, compute `applicability(card, network) -> {ok: bool, changes: [{describe, resolved_ids: int, missing_ids: [...], reason}]}` by resolving each change's facility against the open network *without mutating*: id selections check the ids exist; query selections use the provenance ids (`netstead.resolved[i]`) and, when absent, report `reason: "query without provenance; re-resolution not supported"`), `summary.py` (pure: build the session summary text from `applied`, the draft, and the `project_cards.*` entries of `/api/history`)
- Modify: `plugins/project-cards/netstead_project_cards/static/main.js`, `controller.js`, `cardmodel.js` (the Cards panel: **Catalog** section [folder path + Open/Refresh, card list with ✓ / ✗ / ? applicability badges, per-card Preview / Apply… / Details], **Draft** section [existing compose/draft/commit UI], **Applied this session** list, **Session summary** [Copy / Download buttons])
- Modify: `plugins/project-cards/tests/*` (routes, handlers, frontend model, frontend main), `plugins/project-cards/README.md`, `CHANGELOG.md`
- Test: as listed, plus `packages/netstead/tests/test_workbench_plugins.py` for the Host additions

**Behaviour:**

1. Host API 1.7: `host.readable(path)` resolves a path inside `io.allowed_roots` for reading (same `PathNotAllowed` as `writable`, no write intent); `host.list_dir(path)` returns the entries `files.list_dir` returns, filtered to files and folders (no hidden entries). `project_cards.import` switches to `readable`.
2. Catalog: `open_catalog(path)` validates with `host.readable`, scans (non-recursive, then one level of sub-folders), stores entries `{card_id (path-relative), path, project, tags, change_count, applicability: null}`; `refresh_catalog` rescans; the route `GET /catalog` returns entries and runs `check_card` lazily for entries whose applicability is `null` (cap work: compute at most 50 per request, the UI shows `?` until known). The plugin setting `catalog_dir` is the default folder opened on load if set.
3. Preview a catalog card: reuse `project_cards.preview` on a derived network with all of the card's changes applied; additionally publish `project_cards.card_selection` with the union of resolved link/node ids so core highlights them (via `wb.on` in `main.js` → `store.set({highlights})`) — this is the "see the selection the card makes visually" requirement.
4. Apply: `apply_card(card_id, options={existing_value_conflict: "error"|"warn"|"skip", ignore_missing: bool | null})` applies the card to the base network through the same lowering as a draft change (one `host.mutate` per change), records `AppliedCard{card_id, path, project, applied_at, changes, source:"catalog", edit_ids}`, and does **not** write YAML (the card already exists). It refuses when applicability is not ok unless `ignore_missing` is set.
5. Session summary: plain text (also Markdown-friendly): header (network name, source, spec version, date), *Applied cards* (one line each, with change counts and the edit ids), *Draft card* (project + changes, or "none"), *Actions* (chronological `project_cards.*` history lines with ok/failed), footer with the Python replay hint. `GET /summary.txt` serves it; the panel's Copy button copies it and Download saves `<network>-cards-session.txt`.
6. UI badges: `✓` (applies cleanly), `✗` (some ids missing / a refusal reason, shown on hover), `?` (not yet checked).

- [ ] **Step 1: Host tests** (`test_workbench_host_api_1_7.py`): `readable` inside roots returns a Path and outside raises `PathNotAllowed`; `list_dir` of a temp root lists a `.yml` file and a sub-folder and skips `.hidden`. Run → FAIL; implement; → PASS. Bump `HOST_API`; update the plugin's compat declaration and `test_plugin_loads.py`.
- [ ] **Step 2: `catalog.py` tests**: a temp folder with two valid cards (one whose ids exist in Leavenworth, one naming link id 999999) and one non-card YAML → two entries; applicability ok for the first, `missing_ids == [999999]` for the second; a query card without provenance → `reason` set, `ok` false.
- [ ] **Step 3: handler/action tests**: `open_catalog` → state snapshot has `catalog.cards` of length 2; `apply_card` on the good card mutates the network (the changed property reads back) and `applied` has one entry with `source == "catalog"`; `apply_card` on the bad card without `ignore_missing` raises `ActionError`; `session_summary()` contains the applied card's project name and the network name.
- [ ] **Step 4: route tests**: `GET /api/plugins/project_cards/catalog` returns entries with applicability; `GET .../summary.txt` is `text/plain`.
- [ ] **Step 5: front-end model tests** (`test_frontend_model.py`): `cardmodel.catalogRows(snapshot)` yields badge text `✓/✗/?` and tooltips; `test_frontend_main.py` panel harness: clicking Apply on a `✗` card opens the options dialog with `ignore_missing` unchecked.
- [ ] **Step 6: wire the panel** in `main.js`; run `uv run pytest -m "" -n auto plugins/project-cards/tests --doctest-modules plugins/project-cards/netstead_project_cards -q` → PASS.
- [ ] **Step 7: browser check**: create a catalog folder with a card committed from the draft UI; open it in the Cards tab; it shows ✓; Preview highlights its links; Apply changes the network and lists it under Applied; the summary downloads.
- [ ] **Step 8: CHANGELOGs + commit** — `feat(project-cards): Cards panel with catalog, applicability, preview, apply, applied record and session summary (Host API 1.7: readable, list_dir)`.

---

### Task 11: Documentation and plan close-out

**Files:**
- Modify: `packages/netstead/docs/quickstart-workbench.md` (the tab names and the moved selection box; note that screenshots are refreshed in a follow-up: add `<!-- screenshots: refresh after #<PR> -->` markers rather than stale images), `docs/cookbook/workbench.md`, `docs/cookbook/workbench-project-cards.md` (catalog section), `docs/reference/workbench-actions.md` (new actions: `redo_edit`, `set_style.node_color_by`, the `project_cards.*` additions), `docs/roadmap.md` (interactive geometry editing, the filter expression box), `packages/netstead/docs/changelog.md` if it is generated from CHANGELOG (check `docgen`)
- Modify: `docs/design/README.md` (this plan: move to the plans table as `implemented` with the PR number), this file's status line
- Test: `uv run --all-extras pytest packages/netstead/tests/test_documented_cli_contract.py packages/netstead/tests/test_documented_api_contract.py packages/netstead/tests/test_documented_python_contract.py -m "" -q`; `uv run --group docs mkdocs build --strict -f packages/netstead/mkdocs.yml` (requires `uv sync --group docs`)

- [ ] **Step 1: docs edits.**
- [ ] **Step 2: docs contract tests + strict build → PASS.**
- [ ] **Step 3: commit** — `docs(workbench): panels, filters, cards catalog, redo; roadmap entries for geometry editing and filter expressions`.

---

## Self-review

- **Coverage:** items 1–19 each map to a task in the table above; 20 is roadmap-only (Task 11).
- **Ordering:** Task 1 provides `icons.js` used by 4, 5, 8, 9, 10. Task 5's `/distinct` route is used by Task 6. Task 7's grouping is rendered by the Validate panel in Task 9. Task 8's validity light opens the panel Task 9 renames (it targets by id `validate`, with a fallback to `issues` until Task 9 lands). Task 10 depends on Task 9's `cards` workspace id.
- **Names used consistently:** `icon(name)`, `redo_edit` / `RedoEdit`, `redo_count`, `node_color_by`, `gridfilter.js` (`setFilter`, `conditions`, `chipText`), `/distinct`, `any_of` / `all_of` / `none_of` / `between`, `validityState`, `resize.js` (`clampSize`, `parseSizes`), `group_issues` / `resource_inventory`, Host `readable` / `list_dir`, `project_cards.open_catalog` / `refresh_catalog` / `check_card` / `apply_card` / `session_summary`.
