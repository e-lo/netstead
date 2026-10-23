---
title: netstead
audience: users
kind: concept
summary: Python toolkit for the General Modeling Network Specification — build, load, validate, scope, edit and explore GMNS networks at regional scale, with CLI / notebook / browser Workbench / HTTP / MCP surfaces. Built on corral.
---

# netstead

A Python toolkit for the [General Modeling Network Specification (GMNS)](https://github.com/zephyr-data-specs/GMNS) — the Zephyr Foundation's open standard for routable transportation networks. Built on top of [corral](https://e-lo.github.io/netstead/corral/).

![Sixty-second walkthrough: open, select, validate, fix, replay](assets/screenshots/workbench-walkthrough.gif){ .screenshot }
*The browser Workbench in a minute: open a network, select by name, validate, fix a finding, replay it all as Python. Start with [Quickstart: the Workbench](quickstart-workbench.md).*

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/e-lo/netstead/blob/main/packages/netstead/docs/notebooks/quickstart.ipynb) — five-cell quickstart in the browser. The first cell installs from GitHub; the rest load a real GMNS network from a URL, validate it, and view findings on an interactive map. Nothing to set up locally.

## What problems netstead solves

**Reading any GMNS network without writing import code.** Local CSV directory, S3 parquet partition, single-file DuckDB, zipped CSV bundle — same `Network.from_source(...)` call, same lazy-evaluation behavior:

<!-- doctest: skip -->
```python
from netstead import Network

net = Network.from_source("./my-network/")              # CSV directory
net = Network.from_source("s3://bucket/network.parquet/")  # cloud parquet
net = Network.from_source("./network.duckdb")              # single duckdb
```

**Validation in one report.** `net.validate()` covers structural (required tables), schema (per-field constraints), foreign-key (cross-table integrity), and sync-state (have FKs gone stale since the last edit?). Severity-graded, rendered as rich console / JSON / interactive HTML.

**Data quality beyond the spec.** Nine rules out of the box — high-speed-on-residential, disconnected components, lane-count mismatch, near-duplicate nodes, sharp-angle bends, implausible v/c ratios, missing critical fields, overlapping links, and repeated rows that should be unique (e.g. two `link_tod` rows for one link and time window). Plug in your own with a class + entry point.

**Network-aware scope** at regional scale. BFS-induced subgraph from seed nodes, Dijkstra-bounded network buffer, spatial-buffer from any link or point, connected-component, zone filter. Chainable, with FK pushdown to every related table (TOD overrides, lane detail, signal control).

**Editing with atomic rollback.** `with Session(net) as s: simplify_geometry(net, s); merge_close_nodes(net, s)` — commit on clean exit, roll back on exception. Pass `log_path=` to persist the edit log as a sidecar file you can replay later.

**Building networks from open data.** `netstead build --source osm` (or `--source overture`) turns a place name, bbox, or point + buffer into a validated GMNS node + link network — no import code, no manual tag mapping.

**Exploring in a browser Workbench.** `netstead app` opens a local app with a linked map and tables, validation with proposed fixes, selection verification, and a natural-language assistant that drafts actions for you to approve.

**Natural-language selection.** `netstead select` resolves a plain-English description ("residential links north of the river") to GMNS link / node ids, through Anthropic, OpenAI, Gemini, or a local Ollama model (the `[nl]` extra).

**Surfaces for every workflow.** CLI for shells and scripts, notebook `_repr_html_` for Jupyter, the browser Workbench for interactive exploration, FastAPI HTTP server for self-hosting, MCP server for Claude Desktop / Claude Code; every data command supports `--json`.

## Use cases — when to install netstead

<div class="grid cards" markdown>

-   :material-map:{ .lg .middle } &nbsp;**MPO / DOT planning**

    ---

    Loading regional networks from OSM-derived sources, validating against the spec, applying data-quality checks before they feed a model.

-   :material-truck-fast-outline:{ .lg .middle } &nbsp;**Travel-demand modeling**

    ---

    Scoping a regional GMNS network down to a project area or analysis subnet, exporting to your modeling tool's format with zero conversion code.

-   :material-pencil-ruler:{ .lg .middle } &nbsp;**Network editing**

    ---

    Cleaning up an OSM-extract before it goes into a model — simplify geometry, merge near-duplicate nodes, remove orphans, recompute lengths.

-   :material-robot-outline:{ .lg .middle } &nbsp;**AI-assisted analysis**

    ---

    Letting Claude (or any MCP-compatible agent) drive validation, quality checks, and scope queries through a tool-call loop.

-   :material-server-network:{ .lg .middle } &nbsp;**Self-hosted network API**

    ---

    Exposing a curated set of GMNS networks behind a FastAPI HTTP server with bearer-token auth for consumers in your organization.

-   :material-school-outline:{ .lg .middle } &nbsp;**GTFS-GMNS interop research**

    ---

    Researching the bridge between transit schedules and routable street networks; netstead gives you the GMNS side as a stable substrate.

</div>

## Why install netstead

**GMNS spec built in.** Vendored 0.95 / 0.96 / 0.97 side-by-side. Default 0.97; per-call override `Network.from_source(path, spec_version="0.96")`. Networks load against the version they were written against — no silent upgrades.

**Regional scale on a laptop.** Lazy ibis + DuckDB by default. A 200k-node network reads in seconds and validates in tens of seconds. Spatial and graph indexes built on demand, cached to sidecar parquet, content-hash keyed for auto-invalidation.

**Four usage modes target the same data.** CLI, notebook, programmatic, browser Workbench — choose by ergonomics, not capability.

**AI-first by design.** Every data command supports `--json` for tool-call loops. `llms.txt` + `llms-full.txt` + `ai/api-index.json` regenerate on every docs build. Optional MCP server (`netstead mcp serve`) for Claude Desktop / Claude Code. Six Claude Code Skills shipped in-repo.

**No vendor lock-in.** Pure Python under Apache 2.0. Formats, quality rules, scope ops all behind protocols you can implement yourself.

## Install

Requires Python 3.11+. `netstead` 1.0.0b2 is not on PyPI yet (the first release under the new names is pending), so one command installs both packages from GitHub, with every optional extra:

```bash
uv init --bare
uv add \
  "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral" \
  "netstead[all] @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/netstead"
```

`uv init --bare` makes a new folder a uv project, which `uv add` needs; skip it when you add netstead to a project you already have.

The four install tools (uv, uv pip, pip, pipx), the full table of extras and what each pulls in, `netstead doctor`, and the first-run errors are all on one page: [Install and troubleshoot](install.md). zsh users: [quote the brackets](install.md#zsh-quote-the-brackets).

## Where to go next

<div class="grid cards" markdown>

-   :material-monitor-dashboard:{ .lg .middle } &nbsp;**Quickstart: the Workbench**

    ---

    Open the bundled Leavenworth network in the browser — select, validate, fix, replay as Python, save a copy. Ten minutes, no code.

    [:octicons-arrow-right-24: Start in the browser](quickstart-workbench.md)

-   :material-rocket-launch:{ .lg .middle } &nbsp;**Quickstart: Python**

    ---

    Install, load the bundled Leavenworth fixture, run validation in five minutes.

    [:octicons-arrow-right-24: Start in Python](quickstart.md)

-   :material-download:{ .lg .middle } &nbsp;**Install and troubleshoot**

    ---

    Python version, the four install tools, every extra, `netstead doctor`, and the first-run errors with their fixes.

    [:octicons-arrow-right-24: Install](install.md)

-   :material-book-open-variant:{ .lg .middle } &nbsp;**What is GMNS?**

    ---

    Plain-English intro to the spec — what it defines, who maintains it, how it relates to GTFS / OSM / Frictionless.

    [:octicons-arrow-right-24: Spec overview](what-is-gmns.md)

-   :material-eye-outline:{ .lg .middle } &nbsp;**Visual tour**

    ---

    The Workbench explained screen by screen — what a GMNS network looks like on the map, what an ambiguous selection means, what the validation severities and the edit log tell you.

    [:octicons-arrow-right-24: Visual tour](visual-tour.md)

-   :material-book-open-page-variant:{ .lg .middle } &nbsp;**Cookbook**

    ---

    Task-oriented recipes — read from S3, scope, edit with rollback, self-host, AI-drive.

    [:octicons-arrow-right-24: Browse recipes](cookbook/index.md)

-   :material-application-cog-outline:{ .lg .middle } &nbsp;**Workbench in full**

    ---

    Every panel and command — linked map and tables, validation with fixes, selection verification, the natural-language assistant, settings.

    [:octicons-arrow-right-24: Explore in the Workbench](cookbook/workbench.md)

-   :material-compare-horizontal:{ .lg .middle } &nbsp;**The Workbench and other tools**

    ---

    How the Workbench differs from GeoLibre, QGIS, ArcGIS and the GMNS modelling tools, and how to use them together.

    [:octicons-arrow-right-24: Compare tools](workbench-and-other-tools.md)

-   :material-api:{ .lg .middle } &nbsp;**API reference**

    ---

    Every public symbol with stable anchors that match the api-index.json.

    [:octicons-arrow-right-24: API reference](reference/api.md)

-   :material-table:{ .lg .middle } &nbsp;**Schema reference**

    ---

    Every GMNS table, field, foreign key, with ER diagrams.

    [:octicons-arrow-right-24: Schema](reference/spec.md)

</div>

## Upgrading from v0.3?

v1.0 is a clean-break rewrite. See the [migration guide](migration/v0.3-to-v1.0.md) for the side-by-side API mapping.
