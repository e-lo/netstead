---
title: Author ProjectCards in the Workbench
audience: users
kind: howto
summary: Turn Workbench edits into network_wrangler ProjectCards with the project cards plugin: select, change, preview, add, commit.
---

# Author ProjectCards in the Workbench

## When to use this

You edit a GMNS network in the [Workbench](workbench.md) and want the edits as a
[ProjectCard](https://github.com/network-wrangler/projectcard): a YAML file that network_wrangler applies to its own
network, and that you review like code. The `project_cards` plugin adds this to the Workbench. Its package is
`netstead-project-cards`, in `plugins/project-cards` of the netstead repository.

The plugin covers roadway changes: property changes, deletions and additions. The transit change types are listed
but answer "not yet supported".

## Install and enable

In the netstead workspace, `uv sync --all-packages` installs `netstead-project-cards` next to netstead. The plugin is
not on PyPI yet. To add it to another environment, install it from a checkout:

```bash
uv pip install -e plugins/project-cards   # brings projectcard>=0.3.3,<0.4 and netstead[server]
uv run netstead app
```

An installed plugin is enabled: there is nothing to switch on. **Settings → Plugins** lists *Project cards* as
loaded, with its version and the plugin API it needs (1.5). If it shows an error instead, the rest of the Workbench
still runs (see [Write a Workbench plugin](workbench-plugins.md#startup-outcomes)).

## Step-by-step

1. **Select links.** Type a request such as "Benton Street" in the command bar. Or turn on **Highlight links** (▯ on
   the map), click links or shift-drag a box over them, then press **Set as selection** under **Highlighted links**
   in the **Details** panel. A click alone only focuses a link: it never changes the selection.
2. **Pick a change.** Right-click the selection (or open **Selection actions ▾** in the **Selection** panel) and
   choose **New change ▸ Property change** or **New change ▸ Deletion**. **New change ▸ Addition** adds a link, and
   optionally a new end node, without a selection, so it is only in the command palette (**Ctrl+K**, **⌘K** on a
   Mac). The **Card** dock panel opens with the form.
3. **Fill in the form.** The panel says what the change applies to ("1 picked link", or "4 links matching
   {"name": ["Benton Street"]}").
    - **Property change:** one field per column, titled with its current value when every selected link agrees
      ("lanes (now 1)"), or "(values differ)" or "(empty)". Columns that hold lists are left out. A blank field is
      left alone. **How** is *set* (a new value) or *change* (a number added to each link's current value).
      When the selected links agree on a value, the card records it as `existing`, and **If a current value differs
      when the card is applied** says what happens when the network has moved on: `error` (stop, the default),
      `warn` or `skip`.
    - **Deletion:** **Also delete end nodes no other link uses** (`clean_nodes` in the card).
    - **Addition:** the new link's fields, with `link_id` filled in with the next free id. `link_id`,
      `from_node_id`, `to_node_id`, `name`, `facility_type`, `lanes` and `allowed_uses` are required, since a
      ProjectCard needs them. Fill in the optional node form (it shows the next free `node_id`) to add an end node too.
      Core's live checks flag GMNS-required link fields the addition leaves empty (such as `directed`), and
      **Commit…** then asks you to confirm those warnings.
    - Every form has **Why (written in the card's notes)**.
4. **Preview** applies the change to a copy of the network, makes the copy active and shows it on the map. The
   network itself is untouched. The panel reads "Previewing … on …", and **Back to the network** closes the copy.
   A second preview replaces the first. If the network changes while you preview, the panel says so: preview again
   to see it.
5. **Add to card** applies the change to the network (never to the preview, which then closes) and lists it in the
   draft card. Core's **Edits** panel lists it too, with `project_cards` as its source, and the live checks run as
   they do for any edit. Each change is one step: all of it applies, or none of it. **Cancel** closes the form and
   any preview. If the selection changes while a Property change or Deletion form is open, the form says it is out
   of date and **Preview** and **Add to card** wait until you press **Reload form**, which reads it again for the
   new selection.
6. **Watch the badge.** The **Card** panel's tab shows the number of uncommitted changes with a dirty dot; its
   workspace tab shows the dot. The draft belongs to the network: switch networks and the panel shows that network's
   draft.
7. **Commit.** Under **Commit**, give the card a **Project name**, optional **Tags** (comma separated) and a
   **Card file** inside `io.allowed_roots`, then press **Commit…**. The dialog shows the network's live edit
   warnings and the card's YAML:
    - With no warnings, the button reads **Commit**.
    - With warnings, tick **I have read the warnings: commit anyway**, and the button reads **Commit anyway**. The
      card records the changes as they are.
    - A card that `projectcard`'s own validator rejects can't be committed: the dialog says why ("Not a valid
      ProjectCard yet: …").

    The file is written atomically: a failed write leaves an existing file as it was, and an existing file is
    replaced only when **Replace an existing file** is ticked. When a `catalog` plugin is installed, the card is
    also added to it (`catalog.add_card`). A new, empty draft then starts.

Committing writes the card, not the network: the edits stay pending in the **Edits** panel until you save the
network, as with any edit.

### Undo

**Undo last change** in the panel (or **Undo the draft card's last change** in the command palette) takes back the
draft's newest change, network edits included. A deletion with **Also delete end nodes** is two edits, and both are
undone. Committed cards are not undone.

Core and the plugin each undo only their own edits. Core's **Undo** won't take back a card change, and the plugin's
undo refuses when the network's newest edit is one you made yourself: undo that one first, in the **Edits** panel.

### Import a card

Under **Import a card**, give the path of a card or edit log, then press **Import**. The path must be inside
`io.allowed_roots`. Every change in the file is applied to the network and added to the draft, all or nothing: if
one fails, none are applied, and the message names the one that failed ("change 2 of 2: …"). An empty draft takes
the card's project, tags, dependencies and notes.

Import reads YAML and JSON cards. It also reads the edit log that the offline validation report's "Fix locally"
editor downloads (see [Triage and fix findings](fix-findings.md)), although that file is not a schema-valid
ProjectCard: its facility holds `model_link_id` at the top level, and its changes carry their own `notes`. Each of
those notes becomes the change's note, and is written into the card's notes on commit.

Import refuses:

- **Scoped (time-of-day) values**, in an added record or as a `scoped` property change.
- **TOML cards**: `projectcard` 0.3.3 can't read them. Use YAML or JSON.
- **Transit changes**, `pycode` and `roadway_managed_lanes`.
- **A query selection without netstead provenance.** A card that selects links by a query (`name`, `ref`, …) is
  applied to the link ids recorded in its notes, so only cards that the plugin wrote can be imported this way.
  Re-resolving a query on another version of the network is planned for later.
- **A deletion that doesn't select by id** (`model_link_id` / `model_node_id`).

## What goes in the card

The card uses network_wrangler's names. GMNS names map to them through a data file,
`netstead_project_cards/mappings/gmns_to_wrangler.yaml` (`link_id` → `model_link_id`, `from_node_id` → `A`,
`to_node_id` → `B`, `facility_type` → `roadway`). A column it doesn't list keeps its GMNS name.

| You did | The card says |
|---|---|
| Picked links (click or box) | `facility: {links: {model_link_id: [...], ignore_missing: false}}` |
| Selected by a request ("Benton Street") | The query, `facility: {links: {name: [Benton Street], ignore_missing: true}}`, plus a `netstead.resolved[<i>]` line in the card's `notes` |
| Deleted links | `roadway_deletion` by id, `ignore_missing: false`; **Also delete end nodes** is `clean_nodes: true` |
| Added a link | `roadway_addition` in network_wrangler's names, with `walk_access`, `bike_access` and `drive_access` derived from `allowed_uses` |
| Wrote a note | a `netstead.note[<i>]` line in the card's `notes` |

### Provenance notes

A ProjectCard change has no field for extra information, so the plugin keeps it in the card's top-level `notes`,
one line per item, numbered by the change's position (from 0):

```yaml
notes: |-
  Ski season fixes
  netstead.note[0]: issue 42: two lanes
  netstead.resolved[1]: model_link_id=[1, 5, 6, 8]
```

- `netstead.resolved[<i>]: model_link_id=[...]` records the links that change `i`'s query matched when you made the
  change. Import applies the query to these ids.
- `netstead.note[<i>]: …` is change `i`'s **Why**, on one line.

The rest of the notes are yours: the commit's `notes` field, from Python or the assistant. Import reads the provenance lines back and keeps the
other lines as the card's notes.

### `ignore_missing`

`ignore_missing` says what network_wrangler does when a selected record is not in the network: fail (`false`) or
skip it (`true`).

- **Picked links** are written by id with `ignore_missing: false`. A fix aimed at specific links should fail loudly
  when one of them is gone.
- **A query selection** keeps the query's own `ignore_missing`, which is `true` (network_wrangler's default) when
  the query has none. The plugin applies it now too: a resolved id the network no longer has is skipped and listed,
  instead of refusing the change.
- **Deletions** are always written by id with `ignore_missing: false`, even when the selection came from a query.
- **Reading a card**, a selector without `ignore_missing` reads as `true`. A deletion's flag is dropped, and the
  deletion is written back with `false`. The offline edit log's ids read as `false`.

## From Python

Each step is a recorded Action in the session history, so **Copy as Python** in the history strip replays it. The
copied lines import from `netstead_project_cards.actions`:

| Action | What it does |
|---|---|
| `project_cards.add_change` (`AddChange`) | Apply one change and add it to the network's draft. |
| `project_cards.undo_change` (`UndoChange`) | Undo the draft's last change. |
| `project_cards.preview` (`PreviewChange`) | Preview a change on a copy of the network. |
| `project_cards.close_preview` (`ClosePreview`) | Close the preview. |
| `project_cards.commit` (`CommitCard`) | Write the draft as a card file and start a new draft. |
| `project_cards.import` (`ImportCard`) | Apply a card or edit log to the draft, all or nothing. |

`session.dispatch` (or its alias `app.do`) runs them. A change targets the shared selection (`use_selection=True`),
a saved selection (`saved_selection="<name>"`, Python and the assistant only), or names its own facility. This
example opens the Leavenworth fixture, sets link 1 to two lanes, undoes a second change and commits the card to a
temporary folder. `plugins=[plugin()]` installs just this plugin; a `Session()` without it loads every installed
plugin, as `netstead app` does:

```python
import tempfile
from pathlib import Path

from netstead.fixtures import leavenworth
from netstead.workbench import OpenNetwork, Select, Session
from netstead_project_cards import plugin
from netstead_project_cards.actions import AddChange, CommitCard, UndoChange
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange

fixture, out = leavenworth.parquet_dir(), Path(tempfile.mkdtemp())
roots = {"io.allowed_roots": [str(out), str(fixture.parent)]}
app = Session(project_dir=out, overrides=roots, plugins=[plugin()])
app.dispatch(OpenNetwork(source=str(fixture)))
app.dispatch(Select(link_ids=[1]))
lanes = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(existing=1, set=2)}, note="issue 42")
app.dispatch(AddChange(use_selection=True, change=lanes))
app.dispatch(AddChange(use_selection=True, change={"type": "roadway_property_change",
                                                   "property_changes": {"free_speed": {"change": -5}}}))
app.dispatch(UndoChange())  # takes back the free_speed change
result = app.dispatch(CommitCard(path=str(out / "link-1-lanes.yml"), project="Link 1 lanes", confirm_warnings=True))
print(result["card_id"], result["changes"])  # link-1-lanes 1
```

A change can be a model, as for `lanes`, or a plain dict, as in the copied lines. `confirm_warnings=True` stands in
for the dialog's confirmation: without it, a network with live edit warnings refuses the commit. The written file
loads with `projectcard`'s own reader:

```python
import projectcard

print(projectcard.read_card(out / "link-1-lanes.yml", validate=True).project)  # Link 1 lanes
```

Selecting by a request needs a language model (see [Explore in the Workbench](workbench.md)). Preview and import
work the same way:

<!-- doctest: skip -->
```python
from netstead_project_cards.actions import ClosePreview, ImportCard, PreviewChange

app.dispatch(Select(utterance="Benton Street"))
app.dispatch(PreviewChange(use_selection=True, change={"type": "roadway_deletion"}))
app.dispatch(ClosePreview())
app.dispatch(ImportCard(path="cards/edit-log.yaml"))
```

Over HTTP, `POST /api/actions` takes the same Actions as JSON (`{"type": "project_cards.add_change", ...}`). The
panel also reads two routes: `GET /api/plugins/project_cards/form` (the form for a change type on the selection)
and `GET /api/plugins/project_cards/commit-check` (the warnings, problems and YAML the commit dialog shows). Both
only read.

The library behind the Actions is usable on its own: `DraftCard` builds a card from the change model and writes it
with `to_yaml()`, `read_card_file` reads one back, and `netstead_project_cards.validate.card_problems` runs
`projectcard`'s validator.

## With the assistant

The assistant offers a plugin's Actions only when you opt in. In **Settings → Assistant: actions & plugins**, or in
`~/.config/netstead/config.toml`:

```toml
[assistant]
plugins = ["project_cards"]
compact_tools = "off"
```

`plugins` is the opt-in. `compact_tools = "off"` matters only with a model on this machine (Ollama): its default,
`auto`, gives a local model a smaller set of tools that leaves out the cards tools.

Every `project_cards` Action changes a network, opens or closes a preview, or writes a file, so the assistant
drafts each one for you to approve. Nothing runs until you do. A drafted change made while a preview was open is
refused as stale if the previewed network has changed since.

## Pitfalls

- **Roadway only.** Transit change types answer "not yet supported" in the palette, the Actions and the
  assistant. `roadway_managed_lanes` and `pycode` are not supported either.
- **One number or text per property.** A card can't set a property to empty (clear a cell with a core edit
  instead) or to a list. `true` / `false` are written as `1` / `0`. Scoped (time-of-day) values are not supported.
- **A change must be a valid ProjectCard before it is applied.** For example, a request that names both a street and
  a route number gives a query that ProjectCard doesn't allow, so **Add to card** refuses it: select by one of them.
- **An added record's fields must be columns the network has.** A field with no column answers "not yet
  supported".
- **A draft lives in the Workbench session.** Closing the network or the app drops its draft. The network's edits
  stay pending until you save or discard them. Commit to keep the card.
- **A change that applies nothing is refused**, for example a query whose links are all gone with
  `ignore_missing: true`, or every property skipped by `skip`.
- **Importing a query needs provenance.** See [Import a card](#import-a-card).
- **The panel works on the shared selection.** To use a saved selection, select it first, or use
  `saved_selection` from Python or the assistant.
- **A committed card's id is not written to the network's lineage.** It is listed in the plugin's state
  (`plugins.project_cards.committed`) for the session.

## See also

- [Explore in the Workbench](workbench.md): selection, edits, the Edits panel, saving.
- [Write a Workbench plugin](workbench-plugins.md): the plugin API this plugin is built on.
- [Triage and fix findings](fix-findings.md): the offline report's edit log.
- [projectcard](https://github.com/network-wrangler/projectcard): the ProjectCard schema and validator.
