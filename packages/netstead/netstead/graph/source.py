"""Engine-agnostic access to GMNS tables for graph building.

A :class:`NetworkSource` returns requested columns of a requested GMNS table
(``node``, ``link``, ``movement``, ...) as a ``pyarrow.Table``, regardless of the
underlying storage engine. The graph builder asks only for the columns it needs,
so large stores are read lazily and column-pruned. Parquet and DuckDB are the
preferred engines; an in-memory pandas dict (what ``read_gmns_network`` returns)
is supported for small networks and tests.
"""

from __future__ import annotations

import abc
import os
from collections.abc import Sequence

import pyarrow as pa


def _select_existing(available: Sequence[str], requested: Sequence[str] | None) -> list | None:
    """Intersect requested columns with what exists, preserving requested order.

    Returns ``None`` to mean "all columns" when nothing specific was requested.
    """
    if requested is None:
        return None
    have = set(available)
    return [c for c in requested if c in have]


class NetworkSource(abc.ABC):
    """Read GMNS tables as Arrow, independent of the storage engine."""

    @abc.abstractmethod
    def has_table(self, name: str) -> bool:
        """Whether the named GMNS table exists in this source."""

    @abc.abstractmethod
    def table(self, name: str, columns: Sequence[str] | None = None) -> pa.Table | None:
        """Return ``name`` as an Arrow table with (at least) ``columns``.

        Only columns that actually exist are returned; missing columns are
        silently dropped so the builder can decide which are required and raise
        a clear error. Returns ``None`` if the table itself is absent.
        """


class InMemorySource(NetworkSource):
    """Wrap the ``{table_name: DataFrame}`` dict returned by ``read_gmns_network``."""

    def __init__(self, network: dict):
        """Wrap a ``{table_name: DataFrame}`` mapping as a :class:`NetworkSource`."""
        self._net = network

    @classmethod
    def from_directory(cls, data_directory: str, **kwargs) -> InMemorySource:
        """Load a GMNS network directory via :meth:`Network.from_source` and wrap its tables."""
        from netstead import Network

        net = Network.from_source(data_directory, **kwargs)
        return cls({name: table.to_pandas() for name, table in net.tables.items()})

    def has_table(self, name: str) -> bool:
        """Return ``True`` if ``name`` is present and non-null in the wrapped dict."""
        return name in self._net and self._net[name] is not None

    def table(self, name: str, columns: Sequence[str] | None = None) -> pa.Table | None:
        """Materialize ``name`` as a pyarrow Table, optionally restricted to ``columns``."""
        if not self.has_table(name):
            return None
        df = self._net[name]
        cols = _select_existing(list(df.columns), columns)
        if cols is not None:
            df = df[cols]
        return pa.Table.from_pandas(df, preserve_index=False)


class ParquetSource(NetworkSource):
    """Read GMNS tables from Parquet files.

    ``source`` is either a directory holding ``<table>.parquet`` files or an
    explicit ``{table_name: path}`` mapping.
    """

    def __init__(self, source):
        """Discover ``<table>.parquet`` files in a directory, or accept a ``{table: path}`` mapping."""
        if isinstance(source, dict):
            self._paths = dict(source)
        else:
            self._paths = {}
            for fname in os.listdir(source):
                root, ext = os.path.splitext(fname)
                if ext.lower() in (".parquet", ".pq"):
                    self._paths[root] = os.path.join(source, fname)

    def has_table(self, name: str) -> bool:
        """Return ``True`` if a parquet file is known for ``name``."""
        return name in self._paths

    def table(self, name: str, columns: Sequence[str] | None = None) -> pa.Table | None:
        """Read ``name`` from parquet (column-pruned to ``columns`` when given)."""
        import pyarrow.parquet as pq

        if not self.has_table(name):
            return None
        path = self._paths[name]
        available = pq.ParquetFile(path).schema_arrow.names
        cols = _select_existing(available, columns)
        return pq.read_table(path, columns=cols)


class DuckDBSource(NetworkSource):
    """Read GMNS tables from a DuckDB database.

    ``con`` may be an open ``duckdb`` connection or a path to a ``.duckdb`` file.
    ``tables`` optionally maps GMNS table names to actual relation names; by
    default the GMNS name is used directly.
    """

    def __init__(self, con, tables: dict | None = None):
        """Open a duckdb connection (or accept one) and remember the GMNS->relation map."""
        import duckdb

        self._con = duckdb.connect(con) if isinstance(con, str) else con
        self._tables = tables or {}

    def _relation(self, name: str) -> str:
        """Translate a GMNS table name to its actual duckdb relation name."""
        return self._tables.get(name, name)

    def has_table(self, name: str) -> bool:
        """Return ``True`` if the underlying duckdb has a relation matching ``name``."""
        rel = self._relation(name)
        # pragma: allow-sql — this is a standalone duckdb *reader* in the optional
        # graph extra, not part of corral's engine layer; reading an arbitrary
        # user .duckdb file needs direct SQL (the no-raw-SQL rule targets the
        # engine internals, not external-source adapters).
        found = self._con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?",  # pragma: allow-sql
            [rel],
        ).fetchone()
        return found is not None

    def table(self, name: str, columns: Sequence[str] | None = None) -> pa.Table | None:
        """Read ``name`` from duckdb as an arrow Table (column-pruned to ``columns``)."""
        if not self.has_table(name):
            return None
        rel = self._relation(name)
        available = self._con.execute(f'SELECT * FROM "{rel}" LIMIT 0').arrow().schema.names  # pragma: allow-sql
        cols = _select_existing(available, columns)
        col_sql = "*" if cols is None else ", ".join(f'"{c}"' for c in cols)
        result = self._con.execute(f'SELECT {col_sql} FROM "{rel}"').arrow()  # pragma: allow-sql
        # Newer duckdb returns a RecordBatchReader from .arrow(); the builder
        # expects a materialized pyarrow.Table (so .to_pandas() works).
        if hasattr(result, "read_all"):
            result = result.read_all()
        return result


class PolarsSource(NetworkSource):
    """Read GMNS tables via polars (Parquet or CSV), returning Arrow.

    ``source`` is either a directory holding ``<table>.<ext>`` files or an explicit
    ``{table_name: path}`` mapping. ``fmt`` selects ``"parquet"`` (default) or ``"csv"``.
    polars is built on Arrow, so ``DataFrame.to_arrow()`` is effectively zero-copy.
    """

    def __init__(self, source, fmt: str = "parquet"):
        """Discover ``<table>.<ext>`` files in a directory, or accept a ``{table: path}`` mapping.

        Args:
            source: Directory holding the table files, or an explicit mapping.
            fmt: ``"parquet"`` (default) or ``"csv"`` — selects the polars reader.
        """
        if fmt not in ("parquet", "csv"):
            raise ValueError(f"fmt must be 'parquet' or 'csv', got {fmt!r}.")
        self._fmt = fmt
        exts = (".parquet", ".pq") if fmt == "parquet" else (".csv",)
        if isinstance(source, dict):
            self._paths = dict(source)
        else:
            self._paths = {}
            for fname in os.listdir(source):
                root, ext = os.path.splitext(fname)
                if ext.lower() in exts:
                    self._paths[root] = os.path.join(source, fname)

    def has_table(self, name: str) -> bool:
        """Return ``True`` if a file (parquet or csv) is known for ``name``."""
        return name in self._paths

    def table(self, name: str, columns: Sequence[str] | None = None) -> pa.Table | None:
        """Read ``name`` via polars and hand back an Arrow table (column-pruned)."""
        import polars as pl

        if not self.has_table(name):
            return None
        path = self._paths[name]
        scan = pl.scan_parquet(path) if self._fmt == "parquet" else pl.scan_csv(path)
        available = scan.collect_schema().names()
        cols = _select_existing(available, columns)
        reader = pl.read_parquet if self._fmt == "parquet" else pl.read_csv
        frame = reader(path, columns=cols) if cols is not None else reader(path)
        return frame.to_arrow()


def as_source(obj) -> NetworkSource:
    """Coerce a user-supplied ``source`` into a :class:`NetworkSource`.

    Accepts a :class:`NetworkSource`, a ``{table: DataFrame}`` dict, a DuckDB
    connection, or a path (``.duckdb`` file, or a directory of Parquet/CSV).
    """
    if isinstance(obj, NetworkSource):
        return obj
    if isinstance(obj, dict):
        return InMemorySource(obj)
    if isinstance(obj, str):
        if obj.endswith(".duckdb") or obj.endswith(".db"):
            return DuckDBSource(obj)
        if os.path.isdir(obj):
            has_parquet = any(f.lower().endswith((".parquet", ".pq")) for f in os.listdir(obj))
            if has_parquet:
                return ParquetSource(obj)
            return InMemorySource.from_directory(obj)
    if obj.__class__.__module__.startswith("duckdb"):
        return DuckDBSource(obj)
    raise TypeError(
        f"Cannot interpret {obj!r} as a network source. Pass a NetworkSource, a "
        "{table: DataFrame} dict, a DuckDB connection, or a path."
    )
