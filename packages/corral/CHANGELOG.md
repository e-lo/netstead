# Changelog — corral

All notable changes to the `corral` package. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [Semver](https://semver.org/).

This file is for the **corral** package only. `netstead` (which depends on corral) keeps its own CHANGELOG at [`packages/netstead/CHANGELOG.md`](../netstead/CHANGELOG.md).

## [Unreleased]

### Changed

- **`register_adapter` refuses collisions.** Registering a different adapter
  class under a taken name, or claiming an extension or URL scheme another
  adapter owns, now raises `DuplicateAdapterError` (a `FormatError`) instead
  of silently overwriting. Pass `replace=True` to override on purpose.
  Re-registering the same class still just replaces the instance.
- **`dispatch` logs a probe that raises.** The probe chain still skips an
  adapter whose `probe()` raises, but now says so at DEBUG on the
  `corral.io` logger (adapter name and error; URL userinfo is masked).
- **An unreadable netrc file is reported.** The credentials cascade used to
  skip a `.netrc` (or `$NETRC`) it could not read, such as one with the
  wrong permissions, without a word. It still skips it, but logs a warning
  naming the file and the host. A missing or unparsable netrc is still
  silent.
- **Tests run against the stock adapter registry.** A new private
  `corral.io._reset_registry()` restores the five stock adapters and their
  bindings; one autouse fixture in `tests/conftest.py` calls it around every
  test, replacing the per-file re-registration workarounds.

### Fixed

- **Partitioned parquet writes stream.** `ParquetAdapter.write(...,
  partition_by=[...])` collected the whole table through pandas before
  writing, which runs out of memory on a regional network. It now streams
  the expression's Arrow record batches into pyarrow's dataset writer.
- **`DUCKDB://` URLs open the right file.** Dispatch and `probe` treat the
  `duckdb://` scheme case-insensitively, but the adapter stripped only the
  lowercase spelling, so `DUCKDB:///abs/x.duckdb` was handed to duckdb as a
  literal path. The scheme is now stripped with `urlparse`.
- **`package_to_md` resource links hit their schema headings.** The overview
  table hyphenated the resource name in its in-page link (`#link-tod`) while
  `schemas_to_md` renders `## link_tod`, whose slug keeps the underscore, so
  every resource with an underscore in its name linked to a missing anchor
  (mkdocs logged "no such anchor" 16 times on each of the three netstead
  spec pages). The link now uses the name verbatim.
- **Credentials now reach csv / parquet reads and prefix listings over
  `s3://`, `gs://`, `az://`.** Any non-empty credentials — an explicit
  `credentials=` dict or the env / keyring / netrc cascade — made every csv
  and parquet read fail (`Binder Error: Invalid named parameter
  "storage_options"`): the remote adapter forwarded the fsspec options to
  duckdb's own table functions. The ibis engine now consumes
  `storage_options`, builds the fsspec filesystem from that dict, registers
  it on the duckdb connection under a private protocol and reads (and
  writes) through it; one filesystem per (scheme, host, credentials), so
  different credentials never share one. `RemoteAdapter.scan` takes
  `credentials`, keeps the bucket in the listed path and returns full child
  URLs; `Package.from_source` passes `credentials` to the listing as well as
  the reads. `storage_options` on a local path is now an error. The
  [Read from S3](docs/cookbook/read-from-s3.md) cookbook describes what works.

- **`Package.write(..., overwrite=True)` over the package's own source no
  longer loses tables.** The old target was removed before the tables, lazy
  expressions still reading from it, were written, so only tables already
  materialised in memory survived. Parquet, CSV and DuckDB targets are now
  staged beside `dest` and swapped into place once the write is complete
  (as zip targets already were), so a failed write also leaves an existing
  `dest` intact.
- **Parquet and CSV directory writes now emit a `datapackage.json`.** A
  directory source that carried its own descriptor lost it on an in-place
  write, because only the zip writer produced one. Directory targets now get
  the same self-describing descriptor (package metadata carried over,
  resource paths rewritten, schemas inlined), so `Package.from_source` on
  the written directory auto-discovers the spec again.

## [1.0.0-beta.2] — TBD

First release under the **corral** name (renamed from `datagrove` on
2026-10-05; import `corral`, PyPI distribution `dbcorral` because `corral`
is taken). Version bumped in lockstep with `netstead` v1.0.0-beta.2.

### Changed

- **BREAKING — DuckDB is the only compute engine** (#195). `PandasEngine` and
  `PolarsEngine` are removed, along with per-call `engine="pandas"|"polars"`.
  pandas / polars / pyarrow are I/O formats: frames come in through
  `from_arrow` / `from_records` and go out through `to_pandas()` / `to_polars()`.
  Rationale: [engine strategy ADR](../../docs/design/2026-10-01-engine-strategy-reevaluation.md).
- **Dependency floors raised** (#198): `ibis-framework[duckdb]>=10,<13`,
  `duckdb>=1.1`, `pyarrow>=17` (the old `ibis<10` pin is gone; `<13` added in #212).
- **Geometry is WKB in memory** (#201); CSV reads/writes WKT, Parquet stores WKB.
  [Geometry encoding ADR](../../docs/design/2026-10-01-geometry-encoding-adr.md).

### Fixed

- **Remote zips open through fsspec** (#216). A `.csv.zip` URL used to be
  opened as a local path relative to the working directory, so remote zips
  never worked and a URL containing `..` could read local files.
- **Local `.csv.zip` packages load** through `Package.from_source` (#211);
  members were mis-dispatched to the plain CSV adapter.
- **The shared DuckDB connection is serialized behind an engine lock** (#211),
  since it isn't thread-safe; `to_pandas` converts outside the lock (#212).
- Remote Parquet URLs are no longer mangled into local paths (#211).
- **Undo restores rows that hold nulls.** `reverse_edit` of an `update_rows` or
  `add_rows` edit found the touched rows with an `=` anti-join, which never
  matches `NULL`; a row with any null was left behind as a duplicate (or, for
  an added row, never removed). The reverse joins now compare null-safely.
- **Edits and undo keep column types when a value is `NULL`.** Recorded rows
  (added, deleted, or as they were before an update) are rebuilt with the
  table's own column types instead of types guessed from the values, so a
  `NULL` in a timestamp, decimal, binary or list column no longer arrives typed
  `null`/`string`. Undo of such rows used to fail (`Unsupported cast from
  string to list`) or mis-match them, and adding a row with a `NULL` list
  failed outright. Values are cast to the column type safely: `2.7` added to
  an integer column raises `ArrowInvalid` instead of landing as `2`.

### Added

- **GeoParquet `geo` metadata + bbox** on Parquet writes of WKB geometry
  columns (#202).
- `corral.editing.affected_rows(result)`: the rows an applied edit added,
  changed (as they were before the edit) or removed, read from its rollback
  data. Additive; no existing signature changed.

Test fixtures were also adjusted for the rebuilt Leavenworth fixture
(`netstead.fixtures.leavenworth` now covers the whole city polygon).

## [1.0.0-beta.1] — 2026-06-29

First public preview, tagged `datagrove-v1.0.0-beta.1` (pre-rename; never published to PyPI — names in this section are as tagged). This is a **beta**: API surface is stable enough to build against but we expect bug reports + small breaking changes before 1.0.0 GA.

### What this release covers

- Generic Frictionless Data Package engine with three interchangeable backends — ibis (DuckDB-backed; default), polars, pandas. Switch per call.
- I/O front door: `datagrove.read(source, *, engine=None, spec=None, ...)`. Auto-detects CSV / Parquet / DuckDB / zip-CSV from local paths or `s3://` / `https://` / `duckdb://` URLs.
- Four-pass validator returning a single `ValidationReport`: structural, schema, foreign-key, sync-state. Rich console / JSON / interactive single-file HTML output.
- Generic `editing/` framework with atomic rollback + audit log — used by `gmnspy.clean`.
- Spatial scope primitives in `datagrove.dataset.view`: `from_bbox` / `from_polygon` / `from_geometry_buffer`. Predicates push down to DuckDB SQL where possible.
- Self-hostable FastAPI primitives (`datagrove.api`) and MCP server primitives (`datagrove.mcp`) — assembled into concrete apps by gmnspy.
- Generic data-quality rule framework + entry-point plugin discovery.
- `datagrove` CLI (`validate`, `info`, `convert`) with `--json` on every command for agent / pipeline consumption.
- Cost-model gating on long operations with `--yes` / `DATAGROVE_AUTO_APPROVE=1` bypass for non-interactive use.
- AI artifacts: `llms.txt`, `llms-full.txt`, `ai/api-index.json` regenerate on every docs build.

### Architectural defaults

- Lazy by default. `Package.from_source(...)` returns a Package whose tables are unmaterialised ibis expressions until you `.collect()` / `.to_pandas()` / write.
- No raw SQL outside `datagrove.engines.ibis_engine` (enforced by `scripts/lint_no_sql.py` in CI).
- datagrove never imports gmnspy (enforced by `import-linter`).

### Notable fixes during beta-prep

- `PolarsEngine.from_records` now passes `infer_schema_length=None` so a column that is null for the first >100 rows and then carries a value (common with optional fields) infers correctly instead of raising `ComputeError` on append.

### Known limitations going into beta

- `Package.from_source()` mis-dispatches `.csv.zip` to the CSV adapter — use `csv_dir()` or `parquet_dir()` for now. Fix tracked.
- `datagrove.quality.run()` is named `run_quality()` — alias coming.
- HTML report renderer doesn't yet embed map view for geo-located issues.

### Compatibility

- Python 3.11, 3.12, 3.13.
- Optional extras: `polars`, `pandas`, `s3`, `gcs`, `azure`, `keyring`, `mcp`.

### Migration from pre-1.0 GMNSpy

datagrove is a new package; nothing to migrate from. See [`packages/netstead/docs/migration/v0.3-to-v1.0.md`](../netstead/docs/migration/v0.3-to-v1.0.md) if you're moving from old GMNSpy.

[Unreleased]: https://github.com/e-lo/netstead/compare/datagrove-v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/e-lo/netstead/releases/tag/datagrove-v1.0.0-beta.1
