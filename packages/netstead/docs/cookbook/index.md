---
title: netstead cookbook
audience: users
kind: concept
summary: Task-oriented recipes for the common GMNS workflows — validate, scope, edit, run the bench, self-host, drive from an AI agent.
---

# netstead cookbook

Each recipe is one task, one runnable example, one paragraph of why-it-works. Recipes are intentionally short — for the *design* behind a workflow, follow the "See also" link to the relevant concept page.

For generic Frictionless data-package recipes (S3 reads, format conversion, generic spatial scope), see the [corral cookbook](https://e-lo.github.io/netstead/corral/cookbook/).

## Validation + data quality

* [Run validation and read the report](validate-network.md) — interpret severity / category / code.
* [Customise the data-quality rule pack](customise-quality.md) — threshold overrides, disable rules, plug in your own.
* [Triage findings, propose fixes, apply them](fix-findings.md) — propose fixes on individual findings in the viewer, download them as a YAML edit log, replay it in Python.

## Creating networks

* [Build a GMNS network from OpenStreetMap or Overture](build-from-osm.md) — `netstead build --source osm` (needs the `[osm]` extra) or `--source overture` (needs `[overture]`) from a place / bbox / point+buffer.

## Scope + geographic subsetting

* [Build a scope from seed nodes](scope-from-nodes.md) — BFS shortest-path, network buffers, FK pushdown.

For bbox / polygon (non-GMNS-specific) spatial scope, see [corral cookbook: spatial scope](https://e-lo.github.io/netstead/corral/cookbook/scope-bbox/).

## Querying + editing linked tables

* [Query and update linked tables](query-and-update.md) — join across foreign keys, inspect lazily (no pandas), edit rows Network-Wrangler-style with rollback.
* [Edit a network with atomic rollback](edit-with-rollback.md) — `Session` lifecycle, dry-run preview, persisted history.

## Surfaces

* [View a network on a map](view-your-network.md) — render a self-contained Leaflet map of any GMNS network, with or without validation overlays.
* [Explore networks in the Netstead Workbench](workbench.md) — linked map + tables, validation with fixes, NL selection, replayable actions.
* Select links and nodes in plain English — `netstead select` resolves a description to GMNS ids through Anthropic / OpenAI / Gemini / Ollama (the `[nl]` extra); the Workbench recipe above covers the same selection flow in the browser.
* [Write a Workbench plugin](workbench-plugins.md) — add Actions, settings, state, routes and a front end as a separate pip package; the `Host` API, trust model, testing and versioning.
* [Author ProjectCards in the Workbench](workbench-project-cards.md) — the project cards plugin: select, change, preview and commit network_wrangler ProjectCards.
* [Run language models locally with Ollama](local-llm-ollama.md) — install Ollama, pull `qwen3:4b`, select in plain English with nothing leaving your machine.
* [Self-host the HTTP server](serve-http.md) — config file, bearer auth, deploying behind a proxy.
* [Wire the MCP server to Claude Code / Claude Desktop](serve-mcp.md) — stdio transport, tool list, agent prompts.
* [Run the bundled benchmarks](run-bench.md) — `netstead bench` (load / validate / connectivity timings) and `netstead bench-suite` (create / selection / viz-pack), what to expect on Leavenworth vs regional networks.

## For AI agents specifically

* [Drive both packages from a tool-call loop](https://e-lo.github.io/netstead/corral/ai/json-cli/) — every CLI command emits a parseable document with `--json`.
* [AI surface overview](https://e-lo.github.io/netstead/corral/ai/) — llms.txt + api-index.json + Skills + MCP.

## Contributing a new recipe

A recipe is `kind: howto` per the [Page Style Guide](https://github.com/e-lo/netstead/blob/main/packages/netstead/docs/_page-style-guide.md) (a contributor page in the repo, not on this site):

* `When to use this` — 1-2 sentences, the trigger.
* `Quick example` — ≤20 lines, copy-pasteable against the Leavenworth fixture, with **prose before the code**.
* `Step-by-step` — numbered, one short paragraph each.
* `Common variations` — collapsible accordions (`???`), one per variation.
* `Pitfalls` — explicit gotchas.
* `See also` — one link per related concept, no diffuse paths.

Open a PR with the new recipe + a link from this index. CI runs every column-0 ```python fence in the docs (`packages/netstead/tests/test_documented_python_contract.py`) and fails if it raises; indented accordion / tab blocks and blocks preceded by `<!-- doctest: skip -->` are not executed, and printed output is not compared.
