# netstead-project-cards

[ProjectCard](https://github.com/network-wrangler/projectcard) authoring for the
[netstead](https://github.com/e-lo/netstead/tree/main/packages/netstead) Workbench, as the Workbench plugin
"project cards" (`project_cards`).

    uv sync --all-packages        # in the netstead workspace: installs it next to netstead
    uv run netstead app           # Settings → Plugins lists "Project cards" as loaded

**How to use it:** [Author ProjectCards in the Workbench](https://github.com/e-lo/netstead/blob/main/packages/netstead/docs/cookbook/workbench-project-cards.md)
(the cookbook page: the panel, import, provenance, `ignore_missing`, Python and the assistant, known limits).

**In the Workbench** (`netstead app`): select links, then right-click **New change ▸ Property change** or
**Deletion** (**Addition** is in the command palette). The **Card** dock panel shows a form prefilled from the
selection; **Preview** shows the change on a copy of the network, **Add to card** applies it and adds it to the
network's draft card (badged with its count of uncommitted changes), and **Undo last change** takes it back.
**Commit…** shows the live edit warnings (confirmed with **Commit anyway**) and the card's YAML, then writes the card
atomically inside `io.allowed_roots`. **Import** applies a card, or the offline report's edit log, all or nothing.

A property change's facility is written as a **Segment** (name or ref with the from and to nodes) or a **Link list**
(ids only), in **GMNS** or **OpenStreetMap** ids, chosen in the form beside the count (the Find tab's two choices;
GERS is refused: ProjectCard has no key for it). Clicked links default to a Link list, a named selection to a Segment.

**New card** (in the panel's **Draft** section) walks through the same steps as a guided mode, with a step indicator
(**Type → Select → Details → Add**) and **Back** / **Cancel**: pick a change type (the transit types are shown, not
yet supported), select the facility with the shared selection (links clicked or box-selected in Highlight mode and set as the selection, a
description run through core's natural-language **Select**, or a saved selection; the count updates live), press
**Use these N links**, then fill in the form, **Preview** and **Add to card**. It dispatches the same Actions
(`project_cards.preview`, `project_cards.add_change`) as the right-click entry point, which stays.

The **Cards** panel has four sections, in order: **Catalog** (open a folder of cards; each card has a status pill, **Fits
this network**, **Doesn't fit** (with its first reason) or **Not checked**, against the network the heading names, with **Preview**, **Apply…** (only with `enable_apply` on) and
**Details**; files that are not cards
are listed under "Not cards"), **Draft** (the compose, draft and commit UI above), **This session** (what the
session did with cards, newest first with the time: "Saved <file>", the catalog file actions, and "Applied …" when
`enable_apply` is on; `plugins.project_cards.session`) and **Session summary** (Copy and Download).

**Edit** (on a catalog card) loads the card into the draft, as Import does, and remembers its file (a draft with
uncommitted changes is replaced only when you confirm). **Remove** takes out any change of the draft (the later ones
are re-applied, so the network ends as if it had never been made), **Edit…** removes a change and reopens its form,
and **Commit…** offers **Save over <file>** or **Save as new file**.

The panel's actions are icon buttons (hover for the name): a catalog card's row reads **Preview**, **Inspect**,
**Edit**, **Details** (its file and its check), then its file actions.

Each catalog card's file actions are **Rename…**, **Duplicate** (`<name>-copy.yml`), **Edit metadata…**
(project, tags, dependencies) and **Delete** (moves the file to a `.trash` folder inside the catalog folder, with
**Undo delete** in the status line). Every write stays inside `io.allowed_roots` and the catalog folder, validates
the card with `projectcard` first and never replaces a different file unless asked; the session summary lists them.

**Inspect** (on a catalog card, and on the draft) opens a read-only view: the project, tags, dependencies, notes and
file, then each change with its type, facility (ids, or the query and what it resolved to), property changes
(existing → set or change) and whether it applies to the open network. Clicking a change highlights its links on
the map (`wb.highlight`; the map does not zoom to them, since the Workbench gives plugins no camera control yet), and
**YAML** shows the card's text. Nothing changes.

The library the Workbench pieces build on:

- **The change model** (`netstead_project_cards.types`): one pydantic record per ProjectCard change type
  (`RoadwayPropertyChange`, `RoadwayDeletion`, `RoadwayAddition`; the transit types are declared and refused as not
  yet supported), in GMNS names.
- **`DraftCard`**: groups changes with a project, tags, dependencies and notes, and writes a ProjectCard dict or YAML
  in Wrangler names through the maintained `mappings/gmns_to_wrangler.yaml`. `DraftCard.from_card` reads one back.
- **`read_card_file`**: reads a ProjectCard file (YAML or JSON), or the offline validation report's edit log, as a
  `DraftCard`.
- **`card_problems`** (`netstead_project_cards.validate`): checks a card with `projectcard`'s own validator.
- **`apply_change`** (`netstead_project_cards.lower`): plans a change with core's edit planner and applies it through
  the Workbench `Host` (`host.plan_*`, then `host.mutate`), so core's history and live warnings see it.

**The catalog**: a folder of ProjectCard files (YAML or JSON, one level of sub-folders), opened with
`project_cards.open_catalog` or the `catalog_dir` setting (`[plugins.project_cards]`). Each card is checked against
the open network without changing it (`check_card`: the ids each change resolves, the ids the network lacks, and a
reason when a change cannot be applied, such as a query selection without netstead provenance), previewed on a copy
with the links it aims at highlighted (`preview_card`), and applied to the network through the same lowering as a
draft change (`apply_card`, all or nothing, with `ignore_missing` and `existing_value_conflict` overrides; off unless
the `enable_apply` setting is on). Cards
applied this session, from the catalog or committed from the draft, are listed in `plugins.project_cards.applied`;
`project_cards.session_summary` (and `GET /api/plugins/project_cards/summary.txt`) writes them up as plain text with
the plugin's history for the session.

**Actions** (`netstead_project_cards.actions`):

- `project_cards.add_change`, `project_cards.undo_change`, `project_cards.remove_change`, `project_cards.commit`,
  `project_cards.import`, `project_cards.edit_card`,
  `project_cards.preview`, `project_cards.close_preview`, `project_cards.open_catalog`,
  `project_cards.refresh_catalog`, `project_cards.check_card`, `project_cards.preview_card`,
  `project_cards.apply_card`, `project_cards.rename_card`, `project_cards.duplicate_card`,
  `project_cards.delete_card`, `project_cards.restore_card`, `project_cards.update_card_meta`,
  `project_cards.session_summary`.
- Usable from Python (`app.do(...)`), over HTTP (`/api/actions`), from the command palette, and by the assistant: opt
  in with `assistant.plugins`. A local model is offered every Action except commit and import (which need a path)
  unless `assistant.compact_tools = "off"`. The catalog file Actions (rename, duplicate, delete, restore, update
  metadata) and `apply_card` are never offered to the assistant.

**Routes** (read-only, for the panel): `GET /api/plugins/project_cards/form`, `/commit-check`, `/catalog` (the open
catalog, checking unchecked cards a few at a time), `/card` (the Inspect view of a catalog card, `card_id=`, or of
the draft, `draft=true`: its changes with their targets and check, and the card's text, read through
`host.readable`) and `/summary.txt`.

**Settings** (`[plugins.project_cards]`): `catalog_dir`, a folder inside `io.allowed_roots` opened as the catalog when
the session starts; `enable_apply` (default `false`), which turns on applying catalog cards to a network
(`project_cards.apply_card` and the panel's **Apply…**). Off, `apply_card` is refused with "applying cards is turned
off (plugins.project_cards.enable_apply)" and the panel offers no Apply.

Needs Host API 1.8 (`host.readable`, `host.list_dir`, `host.history`; `host.register_table` for the catalog
table).

**The catalog as a table**: the catalog is also the table view's read-only `cards_catalog` table (under "Plugins"
in the table rail; **Show as table** in the Catalog section opens it): `card_id`, `project`, `tags` (a comma list,
so "any of" matches a tag), `change_count`, `change_types`, `fits` (empty until checked), `reason`,
`links_targeted` and `modified`. Sort and filter it there; clicking a row selects its card in the panel.
