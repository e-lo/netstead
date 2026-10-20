---
title: The compute engine (DuckDB) and dataframe formats
audience: both
kind: concept
summary: DuckDB (via ibis) is corral's single compute engine. pandas, polars and pyarrow are input/output formats, not compute backends. Push predicates down to SQL; materialise to a dataframe only for Python-side work (shapely, regex, ML).
---

# The compute engine and dataframe formats

## What it is

corral has **one compute engine: DuckDB, driven through [ibis](https://ibis-project.org/)**. Everything — reads, filters, joins, validation, foreign-key checks, spatial scopes, edits — is expressed as lazy ibis expressions and executed inside DuckDB, pushing predicates down to SQL so only matching rows ever materialise.

## Why we have it

**pandas, polars and pyarrow are *formats*, not engines.** You hand data in as any of them and get results back as any of them, but the computation always happens in DuckDB. This mirrors the [ibis project's own decision](https://ibis-project.org/posts/farewell-pandas/) to drop its non-SQL execution backends in 10.0: there is no feature gap versus DuckDB, DuckDB is faster, and DuckDB queries pandas/polars/Arrow objects directly (zero-copy via Arrow). We stopped maintaining separate pandas/polars *compute* engines for the same reasons.

## Getting data in and out

**In** — DuckDB reads files directly (`Package.from_source(path)` for CSV / Parquet / DuckDB / zipped CSV), and you can bring an in-memory frame in through Arrow:

<!-- doctest: skip -->
```python
import pyarrow as pa
from corral.engines import get_engine

e = get_engine()                      # the DuckDB engine
expr = e.from_arrow(pa.Table.from_pandas(df))   # pandas -> engine
expr = e.from_arrow(polars_df.to_arrow())       # polars -> engine
expr = e.from_records({"a": [1, 2, 3]})         # columnar dict -> engine
```

**Out** — materialise a table to whichever format the next step wants:

<!-- doctest: skip -->
```python
pkg.tables["link"].to_pandas()        # -> pandas.DataFrame (nullable dtypes)
pkg.tables["link"].to_polars()        # -> polars.DataFrame
pkg.tables["link"].collect()          # -> engine-native (ibis) for further lazy work
```

`to_pandas()` returns the cross-engine nullable dtype family (`Int64` / `Float64` / `string` / `boolean`) so missing integers never silently become floats.

## Mental model

* **Default to pushing down.** If your operation is a predicate (`t.toll > 0`, `t.zone_id.isin([1, 2, 3])`), a spatial scope (`from_bbox(table, minx, miny, maxx, maxy)`), or an aggregation (`t.length.sum()`), write it as an ibis expression — `table.filter(lambda t: t.filter(t.toll > 0))` — and DuckDB answers in milliseconds against a 200k-link network. The data never leaves the engine.
* **Materialise to a dataframe only for Python-side work.** Pure-Python parsing (shapely geometries, regex, fuzzy strings, ML models) needs values in memory. Push down what you *can* first (e.g. a coarse `facility_type == 'residential'` filter), then `.to_pandas()` / `.to_polars()` the surviving rows and do the Python work there.
* **Never materialise a whole table just to filter it in Python.** If the predicate is SQL-expressible, push it down.

## Spatial

`corral.dataset.view.from_bbox` / `from_polygon` / `from_geometry_buffer` build on the same pushdown pattern: the predicate compiles to DuckDB SQL (via the DuckDB spatial extension), partitioned parquet sources prune partitions at scan time, and full materialisation is the exception. `netstead.scope` adds *network-aware* scopes (`from_nodes`, `from_link`, `from_point`, `connected_component`, `from_zone`) on top.

## See also

* [Architecture §6.1 — engine + I/O](../architecture.md#61-engine-io) — design rationale for the DuckDB-first choice.
* [Frictionless data packages](frictionless.md) — the schema/data model the engine sees.
* [DuckDB spatial extension](https://duckdb.org/docs/extensions/spatial/overview.html) — the ST_* functions available from ibis.
* [Ibis: Farewell pandas](https://ibis-project.org/posts/farewell-pandas/) — the upstream decision this mirrors.
