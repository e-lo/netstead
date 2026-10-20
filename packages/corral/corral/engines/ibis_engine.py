"""Ibis (duckdb-backed) execution engine — the default engine for corral.

This is the **only** module in the codebase permitted to embed raw SQL
strings; ``scripts/lint_no_sql.py`` allowlists this file. All other
modules must use ibis expressions (or call into this engine) to talk to
data. In practice the implementation below sticks to ibis-level
expressions and the backend's structured APIs (``create_table``,
``read_csv``, ``to_parquet``); the allowlist exists so that a deliberate
escape hatch is available if one is ever needed.

Default backend
---------------

``IbisEngine()`` constructs a fresh in-memory duckdb connection. Pass a
pre-opened backend (``IbisEngine(con=ibis.duckdb.connect("net.duckdb"))``)
to point at a file or share a connection. The engine owns one duckdb
connection for its lifetime; call ``close()`` to release it.

Thread safety
-------------

A duckdb connection is not safe to use from several threads at once: two
threads interleaving queries on it fail with "Attempting to execute an
unsuccessful or closed pending query result". Every ibis backend this module
creates or adopts is therefore *serialized*: each of its methods runs under one
re-entrant lock stored on the backend (see :func:`serialize_backend`). Locking
the backend, not just this class's methods, also covers code that executes an
expression directly (``expr.execute()``, ``expr.to_pandas()``), since those
calls go through the same backend object.

Per-thread ``con.cursor()`` connections are *not* an option: ibis's
``read_csv``/``read_parquet`` register temp views and :meth:`IbisEngine.materialize`
/ :meth:`IbisEngine.from_records` create temp tables, and duckdb temp objects
are private to the connection that created them, so another thread's cursor
cannot see them. Serializing costs little: duckdb already parallelizes a single
query across cores.

Remaining gaps (the lock wraps *method calls*, so anything that reaches the
duckdb connection outside one is not covered):

- batch readers returned by ``to_pyarrow_batches``/``to_pandas_batches`` are
  consumed after the call, and so the lock, has returned;
- ``_safe_raw_sql`` is a context manager: only entering it is locked, not the
  ``with`` body that uses its cursor; likewise the connection ``raw_sql``
  returns is used unlocked once the call returns (``backend.con`` itself, too:
  hold :func:`backend_lock` around direct use);
- properties that run queries (``current_database``, ``current_catalog``,
  ``version``, ``tables``) are not methods and are not wrapped;
- expressions bound to ibis's process-wide *default* backend (a plain
  ``ibis.memtable(...)`` with no engine) are not covered at all.

Corral's own code stays inside these lines (internal uses of the first three
happen inside an already-locked method); callers that step outside them should
take :func:`backend_lock`.

:meth:`IbisEngine.to_pandas` deliberately holds the lock only for the query: it fetches Arrow
(as ``Backend.execute`` does) and converts to pandas after the lock is released, so a large conversion never
stalls other threads' queries.

Dispatch model (post-issue-#134 inversion)
------------------------------------------

The engine exposes **per-format primitives** (``read_csv``,
``read_parquet``, ``read_duckdb_table``, ``from_records`` + the
matching ``write_*``). Adapters in :mod:`corral.io` call these
primitives directly. :meth:`scan` and :meth:`write` are thin
convenience methods that route format dispatch through
``corral.io.dispatch`` / ``corral.io.get_adapter`` — no per-format
if/elif lives in this module anymore.

The single carve-out in :meth:`scan` is dict sources: the dispatcher
can't sniff a ``{"data": ...}`` or ``{"format": "duckdb", ...}`` dict,
so :meth:`scan` short-circuits those two shapes and calls
:meth:`from_records` / :meth:`read_duckdb_table` directly before
handing the rest off to ``dispatch``.
"""

from __future__ import annotations

import contextlib
import functools
import inspect
import threading
from collections.abc import Callable
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import ibis
from fsspec.core import url_to_fs
from fsspec.implementations.dirfs import DirFileSystem
from ibis.backends import BaseBackend
from ibis.expr import types as ir

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

    from corral.spec.model import Schema


# ---------------------------------------------------------------------------
# Frictionless → ibis dtype map
# ---------------------------------------------------------------------------
# Inlined here rather than imported from a defaults module: the map is
# short, only used in one place, and reading it inline is the fastest
# way for a future reviewer to understand what the cast does. (See
# `netstead-review` SKILL §Lens C — "inline literals".)
#
# Anything not in this map is left at whatever type the CSV/parquet
# reader inferred. We deliberately do not cast to ibis ``date``/
# ``time``/``timestamp`` from Frictionless ``date``/``time``/``datetime``
# at scan time — those need format strings to be safe, which Frictionless
# carries on the field's ``format`` attribute; honouring them is a
# validation-layer concern (task 1.12+) not a scan-time concern.
#
# Note: Frictionless ``any`` is intentionally absent. GMNS schemas use
# ``any`` for fields like ``link_id`` that could be ints or strings;
# overriding the reader's inferred type with ``string`` would force
# numeric IDs into strings, which is the opposite of helpful. Leaving
# ``any`` columns untouched preserves the file's natural typing.
_FRICTIONLESS_TO_IBIS: dict[str, str] = {
    "integer": "int64",
    "number": "float64",
    "boolean": "boolean",
    "string": "string",
}


# ---------------------------------------------------------------------------
# IbisEngine
# ---------------------------------------------------------------------------


class IbisEngine:
    """Ibis engine adapter, backed by duckdb.

    Default-engine, lazy-by-default, no-raw-SQL implementation of the
    :class:`~corral.engines.Engine` protocol.

    Args:
        con: An existing ``ibis`` backend (typically a duckdb one) to
            adopt. When ``None`` the engine creates a fresh in-memory
            duckdb connection. The engine owns the connection's
            lifetime when it created it; if you pass one in, you own
            it.

    Attributes:
        name: Always ``"ibis"`` — used as the registry key.
        con: The underlying ibis backend. Most code should not touch
            this directly; it is exposed for tests and the rare power
            user who needs duckdb-specific extensions.

    Examples:
        Default in-memory connection:

        >>> from corral.engines.ibis_engine import IbisEngine
        >>> engine = IbisEngine()
        >>> engine.name
        'ibis'
        >>> engine.con is not None
        True
        >>> engine.close()
    """

    name: str = "ibis"

    def __init__(self, *, con: BaseBackend | None = None) -> None:
        """Construct an engine; create an in-memory duckdb backend if needed.

        See class docstring for the ``con`` argument's semantics and
        examples.
        """
        # We track whether we created the backend ourselves so close()
        # only disconnects what we own. A caller who passed in their own
        # con keeps responsibility for it.
        if con is None:
            self.con: BaseBackend = ibis.duckdb.connect(":memory:")
            self._owns_con = True
        else:
            self.con = con
            self._owns_con = False
        serialize_backend(self.con)
        # (scheme://host, frozen storage_options) -> private protocol name registered on ``con``.
        self._scoped_filesystems: dict[tuple[str, tuple[tuple[str, str], ...]], str] = {}

    def _resolve_source(self, source: SourceRef, storage_options: dict[str, Any] | None) -> str:
        """Coerce ``source`` to the path duckdb reads or writes.

        A URL with ``storage_options`` — the fsspec options the credentials
        cascade produces (see :mod:`corral.io.credentials`) — is served
        through an fsspec filesystem built from exactly those options and
        registered on this engine's duckdb connection under a private
        protocol name. duckdb's own ``read_csv`` / ``read_parquet`` reject
        ``storage_options`` as a named parameter, so the options never
        reach them. One filesystem is registered per (scheme, host,
        options) and reused; different options for the same host get
        separate filesystems, so two reads with different credentials
        never share one — including lazy expressions executed later.

        Without ``storage_options`` the path goes to duckdb as-is (its
        native httpfs reader handles ``http(s)://`` and ``s3://``).

        Raises:
            InvalidEngineCallError: ``storage_options`` given for a local path.
        """
        path = _as_path_str(source)
        if not storage_options:
            return path
        if _url_scheme(path) is None:
            raise InvalidEngineCallError(
                f"ibis engine: storage_options apply to URL sources only, not to the local path {path!r}"
            )
        parts = urlsplit(path)
        root = f"{parts.scheme}://{parts.netloc}"
        key = (root, tuple(sorted((k, repr(v)) for k, v in storage_options.items())))
        with backend_lock(self.con):
            name = self._scoped_filesystems.get(key)
            if name is None:
                fs, root_path = url_to_fs(root, **storage_options)
                name = f"corral-fs-{next(_SCOPED_FS_IDS)}"
                self.con.register_filesystem(_scoped_filesystem(name, fs, root_path))
                self._scoped_filesystems[key] = name
        tail = parts.path.lstrip("/")
        if parts.query:
            tail = f"{tail}?{parts.query}"
        return f"{name}://{tail}"

    # ------------------------------------------------------------------
    # Read primitives — adapters call these directly
    # ------------------------------------------------------------------

    def read_csv(
        self,
        source: SourceRef,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> ir.Table:
        """Read a CSV file via duckdb's ``read_csv`` (no SQL).

        Args:
            source: Path / URL / ``Path``. ``http(s)://`` and ``s3://``
                are passed through to duckdb's httpfs/s3 extensions.
            schema: Optional Frictionless schema; columns are cast after
                read via :meth:`cast_schema`.
            **kwargs: ``storage_options`` (fsspec options for a URL
                source) is consumed here — see :meth:`_resolve_source`.
                Everything else is forwarded verbatim to
                :meth:`ibis.backends.duckdb.Backend.read_csv` (``delim``,
                ``header``, ``columns``, ...).

        Returns:
            A lazy ``ibis.expr.types.Table`` over the file.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> e = IbisEngine()
            >>> e.read_csv(p).count().to_pyarrow().as_py()
            2
            >>> e.close()
        """
        path = self._resolve_source(source, kwargs.pop("storage_options", None))
        table = self.con.read_csv(path, **kwargs)
        return self.cast_schema(table, schema) if schema is not None else table

    def read_parquet(
        self,
        source: SourceRef,
        schema: Schema | None = None,
        *,
        hive_partitioning: bool = False,
        **kwargs: Any,
    ) -> ir.Table:
        """Read parquet (single file or Hive-partitioned directory).

        Hive-partitioning is auto-detected for directory sources when
        the adapter doesn't say otherwise: a directory + no caller
        override turns ``hive_partitioning=True`` on so duckdb reinjects
        the partition columns into the result. The caller's explicit
        flag wins either way.

        Args:
            source: Path to a ``.parquet`` file or partitioned directory.
            schema: Optional Frictionless schema.
            hive_partitioning: Enable Hive-style partition discovery.
                Auto-enabled for directory sources unless explicitly set.
            **kwargs: ``storage_options`` (fsspec options for a URL
                source) is consumed here — see :meth:`_resolve_source`.
                Everything else is forwarded to duckdb's ``read_parquet``.

        Returns:
            A lazy ``ibis.expr.types.Table``.
        """
        path = self._resolve_source(source, kwargs.pop("storage_options", None))
        # Directory sources: auto-enable hive partitioning and hand duckdb an
        # explicit recursive glob. A bare directory path raises "No files found"
        # on some duckdb versions (<1.5); ``dir/**/*.parquet`` reliably matches the
        # per-partition files pyarrow writes (``col=val/<uuid>-0.parquet``) on all.
        if Path(path).is_dir():
            if not hive_partitioning:
                hive_partitioning = True
            path = str(Path(path) / "**" / "*.parquet")
        if hive_partitioning:
            kwargs = {"hive_partitioning": True, **kwargs}
        table = self.con.read_parquet(path, **kwargs)
        return self.cast_schema(table, schema) if schema is not None else table

    def read_duckdb_table(
        self,
        source: SourceRef,
        *,
        table: str,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> ir.Table:
        """Read one named table out of a duckdb file.

        Opens a fresh ibis duckdb backend pointed at the file and
        returns the named table as an ibis expression. ``kwargs`` are
        accepted for protocol symmetry but currently unused — duckdb's
        relation API takes no per-read options for catalogue reads.

        Args:
            source: Path / URL to a ``.duckdb`` file. ``str`` / ``Path``
                are both accepted.
            table: The table name (required).
            schema: Optional Frictionless schema.
            **kwargs: Reserved for future use.

        Returns:
            A lazy ``ibis.expr.types.Table``.

        Raises:
            InvalidEngineCallError: If ``table`` is empty.
        """
        del kwargs  # reserved for future backend-specific options
        if not table:
            raise InvalidEngineCallError("ibis engine: read_duckdb_table requires a non-empty table= argument")
        backend = serialize_backend(ibis.duckdb.connect(_as_path_str(source)))
        expr = backend.table(table)
        return self.cast_schema(expr, schema) if schema is not None else expr

    def from_records(
        self,
        records: list[dict[str, Any]] | dict[str, list[Any]],
        schema: Schema | None = None,
    ) -> ir.Table:
        """Materialize in-memory records as a duckdb temp table.

        Accepts the two shapes :class:`pandas.DataFrame` accepts (list
        of row dicts OR columnar dict). We convert to pyarrow, wrap it
        in ``ibis.memtable``, and ``create_table`` it into our duckdb
        connection so the table is materialized eagerly (the caller's
        source dict may mutate in its own scope). The memtable wrap is
        required on ibis 10+/duckdb 1.5+, where ``create_table`` with a
        raw pyarrow ``obj`` raises a duckdb ``ParserException``.

        Args:
            records: Either ``[{"a": 1}, {"a": 2}]`` or ``{"a": [1, 2]}``.
            schema: Optional Frictionless schema.

        Returns:
            A lazy ``ibis.expr.types.Table`` backed by a duckdb temp
            table.
        """
        import pyarrow as pa

        # pyarrow has two distinct constructors for the two inline shapes
        # the cross-engine contract accepts.
        arrow = pa.table(records) if isinstance(records, dict) else pa.Table.from_pylist(list(records))
        # DuckDB refuses to CREATE TABLE with NULL-typed columns even
        # though pyarrow / pandas happily produce them when every value
        # in a column is None (a real shape we hit on the bundled
        # Leavenworth fixture where ``link.name`` is entirely null).
        # Cast any such columns to ``string`` — the safe default for an
        # unknown all-null column — before handing the table to duckdb.
        arrow = _coerce_all_null_columns_to_string(arrow)
        name = _temp_table_name("inline")
        self.con.create_table(name, obj=arrow, temp=True)  # raw Arrow: ibis drops its staging view after
        expr = self.con.table(name)
        return self.cast_schema(expr, schema) if schema is not None else expr

    def from_arrow(self, arrow_table: pa.Table) -> ir.Table:
        """Register ``arrow_table`` as a duckdb temp table without round-tripping records.

        Type-preserving counterpart to :meth:`from_records` — the Arrow
        buffer is handed straight to duckdb, so binary / decimal /
        timestamp columns survive (the ``to_pylist`` round-trip used to
        coerce them). All-null (``pa.null()``-typed) columns are coerced
        to ``string`` first — see :func:`_coerce_all_null_columns_to_string`
        for why duckdb refuses to ``CREATE TABLE`` with them otherwise.
        """
        arrow_table = _coerce_all_null_columns_to_string(arrow_table)
        name = _temp_table_name("inline_arrow")
        self.con.create_table(name, obj=arrow_table, temp=True)  # raw Arrow: ibis drops its staging view after
        return self.con.table(name)

    # ------------------------------------------------------------------
    # Write primitives — adapters call these directly
    # ------------------------------------------------------------------

    def write_csv(self, expr: ir.Table, dest: SourceRef, **kwargs: Any) -> None:
        """Write ``expr`` to ``dest`` as CSV via duckdb's ``to_csv``.

        Args:
            expr: An ibis expression.
            dest: Target path or URL.
            **kwargs: ``storage_options`` (fsspec options for a URL
                destination) is consumed here — see :meth:`_resolve_source`.
                Everything else is forwarded to duckdb's ``to_csv``.
        """
        dest_path = self._resolve_source(dest, kwargs.pop("storage_options", None))
        self.con.to_csv(expr, dest_path, **kwargs)

    def write_parquet(
        self,
        expr: ir.Table,
        dest: SourceRef,
        *,
        partition_by: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` to ``dest`` as parquet.

        For partitioned writes the ParquetAdapter handles the
        hive-layout itself (via pyarrow) and never reaches this engine
        primitive — so we reject ``partition_by`` here with a pointer
        at the adapter. Engines could grow native partitioned-write
        support later without changing the adapter.

        Args:
            expr: An ibis expression.
            dest: Target file path.
            partition_by: Must be ``None`` — see note above.
            **kwargs: ``storage_options`` (fsspec options for a URL
                destination) is consumed here — see :meth:`_resolve_source`.
                Everything else is forwarded to duckdb's ``to_parquet``.
        """
        if partition_by:
            raise InvalidEngineCallError(
                "ibis engine: partitioned writes go through "
                "ParquetAdapter.write(partition_by=...), which routes "
                "through pyarrow rather than this primitive"
            )
        dest_path = self._resolve_source(dest, kwargs.pop("storage_options", None))
        self.con.to_parquet(expr, dest_path, **kwargs)

    def write_duckdb_table(
        self,
        expr: ir.Table,
        dest: SourceRef,
        *,
        table: str,
        **kwargs: Any,
    ) -> None:
        """Write ``expr`` into a duckdb file as a named table.

        The destination file is ATTACHed to the connection ``expr`` runs
        on and the table is created there from the expression, so duckdb
        streams the rows from source to destination and nothing is
        materialised in Python (a regional-scale table never has to fit
        in memory). The file is DETACHed afterwards. When ``dest`` is
        already attached (an engine opened on that very file), the table
        is created in that catalog instead.

        Overwrite semantics: an existing destination table is replaced,
        so ``write_duckdb_table`` is idempotent (same as ``to_parquet``'s
        file-replacement behaviour); other tables in the file are kept.

        Args:
            expr: An ibis expression.
            dest: Target duckdb file path.
            table: Destination table name.
            **kwargs: Reserved for future options (currently ignored).
        """
        del kwargs  # reserved for future use
        if not table:
            raise InvalidEngineCallError("ibis engine: write_duckdb_table requires a non-empty table= argument")
        backend: Any = expr._find_backend() if _is_duckdb(expr) else self.con  # a duckdb Backend
        path = Path(_as_path_str(dest)).resolve()
        with backend_lock(backend):
            catalog = _attached_catalog(backend, path)
            if catalog is not None:
                backend.create_table(table, obj=expr, database=(catalog, "main"), overwrite=True)
                return
            alias = _temp_table_name("dest")
            backend.attach(str(path).replace("'", "''"), name=alias)  # ibis interpolates it into ATTACH '...'
            try:
                backend.create_table(table, obj=expr, database=(alias, "main"), overwrite=True)
            finally:
                backend.detach(alias)

    # ------------------------------------------------------------------
    # Schema cast — promoted from internal helper to public primitive
    # ------------------------------------------------------------------

    def cast_schema(self, expr: ir.Table, schema: Schema) -> ir.Table:
        """Cast columns of ``expr`` per a Frictionless schema.

        Only fields whose Frictionless type maps cleanly to an ibis
        dtype (per :data:`_FRICTIONLESS_TO_IBIS`) are cast. Fields not
        present in the table are skipped — that's a validation concern,
        not a scan concern.

        Implementation note: we collect casts into a single ``.cast()``
        call rather than chaining per-field ``.mutate``s. ibis builds a
        single projection either way, but the dict-form ``cast`` reads
        closer to the Frictionless schema for someone debugging types.

        Args:
            expr: An ibis expression.
            schema: A Frictionless :class:`~corral.spec.model.Schema`.

        Returns:
            A new expression with the casts applied, or ``expr``
            unchanged if no field's type was castable.
        """
        if schema is None or not getattr(schema, "fields", None):
            return expr
        casts: dict[str, str] = {}
        columns = set(expr.columns)
        for field in schema.fields:
            if field.name not in columns or field.type is None:
                continue
            ibis_type = _FRICTIONLESS_TO_IBIS.get(field.type)
            if ibis_type is not None:
                casts[field.name] = ibis_type
        if not casts:
            return expr
        return expr.cast(casts)

    # ------------------------------------------------------------------
    # Convenience delegators — route through io.dispatch
    # ------------------------------------------------------------------

    def scan(
        self,
        source: SourceRef,
        format: str | None = None,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> ir.Table:
        """Open ``source`` via the FormatAdapter registry.

        3-line convenience over :func:`corral.io.dispatch` plus a
        short carve-out for dict sources (the dispatcher rejects dicts;
        they're routed to :meth:`from_records` /
        :meth:`read_duckdb_table` directly).

        Args:
            source: Path / URL / ``Path`` / handle dict — see the
                :class:`~corral.engines.base.Engine` protocol's
                :meth:`~corral.engines.base.Engine.scan` for the
                accepted dict shapes.
            format: Optional explicit format hint forwarded to dispatch.
            schema: Optional Frictionless schema.
            **kwargs: Forwarded to the resolved adapter's ``read``.

        Returns:
            A lazy ``ibis.expr.types.Table``.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> engine = IbisEngine()
            >>> engine.scan(p).count().to_pyarrow().as_py()
            2
            >>> engine.close()
        """
        # Dict-source carve-out — the dispatcher can't sniff dicts.
        if isinstance(source, dict):
            return _scan_dict(self, source, schema=schema, **kwargs)
        # All other shapes go through the adapter registry.
        from corral.io import dispatch

        adapter = dispatch(source, format=format)
        return adapter.read(source, engine=self, schema=schema, **kwargs)

    def write(self, expr: ir.Table, dest: SourceRef, fmt: str, **kwargs: Any) -> None:
        """Write ``expr`` to ``dest`` via the FormatAdapter registry.

        3-line convenience over :func:`corral.io.get_adapter`.
        Adapters skip this and call ``engine.write_<fmt>`` directly.

        Args:
            expr: An ibis expression.
            dest: Target path / URL.
            fmt: Format name (``"csv"``, ``"parquet"``, ``"duckdb"``, ...).
            **kwargs: Forwarded to the adapter's ``write``.
        """
        from corral.io import get_adapter

        adapter = get_adapter(fmt)
        adapter.write(expr, dest, engine=self, **kwargs)

    # ------------------------------------------------------------------
    # materialize / converters
    # ------------------------------------------------------------------

    def materialize(self, expr: ir.Table) -> ir.Table:
        """Force execution; return a stable ibis ``Table``.

        Materializes ``expr`` into a duckdb temp table so subsequent
        reads return a fixed row order. This is what callers want when
        they need value-equality semantics (e.g. comparing two scans).

        Args:
            expr: A lazy ibis ``Table``.

        Returns:
            A new ibis ``Table`` pointing at a backend-managed temp
            table. Lifetime is tied to the engine's connection.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> engine = IbisEngine()
            >>> engine.materialize(engine.scan(p)).count().to_pyarrow().as_py()
            2
            >>> engine.close()
        """
        name = _temp_table_name("mat")
        self.con.create_table(name, obj=expr, temp=True)
        return self.con.table(name)

    def to_pandas(self, expr: ir.Table) -> pd.DataFrame:
        """Materialize ``expr`` and return a ``pandas.DataFrame``.

        Per the :class:`~corral.engines.base.Engine` protocol, the
        returned frame uses pandas **numpy-backed nullable dtypes**
        (``Int64`` / ``Float64`` / ``string`` / ``boolean``) so null
        semantics round-trip without silently upcasting integers with
        nulls to ``float64``. We achieve this by post-processing with
        :meth:`pandas.DataFrame.convert_dtypes` — ibis's native
        ``to_pandas`` returns numpy dtypes (``int64``, ``object``,
        ``float64``) which would diverge from polars/pandas engines.
        Only the query runs under the backend lock; the conversion runs
        after it is released (see the module docstring).

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> engine = IbisEngine()
            >>> df = engine.to_pandas(engine.scan(p))
            >>> len(df), str(df["a"].dtype)
            (2, 'Int64')
            >>> engine.close()
        """
        try:
            import pandas  # noqa: F401  (fail with the install hint, not deep inside ibis)
        except ImportError as exc:  # pragma: no cover - pandas is an ibis dep
            raise EngineNotAvailableError(
                "pandas is required for IbisEngine.to_pandas; install with `pip install dbcorral[pandas]`"
            ) from exc
        if not _is_duckdb(expr):
            return expr.to_pandas().convert_dtypes()
        # Only the query holds the backend lock; the Arrow -> pandas conversion, which can cost as
        # much as the query, runs after it is released. The Arrow table is fetched the way
        # ``Backend.execute`` fetches it (the duckdb relation's own ``to_arrow_table``, not
        # ``to_pyarrow``, which re-maps types), so the conversion below sees exactly what it sees.
        backend = expr._find_backend()
        with backend_lock(backend):
            arrow = backend._to_duckdb_relation(expr).to_arrow_table()
        return _arrow_to_pandas(arrow, expr.as_table().schema()).convert_dtypes()

    def to_polars(self, expr: ir.Table) -> pl.DataFrame:
        """Materialize ``expr`` and return a ``polars.DataFrame``.

        Uses ibis's native ``to_polars`` (Arrow-backed) when available.
        Polars is an optional extra; missing-dep failures raise
        :class:`~corral.engines.errors.EngineNotAvailableError` with
        the right ``pip install`` command.

        Examples:
            >>> import pytest, tempfile, pathlib
            >>> _ = pytest.importorskip("polars")
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> engine = IbisEngine()
            >>> len(engine.to_polars(engine.scan(p)))
            2
            >>> engine.close()
        """
        try:
            return expr.to_polars()
        except ImportError as exc:
            raise EngineNotAvailableError(
                "polars is required for IbisEngine.to_polars; install with `pip install dbcorral[polars]`"
            ) from exc

    def to_arrow(self, expr: ir.Table) -> pa.Table:
        """Materialize ``expr`` and return a :class:`pyarrow.Table`.

        Runs ibis's ``to_pyarrow`` (under the backend lock, like every
        backend call); no pandas hop, so binary / decimal / timestamp
        (with tz) / nested columns keep their Arrow types.

        Examples:
            >>> import pyarrow as pa
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> engine = IbisEngine()
            >>> tbl = pa.table({"a": [1, 2], "b": ["x", None]})
            >>> engine.to_arrow(engine.from_arrow(tbl)).equals(tbl)
            True
            >>> engine.close()
        """
        return expr.to_pyarrow()

    # ------------------------------------------------------------------
    # Lazy introspection — routed via ibis' native expression API.
    # Single-line wrappers; promoted to the Engine protocol so the
    # :class:`corral.dataset.Table` wrapper can delegate cleanly
    # instead of duck-typing across engines.
    # ------------------------------------------------------------------

    def columns(self, expr: ir.Table) -> list[str]:
        """Return ``expr``'s column names via ``expr.schema().names``."""
        return list(expr.schema().names)

    def count(self, expr: ir.Table) -> int:
        """Push a ``SELECT COUNT(*)`` to duckdb and return the int."""
        return int(expr.count().to_pyarrow().as_py())

    def head(self, expr: ir.Table, n: int) -> ir.Table:
        """Return ``expr.head(n)`` — ibis builds a lazy LIMIT expression."""
        return expr.head(n)

    def select(self, expr: ir.Table, columns: list[str]) -> ir.Table:
        """Return ``expr.select(*columns)`` — lazy projection."""
        return expr.select(*columns)

    def order_by(self, expr: ir.Table, columns: list[str], descending: bool = False) -> ir.Table:
        """Return ``expr.order_by(...)`` — lazy ORDER BY pushed to duckdb."""
        keys = [ibis.desc(c) if descending else ibis.asc(c) for c in columns]
        return expr.order_by(keys)

    def limit(self, expr: ir.Table, n: int, offset: int = 0) -> ir.Table:
        """Return ``expr.limit(n, offset=offset)`` — lazy LIMIT/OFFSET."""
        return expr.limit(n, offset=offset)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Disconnect the backend if we created it.

        Idempotent — safe to call multiple times; only the first call
        disconnects. A failure to disconnect (a file-backed database
        that cannot be released, say) propagates, and the engine still
        counts as closed. Connections passed in by the caller are left
        alone.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> engine = IbisEngine()
            >>> engine.close()
            >>> engine.close()  # idempotent
        """
        if not (self._owns_con and self.con is not None):
            return
        self._owns_con = False  # first: a disconnect that raises is not retried by the next close()
        self.con.disconnect()

    def __del__(self) -> None:  # pragma: no cover - GC-driven
        """Best-effort connection release on GC; never raise here."""
        with contextlib.suppress(Exception):
            self.close()


# ---------------------------------------------------------------------------
# Helpers (module-level — no state, no polymorphism, easier to read inline)
# ---------------------------------------------------------------------------


#: Private protocol names handed to duckdb for credentialed URL sources (``corral-fs-<n>``).
_SCOPED_FS_IDS = count(1)


def _url_scheme(path: str) -> str | None:
    """The scheme of a ``scheme://...`` string, else ``None`` (same rule as ``corral.io._scheme_of``)."""
    scheme = urlsplit(path).scheme.lower()
    if len(scheme) < 2 or not path.lower().startswith(f"{scheme}://"):
        return None
    return scheme


def _scoped_filesystem(name: str, fs: Any, root: str) -> DirFileSystem:
    """A view of ``fs`` rooted at ``root`` that duckdb reaches as ``<name>://...``.

    ``DirFileSystem`` already forwards every operation to ``fs`` under
    ``root``; the subclass only renames the protocol, so duckdb routes the
    private scheme here (never to its own httpfs reader) and ibis never
    autoloads httpfs for it.
    """
    cls = type(f"_ScopedFileSystem_{name}", (DirFileSystem,), {"protocol": (name,), "cachable": False})
    return cls(path=root, fs=fs)


def _as_path_str(source: SourceRef) -> str:
    """Coerce ``str`` / ``Path`` / dict-with-path source to a string path."""
    if isinstance(source, Path):
        return str(source)
    if isinstance(source, str):
        return source
    if isinstance(source, dict) and "path" in source:
        return str(source["path"])
    raise UnsupportedSourceError(
        f"ibis engine: cannot coerce source {source!r} to a path (expected str, Path, or dict with 'path' key)"
    )


def _scan_dict(
    engine: IbisEngine,
    source: dict[str, Any],
    *,
    schema: Schema | None,
    **kwargs: Any,
) -> ir.Table:
    """Route the two supported dict-source shapes to the right primitive.

    The dispatcher rejects dict sources (it has no way to sniff them);
    this helper keeps :meth:`IbisEngine.scan` short by handling the two
    accepted shapes here. Both shapes are documented on
    :meth:`corral.engines.base.Engine.scan`.
    """
    if "data" in source:
        # Inline data — kwargs are intentionally ignored (records have
        # no reader options).
        return engine.from_records(source["data"], schema=schema)

    fmt = str(source.get("format", "")).lower()
    path = source.get("path")
    is_duckdb_handle = fmt == "duckdb" or (isinstance(path, (str, Path)) and str(path).lower().endswith(".duckdb"))
    if is_duckdb_handle:
        if path is None:
            raise InvalidEngineCallError(
                "duckdb dict source must include 'path' (e.g. {'path': 'net.duckdb', 'table': 'link'})"
            )
        table = source.get("table") or kwargs.pop("table", None)
        if not table:
            raise InvalidEngineCallError(
                "duckdb dict source must include 'table' (e.g. {'path': 'net.duckdb', 'table': 'link'})"
            )
        return engine.read_duckdb_table(str(path), table=table, schema=schema, **kwargs)

    raise UnsupportedSourceError(
        f"ibis engine: dict source shape not recognised (keys={sorted(source)!r}). "
        "Supported shapes: {'data': [...]} for inline data, or "
        "{'format': 'duckdb', 'path': '...', 'table': '...'} for a duckdb handle."
    )


def _coerce_all_null_columns_to_string(arrow_table: Any) -> Any:
    """Cast every all-null column in ``arrow_table`` to pyarrow ``string``.

    DuckDB raises :class:`ibis.common.exceptions.IbisTypeError` with
    "DuckDB does not support creating tables with NULL typed columns"
    when ``CREATE TABLE`` is handed a pyarrow column whose type is
    ``pa.null()`` — which is what pyarrow infers for a column where
    every value is ``None`` (e.g. an entirely-empty optional ``name``
    column on the Leavenworth fixture). pandas + pyarrow are happy to
    *materialise* such columns; only the duckdb write-path objects.

    String is the safe default for an unknown all-null column: it
    survives schema-cast later if the caller does know the real type,
    and round-trips cleanly through CSV / parquet otherwise. We don't
    try to be clever (no infer-from-name) because there's nothing to
    infer from — the column is genuinely typeless on the wire.

    Args:
        arrow_table: The :class:`pyarrow.Table` to scan.

    Returns:
        Either ``arrow_table`` unchanged (no all-null columns) or a
        new table with the offending columns cast to ``string``.
    """
    import pyarrow as pa

    fields_to_cast = [field.name for field in arrow_table.schema if pa.types.is_null(field.type)]
    if not fields_to_cast:
        return arrow_table
    new_schema = arrow_table.schema
    for name in fields_to_cast:
        idx = new_schema.get_field_index(name)
        new_schema = new_schema.set(idx, pa.field(name, pa.string()))
    return arrow_table.cast(new_schema)


def _attached_catalog(backend: BaseBackend, path: Path) -> str | None:
    """The catalog name under which ``backend`` already has the duckdb file ``path`` attached, if any.

    Caller holds :func:`backend_lock`; ``raw_sql`` hands back a cursor used outside the method wrapper.
    """
    rows = backend.raw_sql("SELECT database_name, path FROM duckdb_databases()").fetchall()  # type: ignore[attr-defined]
    return next((name for name, p in rows if p and Path(p).resolve() == path), None)


def _is_duckdb(expr: Any) -> bool:
    """Whether ``expr`` is bound to a duckdb backend (the only one whose conversion we replicate)."""
    try:
        return expr._find_backend().name == "duckdb"
    except Exception:  # an unbound memtable has no backend: use ibis's own path
        return False


def _arrow_to_pandas(table: pa.Table, schema: Any) -> pd.DataFrame:
    """Arrow -> pandas exactly as ibis 12's duckdb ``Backend.execute`` converts it, without its lock.

    Mirrors ``ibis.backends.duckdb.Backend.execute``: nested, dictionary and null-bearing columns go
    through ``to_pylist`` (Arrow's own ``to_pandas`` would turn null ints into floats), the rest
    through ``to_pandas``, then ibis's ``DuckDBPandasData`` coerces to the ibis schema.
    ``test_to_pandas_matches_ibis_execute_for_every_leavenworth_table`` pins the equivalence.
    """
    import pandas as pd
    import pyarrow.types as pat
    from ibis.backends.duckdb.converter import DuckDBPandasData

    columns = {
        name: (
            col.to_pylist()
            if pat.is_nested(col.type) or pat.is_dictionary(col.type) or col.null_count
            else col.to_pandas()
        )
        for name, col in zip(table.column_names, table.columns, strict=True)
    }
    return DuckDBPandasData.convert_table(pd.DataFrame(columns), schema)


_LOCK_ATTR = "_corral_lock"


def _locked(method: Callable[..., Any], lock: threading.RLock) -> Callable[..., Any]:
    @functools.wraps(method)
    def call(*args: Any, **kwargs: Any) -> Any:
        with lock:
            return method(*args, **kwargs)

    return call


#: How long a memtable finalizer waits for the backend lock at interpreter exit before giving up.
_FINALIZER_WAIT_S = 1.0


def _locked_finalizer_factory(make: Callable[..., Any], lock: threading.RLock) -> Callable[..., Any]:
    """Wrap ``_make_memtable_finalizer`` so the finalizer it returns also takes ``lock``, briefly.

    ibis 12 registers each memtable's finalizer with :mod:`atexit` (duckdb's own backend returns
    none, but a SQL backend handed to :class:`IbisEngine` may); it drops the table through
    ``con.cursor()``, which must not race a query another (daemon) thread may still be running on
    the same duckdb connection. At exit the drop is pointless anyway (the in-memory database goes
    away with the process), so the finalizer waits at most :data:`_FINALIZER_WAIT_S` for the lock
    and skips the drop if it is still held: Ctrl-C never hangs behind an in-flight job query.
    """

    def run_if_free(finalizer: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(finalizer)
        def call(*args: Any, **kwargs: Any) -> Any:
            if not lock.acquire(timeout=_FINALIZER_WAIT_S):
                return None
            try:
                return finalizer(*args, **kwargs)
            finally:
                lock.release()

        return call

    @functools.wraps(make)
    def factory(*args: Any, **kwargs: Any) -> Any:
        finalizer = make(*args, **kwargs)
        return None if finalizer is None else run_if_free(finalizer)

    return factory


def backend_lock(backend: Any) -> contextlib.AbstractContextManager[Any]:
    """The lock :func:`serialize_backend` put on ``backend`` (a no-op context if it has none).

    Hold it around any direct use of the raw duckdb connection (``backend.con``), which the
    method wrappers cannot see.
    """
    lock = getattr(backend, _LOCK_ATTR, None)
    return lock if lock is not None else contextlib.nullcontext()


def serialize_backend(backend: BaseBackend) -> BaseBackend:
    """Make ``backend`` safe to share between threads by running each of its methods under one lock.

    Every method defined on the backend's class (public and private: ``execute``,
    ``to_pyarrow``, ``create_table``, ``read_parquet``, ``raw_sql``, ...) is replaced on the
    *instance* by a wrapper holding a re-entrant lock, so nested calls (``execute`` ->
    ``_to_duckdb_relation`` -> ``raw_sql``) re-enter freely while a second thread waits for the
    whole call. Idempotent: the lock lives on the backend, so wrapping it again (two engines
    sharing one ``con``) is a no-op. Returns ``backend`` for chaining.
    """
    if getattr(backend, _LOCK_ATTR, None) is not None:
        return backend
    lock = threading.RLock()
    # Look the methods up on the class: inspecting the instance would evaluate its properties,
    # some of which (``version``, ``current_database``) run queries.
    for name, _ in inspect.getmembers(type(backend), inspect.isfunction):
        if name.startswith("__"):
            continue
        method = getattr(backend, name)
        wrap = _locked_finalizer_factory if name == "_make_memtable_finalizer" else _locked
        setattr(backend, name, wrap(method, lock))
    setattr(backend, _LOCK_ATTR, lock)
    return backend


_TEMP_COUNTER = count()


def _temp_table_name(prefix: str) -> str:
    """Return a process-unique temp table name for ``create_table``.

    A module-level :class:`itertools.count` iterator avoids the ibis
    backend complaining about a name collision when the same engine
    materializes more than one expression. ``next(counter)`` is more
    legible than the list-mutation trick (``_C[0] += 1``) and removes
    a tiny clever-Python footnote from Lens C.
    """
    return f"_corral_{prefix}_{next(_TEMP_COUNTER)}"


__all__ = ["IbisEngine", "backend_lock", "serialize_backend"]
