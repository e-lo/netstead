"""CSV :class:`FormatAdapter` — thin glue between dispatch and the engine.

Importing this module self-registers a ``"csv"`` adapter into the
:mod:`corral.io` registry; from then on any source ending in
``.csv`` resolves to :class:`CsvAdapter`. The adapter itself contains
no parsing logic — it forwards reads to the engine's :meth:`read_csv`
primitive and writes to its :meth:`write_csv` primitive. Each engine
(:class:`~corral.engines.ibis_engine.IbisEngine`,
:class:`~corral.engines.ibis_engine.IbisEngine`,
:class:`~corral.engines.ibis_engine.IbisEngine`) already owns a
CSV reader; the adapter's job is to be the dispatch target so callers
don't have to special-case the format on the read path.

CSV-specific kwargs flow through unchanged. Names differ per engine
because each delegates to a different underlying library:

- **ibis (duckdb)**: ``delim=";"``, ``header=True``, ``columns={...}``
  — see :meth:`ibis.backends.duckdb.Backend.read_csv`.
- **polars**: ``separator=";"``, ``has_header=True``, ``schema={...}``
  — see :func:`polars.scan_csv`.
- **pandas**: ``sep=";"``, ``header=0``, ``encoding=...`` — see
  :func:`pandas.read_csv`.

We deliberately do not paper over the naming differences. The adapter
is a routing layer, not a compatibility shim; doing so would lock us
into one engine's vocabulary and surprise readers who know the other
two libraries. Sibling adapters (parquet, duckdb, zipcsv, remote) follow
the same passthrough convention — see ``packages/corral/corral/io/__init__.py``
for the registry and dispatch flow.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from corral.io import register_adapter
from corral.io._package import write_flat_package
from corral.io.base import ResourceListing, ResourceRef, SourceRef

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from corral.engines.base import Engine, TableExpr
    from corral.io.base import DescriptorFactory
    from corral.spec.model import Schema


class CsvAdapter:
    """Read/write adapter for single-file CSV sources.

    Attributes:
        name: Registry key (always ``"csv"``).
        extensions: Extensions this adapter owns (``("csv",)``). The
            ``.csv.gz`` form is left to a future ``zipcsv``-style
            adapter so the routing stays unambiguous.
        schemes: Empty — CSV has no URL scheme. Remote CSV URLs route
            here via extension match (``s3://bucket/links.csv``).

    Examples:
        Probe a path:

        >>> from corral.io.csv_adapter import CsvAdapter
        >>> CsvAdapter().probe("data/link.csv")
        True
        >>> CsvAdapter().probe("data/link.parquet")
        False

        Scan a single CSV — returns one resource named after the stem:

        >>> import tempfile, pathlib
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> p = pathlib.Path(tempfile.mkdtemp()) / "link.csv"
        >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
        >>> eng = IbisEngine()
        >>> listing = CsvAdapter().scan(p, engine=eng)
        >>> listing[0].name, listing[0].format
        ('link', 'csv')
        >>> eng.close()
    """

    name: str = "csv"
    extensions: tuple[str, ...] = ("csv",)
    schemes: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def probe(self, source: SourceRef) -> bool:
        """Return True if ``source`` looks like a CSV path.

        Cheap extension sniff — never opens the file, never raises.
        The dispatcher already catches probe exceptions (see
        :func:`corral.io.dispatch`), but the
        :class:`~corral.io.base.FormatAdapter` contract requires
        probe to be total in its own right; we honour that here rather
        than leaning on the catch.

        Args:
            source: A path, URL, or arbitrary object.

        Returns:
            True if ``source`` is a string or ``Path`` whose tail ends
            in ``.csv`` (case-insensitive). False for any other input,
            including dicts, ``None``, and unrelated extensions.

        Examples:
            >>> from corral.io.csv_adapter import CsvAdapter
            >>> a = CsvAdapter()
            >>> a.probe("foo.csv"), a.probe("FOO.CSV")
            (True, True)
            >>> a.probe(None), a.probe({"data": []}), a.probe(42)
            (False, False, False)
        """
        if isinstance(source, Path):
            return source.suffix.lower() == ".csv"
        if isinstance(source, str):
            # Path() on a URL-looking string still gives a usable suffix
            # because Path keeps everything after the last '.'. We don't
            # try to strip URL fragments or query strings here — that's
            # the dispatcher's concern, and the adapter's job is the
            # cheapest possible check.
            return source.lower().endswith(".csv")
        return False

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
        """Return a lazy table expression for ``source`` from ``engine``.

        Delegates to ``engine.read_csv(source, schema=schema, **kwargs)``
        — the per-format primitive on the Engine protocol. The adapter
        owns format dispatch; the engine owns parsing.

        Args:
            source: Path, ``Path``, or URL pointing at a single CSV file.
            engine: The execution engine to defer the read to.
            schema: Optional resolved Frictionless schema. Engine applies
                column-type casts where the type maps cleanly.
            **kwargs: CSV-specific options forwarded to the engine's
                reader (delimiter, encoding, header, ...). Names follow
                the underlying library; see module docstring.

        Returns:
            A lazy, engine-native table expression.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.io.csv_adapter import CsvAdapter
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "t.csv"
            >>> _ = p.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> eng = IbisEngine()
            >>> expr = CsvAdapter().read(p, engine=eng)
            >>> expr.count().to_pyarrow().as_py()
            2
            >>> eng.close()
        """
        return engine.read_csv(source, schema=schema, **kwargs)

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
        """Persist ``expr`` to ``dest`` as CSV via ``engine``.

        Delegates to ``engine.write_csv(expr, dest, **kwargs)`` — the
        per-format primitive on the Engine protocol.

        Args:
            expr: An engine-native lazy or eager table expression.
            dest: Target path or URL. The engine handles directory
                creation conventions; the adapter does not pre-create
                parent dirs.
            engine: The engine to perform the write.
            **kwargs: CSV writer options forwarded to the engine
                (compression, header, line terminator, ...). Names follow
                the underlying library.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.io.csv_adapter import CsvAdapter
            >>> tmpdir = pathlib.Path(tempfile.mkdtemp())
            >>> src = tmpdir / "in.csv"
            >>> _ = src.write_text(chr(10).join(["a,b", "1,2", "3,4", ""]))
            >>> eng = IbisEngine()
            >>> a = CsvAdapter()
            >>> out = tmpdir / "out.csv"
            >>> a.write(a.read(src, engine=eng), out, engine=eng)
            >>> a.read(out, engine=eng).count().to_pyarrow().as_py()
            2
            >>> eng.close()
        """
        engine.write_csv(expr, dest, **kwargs)

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------

    def write_package(
        self,
        tables: Mapping[str, TableExpr],
        dest: Path,
        engine: Engine,
        *,
        describe: DescriptorFactory,
    ) -> None:
        """Write a directory: one ``<table>.csv`` per table plus a ``datapackage.json``."""
        write_flat_package(self, tables, dest, engine, describe=describe)

    def scan(self, source: SourceRef, engine: Engine | None = None) -> ResourceListing:
        """Enumerate the resources at ``source``.

        CSV is a single-table format, so a path resolves to a one-element
        listing named after the file stem. Dict handles have no
        meaningful CSV stem — they're an engine-side construct (see
        :data:`~corral.types.SourceRef`) — and resolve to an empty
        listing rather than raising, so callers can iterate adapter
        outputs uniformly.

        Args:
            source: Path or URL string.
            engine: Unused for CSV (no metadata read required to know
                the resource name); accepted to satisfy the
                :class:`~corral.io.base.FormatAdapter` protocol.

        Returns:
            One :class:`~corral.io.base.ResourceRef` for a path-like
            source; an empty list for a dict handle.

        Examples:
            >>> import tempfile, pathlib
            >>> from corral.engines.ibis_engine import IbisEngine
            >>> from corral.io.csv_adapter import CsvAdapter
            >>> p = pathlib.Path(tempfile.mkdtemp()) / "node.csv"
            >>> _ = p.write_text(chr(10).join(["a", "1", ""]))
            >>> eng = IbisEngine()
            >>> [r.name for r in CsvAdapter().scan(p, engine=eng)]
            ['node']
            >>> eng.close()
        """
        del engine  # CSV scan needs no engine call; arg kept for protocol shape.
        if isinstance(source, (str, Path)):
            # Use the shared coercer for protocol parity, then read the
            # stem off the result so the name resolution is identical to
            # what dispatch / other adapters see.
            path_str = str(source) if isinstance(source, Path) else source
            name = Path(path_str).stem
        else:
            # dict handles (e.g. {"data": [...]}) have no stem; return an
            # empty listing so callers iterating adapter outputs don't
            # have to handle a raise.
            return []
        return [ResourceRef(name=name, path=path_str, format=self.name)]


# Self-register at import time. Re-registration is idempotent
# (``register_adapter`` overwrites by name) so importing this module
# twice does not pollute the registry.
register_adapter(CsvAdapter())


__all__ = ["CsvAdapter"]
