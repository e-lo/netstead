# Changelog — netstead-project-cards

All notable changes to the `netstead-project-cards` package (the Workbench plugin `project_cards`). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

## [Unreleased]

### Added

- **New card mode.** A **New card** button in the Cards panel's Draft section starts a guided mode with a step
  indicator (Type → Select → Details → Add), Back and Cancel: pick a change type (roadway property change, deletion,
  addition; the transit types are shown disabled, "not yet supported"), select the facility with the shared
  selection (links clicked or box-selected in Highlight mode and set as the selection, a description run through core's `select` Action, or
  a saved selection) with a live count and **Use these N links**, then the change form, Preview and Add to card. It
  dispatches the existing `project_cards.preview` and `project_cards.add_change`; an addition skips Select. The
  right-click "New change ▸ …" entry point stays.
- **Edit a card.** `project_cards.edit_card(card_id, replace_draft=False)` (the catalog row's **Edit**) loads a
  catalog card into the open network's draft with `import` semantics (all or nothing) and remembers its file; a
  draft with uncommitted changes is refused unless `replace_draft`, which undoes them first. The commit dialog then
  offers **Save over <file>** (overwrite) or **Save as new file**. `project_cards.remove_change(index)` removes any
  change of the draft, not only the last: it undoes the draft back to that change and re-applies the later ones, so
  the network ends as if the change had never been made, and refuses (changing nothing) when a later change depends
  on the removed one. The draft's **Edit…** is remove plus re-add through the form. `plugins.project_cards.drafts`
  entries now carry `tags`, `dependencies`, `notes`, `source` (`{card_id, path}` or `null`) and each change's
  `index`. A commit inside the open catalog's folder rescans the catalog (its result's `listed`).
- **Edit the catalog's files.** Each catalog card has a **File** disclosure: **Rename…**
  (`project_cards.rename_card(card_id, new_name, overwrite=False)`), **Duplicate** (`duplicate_card`, to
  `<name>-copy.yml`, `-copy-2`, …), **Edit metadata…** (`update_card_meta(card_id, project=, tags=,
  dependencies=)`, changing only those keys of the file) and **Delete** (`delete_card`, which moves the file to a
  `.trash` folder inside the catalog folder; never a hard delete), with **Undo delete** in the status line
  (`restore_card`). Every write goes through `host.writable`, stays inside the catalog folder (one sub-folder, nothing
  hidden), validates the card with `projectcard` first and never replaces a different file unless `overwrite`. They
  change files, not a network (`mutates` false), are kept out of the assistant's tools, are rolled back with a
  failing parent Action, and are listed in the session summary's new **Catalog files** section
  (`plugins.project_cards.files`; deletions this session are `plugins.project_cards.trash`).
- **Inspect a card.** **Inspect** on a catalog card (and on the draft) opens a read-only view: project, tags,
  dependencies, notes and file; each change with its type, facility (ids, or the query and what it resolved to),
  property changes (existing → set or change) and its applicability on the open network. Clicking a change
  highlights its links on the map (`wb.highlight`; the map does not zoom, since `wb` has no camera control), and a
  **YAML** toggle shows the card's text. `GET /api/plugins/project_cards/card?card_id=…` (or `?draft=true`) serves
  it, reading the file through `host.readable`; nothing is mutated or stored.
- **A catalog of cards, checked against the network, previewed and applied.** `project_cards.open_catalog(path)`
  (or the new `catalog_dir` setting) lists the ProjectCard files of a folder and one level of its sub-folders, with
  the files that are not cards and why; `refresh_catalog` rescans. `check_card(card_id)` says whether a card applies
  to the open network as it is now, per change: the records it resolves, the ids the network lacks, and a reason
  when a change cannot be applied (a query selection without netstead provenance: "query without provenance;
  re-resolution not supported"). `preview_card(card_id)` shows every change of a card on a copy of the network and
  publishes `project_cards.card_selection` (the links and nodes the card aims at) for the map. `apply_card(card_id,
  existing_value_conflict=..., ignore_missing=...)` applies a card to the network through the same lowering as a
  draft change, all or nothing, and records it; no file is written. The cards applied this session (committed from
  the draft or applied from the catalog) are `plugins.project_cards.applied` (`card_id`, `path`, `project`,
  `applied_at`, `changes`, `source`, `edit_ids`); the open catalog is `plugins.project_cards.catalog`.
- **A session summary.** `project_cards.session_summary()` and `GET /api/plugins/project_cards/summary.txt`: the
  network, the cards applied (with their edit ids), the draft, and this plugin's Actions in order, as plain text.
- `GET /api/plugins/project_cards/catalog` lists the open catalog and checks up to 50 unchecked cards per request.
- Needs Host API 1.7 (`host.readable`, `host.list_dir`, `host.history`).
- **The Cards panel has four sections.** *Catalog*: a folder path with Open and a rescan button, then each card with
  its applicability badge (`✓` applies, `✗` does not, with the reasons or missing ids on hover, `?` not checked yet),
  project, change count and tags, and Preview (the card's links highlighted on the map), Apply… (a dialog for
  `existing_value_conflict` and `ignore_missing`, pre-checked only when the check found missing ids) and Details
  (each change's check); files that are not cards are under "Not cards (n)". *Draft*: the compose, draft and commit
  UI as before. *Applied this session*: the cards committed or applied, newest first, with their source.
  *Session summary*: Copy and Download (`<network>-cards-session.txt`).

### Changed

- **A status pill says whether a card fits.** The catalog's `✓` / `✗` / `?` glyph is now a pill: green "Fits this
  network", red "Doesn't fit" (its first reason inline, every reason in the tooltip: "missing link 999999",
  "conflict: …", "already applied …" with apply on) or gray "Not checked" with a check button
  (`project_cards.check_card`). The heading reads "Catalog · checked against <network>". Each catalog entry
  (`state()` and `GET /catalog`) carries `fit`: `{state: fits | no | unchecked, label, reasons, warnings, net_id}`
  (`netstead_project_cards.catalog.fit`); a change's check lists `lacks` (`"link 999999"`) for the missing ids that
  refuse it.
- **"This session", not "Applied this session".** The panel's third section lists what the session did with cards,
  newest first with the time and an icon: "Saved <file>" (a commit), the catalog file actions (renamed, duplicated,
  deleted, restored, metadata edited) and "Applied <card> to <network>" only while `enable_apply` is on. It reads
  `plugins.project_cards.session` (`{kind, verb, text, detail, card_id, at}` each), a log kept with the commit and
  file records and taken back with them on a rollback. A committed record now carries `at`.
- **Applying cards is off by default.** The new setting `[plugins.project_cards] enable_apply` (default `false`)
  gates `project_cards.apply_card`: off, it is refused with "applying cards is turned off
  (plugins.project_cards.enable_apply)", the panel offers no **Apply…** (`plugins.project_cards.enable_apply` in the
  state says which), and a card saved this session no longer reads as "already applied". The assistant is never
  offered `apply_card` (`assistant = False`). The apply code and its tests stay (run with the setting on).
- **Icon buttons.** The Cards panel's per-card and per-section actions are icon buttons with a tooltip and an
  accessible name that says what they act on (`iconbtn`, core's icons through `wb.icon`, plus a few drawn by the
  panel; an older host shows their text). A catalog card's actions sit in one row; its **File** disclosure is gone
  (the file actions are in the row, the path is under **Details**). The dialogs keep their text buttons.
- **The panel is the dock's Cards tab.** It registers as `cards` (the bare id core reserves for this plugin), titled
  "Cards", between core's Edit and Validate tabs (order 25); it was `project_cards.draft`, titled "Card", last.
- **A query selection without provenance reads; applying it is what is refused.** A card that selects links by a
  query (`name`, `ref`, …) with no `netstead.resolved[i]` note used to be refused on read ("re-resolving a query
  arrives with apply_card"). It now reads as an unresolved `Selection` (`unresolved` is true, `target_ids()` is
  empty) and writes back unchanged, so a catalog can list it; import, preview and apply refuse the change with
  "query without provenance; re-resolution not supported".
- `project_cards.import` resolves its path with `host.readable`: a path with nothing at it is refused with "no such
  file or folder", before the "is not a file" check.

- **Importing the offline report's edit log.** netstead now writes it as a schema-valid card (picked ids with
  `ignore_missing: false`, notes as `netstead.note[<i>]` lines), which import reads as an ordinary card. The tolerant
  reading of the old shape (ids at the top of the facility, a per-change `notes`) stays, for logs written before.
- **A deletion made from a query selection honours the query's `ignore_missing`.** `RoadwayDeletion` gains
  `ignore_missing` (its links'; default `false`). `project_cards.add_change` sets it from the selection: `false` for
  a pick, the query's own flag (default `true`) for a query. With `true`, a resolved link the network no longer has is
  skipped and listed instead of refusing the change, and the card writes `links.ignore_missing: true`, so replaying
  it in network_wrangler does the same. Import keeps a deletion's links flag (it used to drop it); its nodes are still
  written with `false`.

### Fixed

- **Applicability checks `existing` values.** A card whose property change expects a value the network no longer
  has (for example one already applied on this network) used to show `✓` and then fail when applied. The check now
  compares each property's `existing` with the target rows, the same way applying does (1 matches 1.0), following
  `existing_value_conflict`: `error` makes the change not ok (`conflict: lanes: expected 1, found 2 on 4 of 4 links`,
  shown as `✗` with the reason in the tooltip), `warn` and `skip` stay ok and list a `warnings` entry. A card
  applied on the network this session says "already applied on <net> at HH:MM". `project_cards.apply_card` checks
  with its `existing_value_conflict` / `ignore_missing` overrides, so a `warn` or `skip` override is not refused.
- **Renaming, deleting or restoring a card's file updates an open draft's source.** A draft loaded with Edit kept
  the old path after `rename_card` (so "Save over" wrote the old name); it now follows the rename, loses its source
  on `delete_card` (the commit dialog then offers only "Save as new file"), gets it back on `restore_card`, and
  all three are undone with a rolled-back Action.
- **`project_cards.undo_change` undoes an applied catalog card.** Core's Edit panel refuses its own Undo on a card
  applied from the catalog ("the last change was made by project_cards; undo it there"), pointing at this plugin's
  undo — but it used to answer "nothing to undo" for it (it only ever took back the draft's last change), leaving
  the card stuck on the network. It now undoes whichever plugin change is newest: an applied catalog card (every
  one of its edits, newest first, dropped from `applied`) or the draft's last change, as before. A core (or other
  plugin's) change on top is still refused, naming it; a committed card is still never undone this way.

### Added

- The Workbench UI (`static/main.js`, with the rules in `cardmodel.js` and the flow in `controller.js`):
  - **New change ▸ Property change / Deletion** on the selection's right-click and **Selection actions ▾** menus and
    in the command palette; **New change ▸ Addition** in the palette; the transit types in the palette, answering
    "not yet supported".
  - A **Card** dock panel: a form prefilled from the selection (each column titled with its current value when the
    selected links agree, which the card records as `existing`), **Preview**, **Add to card**, **Cancel**, the
    draft's changes with **Undo last change**, and **Import**. Its tab carries the count of uncommitted changes as a
    dirty badge. A form read from the selection says when the selection has changed since, and waits for
    **Reload form** before it previews or adds anything; an addition's node form is checked only when it has a
    `node_id`.
  - A commit dialog that lists the live edit warnings (committing with any needs **Commit anyway**), says why a card
    `projectcard` rejects can't be committed, and shows the card's YAML.
  - **Undo the draft card's last change** in the command palette.
- Routes for the panel, both read-only: `GET /api/plugins/project_cards/form` (the targets, each column with its
  current value, and the options for a change type on the shared or a saved selection) and
  `GET /api/plugins/project_cards/commit-check` (the live edit warnings, the schema problems and the draft as YAML).
- A cookbook page, "Author ProjectCards in the Workbench" (`packages/netstead/docs/cookbook/workbench-project-cards.md`).
- Recorded Actions for authoring a card in the Workbench, each replayable with "Copy as Python" and offered to the
  natural-language assistant (opt in with `assistant.plugins`), which drafts them for the user to approve:
  - `project_cards.add_change`: apply one roadway change (property change, deletion or addition) to a network
    through core's edit planner and add it to that network's draft card; the targets can come from the shared or a
    saved selection.
  - `project_cards.undo_change`: undo the draft's last change (only the edits the draft made).
  - `project_cards.commit`: write the draft as a ProjectCard YAML file inside `io.allowed_roots` (live edit
    warnings confirmed, an existing file replaced only with `overwrite`), and add it to the catalog when that
    plugin is installed.
  - `project_cards.import`: apply every change of a card (or the offline report's edit log) to the draft, all or
    nothing.
  - `project_cards.preview` and `project_cards.close_preview`: preview a change on a derived copy, made active,
    without touching the network. While previewing, a change (and a selection made on the preview) goes to the
    network the preview was made from; the preview is reported `stale` once that network changes, and stops
    standing for it once it is closed.
- Approving a drafted card change made while a preview is active is refused as stale when the previewed network
  changed since (core checks a derived copy's base).
- Local models are offered `add_change`, `undo_change`, `preview` and `close_preview` in the compact tool set
  (`assistant.compact_tools = "auto"` on a local endpoint): they set `assistant_compact` (Host API 1.6). `commit` and `import`
  (a file path) still need `compact_tools = "off"`.
- Requires Host API 1.6. Drafts are reconciled against the network's pending edits (`host.pending_edits`, the
  edit seqs `host.mutate` returns) instead of its lineage. A read is trusted only for edits made by then
  (`PendingEdits.issued`), so a route reconciling outside the session lock never drops a change that landed after
  its read.
- A `project_cards.commit` nested in an Action that fails afterwards is rolled back too (`host.on_rollback`): its
  file is removed (or the file it replaced restored, with the folders it made), the committed card is forgotten and
  the draft comes back. A file that can't be put back is logged.

- The library for ProjectCard authoring: the change model (one record per ProjectCard change type, in GMNS names),
  `DraftCard` (write a card dict or YAML in Wrangler names through the maintained GMNS ↔ Wrangler mapping, and read
  one back), `read_card_file` (YAML or JSON cards, and the offline report's edit log), `card_problems`
  (`projectcard`'s own validator) and `apply_change` (plan a roadway change with core's edit planner and apply it
  through the Workbench `Host`).
- Requires netstead with plugin Host API 1.5 and `projectcard>=0.3.3,<0.4`.
