---
title: Netstead + corral
audience: both
kind: concept
hide:
  - navigation
  - toc
summary: Two PyPI packages with separate utility and separate audiences. corral is the generic Frictionless data-package engine; netstead is the GMNS-specific toolkit built on top. Each has its own documentation site — pick the one that matches your work.
---

# Netstead + corral

This repo holds **two PyPI packages with separate utility and separate audiences**. Each ships its own documentation site:

<div class="grid cards" markdown>

-   :material-database-outline:{ .lg .middle } &nbsp;**corral**

    ---

    Generic engine for **Frictionless tabular data packages** — any spec. Lazy DuckDB compute (via ibis); pandas / polars / Arrow in and out. Reads CSV / Parquet / DuckDB / zip-CSV from local paths and URLs with a credentials cascade. Composable primitives for validation, scope, editing, HTTP and MCP.

    Pick corral if you're working with **any Frictionless data package** — GTFS-derived feeds, OGD datasets, a custom internal spec, or building your own toolkit.

    [:octicons-arrow-right-24: corral docs](https://e-lo.github.io/netstead/corral/){ .md-button .md-button--primary }
    [Quickstart](https://e-lo.github.io/netstead/corral/quickstart/){ .md-button }

-   :material-map-marker-path:{ .lg .middle } &nbsp;**netstead**

    ---

    GMNS-specific toolkit on top of corral. Adds the `Network` class, the vendored GMNS spec (0.95 / 0.96 / 0.97), network-aware scope operations, the data-quality rule pack, optional editing tools with rollback, building networks from OpenStreetMap or Overture (`netstead build`), natural-language selection, a browser Workbench (`netstead app`) with linked map and tables, and an optional self-hosted HTTP server + MCP server.

    Pick netstead if you're working with **transportation networks** — DOT / MPO planning, travel-demand modeling, GTFS ↔ GMNS interop, OSM-derived routing graphs.

    [:octicons-arrow-right-24: netstead docs](https://e-lo.github.io/netstead/netstead/){ .md-button .md-button--primary }
    [Quickstart](https://e-lo.github.io/netstead/netstead/quickstart/){ .md-button }

</div>

## Which package do I install?

Installing `netstead` brings `corral` along automatically. You only install `corral` directly if you're working on something non-GMNS.

```bash
pip install netstead            # GMNS toolkit (brings corral with it)
pip install dbcorral         # generic engine only — for non-GMNS use cases
```

## Source

* GitHub: [e-lo/netstead](https://github.com/e-lo/netstead) — monorepo holding both packages.
* License: [Apache-2.0](https://github.com/e-lo/netstead/blob/main/LICENSE).
* Status: v1.0 in beta. `netstead-v1.0.0` ships when the [Phase 5 acceptance criteria](https://github.com/e-lo/netstead/issues?q=label%3Ablocks-ga) are green.

## Contributing

Both packages live in the same monorepo. See the [development guide](https://e-lo.github.io/netstead/corral/development/) (in the corral site — it covers the monorepo as a whole).
