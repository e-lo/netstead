---
title: corral
audience: users
kind: concept
summary: Generic Frictionless data-package engine — lazy ibis with DuckDB; pandas / polars / Arrow in and out. Reads CSV / Parquet / DuckDB / zip-CSV from local paths and URLs with a credentials cascade. Composable validation, scope, editing, HTTP and MCP primitives.
---

# corral

A generic engine for tabular data packages in the [Frictionless](concepts/frictionless.md) format. Lazy by default, regional-scale ready, with composable primitives for validation, scope, editing, and serving.

## What problems corral solves

**Reading any Frictionless data package**, regardless of physical format, with one API surface:

<!-- doctest: skip -->
```python
from corral import Package

pkg = Package.from_source("local/path/datapackage.json")      # local directory
pkg = Package.from_source("s3://bucket/path/datapackage.json")  # cloud, credentials cascade
pkg = Package.from_source("./mydata.duckdb")                    # single-file duckdb
pkg = Package.from_source("./mydata.csv.zip")                   # zipped CSV bundle
```

**Lazy evaluation at regional scale.** Tables aren't materialized until you ask. `pkg.tables["link"].filter(...).count()` pushes the count to DuckDB; only the integer comes back to Python.

**One compute engine, any dataframe format.** DuckDB (driven through ibis) does all the work. Results come out as pandas, polars or Arrow via `Table.to_pandas()` / `.to_polars()` / `.collect()`, and in-memory frames go in via `from_arrow()`. See the [engine + formats concept](concepts/engines.md).

**Validation as a single report.** `pkg.validate()` returns one `ValidationReport` covering structural, schema, foreign-key, and sync-state checks. Severity-graded; rendered as rich console, JSON, or interactive HTML.

**Composable editing with rollback.** Open a `Session`, apply one or many edits, commit or roll back atomically. Persisted log as a sidecar parquet file.

**Generic surfaces** — CLI, FastAPI HTTP primitives (`corral.api`), and MCP server primitives (`corral.mcp`) — all reusable for any domain-specific spec that builds on corral (netstead being the canonical example). `corral.api` imports FastAPI but `dbcorral` declares no FastAPI dependency: `pip install fastapi pydantic-settings` (or install `netstead[server]`) before using it.

## Use cases — when to install dbcorral directly

<div class="grid cards" markdown>

-   :material-truck-fast-outline:{ .lg .middle } &nbsp;**GTFS interop research**

    ---

    Building a GTFS ↔ GMNS bridge, or analysing GTFS feeds with the same toolchain you use for other tabular specs.

-   :material-file-cog-outline:{ .lg .middle } &nbsp;**Custom internal spec**

    ---

    Your organization has a tabular data spec (sensor metadata, asset catalogs, planning datasets) that you want to validate, version, and serve consistently.

-   :material-cloud-outline:{ .lg .middle } &nbsp;**Cloud-resident data**

    ---

    Tables live in S3 / Azure Blob / GCS, and you want lazy SQL pushdown without writing the connection plumbing yourself.

-   :material-wrench-cog-outline:{ .lg .middle } &nbsp;**Building your own toolkit**

    ---

    You want the generic primitives (`Engine` protocol, FormatAdapter registry, ValidationReport, Session, FastAPI helpers) to compose into a domain-specific package. netstead is the worked example.

</div>

## Why install dbcorral

**It's small and focused.** Generic data-package primitives only — no domain semantics. Easy to reason about, easy to extend.

**DuckDB-backed, dataframe-friendly.** Every table is a lazy ibis expression pushed down to DuckDB; pandas / polars / Arrow sit at the edges as input and output formats, never as compute backends. The `Engine` protocol describes that single engine's surface (`read_csv`, `read_parquet`, `from_arrow`, `to_pandas`, …).

**Production-grade defaults.** Bearer-token auth on the HTTP server by default. Warn-loudly on misconfiguration (e.g. `auth=none` + non-localhost bind). Cost-model gating on long operations with explicit approval semantics.

**Extension points are first-class.** `register_adapter` for new storage formats. `register_rule` for quality rules. `extra_router_factory` for HTTP extensions. Same pattern across the surface.

## Install

`dbcorral` 1.0.0b2 is not on PyPI yet (the package was renamed from `datagrove` in October 2026; the first
release under the new name is pending), so the beta installs from GitHub with any of the tools below:

```bash
uv init --bare
uv add "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral"
```

`uv init --bare` makes a new folder a uv project, which `uv add` needs; skip it when you add netstead to a project you already have.

Once the release is out, pick the tool you already use — these all produce the same install:

=== "uv (recommended)"

    ```bash
    uv add dbcorral
    ```

    Fastest. Works inside a `uv`-managed project and writes to your `pyproject.toml` + `uv.lock`.

=== "uv pip"

    ```bash
    uv pip install dbcorral
    ```

    Drop-in `pip` replacement. Use this in a plain `venv` without a project file.

=== "pip"

    ```bash
    pip install dbcorral
    ```

    Classic. Works anywhere Python does.

=== "pipx"

    ```bash
    pipx install dbcorral
    ```

    Isolated env for the `corral` CLI only — your project env stays untouched.

### Optional extras

The default install ships the ibis + DuckDB engine and Frictionless loader. Extras let you opt in to dataframe output formats, cloud backends, and the AI surface:

| Extra | Pulls in | When you need it |
|---|---|---|
| `polars` | `polars>=1.0` | Get results as polars DataFrames via `Table.to_polars()` — output only; DuckDB still does the compute (see [engine + formats](concepts/engines.md)) |
| `pandas` | `pandas>=2.2` | Get results as pandas DataFrames via `Table.to_pandas()` — output only |
| `s3` / `gcs` / `azure` | corresponding fsspec adapter | Read from cloud-storage URLs |
| `keyring` | `keyring>=24` | Resolve credentials from the system keychain |
| `mcp` | `mcp>=1.0` | Build an MCP server with `corral.mcp.build_server()`; the runnable server is `netstead mcp serve` |

Install with the same syntax (uv shown — substitute your tool):

```bash
uv add 'dbcorral[polars,s3,keyring]'   # combine with commas
```

!!! tip "zsh users: quote the brackets"
    On **zsh** (the default shell on macOS), `[` and `]` are glob characters. Running `uv add dbcorral[polars]` unquoted gives `zsh: no matches found: dbcorral[polars]`. Always wrap the extras in quotes (`'dbcorral[polars]'` or `"dbcorral[polars]"`), or run `setopt no_nomatch` once per session to disable the check. bash users don't hit this.

## Where to go next

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } &nbsp;**Quickstart**

    ---

    Load a data package, validate it, scope it, write it out — in five minutes.

    [:octicons-arrow-right-24: Get started](quickstart.md)

-   :material-book-open-page-variant:{ .lg .middle } &nbsp;**Cookbook**

    ---

    Task-oriented recipes — read from S3, convert formats, spatial scope.

    [:octicons-arrow-right-24: Browse recipes](cookbook/index.md)

-   :material-api:{ .lg .middle } &nbsp;**API reference**

    ---

    Every public symbol, auto-generated from docstrings, with stable anchors.

    [:octicons-arrow-right-24: API reference](reference/api.md)

-   :material-architecture:{ .lg .middle } &nbsp;**Architecture**

    ---

    Defaults, rationales, extension points. Single source of truth.

    [:octicons-arrow-right-24: Architecture](architecture.md)

</div>
