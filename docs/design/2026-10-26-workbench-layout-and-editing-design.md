# Workbench layout, editing states and cards-from-changes: design

Status: **proposed** · Date: 2026-10-26 · Owner: Elizabeth Sall

Responds to the beta UX notes of 2026-10-26: map settings belong to the map, not a side-panel pane; panes
are crowded; Inspect doubles as the editor; the state of an applied-but-unsaved edit is unclear; the pencil
tab is really a change log; tooltips are clipped; cards should be made from chosen changes in their own flow;
and edits should be checked before they are staged and again before they become a card.

## Principles

- **KISS.** Rearrange what exists before adding anything. No new framework, no new state store.
- **The map owns map settings; the side panel owns work.** A control lives next to the thing it changes.
- **Felt's shape, where it fits:** a top toolbar, a left list of what is on the map, a right detail panel
  that opens on selection, and a bottom table ([Felt interface tour](https://help.felt.com/getting-started/tour-the-interface),
  [Felt layer list](https://help.felt.com/layers/list)). Felt edits save as you go; netstead keeps an explicit
  save, because a network edit is a change to a dataset someone will model with.
- **One word per state, used everywhere** (header, table cells, Details, change log, cards).

## 1. Layout

```
┌ toolbar: network · Open · Save · Map/Split/Table · validity · model · jobs · commands · settings ┐
│ rail │                     map (or table)                      │ detail panel (on selection) │
│  ⌕   │  ┌ Layers & legend ┐                     [zoom][fit]   │  link 60 · Pine Street       │
│  ⓘ   │  │ ◉ Links  ▣ by facility_type ▾ │                      │  fields … (edit in place)    │
│  ⟲   │  │ ◉ Nodes  ● by ctrl_type ▾     │                      │                              │
│  ✓   │  │ ◉ Issues · Anchors           │                      │                              │
│  ▤   │  └─────────────── (collapse ▴) ┘                      │                              │
│  💬  │                                                         │                              │
└──────┴────────────────────── table (Split/Table) ──────────────┴──────────────────────────────┘
```

### 1a. Map settings attach to the map

- A **Layers & legend card** floats on the map's top-left (below the zoom control), replacing both the View
  tab and the separate legend. It is the legend: each row is a layer with a visibility eye, its swatch or
  color ramp, and a "by ‹column›" picker. Expanding a row shows that layer's options (width by, direction and
  offset for links, size for nodes, the "Show only links where…" filter for links). Overlays (Issues,
  Anchor candidates, plugin layers) are rows too. The card collapses to a single "Layers" button.
- It exists only where the map is: hidden in Table view, half-height in Split view (scrolls).
- Basemap choice and labels move into this card's footer (one line), out of Settings.
- **Removed:** the View tab, the map's separate legend box.

### 1b. A left rail instead of a crowded tab strip

- The six side-panel tabs become a **vertical icon rail on the left edge** (Felt keeps its lists on the left).
  Each icon has a tooltip; clicking opens its pane in a left drawer next to the rail, clicking again closes it,
  so the map can use the full width. The drawer is resizable (the existing handle).
- Rail entries: **Find** (search), **Changes** (history icon, see 2c), **Validate** (shield-check),
  **Cards** (stacked cards), **Assistant** (chat). Plugin panels add entries below a divider.
- **Details moves to the right** as a detail panel that opens when something is selected or focused and
  closes with ×, like Felt's. It is not a rail entry. The table's row click opens it too.
- This answers "cascading menu": one level, icons with labels on hover, no nested menus. Nested flyouts were
  considered and rejected: they hide state and are slow to target.

### 1c. Tooltips float above everything

- One tooltip element appended to `<body>` (fixed position, top z-index), positioned from the target's
  bounding box, flipped to stay on screen. Replaces the CSS `::after` tooltips, which the map and drawers clip.

## 2. Editing

### 2a. States and words

| State | Meaning | Where it shows | Undo |
|---|---|---|---|
| **Typing** | a value in a field, not applied | the field itself | Escape |
| **Pending** | applied to this session's working copy of the network; visible on the map, in the table, to validation; not written anywhere | yellow outline on the field/cell with a tooltip "Pending: applied in this session, not saved. Save a copy or put it in a card." and a count on the Changes rail icon | Undo / redo |
| **Saved** | written to a file with Save a copy | the change log row shows the file; the outline clears | n/a (it is on disk) |
| **In a card** | included in a ProjectCard file | the change log row shows the card | n/a |

"Pending" is preferred over "staged" (git's word, which implies a later commit step) and "applied" (which
users read as "done"). The header's save icon shows the pending count, like an unsaved-document dot.

### 2b. Details is where you edit

- The right **detail panel** shows a record's fields read-only; a pencil icon (or double-click a field, or
  Enter) switches a field to editing. Header icons: undo, redo (both with tooltips "Undo last change",
  "Redo"), and close.
- **Cmd/Ctrl+Enter applies** in every edit form (Details, cell editor, card forms); Escape cancels.
- A field that has a pending change keeps the yellow outline in Details and the table until saved or undone.
- Before a value is applied it is checked (2d); the field shows allowed values and the check's verdict inline.

### 2c. The change log

- The pencil tab becomes **Changes** (history icon: a clock with a counter-clockwise arrow).
- It lists pending changes newest first, each with what changed, its check result and checkboxes.
- With changes selected: **Undo selected** (only the newest contiguous run is undoable; others are disabled
  with a tooltip), **Make a card…** (2e), **Save a copy…**.
- The "help" paragraph stays dismissible; the "last change was made by …" line stays.

### 2d. Check before applying, and before a card

- **Before applying** a value: the same schema / enum / foreign-key / quality checks the edit-warning system
  runs today, but on the proposed value, shown inline as a hint ("free_speed 40 > 35 for residential; use
  ≤ 35 or set facility_type to tertiary"). Applying a value that warns is allowed; it is pending with a
  warning badge.
- **Fix from Validate** opens the record in Details with the finding's field focused and the rule's
  suggested values listed (each quality rule provides `suggest(row)`: e.g. high_speed_residential suggests
  `free_speed ≤ threshold` or the facility types whose speed range fits).
- **Transient problems:** a change set (the changes selected for a card, or all pending) is checked as a whole,
  so a foreign key that the next change satisfies (add a node, then the link that uses it) is shown as
  "resolved within these changes", not as an error. Only problems left after the whole set count.
- **Before a card is written:** validation runs on a derived copy with exactly the chosen changes applied,
  and the card dialog shows the finding difference versus the base network: new findings, resolved findings.
  New errors block writing unless the user confirms.

### 2e. Make a card from chosen changes

A dialog of its own, separate from browsing the catalog:

1. **Changes:** the selected pending changes (adjustable), each with its facility form (Segment or Link list)
   and id system (from the strict forms already built).
2. **About the card:** a form generated from the ProjectCard schema: `project` (required), `tags`,
   `dependencies` (prerequisites, corequisites, conflicts), `notes`. The schema has no year field;
   "year" is a tag convention (e.g. `year:2030`) unless we add a netstead extension field.
3. **Check:** the finding difference from 2d.
4. **Where:** an existing catalog folder, a new catalog folder, download the `.yml`, or copy the YAML.
   A remote registry is a later option, behind an interface (`CardDestination`) so a registry can plug in.

The Cards pane keeps only browsing the catalog (fit pill, inspect, edit, file actions, table view).

## Phases

1. **Layout:** left rail, right detail panel, Layers & legend card, tooltip layer. No behavior change.
2. **Editing states:** Pending wording and outlines, Details as the editor, Cmd/Ctrl+Enter, undo/redo icons,
   Changes pane with selection.
3. **Cards from changes:** the dialog in 2e, reusing the plugin's draft and commit code underneath.
4. **Checks:** pre-apply checks and hints, rule `suggest()`, change-set evaluation, finding difference.

Each phase is one PR, browser-checked, with the docs and screenshots updated in the same PR.

## Open questions

1. **Pillar:** which product is meant (a link)? Felt is the main reference above.
2. **"Pending"** as the state word (alternatives: "staged", "unsaved")?
3. **Remote registry:** which one (a GitHub repo of cards, network-wrangler's registry, something else)?
4. **Year:** a `year:NNNN` tag convention, or a netstead extension field on the card?
5. **Details on the right:** acceptable to have panes on both sides (rail drawer left, details right)?
