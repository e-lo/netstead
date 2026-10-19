---
title: "Quickstart: the Workbench"
audience: users
kind: tutorial
summary: Open the bundled Leavenworth network in the browser Workbench — select, validate, fix, replay as Python, save a copy. Ten minutes, no code.
---

# Quickstart: the Workbench

Prefer code? → [Quickstart: Python](quickstart.md) does the same work from a Python session.

## What you'll build

![Sixty-second walkthrough: open, select, validate, fix, replay](assets/screenshots/workbench-walkthrough.gif){ .screenshot }
*The whole tutorial in a minute: open, select, validate, fix, replay.*

In ten minutes, with nothing but a browser, you will:

1. Open the bundled Leavenworth, WA network (339 links, 121 nodes) in the Workbench.
2. Pick a link on the map and see its row in the table, and the other way round.
3. Select a street by name, then resolve a selection the resolver found **ambiguous**.
4. Validate the network against the GMNS spec and the quality rules.
5. Fix a finding, see the edit tracked, and undo it.
6. See every step as a Python script you can run again.
7. Save the edited network as a copy, with the original untouched.

Everything runs on your machine. With `--provider stub` the selection box uses an offline pattern parser, so
nothing leaves your computer and you need no API key.

## Prerequisites

Python 3.11 or newer, and `netstead` with three extras: `[server]` is the Workbench itself, `[graph]` runs the
quality rules inside validation and the path search behind "between", and `[nl]` adds the language-model
providers you can switch to later (the offline parser needs none of them). If anything below fails to
install or start, see [Install and troubleshoot](install.md).

## Steps

### 1. Install and open the network

Install with the tool you already use. The quotes matter on macOS (zsh): see
[Install and troubleshoot](install.md#zsh-quote-the-brackets).

```bash
uv add 'netstead[server,graph,nl]'
```

(With pip: `pip install 'netstead[server,graph,nl]'`.)

The Leavenworth fixture ships inside the `netstead` wheel, so there is nothing to download. Ask Python where
it landed and keep the path in a shell variable:

```bash
LV=$(uv run python -c "from netstead.fixtures import leavenworth; print(leavenworth.csv_dir())")
echo "$LV"
```

Start the Workbench on that folder. `--provider stub` picks the offline parser so the tutorial works without
a network connection or a key.

```bash
uv run netstead app "$LV" --provider stub
```

Open <http://127.0.0.1:8850> in your browser.

You should see the **Netstead Workbench** header with the network picker showing `leavenworth`, the count
**339 links · 121 nodes**, the **Map / Split / Table** view buttons, a selection box with **Select** and
**Ask** beside it, and the map below. If the map opens zoomed out, press the map's **Zoom to full network**
button (the second of the small buttons on the map) and the downtown Leavenworth grid fills the view.

![The Workbench with the Leavenworth network open](assets/screenshots/workbench-01-open.png){ .screenshot }
*The Workbench with the Leavenworth network open: header, network picker, counts, and the map.*

### 2. Link the map and the table

Press **Split** in the header. The map stays on the left and the `link` table opens on the right, with a rail
listing every table in the network and its row count.

Click a link on the map. The **Details** panel in the dock on the right shows that link's record (its
`facility_type`, `free_speed`, `lanes`, and so on), and the table outlines its row and scrolls to it, on
whichever page it sits. Now click a different row in the table: it becomes the focused record and the map flies
to it.

Clicking never changes the selection; focus is just a view. That matters in step 3, where the selection is the
thing the Workbench records.

![Split view: a link picked on the map, its row focused in the table](assets/screenshots/workbench-02-split.png){ .screenshot }
*Split view: a link picked on the map, its row focused in the table, its record in Details.*

### 3. Select a street by name

Type `Ski Hill Drive` in the header box and press **Select** (or Enter).

Twenty links light up in the selection colour, and the **Selection** panel opens in the dock with the status
`resolved`, the phrase you typed, a diagnostic line `facility ['Ski Hill Drive']: 20 link(s)`, and the
**Fragment**: the GMNS selection fragment as JSON, listing the twenty `link_id`s. In the table bar, **Show →
Selection** lists just those rows. The strip at the bottom of the window now reads
`app.do(Select(utterance='Ski Hill Drive'))`: the same selection as Python, which step 7 comes back to.

![Ski Hill Drive selected from the header box](assets/screenshots/workbench-03-select.png){ .screenshot }
*Ski Hill Drive selected: 20 links on the map, the status and fragment in the Selection panel.*

### 4. Resolve an ambiguous selection

Replace the phrase with `Cherry Street between Prospect Street and West Center Street` and press **Select**.

This time the status is **ambiguous** and the Selection tab's badge shows `?`. Under **Anchors**, the `from`
anchor resolved cleanly (`Prospect Street → node 53 · intersection`), but the `to` anchor has more than one
intersection that could be "West Center Street": the resolver picked the nearest, node 60, and lists the other
candidate, node 61, after **Or:**. The candidates are drawn as rings on the map (the **Anchor candidates**
overlay), and the diagnostics say why: `multiple intersection candidates; picked nearest (see candidates)`.

Pin the anchor to settle it. Click `node 61` to choose the other intersection, or press **Pin node 60** to keep
the resolver's choice. The selection is resolved again: the status becomes `resolved`, the rings disappear, and
three links of Cherry Street are selected between the two pinned intersections. **Unpin** returns to the
resolver's choice. The strip shows `app.do(PinAnchor(role='to', node_id=60))`, so the pin is part of the
recorded selection, not a one-off click.

![An ambiguous selection: candidate nodes for the anchor, as rings on the map](assets/screenshots/workbench-04-ambiguous.png){ .screenshot }
*An ambiguous selection: the `to` anchor's candidate nodes drawn as rings, with Pin buttons in the Selection panel.*

### 5. Validate the network

Open the **Issues** tab in the dock and press **Run validation**. It runs as a background job (the **Jobs**
count in the header ticks while it works) and takes a few seconds on Leavenworth.

When it finishes the status line reads `Validated v0: 272 warnings, 16 info`. That is two reports in one
list. The spec checks find **0 errors, 1 warning and 16 info**: the warning is `fk.unverifiable` (`node.zone_id`
points at a `zone` table the fixture doesn't ship, so the key can't be checked) and the 16 info findings are
`structural.missing_optional_resource`, one per optional GMNS table that is absent. The quality rules add
**271 warnings** of `quality.high_speed_residential`: residential links whose `free_speed` of 40 is above the
default 35 mph threshold. The **Severity**, **Table** and **Code** filters show the same counts, for example
`quality.high_speed_residential (271)`.

Every located issue is a dot on the map. Click an issue in the list: the map flies to its link, Details shows
the record, and the table scrolls to its row and marks it. The 17 table-level findings have no place on the
map, so they sit under **Unlocated** at the bottom of the panel.

![The Issues panel after Run validation](assets/screenshots/workbench-05-issues.png){ .screenshot }
*The Issues panel after Run validation: 272 warnings and 16 info, each located issue a dot on the map.*

### 6. Fix a finding

Pick any `quality.high_speed_residential` issue and press its **Fix** button. The fix editor opens at the top
of the Issues panel on that link and that column (`link 1 · free_speed`, for the first one), with the current
value `40` filled in and the issue text as the edit's note.

Change `free_speed` to `35` and press **Apply**. One edit is sent with only the value you changed. Three things
update at once:

- The **Edits** panel lists the edit (`#1 link 1: free_speed = 35.0 (quality.high_speed_residential: …)`,
  `1 link · v1`), the status reads `1 unsaved`, and **Undo last** is enabled. The network's version moved from
  `v0` to `v1`; nothing on disk changed.
- The Issues panel re-checks the rows you touched straight away: a value the spec disagrees with would appear
  under **Edit warnings** with its own **Fix** button. This one is fine, so the count stays at 0. The stored
  validation is now stale (`the network changed since: re-run`), and the button becomes **Re-run validation**;
  the list stays so you can work down it.
- The strip shows the edit as Python: `app.do(EditCells(ids=[1], values={'free_speed': 35}, note='…'))`.

Press **Undo last** and the value goes back to 40, recorded as `app.do(UndoEdit())`. Then press **Fix** and
apply the change once more, so there is an edit to save in step 8.

![Fixing a free_speed value from an issue; the Edits panel shows the pending edit](assets/screenshots/workbench-06-fix.png){ .screenshot }
*Fixing a free_speed value from an issue; the Edits panel shows the pending edit and Undo last.*

### 7. Replay the session as Python

The strip at the bottom always shows the last action as Python, with its sequence number (`#8`); **Copy**
copies that one line. Press **Session as Python** to see the whole session as a script: the imports, then
`app = Session()`, then one `app.do(...)` line per action you took, from `OpenNetwork(source=…)` through the
selects, the pin, the validation, the edit, the undo and the edit again. **Copy all** puts it on the clipboard.

Run that script with `uv run python` and it repeats your session without the browser. Everything in the
Workbench is a typed action, so there is no "export" step to remember.

![Session as Python: the whole session as a replayable script](assets/screenshots/workbench-07-python.png){ .screenshot }
*Session as Python: the whole session as a replayable script.*

### 8. Save a copy

Open the **Edits** panel and find **Save a copy**. Name it (`leavenworth-fixed`), leave the format on
**Parquet folder**, and press **Choose folder…**. A file browser opens; move to a folder inside your home
directory and press **Use this folder**.

The save runs as a job. When it finishes, a toast reads `Saved a copy to <folder>/leavenworth-fixed (1 edit).`
and names the tables that differ from the source (`link`). In the Edits panel the edit is now marked `saved`
and the unsaved badge clears. The folder you opened in step 1 is untouched: the Workbench never writes to the
network it opened, and never overwrites an existing copy. A folder outside your home directory is refused
(`… is outside the allowed folders`), which `io.allowed_roots` can widen: see
[Which folders the app can read and write](cookbook/workbench.md#which-folders-the-app-can-read-and-write).

![Save a copy: the edited network written to a new folder](assets/screenshots/workbench-08-save.png){ .screenshot }
*Save a copy: the edited network written to a new folder; the original stays as it was.*

Stop the server with Ctrl+C in the terminal when you are done.

## Next steps

* [Visual tour](visual-tour.md) — the same eight steps, with more on what you are looking at and why it matters.
* [Explore networks in the Netstead Workbench](cookbook/workbench.md) — every panel and command: open or build
  a network, highlights and related records, filters and styling, the assistant, settings, language models.
* [Quickstart: Python](quickstart.md) — load, validate, quality-check and scope the same fixture from Python.
* [Validate a network](cookbook/validate-network.md) and [Triage and fix findings](cookbook/fix-findings.md) —
  the validation and quality passes in depth, from the CLI and Python.
* [Install and troubleshoot](install.md) — extras, `netstead doctor`, and the common first-run errors.
