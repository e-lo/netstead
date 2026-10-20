"""Parquet FormatAdapter — single-file and Hive-partitioned directories.

Two physical layouts share one logical "parquet table":

1. **Single file** — ``foo.parquet``. Trivial: pass the path through to
   the engine's :meth:`~corral.engines.base.Engine.read_parquet`
   primitive.
2. **Hive-partitioned directory** — ``mynet.gmns/link/h3=8829a0c00b/part-0.parquet``
   etc. This is the **recommended persistent layout**
   (see :doc:`architecture` §6.1). Reads here must enable Hive partition
   discovery so the partition columns (``h3`` in the example above) are
   reinjected into the result, and so that a downstream filter on those
   columns becomes a true partition prune.

The adapter does no I/O itself — it detects "is this a partitioned
dir" locally and calls the engine's
:meth:`~corral.engines.base.Engine.read_parquet` primitive with
``hive_partitioning=`` set. Each engine owns the per-library
partitioning detail (duckdb auto-detects Hive style; polars wants a
glob + explicit flag; pandas/pyarrow handles directories natively).
Partitioned writes use pyarrow's ``write_to_dataset`` because not
every engine exposes partitioned-write through its own writer.

Examples:
    Single-file roundtrip with the default ibis engine::

        >>> from pathlib import Path
        >>> import tempfile
        >>> import pyarrow as pa
        >>> import pyarrow.parquet as pq
        >>> from corral.engines import get_engine
        >>> from corral.io.parquet_adapter import ParquetAdapter
        >>> tmp = Path(tempfile.mkdtemp())
        >>> _ = pq.write_table(pa.table({"a": [1, 2, 3]}), tmp / "t.parquet")
        >>> adapter = ParquetAdapter()
        >>> engine = get_engine("ibis")
        >>> df = engine.to_pandas(adapter.read(tmp / "t.parquet", engine))
        >>> int(df["a"].sum())
        6
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from corral.io import _scheme_of, register_adapter
from corral.io._package import write_flat_package
from corral.io._paths import normalize_to_path, normalize_to_str
from corral.io.base import ResourceListing, ResourceRef, SourceRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from corral.engines.base import Engine, TableExpr
    from corral.io.base import DescriptorFactory
    from corral.spec.model import Schema


# ---------------------------------------------------------------------------
# Module-level helpers (no state — easier to read inline than as methods)
# ---------------------------------------------------------------------------


#: Conventional primary geometry column name (GeoParquet / geopandas default).
_DEFAULT_GEOMETRY_COLUMN = "geometry"

#: GeoParquet spec version this adapter emits.
_GEOPARQUET_VERSION = "1.1.0"


def _as_path(source: SourceRef) -> Path:
    """Coerce ``source`` to a :class:`Path` for filesystem checks.

    The adapter only handles local-path-ish sources (single file or local
    directory). Remote URLs are the ``remote`` adapter's job. Delegates
    to the shared :func:`corral.io._paths.normalize_to_path` helper so
    every adapter accepts/rejects the same SourceRef shapes.
    """
    return normalize_to_path(source, adapter="ParquetAdapter")


def _has_wkb_geometry(expr: TableExpr, column: str) -> bool:
    """True when ``expr`` has a binary (WKB) column named ``column``.

    The trigger for GeoParquet output. Binary dtype is the signal (WKB is the
    canonical in-memory geometry encoding, per the geometry ADR); a plain
    non-binary ``geometry`` column or no such column means a normal parquet
    write. Only ibis expressions are inspected — the single compute engine post
    the DuckDB consolidation.
    """
    import ibis

    if not isinstance(expr, ibis.expr.types.Table) or column not in expr.columns:
        return False
    try:
        return bool(expr[column].type().is_binary())
    except Exception:  # pragma: no cover - defensive
        return False


def _geo_metadata(column: str, bounds: tuple[float, float, float, float] | None) -> dict:
    """Build the GeoParquet ``geo`` file-metadata object for one WKB column.

    ``crs`` is intentionally omitted — per the GeoParquet spec an absent ``crs``
    means OGC:CRS84 (WGS84 lon/lat), which is exactly netstead's EPSG:4326 default.
    Recording an explicit non-CRS84 CRS (PROJJSON) is a later slice. ``bbox`` is
    included when computable; ``geometry_types`` is left ``[]`` (spec-valid,
    "not enumerated") to avoid a second decode pass.
    """
    col: dict[str, Any] = {"encoding": "WKB", "geometry_types": []}
    if bounds is not None:
        col["bbox"] = [bounds[0], bounds[1], bounds[2], bounds[3]]
    return {"version": _GEOPARQUET_VERSION, "primary_column": column, "columns": {column: col}}


def _write_geoparquet(expr: TableExpr, dest_path: Path, column: str) -> None:
    """Write ``expr`` as GeoParquet: a WKB ``column`` + the ``geo`` file metadata.

    Materialises through pyarrow (the geometry column is already WKB binary) and
    attaches the ``geo`` metadata to the file schema. Unlike the engine's
    streaming ``write_parquet`` primitive this buffers the table, which is the
    price of standards-compliant geometry metadata; non-geometry tables keep the
    streaming path.
    """
    import json

    import pyarrow.parquet as pq

    from corral.dataset.geometry import geoparquet_bounds

    bounds = geoparquet_bounds(expr, column)
    table = expr.to_pyarrow()
    meta = dict(table.schema.metadata or {})
    meta[b"geo"] = json.dumps(_geo_metadata(column, bounds)).encode("utf-8")
    pq.write_table(table.replace_schema_metadata(meta), str(dest_path))


def _looks_partitioned(path: Path) -> bool:
    """Heuristic: does ``path`` look like a parquet dataset directory?

    True when ``path`` is a directory and either:

    * has a pyarrow ``_metadata`` / ``_common_metadata`` sidecar, or
    * has at least one direct-child ``.parquet`` file, or
    * has at least one ``key=value`` Hive-style subdirectory with a
      direct-child ``.parquet`` file.

    # WHY single-depth: GMNS's recommended Hive layout is a single
    # partition column (``h3=...`` or ``zone_id=...``); multi-level
    # Hive (``h3=x/zone=y/part.parquet``) is uncommon in our schema.
    # If you have a deeper layout, pass ``format='parquet'`` to bypass
    # this probe entirely. Short-circuit on the first hit (S2).
    """
    if not path.is_dir():
        return False
    # Pyarrow dataset sidecars — cheapest signal.
    if (path / "_metadata").exists() or (path / "_common_metadata").exists():
        return True
    # Walk a single level of children. Short-circuit on the first hit.
    for child in path.iterdir():
        if child.is_file() and child.suffix.lower() == ".parquet":
            return True
        if child.is_dir() and "=" in child.name:
            # Hive-style subdir — peek one level for a direct .parquet hit.
            for grandchild in child.iterdir():
                if grandchild.is_file() and grandchild.suffix.lower() == ".parquet":
                    return True
    return False


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ParquetAdapter:
    """Adapter for single-file and partitioned parquet datasets.

    The adapter declares the ``parquet`` extension only — Hive-partitioned
    directories are matched via :meth:`probe`, since they have no extension
    of their own.

    Attributes:
        name: ``"parquet"`` — registry key.
        extensions: ``("parquet",)`` — single-file extension binding.
        schemes: ``()`` — no URL schemes (the remote adapter handles
            ``s3://``/``https://``).
    """

    name: str = "parquet"
    extensions: tuple[str, ...] = ("parquet",)
    schemes: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def probe(self, source: SourceRef) -> bool:
        """Return True if ``source`` looks like parquet.

        Accepts a ``.parquet`` extension (single-file case) OR an existing
        directory that looks parquet-shaped (Hive-style ``key=value``
        subdirs containing ``.parquet`` files, or a ``_metadata`` sidecar,
        or direct-child ``.parquet`` files). Never raises.

        Args:
            source: Candidate source — string path, ``Path``, or dict
                handle with a ``"path"`` key.

        Returns:
            True if the adapter is willing to read ``source``.

        Examples:
            >>> from pathlib import Path
            >>> ParquetAdapter().probe(Path("foo.parquet"))
            True
            >>> ParquetAdapter().probe("foo.csv")
            False
        """
        try:
            path = _as_path(source)
        except TypeError:
            return False

        # Cheap path: extension match — no filesystem touch needed.
        if path.suffix.lower() == ".parquet":
            return True

        # Slow path: directory check + shallow walk.
        try:
            return _looks_partitioned(path)
        except (OSError, PermissionError):
            return False

    # ------------------------------------------------------------------
    # scan — single ResourceRef whether single file or partitioned dir
    # ------------------------------------------------------------------

    def scan(self, source: SourceRef, engine: Engine | None = None) -> ResourceListing:
        """Enumerate the (single) resource at ``source``.

        Parquet is single-table-per-file (and single-table-per-dataset for
        the partitioned case), so this always returns a one-element
        listing. The resource's ``name`` is the file stem for single files
        and the directory basename for partitioned dirs.

        Args:
            source: Path to a ``.parquet`` file or partitioned directory.
            engine: Unused — kept for protocol parity.

        Returns:
            A one-element :data:`ResourceListing`.

        Examples:
            >>> from pathlib import Path
            >>> import tempfile, pyarrow as pa, pyarrow.parquet as pq
            >>> from corral.engines import get_engine
            >>> tmp = Path(tempfile.mkdtemp())
            >>> _ = pq.write_table(pa.table({"a": [1]}), tmp / "t.parquet")
            >>> refs = ParquetAdapter().scan(tmp / "t.parquet", get_engine("ibis"))
            >>> refs[0].name
            't'
        """
        del engine  # protocol parity; we don't need it for scan
        src = normalize_to_str(source, adapter="ParquetAdapter")
        if _scheme_of(src) is not None:
            # A URL (reached via RemoteAdapter): never wrap it in Path(), which collapses "//" and
            # turns "https://h/x.parquet" into the local relative path "https:/h/x.parquet".
            name = PurePosixPath(urlsplit(src).path).stem
            return [ResourceRef(name=name or "parquet", path=src, format=self.name)]
        path = Path(src)
        # Single file: use the stem (strip ``.parquet``). Directory: use the
        # basename so the dataset's logical name matches the folder name.
        # ``Path("dataset/").name`` is ``""``; fall back to ``parent.name``
        # so trailing slashes don't change the logical name.
        name = path.stem if path.suffix.lower() == ".parquet" else path.name or path.parent.name
        return [ResourceRef(name=name or "parquet", path=str(path), format=self.name)]

    # ------------------------------------------------------------------
    # read — delegates to engine.read_parquet primitive + Hive detection
    # ------------------------------------------------------------------

    def read(
        self,
        source: SourceRef,
        engine: Engine,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Return a lazy table expression for ``source``.

        Detects whether ``source`` is a partitioned directory locally
        and passes ``hive_partitioning=`` to the engine's
        :meth:`~corral.engines.base.Engine.read_parquet` primitive.
        The engine still owns the per-library partitioning detail (e.g.
        polars needs an explicit glob); the adapter just tells it
        "treat this as a Hive dataset" so the engine doesn't have to
        re-stat the filesystem.

        Args:
            source: Path to a ``.parquet`` file or partitioned directory.
            engine: Engine whose ``read_parquet`` performs the actual read.
            schema: Optional Frictionless schema forwarded to the engine.
            **kwargs: Extra options forwarded verbatim to
                :meth:`~corral.engines.base.Engine.read_parquet`.

        Returns:
            An engine-native lazy table expression.
        """
        src = normalize_to_str(source, adapter="ParquetAdapter")
        # A URL (reached via RemoteAdapter) goes to the engine verbatim: Path() would collapse "//"
        # and make "https://h/x.parquet" the local relative path "https:/h/x.parquet".
        remote = _scheme_of(src) is not None
        # Caller's explicit hive_partitioning kwarg wins; otherwise we
        # detect "is this a partitioned dir" once here (local paths only)
        # so the engine primitive doesn't have to re-walk the filesystem.
        is_partitioned = kwargs.pop("hive_partitioning", None)
        if is_partitioned is None:
            is_partitioned = False if remote else _looks_partitioned(Path(src))
        return engine.read_parquet(
            src if remote else str(Path(src)),
            schema=schema,
            hive_partitioning=bool(is_partitioned),
            **kwargs,
        )

    # ------------------------------------------------------------------
    # write — single file via engine; partitioned via pyarrow
    # ------------------------------------------------------------------

    def write(
        self,
        expr: TableExpr,
        dest: SourceRef,
        engine: Engine,
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` to ``dest`` as parquet.

        Two modes:

        * **Single file** (default) — delegates to the engine's
          :meth:`~corral.engines.base.Engine.write_parquet` primitive.
        * **Partitioned** — caller passes ``partition_by=['col', ...]``.
          We materialize ``expr`` through pyarrow and call
          :func:`pyarrow.parquet.write_to_dataset` with a Hive-style
          partitioning. We use pyarrow directly (rather than per-engine
          partitioned writers) so the on-disk layout is consistent across
          engines and the partition columns end up encoded in directory
          names rather than the data files.

        Args:
            expr: Engine-native table expression.
            dest: Path to write to. For partitioned writes this is the
                root directory; partition subdirs are created under it.
            engine: Engine for the single-file path; for partitioned
                writes only used to convert to pandas/pyarrow.
            **kwargs: Forwarded to the writer.

                * ``partition_by`` (``list[str]``, optional) — Hive
                  partition columns. Triggers the partitioned-write path.

        Examples:
            >>> from pathlib import Path
            >>> import tempfile, pyarrow as pa, pyarrow.parquet as pq
            >>> from corral.engines import get_engine
            >>> tmp = Path(tempfile.mkdtemp())
            >>> _ = pq.write_table(pa.table({"a": [1, 2]}), tmp / "in.parquet")
            >>> adapter = ParquetAdapter()
            >>> engine = get_engine("ibis")
            >>> expr = adapter.read(tmp / "in.parquet", engine)
            >>> adapter.write(expr, tmp / "out.parquet", engine)
            >>> (tmp / "out.parquet").exists()
            True
        """
        partition_by = kwargs.pop("partition_by", None)
        geometry_column = kwargs.pop("geometry_column", _DEFAULT_GEOMETRY_COLUMN)

        if partition_by:
            self._write_partitioned(expr, dest, engine, partition_by, **kwargs)
            return

        # GeoParquet: a WKB (binary) geometry column gets the `geo` file
        # metadata so GDAL / geopandas / QGIS / duckdb read it as geometry.
        # Falls through to a plain parquet write otherwise (full back-compat).
        if _has_wkb_geometry(expr, geometry_column):
            _write_geoparquet(expr, _as_path(dest), geometry_column)
            return

        # Single-file: defer to the engine's parquet primitive.
        dest_path = _as_path(dest)
        engine.write_parquet(expr, str(dest_path), **kwargs)

    def write_package(
        self,
        tables: Mapping[str, TableExpr],
        dest: Path,
        engine: Engine,
        *,
        describe: DescriptorFactory,
    ) -> None:
        """Write a directory: one ``<table>.parquet`` per table plus a ``datapackage.json``."""
        write_flat_package(self, tables, dest, engine, describe=describe)

    def _write_partitioned(
        self,
        expr: TableExpr,
        dest: SourceRef,
        engine: Engine,
        partition_by: list[str],
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` as a Hive-partitioned parquet dataset under ``dest``.

        Streams: the expression's Arrow record batches go straight into
        pyarrow's dataset writer, so a regional-scale table is never held in
        memory whole (no pandas, no single ``pa.Table``). Pyarrow produces
        Hive layout (``col=value/<guid>-N.parquet``), which duckdb
        auto-detects on read. The engine's own ``write_parquet`` primitive
        is single-file only.
        """
        # ``pyarrow`` is a required corral dependency (see
        # ``packages/corral/pyproject.toml``). The local import keeps the
        # failure localized to the partitioned-write code path if dependency
        # resolution drifts in a future release.
        import pyarrow as pa
        import pyarrow.dataset as ds
        import pyarrow.parquet as pq

        dest_path = _as_path(dest)
        dest_path.mkdir(parents=True, exist_ok=True)

        from corral.engines.ibis_engine import backend_lock

        # The reader keeps pulling from the duckdb connection while pyarrow
        # writes, after ``to_pyarrow_batches`` (and its method lock) has
        # returned, so hold the backend lock for the whole write (see the
        # ``corral.engines.ibis_engine`` module docstring).
        with backend_lock(expr._find_backend(use_default=True)):
            batches = expr.to_pyarrow_batches()
            # ``write_to_dataset(partition_cols=...)`` derives the partition schema
            # with ``table.select``, which a batch reader lacks; build the same
            # Hive partitioning from the reader's schema instead.
            partition_schema = pa.schema([batches.schema.field(col) for col in partition_by])
            pq.write_to_dataset(
                batches,
                root_path=str(dest_path),
                partitioning=ds.partitioning(partition_schema, flavor="hive"),
                **kwargs,
            )


# ---------------------------------------------------------------------------
# Self-registration on import
# ---------------------------------------------------------------------------

register_adapter(ParquetAdapter())


__all__ = ["ParquetAdapter"]
