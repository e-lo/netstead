# corral

Generic, Frictionless-aligned tabular-data-package engine. Powers [Netstead](https://github.com/e-lo/netstead) and is designed to support other tabular data specifications (GTFS, custom formats) via the same primitives.

**Status:** beta (`1.0.0b2`). The public API is settling; breaking changes are still possible before 1.0.0.

## What it provides

- Pydantic models for Frictionless Data Package, Resource, Schema, Field, ForeignKey
- One compute engine — DuckDB driven through ibis, behind the `Engine` protocol. pandas / polars / Arrow are I/O formats (`Table.to_pandas()` / `.to_polars()` out, `from_arrow()` in), not compute backends
- Format adapters: CSV, Parquet (partitioned), DuckDB, zipped CSV, remote URLs (fsspec)
- Validation framework: schema, structural, foreign-key, sync-state (dirty-tracker)
- Lazy dataset surface: `Package`, `Table`, `View` with geographic scoping (bbox, polygon, geometry-buffer)
- Operation cost model + gating, batched/pooled writes
- Report renderers: rich console, JSON, interactive single-file HTML
- Generic edit/diff/session/rollback framework
- **Generic CLI** (`corral …`): `validate`, `convert`, `info` — extendable by domain packages via plugin pattern
- **Generic data-quality framework** (rule base class, threshold config, entry-point plugin discovery — no domain rules)
- **Generic notebook helpers** (`_repr_html_` for Package/Table/ValidationReport/EditResult)
- FastAPI primitives for building self-hostable data APIs (`corral.api`; `dbcorral` declares no FastAPI dependency — `pip install fastapi pydantic-settings`, or install `netstead[server]`)
- MCP server primitives for AI-agent consumption (`corral.mcp.build_server()`; the runnable server is `netstead mcp serve`)
- Docgen: markdown + `llms.txt` + machine-readable API index

## Composition with domain packages

`netstead` and (in the future) similar packages compose on top:

| Concern | corral (generic) | netstead (GMNS-specific) |
|---|---|---|
| editing framework | `editing/` | `clean/` (simplify_geometry, …) |
| HTTP server | `api/` (primitives) | `server/` (assembled FastAPI app) |
| MCP | `mcp/` (primitives) | `mcp/` (GMNS tool registrations) |
| CLI | `cli/` (generic commands + extension hook) | `cli/` (GMNS commands on the same typer app) |
| Quality | `quality/` (rule framework) | `quality/` (GMNS rule pack via entry point) |
| Notebook | `notebook/` (Package/Table/ValidationReport reprs) | `notebook/` (Network repr + scope widgets) |

## Install

Pick the tool you already use — these are equivalent:

`dbcorral` 1.0.0b2 is not on PyPI yet (the package was renamed from `datagrove` in October 2026; the first
release under the new name is pending), so the beta installs from GitHub:

```bash
uv init --bare
uv add "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral"
```

`uv init --bare` makes a new folder a uv project, which `uv add` needs; skip it when you add corral to a project
you already have. `uv pip install` and `pip install` take the same argument.

Once the PyPI release is out, the usual forms apply:

```bash
# uv (recommended — fastest, handles workspace projects + lockfiles)
uv add dbcorral

# uv pip (drop-in pip replacement, no project file needed)
uv pip install dbcorral

# pip (classic)
pip install dbcorral

# pipx (CLI-only, isolated env; gives you the `corral` command without
#       polluting your project env)
pipx install dbcorral
```

### Optional extras

```bash
# Output formats (DuckDB does the compute either way)
uv add 'dbcorral[polars]'      # Table.to_polars() -> polars.DataFrame
uv add 'dbcorral[pandas]'      # Table.to_pandas() -> pandas.DataFrame

# Cloud storage backends (read from s3://, gs://, az:// / abfs:// / abfss://)
uv add 'dbcorral[s3]'          # add 'gcs' or 'azure' as needed

# Credential helpers + AI surface
uv add 'dbcorral[keyring]'     # resolve creds from system keychain
uv add 'dbcorral[mcp]'         # corral.mcp.build_server() primitives (run via `netstead mcp serve`)
```

Combine extras with commas: `uv add 'dbcorral[polars,s3,keyring]'`.

## Repo

Developed in the [Netstead monorepo](https://github.com/e-lo/netstead) under `packages/corral/`.
