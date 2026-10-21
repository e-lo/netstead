# Changelog — netstead-project-cards

All notable changes to the `netstead-project-cards` package (the Workbench plugin `project_cards`). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

## [Unreleased]

### Added

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

### Changed

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
