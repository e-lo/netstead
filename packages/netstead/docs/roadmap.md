---
title: Status and roadmap
audience: users
kind: concept
summary: What netstead ships today (v1.0 beta), what is being built next, and what is designed but not started.
---

# Status and roadmap

## What it is

A one-page answer to "can I rely on this yet, and what is coming?". It summarizes the design
records that steer the project; those records live in the repository under
[`docs/design/`](https://github.com/e-lo/netstead/tree/main/docs/design) and are reviewed like
code, so this page follows them rather than the other way round.

## Why we have it

netstead is in public beta (`1.0.0b2`). The library surface (read, validate, scope, edit) is
stable enough to build on; the Workbench grows by phases, and each phase lands as one pull request.
Knowing which phase is on `main` tells you which cookbook pages apply to the version you installed.

## Mental model

Three layers, maturing at different speeds:

| Layer | Status | Where documented |
|---|---|---|
| **Library** (`netstead.Network`, validation, quality rules, scope, edit with rollback, GeoParquet I/O) | beta, API frozen for v1.0 | [Quickstart](quickstart.md), [Cookbook](cookbook/index.md), [API](reference/api.md) |
| **Builders and surfaces** (OSM and Overture builds, natural-language selection, CLI `--json`, HTTP server, MCP server) | beta | [Build a network](cookbook/build-from-osm.md), [Self-host](cookbook/serve-http.md), [MCP](cookbook/serve-mcp.md) |
| **Workbench** (`netstead app`) and its plugin API | beta; phased; the plugin API is provisional until v1.0 | [Explore in the Workbench](cookbook/workbench.md), [Write a plugin](cookbook/workbench-plugins.md) |

## Shipped

Everything below is on `main` and in the current beta.

- **Library.** DuckDB is the single compute engine (via ibis); pandas, polars and Arrow are
  in/out formats. Geometry is WKB in memory, WKT in CSV and GeoParquet on disk.
- **Builders.** `netstead build` from OpenStreetMap (place, bbox, point and buffer, or a local
  `.osm` file) and from Overture Maps.
- **Natural-language selection.** `netstead select` and the Workbench resolve a phrase such as
  "I-90 EB between Exit 85 and Exit 87" to links, with Anthropic, OpenAI, Gemini or a local Ollama
  model; keys are write-only and nothing leaves the machine unless you opt in.
- **Workbench phases P0 to P3.** Open or import a network (local, URL, OSM, Overture) with
  estimate and approval; linked map and tables with foreign-key navigation; layered settings;
  validation with an issues panel; table edits with live warnings, undo and "save a copy";
  selection verification with pinned anchors, saved selections and export; style, filter and
  navigate actions; the natural-language assistant with approved drafts. Every action is recorded
  and replayable as Python.
- **Plugins.** Python entry-point plugins with a versioned `Host` API (currently 1.7) and
  browser slots for workspaces, panels, commands and map layers.
- **Project cards plugin.** Lives in the repository at `plugins/project-cards` and authors
  [ProjectCards](https://github.com/network-wrangler/projectcard) from Workbench edits: the change
  model, draft cards written as YAML or JSON in Wrangler names, import of card files and of the
  offline edit log, the `project_cards.*` Actions (add a change, undo, commit, import, preview), a
  catalog of cards checked against the open network and applied with a session summary, and the
  Cards panel in the Workbench. See
  [Author ProjectCards in the Workbench](cookbook/workbench-project-cards.md).
- **AI surface.** `--json` on every data command, `llms.txt`, an MCP server with seven tools,
  and Claude Code skills in the repository.

## Being built next

In the order the design records schedule them. None of these are on `main` yet, so no cookbook
page describes them.

1. **Workbench P4: live Python session.** An `app(net)` helper in the top-level package starts the
   Workbench from a notebook on the same `Network` objects, with a handle offering the live selection,
   `show(...)` for temporary layers, `do(action)` and an optional Python console.
2. **Workbench P5: Compare.** `diff_networks(a, b)` and a Compare workspace with added, removed
   and modified features and validation-issue deltas.
3. **Workbench P6: Transit.** A GTFS component beside the roadway network: routes and stops
   layers, transit selections, roadway-to-transit consistency checks.

## Designed, not scheduled

- Applying cards and scenario management inside netstead (builds on the project-cards plugin).
- Cross-linking and export in the data-table view (phase 2 of that scope).
- A nightly performance-regression job for the benchmark suite.
- Interactive geometry editing in the Workbench map (reshaping a link's path, moving a node) —
  beta feedback item 20 from the 2026-10-20 quickstart walk.
- A global filter expression box in the table view, compiled to the same predicate the per-column
  filter popovers use, instead of setting each column separately.
- A GeoLibre plugin that mirrors the Workbench selection; see
  [The Workbench and other tools](workbench-and-other-tools.md).

## How it relates to ...

- The per-release detail is in the [changelog](changelog.md).
- Beta feedback goes through the
  [beta program](https://github.com/e-lo/netstead/blob/main/BETA.md).
- Breaking changes from v0.3 are in the [migration guide](migration/v0.3-to-v1.0.md).

## See also

- [Explore networks in the Netstead Workbench](cookbook/workbench.md)
- [The Workbench and other tools](workbench-and-other-tools.md)
- [What is GMNS?](what-is-gmns.md)
