---
title: Quickstart
audience: users
kind: howto
summary: Install, load any Frictionless data package, validate it, scope it, and write it out in five minutes — using only corral, no GMNS bits.
---

# Quickstart — corral

## When to use this

You want to load + validate + (optionally) spatially scope a Frictionless data package — any spec, not just GMNS — and see what `corral` gives you in five minutes. If you're working with GMNS networks specifically, the [netstead quickstart](https://e-lo.github.io/netstead/netstead/quickstart/) is the better starting point; everything here applies underneath that surface.

## Quick example

The fastest way to confirm `corral` is wired up is to load a real Frictionless package, run the four-pass validator, and print a one-line summary. The example below uses corral's own bundled sample fixture (three tables — `author`, `venue`, `book` — with two foreign keys and a WKT geometry column) as a stand-in for "any Frictionless package" — the API is identical regardless of spec.

`dbcorral` 1.0.0b2 is not on PyPI yet (the first release under the new name is pending), so the beta installs from GitHub:

```bash
pip install "dbcorral @ git+https://github.com/e-lo/netstead.git@main#subdirectory=packages/corral"
```

Then in Python:

```python
from corral import Package
from corral.fixtures import sample  # any Frictionless dir works here

pkg = Package.from_source(sample.csv_dir(), spec=sample.DATAPACKAGE)
report = pkg.validate()
print(f"{len(pkg.tables)} tables, {len(report.issues)} validation issues")
```

Expected:

```text
3 tables, 0 validation issues
```

You just loaded a Frictionless data package through the ibis + DuckDB engine, then ran the structural / schema / FK / sync-state validator in a single call. Nothing was eagerly materialized — `pkg.tables["book"]` is still a lazy expression.

## Step-by-step

### 1. Install

The default install ships everything for the four-pass validator below. Pick your tool:

=== "uv (recommended)"

    ```bash
    uv add dbcorral
    ```

=== "uv pip"

    ```bash
    uv pip install dbcorral
    ```

=== "pip"

    ```bash
    pip install dbcorral
    ```

=== "pipx"

    ```bash
    pipx install dbcorral
    ```

Optional extras (`polars` / `pandas` / `s3` / `gcs` / `azure` / `keyring` / `mcp`) opt in to dataframe output formats (`Table.to_polars()` / `.to_pandas()`), cloud backends, and the AI surface — DuckDB is the only compute engine either way. See the [install guide](index.md#optional-extras) for the full list.

### 2. Load a Frictionless package

`Package.from_source` accepts a local path, an `s3://` / `https://` / `duckdb://` URL, or a directory of CSV / Parquet / DuckDB / zip-CSV files. The example below loads the Leavenworth GMNS fixture (a CSV directory that ships with `netstead` — [install netstead](https://e-lo.github.io/netstead/netstead/install/) to follow along, or keep using `corral.fixtures.sample`). Because a CSV-only directory doesn't carry a `datapackage.json` manifest, pass `spec=` explicitly to tell `corral` which schema to validate against.

```python
from corral import Package
from netstead.fixtures import leavenworth

pkg = Package.from_source(
    leavenworth.csv_dir(),
    spec=leavenworth.spec_path(),
)
```

You should see something like:

<!-- doctest: skip -->
```python
>>> pkg
Package(source='.../leavenworth/csv', tables=9)
```

Substitute any other Frictionless package — your own spec, a GTFS feed converted to a Frictionless package, or a cloud-hosted parquet partition — and the rest of the steps below are identical.

### 3. Validate and read the report

Validation runs four passes in one call — **structural** (required resources present), **schema** (field types and constraints), **foreign-key** (cross-table integrity), and **sync-state** (have FKs gone stale since a previous edit?). The result is one `ValidationReport` with severity-graded issues.

```python
report = pkg.validate()
print(f"{len(report.issues)} issues across {len(pkg.tables)} tables")

for issue in report.issues[:5]:
    print(f"  {issue.severity.value:9} {issue.code:30} {issue.message[:60]}")
```

In a Jupyter notebook, evaluating `report` on its own renders an HTML card grouped by severity. In a script, you can also serialize to JSON for CI:

```python
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    report.to_json(pathlib.Path(d) / "validation.json")
```

### 4. Scope to a spatial subset

If your package has a geometry column (WKT or WKB), `corral.dataset.view.from_bbox` returns a lazy `Table` filtered to features that intersect a bounding box. The example below scopes the Leavenworth `geometry` table (which carries the inline WKT) to a small bbox around the historic core. A table with no geometry column passes through unfiltered — Leavenworth's `link` table, for example, carries only a `geometry_id`.

```python
from corral.dataset.view import from_bbox

geometry = pkg.tables["geometry"]
scoped_geom = from_bbox(geometry, -120.665, 47.594, -120.655, 47.600)
print(f"scoped: {scoped_geom.count()} geometries in bbox")
```

Expected:

```text
scoped: 118 geometries in bbox
```

The filter runs inside DuckDB's spatial extension (`ST_Intersects`) — only the matching rows are read.

### 5. Write the package out

`pkg.write(dest)` round-trips the package to a new location. The output format is inferred from the extension (case-insensitively): `.parquet` — or no extension at all — for a Parquet directory with one GeoParquet file per table, `.csv` for a CSV directory, `.duckdb` for a single-file DuckDB database, and `.zip` for zipped CSV. Writing onto an existing destination raises `FileExistsError` unless you pass `overwrite=True`.

```python
import tempfile, pathlib
with tempfile.TemporaryDirectory() as d:
    pkg.write(pathlib.Path(d) / "out.parquet")
```

Parquet and CSV directories and the zip each carry a `datapackage.json` with the schemas inlined. `Package.from_source` picks it up from a directory on its own; for a zip, pass `spec=` when you load it back (the descriptor travels inside the archive but is not read automatically yet). DuckDB output is data only, so pass the same `spec=` when you reload it.

## Common variations

Each accordion below is one alternative to the defaults used above. The first (most common) is expanded; the rest are collapsed — open the one that matches your situation.

???+ note "Get results as pandas or polars"
    DuckDB is the only compute engine; pandas and polars are output formats. Materialize any `Table` to the frame type the next step wants (needs the `pandas` / `polars` extra respectively).

    <!-- doctest: skip -->
    ```python
    df = pkg.tables["book"].to_pandas()    # pandas.DataFrame, nullable dtypes
    pl_df = pkg.tables["book"].to_polars() # polars.DataFrame
    ```

??? note "Read from S3 with credentials"
    Credentials resolve via a cascade: `credentials=` keyword arg → `CORRAL_CRED_<HOST>_TOKEN` env var (or `CORRAL_CRED_<HOST>_KEY` + `_SECRET` for S3-style keys) → OS keyring → `.netrc`. No code changes needed if the environment is set up. Cloud drivers come from the `s3` / `gcs` / `azure` extras.

    <!-- doctest: skip -->
    ```python
    pkg = Package.from_source(
        "s3://bucket/path/",
        credentials={"key": "AKIA…", "secret": "…"},  # optional — omit to use the cascade
    )
    ```

??? note "Load only a subset of tables"
    Pass `tables=` to materialize only the tables you need — useful when the package is large and you only care about a few resources.

    ```python
    pkg = Package.from_source(
        sample.csv_dir(),
        spec=sample.DATAPACKAGE,
        tables=["author", "book"],
    )
    ```

??? note "Pass the spec from a URL or another package"
    `spec=` accepts a local path, a URL, or an already-loaded `Spec` object. Useful for sharing a spec across many sibling datasets.

    <!-- doctest: skip — placeholder URL; fetching a spec needs network access -->
    ```python
    pkg = Package.from_source(
        sample.csv_dir(),
        spec="https://example.org/specs/my-spec/datapackage.json",
    )
    ```

## Pitfalls

* **No `datapackage.json` in your directory → pass `spec=` explicitly.** CSV-only fixtures and ad-hoc directories don't carry a manifest, so `corral` can't infer the schema. Point `spec=` at the canonical `datapackage.json` for your data. See [Frictionless data packages](concepts/frictionless.md) for the spec-vs-data distinction.
* **Credential cascade order matters.** Per-call `credentials=` always wins; otherwise the `CORRAL_CRED_<HOST>_TOKEN` env var (or `CORRAL_CRED_<HOST>_KEY` + `CORRAL_CRED_<HOST>_SECRET`), then OS keyring, then `.netrc`. If you've stashed a token in two places, the higher-priority one is the one that gets used.
* **Lazy by default.** Tables are lazy ibis expressions over DuckDB — nothing materializes until you call `.to_pandas()` / `.to_polars()` / `.count()`. Push predicates down and only pull the rows you need into Python. `.to_pandas()` and `.to_polars()` need the `pandas` / `polars` extras.

## See also

* [Cookbook](cookbook/index.md) — task-oriented recipes (read from S3, convert formats, spatial scope).
* [API reference](reference/api.md) — every public symbol with stable anchors.
* [Architecture](architecture.md) — defaults, rationales, extension points.
* [Frictionless data packages](concepts/frictionless.md) — the spec `corral` builds on.
