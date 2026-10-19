# netstead-project-cards

ProjectCard authoring for the [netstead](../../packages/netstead) Workbench, as the Workbench plugin "project cards"
(`project_cards`).

Select links in `netstead app`, right-click **New change ▸ Property change** (or Deletion, Addition), fill the form,
preview the change on a copy of the network, add it to the draft card, and commit the card as a
[ProjectCard](https://github.com/network-wrangler/projectcard) YAML file that `projectcard` validates.

    uv sync --all-packages        # in the netstead workspace: installs it next to netstead
    uv run netstead app

See the cookbook page "Author ProjectCards in the Workbench" in the netstead docs.
