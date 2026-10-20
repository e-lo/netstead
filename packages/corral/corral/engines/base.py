"""Engine protocol + supporting types for the corral engine layer.

An ``Engine`` is the *execution* abstraction over a Frictionless tabular
data package. It exposes a set of **per-format read/write primitives**
(``read_csv``, ``read_parquet``, ``read_duckdb_table``, ``from_records``,
``write_csv``, ``write_parquet``, ``write_duckdb_table``) plus a
``cast_schema`` helper. ``scan`` and ``write`` are thin convenience
methods that delegate format routing to the
:class:`~corral.io.FormatAdapter` registry.

Concrete engines (one per file in this package) implement this protocol:

- ``IbisEngine`` (default; backed by duckdb) — ``TableExpr`` is
  ``ibis.expr.types.Table``.
- ``IbisEngine`` — ``TableExpr`` is ``polars.LazyFrame``.
- ``IbisEngine`` — ``TableExpr`` is ``pandas.DataFrame`` (eager;
  pandas has no lazy mode).

The protocol is **structural** (``typing.Protocol``,
``runtime_checkable``). The exact concrete types of ``TableExpr`` and
``NativeFrame`` differ per engine — that is intentional. Generic code
that hands an expression back to the same engine that produced it does
not need to care; cross-engine flow goes through the
``to_pandas`` / ``to_polars`` converters.

Dispatch model (single source of truth)
---------------------------------------

After issue #134 (the engine/adapter inversion), format dispatch lives
in exactly one place — the :class:`~corral.io.FormatAdapter` registry.

- ``Adapter.read(source, engine, ...)`` calls the engine's matching
  **primitive** (``engine.read_csv``, ``engine.read_parquet``,
  ``engine.read_duckdb_table``, ``engine.from_records``). No
  engine-name dispatch inside adapters.
- ``Engine.scan(source, format=None, ...)`` is a 3-line convenience
  that resolves ``source`` via ``corral.io.dispatch`` and delegates
  to the chosen adapter's ``read``. It exists so callers who don't want
  to think about adapters can just say ``engine.scan(path)``.

The inversion means an engine never owns a per-format if/elif ladder
and adapters never name engines; both can grow independently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from corral.types import SourceRef

from .errors import (
    EngineNotAvailableError,
    InvalidEngineCallError,
    UnsupportedSourceError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd
    import polars as pl
    import pyarrow as pa

# ---------------------------------------------------------------------------
# Public type aliases
# ---------------------------------------------------------------------------

# ``SourceRef`` is re-exported from :mod:`corral.types` so that
# ``corral.io`` and ``corral.engines`` cannot drift apart on the
# accepted shape of a source reference. The canonical definition lives
# in ``corral.types``; the re-export here preserves the existing
# import path ``from corral.engines.base import SourceRef`` used by
# the concrete engine stubs and the registry.

#: An engine-native lazy/eager table expression returned by ``scan`` and
#: accepted by ``materialize`` / ``write`` / ``to_pandas`` / ``to_polars``.
#:
#: The concrete type depends on the engine (``ibis.expr.types.Table``,
#: ``polars.LazyFrame``, ``pandas.DataFrame``). The protocol is
#: structural; callers that mix engines should round-trip through
#: ``to_pandas()`` / ``to_polars()``.
TableExpr = Any

#: An engine-native fully-materialized frame returned by ``materialize``.
#: Concrete type depends on the engine (typically ``pyarrow.Table`` for
#: ibis, ``polars.DataFrame`` for polars, ``pandas.DataFrame`` for
#: pandas).
NativeFrame = Any


# ---------------------------------------------------------------------------
# Errors — re-exported from corral.engines.errors so the existing
# ``from corral.engines.base import EngineNotAvailableError`` import
# path keeps working (architecture §9 — structured exceptions live in
# errors.py, surfaced from the module that callers use).
# ---------------------------------------------------------------------------

# Re-exported via the imports at the top of the file; listed in __all__.


# ---------------------------------------------------------------------------
# Engine protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Engine(Protocol):
    """Execution engine for tabular operations on a Frictionless data package.

    Implementations are concrete classes (one per backend: ibis, polars,
    pandas) that satisfy this protocol structurally. The registry
    (``corral.engines``) holds singleton instances and resolves them
    by ``name``.

    The contract has three layers:

    1. **Per-format primitives** (``read_csv`` / ``read_parquet`` /
       ``read_duckdb_table`` / ``from_records`` / their write
       counterparts). Adapters call these directly. An engine that
       doesn't natively support a primitive (e.g.
       :class:`~corral.engines.ibis_engine.IbisEngine.write_duckdb_table`)
       raises :class:`~corral.engines.errors.EngineNotAvailableError`
       with a clear pointer at the engine that does.
    2. **Schema casting** (``cast_schema``). Promoted from a per-engine
       internal helper so adapters can apply Frictionless schema casts
       uniformly after a primitive read.
    3. **Convenience delegators** (``scan`` / ``write``). 3-line
       wrappers that route through ``corral.io.dispatch``. They exist
       so callers who don't care about adapters can still write
       ``engine.scan(path)``.

    Attributes:
        name: Short identifier used as the registry key
            (``"ibis"`` / ``"polars"`` / ``"pandas"``).
    """

    name: str

    # ------------------------------------------------------------------
    # Per-format read primitives — adapters call these directly
    # ------------------------------------------------------------------

    def read_csv(
        self,
        source: SourceRef,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Read a single CSV file into an engine-native lazy expression.

        Adapters (``CsvAdapter``, ``ZipCsvAdapter`` after member
        extraction, ``RemoteAdapter`` after URL resolution) call this
        primitive directly. Engines should apply ``cast_schema`` at the
        tail when ``schema`` is non-None so behaviour matches the
        convenience ``scan(..., schema=...)`` path.

        Args:
            source: Filesystem path / URL / ``Path``.
            schema: Optional Frictionless :class:`Schema` to cast columns
                with after the read.
            **kwargs: Forwarded verbatim to the underlying library reader.
                Names follow the library, not a normalized vocabulary —
                e.g. polars wants ``separator=``, pandas wants ``sep=``,
                duckdb wants ``delim=``. One key is the engine's own:
                ``storage_options`` (fsspec options for a URL source, as
                forwarded by ``RemoteAdapter``) must be consumed by the
                engine and used to open the URL, never passed to the
                library reader.
        """
        ...

    def read_parquet(
        self,
        source: SourceRef,
        schema: Any | None = None,
        *,
        hive_partitioning: bool = False,
        **kwargs: Any,
    ) -> TableExpr:
        """Read parquet (single file or Hive-partitioned directory).

        When ``hive_partitioning=True`` the engine must enable partition
        discovery (so partition columns survive into the result and
        downstream filters become true partition prunes). For
        single-file parquet ``hive_partitioning`` is ignored.

        Args:
            source: Path to a ``.parquet`` file or partitioned directory.
            schema: Optional Frictionless schema, applied after read.
            hive_partitioning: Enable Hive-style partition discovery.
            **kwargs: Forwarded to the underlying reader, except
                ``storage_options`` — see :meth:`read_csv`.
        """
        ...

    def read_duckdb_table(
        self,
        source: SourceRef,
        *,
        table: str,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Read one named table out of a ``.duckdb`` file.

        Args:
            source: Path / URL to a ``.duckdb`` file.
            table: The table name to read. Required (no defaulting —
                the duckdb adapter's ``scan()`` enumerates tables).
            schema: Optional Frictionless schema, applied after read.
            **kwargs: Forwarded to the engine-specific reader.

        Raises:
            EngineNotAvailableError: If the engine can't read duckdb
                files (none of the stock engines raise — all three
                support this primitive).
        """
        ...

    def from_records(
        self,
        records: list[dict[str, Any]] | dict[str, list[Any]],
        schema: Any | None = None,
    ) -> TableExpr:
        """Build a table expression from in-memory records.

        Accepts the two shapes a caller might naturally write:

        - **List of row dicts**: ``[{"a": 1, "b": 2}, {"a": 3, "b": 4}]``
        - **Columnar dict**: ``{"a": [1, 3], "b": [2, 4]}``

        Adapters do not call this primitive (no on-disk format produces
        in-memory records); ``Engine.scan`` calls it directly for the
        ``{"data": ...}`` dict-source contract documented on
        :meth:`scan`.

        Args:
            records: Either shape above.
            schema: Optional Frictionless schema, applied after build.
        """
        ...

    def from_arrow(self, arrow_table: pa.Table) -> TableExpr:
        """Wrap a :class:`pyarrow.Table` as an engine-native lazy expression.

        Type-preserving counterpart to :meth:`from_records`. Use this
        whenever the source is already an Arrow table — going through
        ``records = arrow.to_pylist()`` then ``from_records(records)``
        is lossy (binary becomes ``bytes``, decimals become ``Decimal``,
        timestamps lose tz, all numerics widen via JSON-ish coercion)
        and is the round-trip the editing layer formerly used. Engines
        materialise the Arrow buffer directly:

        - **Ibis** registers it as a duckdb temp table via
          ``con.create_table(name, obj=arrow_table, temp=True)``.
        - **Polars** wraps it as a :class:`polars.LazyFrame` via
          ``pl.from_arrow(arrow_table).lazy()``.
        - **Pandas** materialises with
          ``arrow_table.to_pandas().convert_dtypes()`` so the
          numpy-backed nullable dtype contract documented on
          :meth:`to_pandas` is preserved end-to-end.

        Args:
            arrow_table: Already-materialised :class:`pyarrow.Table`.

        Returns:
            An engine-native lazy expression; the contents match
            ``arrow_table`` 1:1 modulo each engine's documented dtype
            convention.

        Examples:
            >>> import pyarrow as pa
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> tbl = pa.table({"a": [1, 2], "b": ["x", "y"]})
            >>> e = IbisEngine()
            >>> expr = e.from_arrow(tbl)
            >>> list(expr.columns), expr.count().to_pyarrow().as_py()
            (['a', 'b'], 2)
        """
        ...

    # ------------------------------------------------------------------
    # Per-format write primitives — adapters call these directly
    # ------------------------------------------------------------------

    def write_csv(self, expr: TableExpr, dest: SourceRef, **kwargs: Any) -> None:
        """Write ``expr`` to ``dest`` as a single CSV file.

        Args:
            expr: Engine-native expression to materialize and write.
            dest: Target path.
            **kwargs: Forwarded to the underlying CSV writer (compression,
                line terminator, ...). Names follow the library.
        """
        ...

    def write_parquet(
        self,
        expr: TableExpr,
        dest: SourceRef,
        *,
        partition_by: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` to ``dest`` as parquet (single file or partitioned).

        When ``partition_by`` is given the engine writes a Hive-style
        partitioned dataset under ``dest``. The parquet adapter
        currently handles partitioned writes through pyarrow regardless
        of engine; engines may either implement the partitioned-write
        path natively or document that they only support the
        single-file case.

        Args:
            expr: Engine-native expression.
            dest: Target path or directory.
            partition_by: Optional Hive partition columns. ``None``
                writes a single file.
            **kwargs: Forwarded to the underlying writer.
        """
        ...

    def write_duckdb_table(
        self,
        expr: TableExpr,
        dest: SourceRef,
        *,
        table: str,
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` into a ``.duckdb`` file as a named table.

        Engines that cannot natively write duckdb without raw SQL
        (notably :class:`~corral.engines.ibis_engine.IbisEngine`)
        raise :class:`~corral.engines.errors.EngineNotAvailableError`
        and point callers at :class:`~corral.engines.ibis_engine.IbisEngine`
        for this primitive. The architecture's "no raw SQL outside
        ibis_engine" rule is the constraint that forces this split.

        Args:
            expr: Engine-native expression.
            dest: Target duckdb file path.
            table: Destination table name (required).
            **kwargs: Forwarded to the underlying writer.
        """
        ...

    # ------------------------------------------------------------------
    # Schema casting — promoted from per-engine internal helper
    # ------------------------------------------------------------------

    def cast_schema(self, expr: TableExpr, schema: Any) -> TableExpr:
        """Cast columns of ``expr`` per a Frictionless ``schema``.

        Fields whose Frictionless type does not map cleanly to an
        engine-native type are left untouched (the v0.3 ``apply_schema_to_df``
        bug was a reminder that silent-but-noisy is the safer default
        for partial schemas). Fields named in the schema but absent from
        ``expr`` are skipped — that's a validation concern, not a
        scan-time concern.

        Args:
            expr: Engine-native expression.
            schema: Frictionless :class:`~corral.spec.model.Schema`.

        Returns:
            A new expression with the casts applied, or ``expr``
            unchanged if no field type was castable.
        """
        ...

    # ------------------------------------------------------------------
    # Convenience delegators — single dispatch point through io.dispatch
    # ------------------------------------------------------------------

    def scan(
        self,
        source: SourceRef,
        format: str | None = None,
        schema: Any | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Open ``source`` as a lazy table expression.

        **Convenience method** — the body is a ~3-line delegation to
        ``corral.io.dispatch(source, format=format).read(source,
        engine=self, schema=schema, **kwargs)``. Power users and
        adapters skip this method entirely and call the primitives
        (:meth:`read_csv`, etc.) directly.

        Args:
            source: A path, URL, ``Path``, or handle dict pointing at a
                tabular file or table. Format dispatch is delegated to
                the ``corral.io`` ``FormatAdapter`` layer. The two
                handle-dict shapes every engine MUST accept are:

                - ``{"data": [...]}`` — inline data. Value is either a
                  list of row dicts (``[{"a": 1}, {"a": 2}]``) or a
                  columnar dict (``{"a": [1, 2]}``). Used for in-memory
                  test fixtures and small synthetic frames. The
                  dispatcher can't sniff a dict source so this case is
                  short-circuited inside :meth:`scan` and routed
                  directly to :meth:`from_records`.
                - ``{"format": "duckdb", "path": "net.duckdb", "table":
                  "link"}`` — a duckdb table handle. ``"format"`` is
                  optional when ``"path"`` ends in ``.duckdb``. Also
                  short-circuited inside :meth:`scan` (the dispatcher
                  rejects dict sources).

                Any other dict shape MUST raise
                :class:`~corral.engines.errors.UnsupportedSourceError`
                with a message listing these supported shapes.
            format: Optional explicit format hint forwarded to
                ``corral.io.dispatch(source, format=format)``. When
                ``None`` the dispatcher uses URL-scheme / extension /
                ``probe`` resolution. Use this when the source is
                ambiguous (e.g. an extensionless file, an ``http://``
                URL that returns parquet bytes).
            schema: Optional Frictionless ``Schema`` to apply at scan
                time (column types, missing-value handling). If
                ``None``, the engine infers from file metadata.
            **kwargs: Adapter-specific options forwarded verbatim to the
                resolved ``FormatAdapter.read`` (delimiter, compression,
                partition pruning predicate, etc.). Engines must not
                strip or mutate this mapping.

        Returns:
            An engine-native lazy expression. Concrete type depends on
            the engine — see module docstring.
        """
        ...

    def materialize(self, expr: TableExpr) -> NativeFrame:
        """Execute ``expr`` and return the engine's native materialized frame.

        For ibis this triggers backend execution; for polars this
        ``.collect()``-s a ``LazyFrame``; for pandas this is an identity
        (pandas is already eager).
        """
        ...

    # ------------------------------------------------------------------
    # Lazy introspection — promoted from per-engine duck-typed helpers
    # so the dataset/table.py wrapper can delegate without the getattr/
    # try-except ladder that lived in ``_engine_columns`` /
    # ``_engine_count`` / ``_engine_head`` / ``_engine_select``.
    # ------------------------------------------------------------------

    def columns(self, expr: TableExpr) -> list[str]:
        """Return the column names of ``expr`` without materialising rows.

        Each engine has a cheap lazy schema path (ibis ``expr.schema().names``,
        polars ``expr.collect_schema().names()``, pandas
        ``list(expr.columns)``). Implementations route to that path so
        the wrapper in :class:`corral.dataset.Table` is one line.

        Args:
            expr: Engine-native expression.

        Returns:
            A new list of column names in declaration order.
        """
        ...

    def count(self, expr: TableExpr) -> int:
        """Return the row count of ``expr``, pushing the aggregate to the engine.

        ibis pushes a ``SELECT COUNT(*)`` to duckdb; polars selects
        ``pl.len()`` inside the lazy plan; pandas calls ``len(expr)``.
        Implementations must not full-materialise the table to count it.

        Args:
            expr: Engine-native expression.

        Returns:
            Number of rows in ``expr``.
        """
        ...

    def head(self, expr: TableExpr, n: int) -> TableExpr:
        """Return a new lazy expression with only the first ``n`` rows.

        Lazy — the engine builds a head expression but does not execute
        it. The pandas engine is the only one where this materialises
        (pandas has no lazy mode).

        Args:
            expr: Engine-native expression.
            n: Row count to keep.

        Returns:
            A new engine-native expression of the same type as ``expr``.
        """
        ...

    def select(self, expr: TableExpr, columns: list[str]) -> TableExpr:
        """Return a new lazy expression projected to ``columns``.

        Args:
            expr: Engine-native expression.
            columns: Column names to keep. Must all exist on ``expr``.

        Returns:
            A new engine-native expression carrying only ``columns``.
        """
        ...

    def order_by(self, expr: TableExpr, columns: list[str], descending: bool = False) -> TableExpr:
        """Return a new lazy expression sorted by ``columns``.

        ibis emits ``ORDER BY`` (``expr.order_by``); polars ``expr.sort``;
        pandas ``sort_values`` (stable). ``descending`` applies to all
        columns. With :meth:`limit` this is the server-side paging path.

        Args:
            expr: Engine-native expression.
            columns: Column names to sort by, in priority order.
            descending: Sort descending instead of ascending.

        Returns:
            A new engine-native expression of the same type as ``expr``.
        """
        ...

    def limit(self, expr: TableExpr, n: int, offset: int = 0) -> TableExpr:
        """Return a new lazy expression of ``n`` rows starting at ``offset``.

        ibis emits ``LIMIT n OFFSET offset``; polars ``expr.slice``; pandas
        positional ``iloc``. Pairs with :meth:`order_by` for stable paging.

        Args:
            expr: Engine-native expression.
            n: Maximum rows to keep.
            offset: Leading rows to skip.

        Returns:
            A new engine-native expression of the same type as ``expr``.
        """
        ...

    def to_pandas(self, expr: TableExpr) -> pd.DataFrame:
        """Materialize ``expr`` and return it as a ``pandas.DataFrame``.

        Cross-engine convergence point. Implementations may raise
        :class:`~corral.engines.errors.EngineNotAvailableError` if
        pandas is not installed.

        **Dtype convention (cross-engine contract).** The returned
        DataFrame uses pandas **numpy-backed nullable dtypes**:
        ``Int64`` (capital I), ``Float64``, ``string``, ``boolean``.
        We standardize on this family because:

        - Null semantics are preserved without silently upcasting
          integer columns with missing values to ``float64`` (the
          numpy default's footgun).
        - Numpy-backed (not ``pyarrow``-backed) dtypes keep
          compatibility with downstream libraries (sklearn, older
          matplotlib, geopandas pre-1.0) that don't understand
          ``pd.ArrowDtype`` columns.

        Implementations achieve this by post-processing with
        :meth:`pandas.DataFrame.convert_dtypes` (the universal path),
        regardless of which engine produced ``expr``. The Leavenworth
        fixture's ``link.from_node_id`` column round-trips as ``Int64``
        from all three stock engines under this convention — that is
        the regression locked in by the cross-engine parity test.
        """
        ...

    def to_polars(self, expr: TableExpr) -> pl.DataFrame:
        """Materialize ``expr`` and return it as a ``polars.DataFrame``.

        Cross-engine convergence point. Implementations may raise
        ``EngineNotAvailableError`` if polars is not installed.
        """
        ...

    def write(self, expr: TableExpr, dest: SourceRef, fmt: str, **kwargs: Any) -> None:
        """Write ``expr`` to ``dest`` in format ``fmt``.

        **Convenience method** — the body is a ~3-line delegation to
        ``corral.io.get_adapter(fmt).write(expr, dest, engine=self,
        **kwargs)``. Adapters skip this and call the write primitives
        (:meth:`write_csv`, :meth:`write_parquet`,
        :meth:`write_duckdb_table`) directly.

        Args:
            expr: The expression to materialize and write.
            dest: A path, URL, or handle dict — same contract as
                ``scan``'s ``source``.
            fmt: Format name (``"parquet"``, ``"csv"``, ``"duckdb"``,
                etc.). Dispatched through the ``corral.io``
                ``FormatAdapter`` layer.
            **kwargs: Format-specific options (compression, partitioning,
                etc.). Adapter-defined.
        """
        ...


__all__ = [
    "Engine",
    "EngineNotAvailableError",
    "InvalidEngineCallError",
    "NativeFrame",
    "SourceRef",
    "TableExpr",
    "UnsupportedSourceError",
]
