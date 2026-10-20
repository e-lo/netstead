"""DuckDB FormatAdapter — multi-table .duckdb files + ``duckdb://`` URLs.

DuckDB is the **default API-download format** per ``docs/architecture.md``
§6.1: a single self-contained file with all tables of a network bundled
together, plus the ``_netstead_meta.json`` sidecar. This adapter is the
on-ramp for every API consumer.

What's different from csv/parquet adapters
------------------------------------------

DuckDB files are **multi-table containers** — one file holds N tables.
That has two consequences:

1. ``scan()`` is a real enumeration step (it returns N ``ResourceRef``s,
   one per table). Callers can't just open the file blindly and read
   "the data" the way a single-csv read works.
2. ``read()`` and ``write()`` require a ``table=`` kwarg. Without it
   they raise :class:`InvalidEngineCallError` with a hint to call
   ``scan()`` first to list the tables.

No raw SQL here
---------------

Per the architecture's hard rule, only ``corral.engines.ibis_engine``
is allowed to embed raw SQL strings. We follow the rule rigidly:

* Table enumeration uses ``duckdb.connect(...).table_function('duckdb_tables')``
  — duckdb exposes its system catalogue as a relation, so we read a
  relation and filter in Python rather than running ``SHOW TABLES``.
* Reads and writes delegate to the engine via the cross-engine
  ``{"format": "duckdb", "path": ..., "table": ...}`` source-dict
  contract. The polars and pandas engines both use the duckdb Python
  relation API (``con.table(name).pl()`` / ``con.table(name).df()``)
  to satisfy that contract without SQL; the ibis engine uses its
  duckdb backend. This adapter does not need to know which is which —
  it just hands the dict to the engine.

URL scheme
----------

``duckdb://path/to/file.duckdb`` is parsed as a sub-locator alias for
``path/to/file.duckdb``. The scheme is useful for the remote/cache
flows that the API-download path will lean on (task 1.11): a URL is
the unambiguous form for "fetch this duckdb and open it".
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

from corral.engines.errors import InvalidEngineCallError
from corral.io import register_adapter
from corral.io._paths import normalize_to_str
from corral.io.base import ResourceListing, ResourceRef, SourceRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.engines.base import Engine, TableExpr
    from corral.spec.model import Schema


# ---------------------------------------------------------------------------
# Helpers — kept module-level + tiny so they read inline (Lens C)
# ---------------------------------------------------------------------------


def _coerce_path(source: SourceRef) -> str:
    """Normalize a SourceRef to a filesystem path string.

    Thin wrapper around the shared
    :func:`corral.io._paths.normalize_to_str` that adds one piece of
    duckdb-specific handling: stripping the ``duckdb://`` URL scheme so
    the duckdb client sees a real path. Two forms in the wild:

        duckdb:///abs/path.duckdb   → "/abs/path.duckdb"
        duckdb://rel/path.duckdb    → "rel/path.duckdb"

    Dict sources are not accepted here: the dispatcher does not feed
    dicts to adapters (``dispatch``'s own ``normalize_to_str`` call rejects
    them upstream), and our own read()/write() build the dict-form
    source themselves before passing to the engine.
    """
    if isinstance(source, str) and source.startswith("duckdb://"):
        # urlparse is overkill for one prefix; a literal strip is both
        # more legible and more robust to weird URL fragments we don't
        # care about for local paths.
        return source[len("duckdb://") :]
    return normalize_to_str(source, adapter="DuckdbAdapter")


def _list_tables(path_str: str) -> list[str]:
    """Return the names of every user table inside the duckdb file.

    Uses ``con.table_function('duckdb_tables')`` — duckdb's system
    catalogue exposed as a relation. We project the ``table_name``
    column and filter to schema=='main' in Python (no SQL string).

    Connection mode: we prefer ``read_only=True`` because it lets us
    coexist with other processes that have the file open. But duckdb
    refuses to mix configurations within one process — if another part
    of the program (an engine, typically) already holds a read-write
    connection on this file, opening a second read-only one raises
    :class:`duckdb.ConnectionException`. The fallback is to open in the
    same mode the existing connection uses (read-write). Both branches
    use the same relational-API path, so no SQL strings appear either
    way.
    """
    try:
        con = duckdb.connect(path_str, read_only=True)
    except duckdb.ConnectionException:
        # Another connection in this process is read-write; match it.
        con = duckdb.connect(path_str)
    try:
        # table_function returns a DuckDBPyRelation over duckdb_tables(),
        # which is the canonical no-SQL way to introspect the catalogue.
        # Columns include schema_name, table_name, internal, temporary, etc.
        # We pull table_name + schema_name + internal and filter in Python:
        # .project() takes column names (not a SQL boolean expr) so no SQL
        # string crosses this boundary.
        rel = con.table_function("duckdb_tables").project("schema_name, table_name, internal")
        rows = rel.fetchall()
    finally:
        con.close()
    # Keep only user tables in the default 'main' schema; sort for stable
    # ordering across calls (callers may rely on it; tests that use set
    # equality don't care, but determinism is a courtesy).
    names = sorted(name for (schema, name, internal) in rows if schema == "main" and not internal)
    return names


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class DuckdbAdapter:
    """FormatAdapter for ``.duckdb`` files and ``duckdb://`` URLs.

    Attributes:
        name: Registry key (``"duckdb"``).
        extensions: ``("duckdb",)`` — files with the ``.duckdb`` suffix.
        schemes: ``("duckdb",)`` — URLs of the form ``duckdb://...``.

    Examples:
        Enumerate tables in a duckdb file::

            >>> from corral.io.duckdb_adapter import DuckdbAdapter
            >>> from corral.fixtures import sample
            >>> a = DuckdbAdapter()
            >>> listing = a.scan(sample.duckdb_path(), engine=None)
            >>> "book" in {ref.name for ref in listing}
            True

        Read a specific table via any engine (here pandas)::

            >>> from corral.engines.ibis_engine import IbisEngine
            >>> a = DuckdbAdapter()
            >>> engine = IbisEngine()
            >>> df = engine.to_pandas(
            ...     a.read(sample.duckdb_path(), engine=engine, table="book")
            ... )
            >>> "title" in df.columns
            True
    """

    name: str = "duckdb"
    extensions: tuple[str, ...] = ("duckdb",)
    schemes: tuple[str, ...] = ("duckdb",)

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def probe(self, source: SourceRef) -> bool:
        """Cheap acceptance check — extension or scheme match, no I/O.

        Returns True when ``source`` is a ``.duckdb`` path (regardless
        of whether the file exists yet) or a ``duckdb://`` URL. Total
        and never raises — anything weird (dict, None, int) is silently
        a non-match, per the FormatAdapter contract.

        Args:
            source: The candidate source.

        Returns:
            True if this adapter would accept ``source`` in a read().

        Examples:
            >>> from corral.io.duckdb_adapter import DuckdbAdapter
            >>> a = DuckdbAdapter()
            >>> a.probe("net.duckdb"), a.probe("duckdb://x"), a.probe("foo.csv")
            (True, True, False)
        """
        try:
            if isinstance(source, Path):
                return source.suffix.lower() == ".duckdb"
            if isinstance(source, str):
                lower = source.lower()
                return lower.endswith(".duckdb") or lower.startswith("duckdb://")
        except Exception:  # pragma: no cover - probe must be total
            return False
        return False

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------

    def scan(self, source: SourceRef, engine: Engine | None = None) -> ResourceListing:
        """Enumerate every user table in the duckdb file.

        Args:
            source: A path or ``duckdb://`` URL pointing at a duckdb file.
            engine: Unused — table enumeration is a metadata-only op that
                talks to duckdb directly. The arg is kept for protocol
                conformance (and so a future ATTACH-based fast path
                could pull metadata via the user-provided engine instead).

        Returns:
            One :class:`ResourceRef` per table. The ``path`` field uses
            the ``"file::table"`` sub-locator convention documented on
            :class:`ResourceRef`.

        Examples:
            >>> from corral.io.duckdb_adapter import DuckdbAdapter
            >>> from corral.fixtures import sample
            >>> sorted(
            ...     r.name for r in DuckdbAdapter().scan(
            ...         sample.duckdb_path(), engine=None
            ...     )
            ... )
            ['author', 'book', 'venue']
        """
        # ``engine`` is kept in the signature for FormatAdapter protocol
        # conformance and so a future ATTACH-based fast path can pull
        # metadata via the user-provided engine. Today we go directly to
        # duckdb because table enumeration is a metadata-only op.
        del engine
        path_str = _coerce_path(source)
        return [
            ResourceRef(name=t, path=f"{path_str}::{t}", format=self.name, container_adapter=self.name, member=t)
            for t in _list_tables(path_str)
        ]

    # ------------------------------------------------------------------
    # read
    # ------------------------------------------------------------------

    def read(
        self,
        source: SourceRef,
        engine: Engine,
        schema: Schema | None = None,
        **kwargs: Any,
    ) -> TableExpr:
        """Read one named table out of a duckdb file as a lazy engine expression.

        A duckdb file contains multiple tables; a ``table=`` kwarg names
        the one to read. Without it we raise immediately rather than
        guess — the caller (or AI agent) should call :meth:`scan` first
        to see what's available.

        Delegation: this method calls the engine's
        :meth:`~corral.engines.base.Engine.read_duckdb_table`
        primitive directly. The polars and pandas engines use duckdb's
        Python relation API (no SQL); the ibis engine uses its duckdb
        backend.

        Args:
            source: The duckdb file path / URL.
            engine: The engine to defer to.
            schema: Optional Frictionless :class:`Schema` to cast columns
                with. Forwarded verbatim to the engine.
            **kwargs: Forwarded to the engine. ``table=`` is required
                and is consumed (popped) before forwarding so it isn't
                passed twice when the engine reads it from the dict.

        Returns:
            The engine's native lazy expression type
            (``ibis.expr.types.Table`` / ``polars.LazyFrame`` /
            ``pandas.DataFrame`` — pandas is eager by design).

        Raises:
            InvalidEngineCallError: If ``table=`` is not provided.

        Examples:
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.io.duckdb_adapter import DuckdbAdapter
            >>> from corral.fixtures import sample
            >>> e = IbisEngine()
            >>> df = DuckdbAdapter().read(
            ...     sample.duckdb_path(), engine=e, table="book"
            ... )
            >>> df.count().to_pyarrow().as_py() > 0
            True
        """
        table = kwargs.pop("table", None)
        if table is None:
            raise InvalidEngineCallError(
                "DuckdbAdapter.read requires a 'table=' kwarg naming which table "
                "to read from the duckdb file. Call DuckdbAdapter.scan(source) first "
                "to list available tables."
            )
        path_str = _coerce_path(source)
        # Delegate to the engine's per-format primitive — no engine-name
        # dispatch, no source-dict construction at this layer.
        return engine.read_duckdb_table(path_str, table=table, schema=schema, **kwargs)

    # ------------------------------------------------------------------
    # write
    # ------------------------------------------------------------------

    def write(
        self,
        expr: TableExpr,
        dest: SourceRef,
        engine: Engine,
        **kwargs: Any,
    ) -> None:
        """Persist ``expr`` to a duckdb file as a named table.

        Mirrors :meth:`read` — ``table=`` is required and is consumed
        from ``kwargs`` before delegating to the engine's
        :meth:`~corral.engines.base.Engine.write_duckdb_table`
        primitive. The engine handles the actual file mutation (the
        pandas engine uses ``con.register`` + the relational API; ibis
        hops through Arrow + ``create_table``; both are no-SQL paths).

        Args:
            expr: An engine-native expression to write.
            dest: Target duckdb file path / URL. Created if absent;
                if the table already exists, engine-specific overwrite
                semantics apply (the ibis engine drops + recreates).
            engine: The engine that owns ``expr``.
            **kwargs: Forwarded to the engine. ``table=`` is required
                and is consumed before forwarding.

        Raises:
            InvalidEngineCallError: If ``table=`` is not provided.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.io.duckdb_adapter import DuckdbAdapter
            >>> e = IbisEngine()
            >>> df = e.scan({"data": [{"x": 1}, {"x": 2}]})
            >>> with tempfile.TemporaryDirectory() as tmp:
            ...     out = pathlib.Path(tmp) / "out.duckdb"
            ...     DuckdbAdapter().write(df, out, engine=e, table="tbl")
            ...     out.exists()
            True
        """
        table = kwargs.pop("table", None)
        if table is None:
            raise InvalidEngineCallError(
                "DuckdbAdapter.write requires a 'table=' kwarg naming the destination table inside the duckdb file."
            )
        path_str = _coerce_path(dest)
        engine.write_duckdb_table(expr, path_str, table=table, **kwargs)


# ---------------------------------------------------------------------------
# Self-registration on import
# ---------------------------------------------------------------------------
# Per the io package's docstring, adapters register themselves at import
# time. The register_adapter() function is idempotent for re-registration
# (it scrubs the old extension/scheme bindings before re-binding) so
# importlib.reload() is safe.

register_adapter(DuckdbAdapter())


__all__ = ["DuckdbAdapter"]
