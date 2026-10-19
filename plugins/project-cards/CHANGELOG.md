# Changelog — netstead-project-cards

All notable changes to the `netstead-project-cards` package (the Workbench plugin `project_cards`). Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

## [Unreleased]

### Added

- The library for ProjectCard authoring: the change model (one record per ProjectCard change type, in GMNS names),
  `DraftCard` (write a card dict or YAML in Wrangler names through the maintained GMNS ↔ Wrangler mapping, and read
  one back), `read_card_file` (YAML or JSON cards, and the offline report's edit log), `card_problems`
  (`projectcard`'s own validator) and `apply_change` (plan a roadway change with core's edit planner and apply it
  through the Workbench `Host`). The plugin registers with the Workbench but contributes no actions or UI yet; those
  arrive in the following releases.
