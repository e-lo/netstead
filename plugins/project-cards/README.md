# netstead-project-cards

[ProjectCard](https://github.com/network-wrangler/projectcard) authoring for the
[netstead](https://github.com/e-lo/netstead/tree/main/packages/netstead) Workbench, as the Workbench plugin
"project cards" (`project_cards`).

    uv sync --all-packages        # in the netstead workspace: installs it next to netstead

This release ships the library the Workbench pieces build on:

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

**Actions** (`netstead_project_cards.actions`):

- `project_cards.add_change`, `project_cards.undo_change`, `project_cards.commit`, `project_cards.import`,
  `project_cards.preview`, `project_cards.close_preview`.
- Usable from Python (`app.do(...)`), over HTTP (`/api/actions`), from the command palette, and by the assistant: opt
  in with `assistant.plugins`; with a local model also set `assistant.compact_tools = "off"`.

Only the Workbench UI (the draft panel and the forms to select, change, preview and commit a card from `netstead app`)
arrives in the next release.
