# Workbench UI style

Status: **standing** · Owner: Elizabeth Sall · Started 2026-11-19

The rules below come from beta-test corrections. Follow them in every Workbench and plugin change; when a new
correction comes in, add it here in the same PR. Layout and editing design: [2026-10-26 design](2026-10-26-workbench-layout-and-editing-design.md).

## Words

- American English: color, behavior, center, catalog, canceled, -ize. Never rename an identifier only for spelling.
- One word per state, everywhere: **Typing**, **Staged**, **Saved**, **In a card**. Not "unsaved", "pending", "dirty".
- Short. Say what a control does, not how it works.

## Controls

- **Actions are icon buttons with a tooltip** (`class="iconbtn"`, `data-tip`, `aria-label`), never text buttons.
  Inline SVG icons from `static/js/icons.js` (Lucide style); add one there if none fits. If two actions would share
  an icon, pick a distinct icon rather than falling back to words.
- Tooltips use the core tooltip layer (`data-tip`), never CSS `::after` or `title`.
- Icon meanings stay fixed:

  | Icon | Means |
  |---|---|
  | `zoom-in` (magnifier +) | show on the map / preview |
  | `search` | find |
  | `play` | run (validation, a job) |
  | `info` | inspect details, or a help tooltip |
  | `pencil` | edit |
  | `undo` / `redo` | undo or redo the last change |
  | `target` | Select (resolve a selection, no AI) |
  | `sparkles` | Ask AI |
  | `gear` | settings |
  | `life-buoy` | help (the docs) |

- **Status is a small icon with a tooltip**, not a pill or badge with words: green check, red X (click for
  details), gray dash for not checked.
- A list row has **at most two actions** (usually Inspect details and Preview). Everything else lives in the
  details view.

## Help text

- No explanatory paragraphs in panes. Put help in an **info icon (ⓘ) tooltip** beside the heading it explains.
- Empty states and intros: at most one line, plus examples to click and a link to the docs.

## Layout

- Felt is the layout reference: left icon rail with a drawer, map in the middle, **details in the right drawer**.
- A control sits next to what it changes: map settings in the Layers card on the map; a color ramp is picked from
  the ramp itself.
- Details of anything (a link, a node, a card) open in the right drawer, not inline in a list.

## Editing

- Edit in place: double-click a field (or Enter on it) to edit; **Enter or Cmd/Ctrl+Enter applies, Escape cancels**.
- Fields with a fixed set of values are a select.
- A staged value has a yellow outline with the Staged tooltip.

## The map

- What the user picked must be obvious: the selection is a wide translucent band under a solid line; anchors are
  large dots ringed in white, drawn on top.
- Zoom to what an action shows (a selection, a card preview).

## Behavior

- A change takes effect at once: no reload to see a new model, setting or catalog.
- Making something new shows it: a new selection turns the selection layer back on.
- Keep it simple: rearrange what exists before adding anything.

## Docs

- Concise. Each change updates its docs and the CHANGELOG (append-only) in the same PR.
