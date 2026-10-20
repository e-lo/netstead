# Changelog — netstead-project-cards

All notable changes to the `netstead-project-cards` package (the Workbench plugin `project_cards`). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

## [Unreleased]

### Added

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
- Local models get no cards tools while `assistant.compact_tools` is compact (`auto` on a local endpoint); set it
  to `off` to draft cards with one.

- The library for ProjectCard authoring: the change model (one record per ProjectCard change type, in GMNS names),
  `DraftCard` (write a card dict or YAML in Wrangler names through the maintained GMNS ↔ Wrangler mapping, and read
  one back), `read_card_file` (YAML or JSON cards, and the offline report's edit log), `card_problems`
  (`projectcard`'s own validator) and `apply_change` (plan a roadway change with core's edit planner and apply it
  through the Workbench `Host`). The plugin registers with the Workbench but contributes no actions or UI yet; those
  arrive in the following releases.
