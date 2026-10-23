---
title: Workbench Actions reference
audience: both
kind: reference
summary: The 21 core Workbench Actions — fields, defaults, whether each mutates or runs as a job, and the replay snippet form — plus how to dispatch them from Python.
---

# Workbench Actions reference

## Summary

Every state change in a Workbench session — a click in the browser, a line of Python, a step the assistant drafts — is one typed Action: a pydantic model in `netstead.workbench.actions`, discriminated on its `type` string. The session validates the Action, applies it under its lock, records a `HistoryEntry` and publishes the new state. Because the vocabulary is the same everywhere, anything the UI did can be copied as Python and replayed.

This page lists the 21 core Actions in `netstead.workbench.actions.CORE_ACTIONS` (plugins register more). For each: the fields with their types and defaults (a field with no default is required), the class flags, and the snippet `to_python` writes for it. The flags are class attributes:

* **mutates** — the assistant must draft the Action and get approval before applying it; it also marks what `undo` and rollback care about.
* **runs as job** — the slow work runs on a background job thread; `Session.dispatch` waits for it, `Session.submit` returns the `Job`.
* **replay** — what the "copy as Python" snippet does with the fields: fields equal to their default are omitted; `replay_omits` fields are left out (`expected_version`, a guard that only means something in the live session); `replay_overrides` are forced (`BuildNetwork` always replays with `approved=True`).

All 21 core Actions have `assistant = True`, the class flag that lets an Action be offered to the natural-language assistant at all. The flag matters for plugin Actions: a plugin that is opted in through `assistant.plugins` can still keep one of its Actions out of the assistant's tools by setting `assistant = False` on that class. Which *core* Actions the assistant may call is a setting, `assistant.actions` (14 by default; `open_network` and `close_network` are never offered) — see the [Settings reference](settings.md#assistant-the-natural-language-assistant).

## Actions

### Open, close, switch

#### `OpenNetwork` — `open_network`

Load a GMNS network from a local path (inside `io.allowed_roots`) or URL and make it active. Runs as a job (`open <name>`); does not mutate. Result: `{"net_id": ...}`.

| Field | Type | Default |
|---|---|---|
| `source` | `str` | required |
| `label` | `str \| None` | `None` |
| `net_id` | `str \| None` | `None` (derived from the source) |

Replay: `app.do(OpenNetwork(source='/data/net', label='Leavenworth'))`

#### `CloseNetwork` — `close_network`

Close an open network. Mutates (it throws away unsaved edits).

| Field | Type | Default |
|---|---|---|
| `net_id` | `str` | required |

Replay: `app.do(CloseNetwork(net_id='leavenworth'))`

#### `SetActiveNetwork` — `set_active_network`

Switch which open network the map and tables show.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str` | required |

Replay: `app.do(SetActiveNetwork(net_id='leavenworth'))`

### Select, pin, save, use, delete selection

#### `Select` — `select`

Select features by natural-language utterance or explicit ids. Give exactly one of `utterance` or `link_ids`. `query` is the parsed intent for `utterance` (the selection tool's arguments); with it no parser runs, so a replay is exact — the assistant records its selections this way.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` (the active network) |
| `component` | `"roadway" \| "transit"` | `"roadway"` |
| `utterance` | `str \| None` | `None` |
| `link_ids` | `list[int \| str] \| None` | `None` |
| `query` | `dict \| None` | `None` |

Replay: `app.do(Select(link_ids=[1, 2, 3]))` or `app.do(Select(utterance='Ski Hill Drive'))`

#### `ClearSelection` — `clear_selection`

Clear the current selection. No fields.

Replay: `app.do(ClearSelection())`

#### `PinAnchor` — `pin_anchor`

Pin the current selection's `from` or `to` anchor to one node, then resolve the selection again (`node_id=None` unpins). A pin that resolves nothing is refused.

| Field | Type | Default |
|---|---|---|
| `role` | `"from" \| "to"` | required |
| `node_id` | `int \| str \| None` | `None` |

Replay: `app.do(PinAnchor(role='to', node_id=60))`

#### `SaveSelection` — `save_selection`

Save the current selection under `name` (refused if the name is taken). Names: letters, digits, space, `_ . -`, up to 64 characters.

| Field | Type | Default |
|---|---|---|
| `name` | `str` | required |

Replay: `app.do(SaveSelection(name='ski hill'))`

#### `UseSelection` — `use_selection`

Make a saved selection current again, resolved against its network as it is now.

| Field | Type | Default |
|---|---|---|
| `name` | `str` | required |

Replay: `app.do(UseSelection(name='ski hill'))`

#### `DeleteSelection` — `delete_selection`

Forget a saved selection. Mutates (it throws away something the user made, so the assistant drafts it).

| Field | Type | Default |
|---|---|---|
| `name` | `str` | required |

Replay: `app.do(DeleteSelection(name='ski hill'))`

### Style, filter, navigate

#### `Style` — `style`

Partially update the map style; omitted fields are unchanged. `color_by` colors links by a column of the link
table and `node_color_by` colors nodes by one of the node table (`"none"` for a single color); `width_by`
sizes links by a numeric column (`"none"` for widths by lane count). A fresh session defaults `color_by` to
`"facility_type"` and `node_color_by` to `"ctrl_type"` when those columns exist (`"none"` otherwise).

| Field | Type | Default |
|---|---|---|
| `color_by` | `str \| None` | `None` |
| `node_color_by` | `str \| None` | `None` |
| `width_by` | `str \| None` | `None` |
| `ramp` | `"YlOrRd" \| "Blues" \| "Viridis" \| None` | `None` |
| `show` | `dict[("links" \| "nodes" \| "labels" \| "selection"), bool] \| None` | `None` |
| `colors` | `dict[("links" \| "nodes" \| "selection"), [r, g, b]] \| None` | `None` |
| `offset` | `bool \| None` | `None` |
| `show_direction` | `bool \| None` | `None` |
| `show_legend` | `bool \| None` | `None` |

Replay: `app.do(Style(width_by='lanes'))`

#### `Filter` — `filter`

Show only the links matching every condition; the map dims the rest and the table's Filtered scope lists them. A constrained predicate over link columns, never SQL. `where=None` (or an empty list) clears the filter; at most `MAX_FILTER_CONDITIONS` conditions.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `where` | `list[FilterCond] \| None` | `None` |

A `FilterCond` is `{col: str, op: FilterOp, val}` with `op` one of `eq ne lt lte gt gte in contains isnull notnull any_of all_of none_of between`; `in` takes a list, `isnull` / `notnull` take no value. The token operators `any_of` / `all_of` / `none_of` match a list of values against a comma-separated cell such as `allowed_uses` (`"auto"` matches `"auto,truck,walk"`; a bare value is read as a one-element list; an empty cell matches only `none_of`). `between` takes `[low, high]`, inclusive.

Replay: `app.do(Filter(where=[{'col': 'lanes', 'op': 'gte', 'val': 2}]))`

#### `Navigate` — `navigate`

Move the map camera. Give exactly one of `bbox`, `to_network`, `to_selection`, `record`.

| Field | Type | Default |
|---|---|---|
| `bbox` | `(west, south, east, north) \| None` | `None` |
| `to_network` | `bool` | `False` |
| `to_selection` | `bool` | `False` |
| `record` | `RecordRef \| None` | `None` |

A `RecordRef` is `{table: "link" | "node", id: int | str}`.

Replay: `app.do(Navigate(to_selection=True))` or `app.do(Navigate(record={'table': 'link', 'id': 170}))`

### Validation

#### `RunValidation` — `run_validation`

Validate a network — spec, keys, structure, and the quality rules configured in `validation.rules` — as a job (`validate <network>`). Does not mutate. Result: `{net_id, version, stale, superseded, counts: {error, warning, info}, unknown_rules}`.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `quality` | `bool` | `True` |

Replay: `app.do(RunValidation())`

### Edit cells, delete rows, add rows, undo, save

#### `EditCells` — `edit_cells`

Set cells on rows of one table, picked by primary key (`None` clears a cell). Values are coerced to the column's storage type; key columns and unknown columns are refused. Spec problems with the new values come back as warnings, never blocks. Mutates. Replay omits `expected_version`.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `component` | `"roadway" \| "transit"` | `"roadway"` |
| `table` | `str` | `"link"` |
| `ids` | `list[int \| str]` (1–10 000) | required |
| `values` | `dict[str, bool \| int \| float \| str \| None]` (≥ 1) | required |
| `note` | `str \| None` (≤ 500) | `None` |
| `expected_version` | `int \| None` | `None` |

Replay: `app.do(EditCells(ids=[170], values={'lanes': 2}))`

#### `DeleteRows` — `delete_rows`

Delete rows of one table by key, with every row that depends on them through the spec's foreign keys (a link takes its lanes and time-of-day rows; a node a link still uses is refused). Mutates. Replay omits `expected_version`, so a replayed delete plans again against the network it runs on.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `component` | `"roadway" \| "transit"` | `"roadway"` |
| `table` | `str` | `"link"` |
| `ids` | `list[int \| str]` (1–10 000) | required |
| `note` | `str \| None` (≤ 500) | `None` |
| `expected_version` | `int \| None` | `None` |

Replay: `app.do(DeleteRows(ids=[170]))`

#### `AddRows` — `add_rows`

Append rows to one table (Python and the API only; the app has no drawing tool). Each row needs its key. Mutates.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `component` | `"roadway" \| "transit"` | `"roadway"` |
| `table` | `str` | required |
| `rows` | `list[dict[str, bool \| int \| float \| str \| None]]` (1–10 000) | required |
| `note` | `str \| None` (≤ 500) | `None` |

Replay: `app.do(AddRows(table='node', rows=[{'node_id': 900, 'x_coord': -120.66, 'y_coord': 47.6}]))`

#### `UndoEdit` — `undo_edit`

Revert the network's last edit, when the Workbench made it (a plugin undoes its own changes). Mutates.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |

Replay: `app.do(UndoEdit())`

#### `RedoEdit` — `redo_edit`

Re-apply the network's newest undone edit (the one `undo_edit` took back most recently). Any other change to
the network since the undo makes it unavailable, and it is refused with "nothing to redo" when there is
nothing to redo. Mutates.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |

Replay: `app.do(RedoEdit())`

#### `SaveNetwork` — `save_network`

Write the open network, with its edits, as a new copy in `output_dir`; the source is never written to and the copy never overwrites anything. Refused while the network has open edit warnings unless `accept_warnings`. Mutates; runs as a job (`save <name>`). A replayed save writes the copy again, so it fails with "already exists" while the earlier copy is there.

| Field | Type | Default |
|---|---|---|
| `net_id` | `str \| None` | `None` |
| `output_dir` | `str` | required |
| `output_format` | `"parquet" \| "csv" \| "duckdb" \| "zip"` | required |
| `name` | `str` (file-name characters, ≤ 100; a format suffix must match `output_format`) | required |
| `accept_warnings` | `bool` | `False` |

Replay: `app.do(SaveNetwork(output_dir='/data/out', output_format='parquet', name='leavenworth-edited'))`

### Build

#### `BuildNetwork` — `build_network`

Build a GMNS network from OSM or Overture, write it to `output_dir`, then open it from disk. Give exactly one of `area` (fetch from the service) or `input_file` (a local `.osm` / Overpass `.json`, or an Overture snapshot folder). Without `approved`, a build estimated over `app.approve_above_s` (or that cannot be estimated) fails with `ApprovalRequired`. Mutates; runs as a job (`build <name>`); replay forces `approved=True`.

| Field | Type | Default |
|---|---|---|
| `source` | `"osm" \| "overture"` | required |
| `area` | `BboxArea \| PointArea \| PlaceArea \| None` (discriminated on `kind`) | `None` |
| `input_file` | `str \| None` | `None` |
| `output_dir` | `str` | required |
| `output_format` | `"parquet" \| "csv" \| "duckdb" \| "zip"` | required |
| `name` | `str` (file-name characters, ≤ 100) | required |
| `network_type` | `str` | `"drive"` |
| `extra_tags` | `list[str] \| None` | `None` |
| `spec_version` | `str \| None` | `None` (`io.spec_version`) |
| `overture_release` | `str \| None` | `None` (`overture.release`; only with `source="overture"`) |
| `label` | `str \| None` | `None` |
| `approved` | `bool` | `False` |

Replay: `app.do(BuildNetwork(source='osm', area={'kind': 'bbox', 'bbox': (-78.91, 35.98, -78.88, 36.01)}, output_dir='/data/out', output_format='parquet', name='durham-core', approved=True))`

### Set setting

#### `SetSetting` — `set_setting`

Change a setting (dotted key) for this session, or persist it to the user or project file. Refuses API-key-shaped keys and values at validation, before anything is recorded; `io.allowed_roots` is refused by the session at every scope. Mutates.

| Field | Type | Default |
|---|---|---|
| `key` | `str` | required |
| `value` | `Any` | `None` (removes the key from that scope) |
| `scope` | `"session" \| "user" \| "project"` | `"session"` |

Replay: `app.do(SetSetting(key='viz.basemap', value='esri'))`

## From Python

`netstead.workbench.Session` is the action bus. Construct one (it loads settings from the usual layers; `overrides` is the session layer), dispatch Actions with `session.dispatch(...)` — `session.do` is the same method, which is why copied snippets read `app.do(...)` — and read `session.history`, a list of `HistoryEntry` records each carrying the Action as JSON, its `python` replay snippet, `ok`, `result` and any `error`.

The fixture folder is passed as an allowed root because a `Session` built from Python only trusts the folders in `io.allowed_roots`:

```python
from netstead.fixtures import leavenworth
from netstead.workbench import Filter, OpenNetwork, Select, Session, Style

source = str(leavenworth.csv_dir())
app = Session(overrides={"io.allowed_roots": [source]})

print(app.do(OpenNetwork(source=source, label="Leavenworth")))
selection = app.do(Select(link_ids=[1, 2, 3]))
print(selection["status"], selection["link_ids"])
app.do(Style(width_by="lanes"))
app.do(Filter(where=[{"col": "lanes", "op": "gte", "val": 2}]))

for entry in app.history:
    print(entry.seq, entry.ok, entry.python)
```

Expected:

```text
{'net_id': 'leavenworth'}
resolved [1, 2, 3]
1 True app.do(OpenNetwork(source='...
2 True app.do(Select(link_ids=[1, 2, 3]))
3 True app.do(Style(width_by='lanes'))
4 True app.do(Filter(where=[{'col': 'lanes', 'op': 'gte', 'val': 2}]))
```

Three details worth knowing:

* `dispatch` raises the recorded failure's type — `ApprovalRequired` (with `.estimate`), `PathNotAllowed`, `NotSupportedYet`, `JobCancelled`, else `ActionError`. `dispatch_recorded` returns the `HistoryEntry` instead and never raises `ActionError`.
* A `dict` is accepted in place of a model: `app.do({"type": "style", "width_by": "lanes"})` is parsed through the session's registry (`session.actions`), which also knows plugin Actions. `netstead.workbench.actions.parse_action` parses core Actions only.
* `Session.add_network(net)` registers an already-loaded `Network` without an Action, so it is not in the history and a copied script does not replay it.

## See also

* [Explore in the Workbench](../cookbook/workbench.md) — the same Actions from the browser, and "Every action is replayable".
* [Write a Workbench plugin](../cookbook/workbench-plugins.md) — registering plugin Actions on the same bus.
* [Settings reference](settings.md) — `assistant.actions`, `app.approve_above_s`, `io.allowed_roots`.
* [API reference](api.md#workbench) — `Session`, `BaseAction`, `CORE_ACTIONS`, `to_python`.
