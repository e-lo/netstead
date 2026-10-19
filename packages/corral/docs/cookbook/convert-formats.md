---
title: Convert CSV ↔ Parquet ↔ DuckDB ↔ zip-CSV
audience: users
kind: howto
summary: Round-trip a data package between the four bundled storage formats — when to pick which, how the CLI and Python entry points compare.
---

# Convert CSV ↔ Parquet ↔ DuckDB ↔ zip-CSV

## When to use this

You have data in format X (a regulator handed you a folder of CSVs, an upstream pipeline emits parquet, a teammate sent a DuckDB file) and a downstream tool wants format Y. Or you're loading the same package over and over and want to switch to a format with faster cold-start.

## Quick example

Convert the bundled Leavenworth CSV directory into a parquet directory. The destination extension determines the output format; `--json` swaps the rich summary table for one JSON document:

```bash
corral convert --json packages/netstead/netstead/fixtures/leavenworth/csv ./leavenworth.parquet
```

Expected:

```json
{
  "source": "packages/netstead/netstead/fixtures/leavenworth/csv",
  "dest": "./leavenworth.parquet",
  "format": "parquet",
  "engine": "IbisEngine",
  "table_count": 9,
  "total_rows": 1239
}
```

Without `--json` the same six fields render as a rich table on stdout. `./leavenworth.parquet` is a directory with one Parquet file per table (`link.parquet`, `node.parquet`, …) and no `datapackage.json`.

## Step-by-step

### 1. Pick the destination format

Each format has different trade-offs for cold-read latency, write speed, and predicate pushdown. Rule of thumb: **parquet for interchange, duckdb for repeated local use, zipcsv for emailing, csv only when a downstream tool demands it**.

| Format | Cold read | Write | Predicate pushdown | Portability |
|---|---|---|---|---|
| `csv` (directory) | Slowest — full scan, no schema. | Slow — text encoding. | None. | Universal. Any tool reads CSV. |
| `parquet` (directory, one file per table) | Fast — columnar, schema embedded. | Fast. | Yes — column + row-group prune. | Wide. Arrow, DuckDB, Polars, pandas. |
| `duckdb` (single file) | Fastest — pre-indexed, statistics cached. | Slowest — DDL + insert per table. | Yes — full SQL. | DuckDB-only readers. |
| `zipcsv` (single `.zip`) | Slow — text decode after unzip. | Medium. | None. | Universal + single-file. Carries an inlined `datapackage.json`. |

### 2. Run `corral convert`

The CLI infers the format from the destination extension — `.parquet`, `.csv`, `.duckdb`, `.zip` — case-insensitively, and an extension-less destination defaults to a parquet directory:

```bash
corral convert <source> <dest>
```

Override the inference with `--format` when the extension is missing or ambiguous:

```bash
corral convert ./csv_dir ./out.duckdb --format duckdb
corral convert ./csv_dir ./out          --format csv
```

`netstead convert` is the same command with GMNS defaults (it knows the vendored spec, so you don't need a `datapackage.json` next to the source).

The same dispatch is available from Python — `Package.write` accepts the same destination + optional `format=` kwarg as the CLI. Leavenworth ships as a CSV directory without a manifest, so pass its spec explicitly:

```python
import tempfile, pathlib
from corral import Package
from netstead.fixtures import leavenworth

pkg = Package.from_source(leavenworth.csv_dir(), spec=leavenworth.spec_path())
with tempfile.TemporaryDirectory() as d:
    pkg.write(pathlib.Path(d) / "leavenworth.duckdb")
```

### 3. (Optional) Keep the schema with the data

Parquet, CSV and DuckDB outputs carry the data only — no `datapackage.json` is written next to them. If the source had a manifest, keep it (or the `spec=` you loaded with) and pass it again when you reload the converted copy. This also matters for all-null columns: without a schema an entirely-empty CSV column comes back as a string column, whereas with `spec=` it is cast to the declared type (the case behind [#163](https://github.com/e-lo/netstead/issues/163)).

### 4. Verify the round-trip

Load the destination back (passing the spec again) and validate. This catches any schema drift introduced by the conversion (e.g. a string column that came back as int because every value happened to parse):

```python
import tempfile, pathlib
from corral import Package
from corral.reports import Severity
from netstead.fixtures import leavenworth

# Round-trip CSV → parquet → re-load → validate.
with tempfile.TemporaryDirectory() as d:
    out = pathlib.Path(d) / "leavenworth.parquet"
    Package.from_source(leavenworth.csv_dir(), spec=leavenworth.spec_path()).write(out)
    pkg = Package.from_source(out, spec=leavenworth.spec_path())
    report = pkg.validate()
    assert not report.has_errors, [i.code for i in report.issues if i.severity is Severity.ERROR]
    print(f"{len(pkg.tables)} tables, {pkg['link'].count()} links — round-trip clean")
```

Expected:

```text
9 tables, 339 links — round-trip clean
```

See [validate-network](https://e-lo.github.io/netstead/netstead/cookbook/validate-network/) for what's in the report.

### 5. (Optional) Convert remote → local

`convert` accepts the same URL surface as `from_source`, so cloud → local is a one-liner. The download happens once; from then on you load the local copy:

```bash
corral convert s3://my-bucket/networks/leavenworth/ ./leavenworth.parquet
```

See [Read from S3](read-from-s3.md) for credential handling.

## Common variations

???+ note "Default — CLI conversion with extension inference"
    Most conversions are one-line CLI calls. The destination extension picks the format.

    ```bash
    corral convert ./csv_dir ./out.parquet
    ```

??? note "Programmatic conversion in Python"
    Same dispatch as the CLI; useful inside scripts and notebooks.
<!-- doctest: skip -->

    ```python
    from corral import Package
    Package.from_source(src, spec=spec).write(dest)
    ```

??? note "Re-pack only a subset of tables"
    Load just the tables you need with `tables=`; the writer only emits what was loaded.
<!-- doctest: skip -->

    ```python
    Package.from_source(src, spec=spec, tables=["link", "node"]).write(dest)
    ```

    `Package.scope(tables=[...])` does the same on an already-loaded package. See the [netstead scope recipes](https://e-lo.github.io/netstead/netstead/cookbook/) for FK-aware variants that walk relationships.

??? note "Parquet directory (one file per table)"
    Any extension-less destination, or `--format parquet`, writes one GeoParquet file per table. Hive partitioning is **not** applied by default — it is opt-in per table via `ParquetAdapter.write(expr, dest, engine, partition_by=[...])` from Python.

    ```bash
    corral convert ./csv_dir ./parquet_dir --format parquet
    ```

??? note "Compress the CSV output (zipcsv)"
    Same wire format as CSV but typically 5–10× smaller — useful for emailing or storing in artifact stores. The archive also carries a `datapackage.json` with the schemas inlined, so it reloads without a separate spec.

    ```bash
    corral convert ./csv_dir ./out.zip --format zipcsv
    ```

??? note "Validate before the write"
    `validate()` returns a report rather than the package, so check it and then write:
<!-- doctest: skip -->

    ```python
    pkg = Package.from_source(src, spec=spec)
    report = pkg.validate()
    if report.has_errors:
        raise SystemExit(f"{len(report.issues)} issues — not writing")
    pkg.write(dest)
    ```

## Pitfalls

* **All-null columns lose their type without a schema.** A column that is 100% null in the source CSV comes back as a string column unless the declared type is available — pass `spec=` (or keep the `datapackage.json` next to the source) so the cast is applied. Background in [#163](https://github.com/e-lo/netstead/issues/163).
* **Converting *to* CSV loses dtypes.** Re-reading the CSV without a schema infers columns afresh. Round-trip safety relies on the schema being re-applied at load time — pass `spec=` again (netstead does this automatically for GMNS-shaped directories).
* **DuckDB files lock per-process.** Two Python processes can't open the same `.duckdb` file in write mode at once. For shared use, write parquet instead.
* **Zip-CSV holds the whole archive in memory on read.** Fine for Leavenworth-scale networks; not fine for regional ones. Convert zip-CSV to parquet up-front if you'll re-read.
* **An existing destination is never clobbered silently.** `Package.write` raises `FileExistsError` unless you pass `overwrite=True`; `corral convert` has no overwrite flag, so delete the old destination first.

## See also

* [Read from S3](read-from-s3.md) — `convert` works across cloud schemes too (`corral convert s3://… ./local.parquet`).
* [Architecture](../architecture.md) — Package → Engine → format dispatch.
* [API reference](../reference/api.md) — `Package.write`, `Package.from_source`.
