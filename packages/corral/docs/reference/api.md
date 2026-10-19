---
title: corral API reference
audience: both
kind: reference
summary: Every public corral symbol, auto-generated from docstrings. Stable anchors match the codes in ai/api-index.json.
stability: stable
---

# corral API reference

## Summary

Auto-generated from package docstrings via [mkdocstrings](https://mkdocstrings.github.io/). Every public symbol gets a stable anchor (`#<dotted.qualname>`) that matches the entries in [`ai/api-index.json`](../ai/index.md). The surface below covers the I/O front door (`corral.read`, `Package`), the lazy `Table`, the spatial scope constructors, the single DuckDB engine, reports, editing, the quality framework, the cost model, and the HTTP / MCP primitives.

For task-oriented recipes see the [cookbook](../cookbook/index.md). For design rationale see [architecture](../architecture.md).

## Top-level

::: corral.read

::: corral.dataset.Package
    options:
      members:
        - from_source
        - from_tables
        - validate
        - scope
        - write
        - safe_count
        - tables
      show_root_heading: true

::: corral.dataset.Table
    options:
      members:
        - filter
        - select
        - head
        - count
        - to_pandas
        - to_polars
        - collect
        - columns
      show_root_heading: true

## Spatial scope

Generic bbox / polygon / buffer scopes over any `Table` with a WKT or WKB geometry column. Each returns a lazy `Table`; a table without the geometry column passes through unfiltered.

::: corral.dataset.view.from_bbox

::: corral.dataset.view.from_polygon

::: corral.dataset.view.from_geometry_buffer

## Engines

DuckDB (via ibis) is the only compute engine; `Engine` is the Protocol it satisfies. `resolve_engine` accepts `"ibis"` / `"duckdb"` / `None` and rejects anything else — pandas / polars / Arrow are output formats, not engines.

::: corral.engines.Engine

::: corral.engines.get_engine

::: corral.engines.register_engine

::: corral.engines.resolve_engine

## Reports

::: corral.reports.ValidationReport

::: corral.reports.Issue

::: corral.reports.Severity

::: corral.reports.Category

## Editing

::: corral.editing.Edit

::: corral.editing.EditResult

::: corral.editing.Session
    options:
      members:
        - add_edit
        - rollback

::: corral.editing.rollback

::: corral.editing.affected_rows

## Quality (generic framework)

::: corral.quality.Rule

::: corral.quality.RuleConfig

::: corral.quality.run_quality

## Operations (cost model + gating)

::: corral.operations.OperationCost

::: corral.operations.gate

::: corral.operations.ApprovalRequired

::: corral.operations.Batch

::: corral.operations.coalesce

## API server primitives

`corral.api` imports FastAPI, but `dbcorral` declares no FastAPI dependency. Install it alongside — `pip install fastapi pydantic-settings` — or install `netstead[server]`, which pins both.

::: corral.api.build_app

::: corral.api.PackageRegistry
    options:
      members:
        - require
        - source_for
        - describe
        - list_ids

::: corral.api.ServerSettings

::: corral.api.AuthSettings

::: corral.api.ExtraRouterFactory

::: corral.api.AuthDep

::: corral.api.PackageLoader

## MCP primitives

`build_server()` returns a FastMCP server with the generic tools `describe_package`, `validate_package` and `list_tables` registered (needs the `mcp` extra). corral has no `mcp` CLI command — the runnable server is `netstead mcp serve`.

::: corral.mcp.build_server

## Examples

Load the bundled sample package, validate it, and scope its `venue` table to a bounding box. Everything stays lazy until `.count()`:

```python
from corral import read
from corral.dataset.view import from_bbox
from corral.fixtures import sample

pkg = read(sample.csv_dir(), spec=sample.DATAPACKAGE)
report = pkg.validate()
venues = from_bbox(pkg["venue"], -123.0, 45.0, -122.0, 46.0)
print(len(pkg.tables), len(report.issues), venues.count())
```

Expected:

```text
3 0 1
```

Apply an edit inside a `Session`, then undo it from the persisted log:

```python
import tempfile, pathlib
from corral import read
from corral.editing import Edit, Session, rollback
from corral.fixtures import sample

pkg = read(sample.csv_dir(), spec=sample.DATAPACKAGE)
with tempfile.TemporaryDirectory() as d:
    log = pathlib.Path(d) / "history.parquet"
    with Session(pkg, log_path=log) as s:
        s.add_edit(Edit(op="delete_rows", table="book", payload={"predicate": lambda t: t.id == 1}))
    after_edit = pkg["book"].count()
    rollback(pkg, log)
    print(after_edit, pkg["book"].count())
```

Expected:

```text
9 10
```

## See also

* [AI surface](../ai/index.md) — explains the api-index.json + llms.txt artifacts.
* [corral cookbook](../cookbook/index.md)
* [Architecture](../architecture.md)
