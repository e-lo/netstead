"""Generic Frictionless-aligned tabular-data-package engine.

Top-level re-exports cover the most common entry points. Submodules
hold the full surface (:mod:`corral.engines`, :mod:`corral.io`,
:mod:`corral.validation`, :mod:`corral.dataset`, ...).

Examples:
    >>> from corral import Package, Table, read
    >>> Package is not None and Table is not None and read is not None
    True
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .dataset import OutOfSyncError, OutOfSyncWarning, Package, Table

if TYPE_CHECKING:
    from .engines.base import Engine
    from .spec import DataPackage


def read(
    source: str | Path,
    *,
    format: str | None = None,
    credentials: dict[str, str] | None = None,
    engine: Engine | None = None,
    scope: dict[str, Any] | None = None,
    spec: DataPackage | str | Path | None = None,
    tables: Any | None = None,
    **kwargs: Any,
) -> Package:
    """Load a Frictionless data package from ``source``. The I/O front door.

    Thin convenience wrapper around :meth:`Package.from_source` — exists
    so the documented top-level form ``corral.read(...)`` works
    without callers needing to know which class to import. Matches the
    signature documented in ``docs/architecture.md`` §6.1.

    Args:
        source: Local path, ``s3://`` / ``https://`` / ``duckdb://`` URL,
            or directory of CSV / Parquet / DuckDB / zipped-CSV files.
        format: Optional explicit adapter name (``"csv"``, ``"parquet"``,
            ``"duckdb"``, ``"zipcsv"``, ``"remote"``). Short-circuits the
            extension-sniff / probe chain — useful for extensionless URLs
            or sources whose extension lies about the inner format.
        credentials: Optional explicit credentials dict forwarded to
            :class:`~corral.io.remote.RemoteAdapter` (e.g.
            ``{"token": "..."}`` or ``{"key": "...", "secret": "..."}``).
            Only consulted for URL sources; merges with the env / keyring /
            netrc cascade per :func:`corral.io.credentials.resolve_credentials`
            (explicit wins).
        engine: Engine to materialize through. Defaults to the registry
            default (typically the ibis + DuckDB engine).
        scope: Optional dict of :meth:`Package.scope` kwargs applied to
            the loaded package before returning — lets callers chain
            scope inline without an extra ``.scope(...)`` call. Keys are
            forwarded verbatim, so e.g. ``scope={"tables": ["link"]}``
            or ``scope={"bbox": (xmin, ymin, xmax, ymax)}`` work.
        spec: A :class:`~corral.spec.DataPackage` instance, or a
            path to a ``datapackage.json`` to load. When omitted,
            :meth:`Package.from_source` looks for one alongside
            ``source``.
        tables: Optional iterable of table names to partial-load.
        **kwargs: Forwarded to :meth:`Package.from_source` — see that
            method for the full signature.

    Returns:
        A :class:`Package` whose tables are lazy engine expressions.

    Examples:
        >>> from corral import read
        >>> from corral.fixtures import sample
        >>> pkg = read(sample.parquet_dir())
        >>> len(pkg.tables)
        3

        Scope inline — only the ``book`` table is loaded into the
        returned :class:`Package`::

            >>> from corral import read
            >>> from corral.fixtures import sample
            >>> pkg = read(sample.parquet_dir(), scope={"tables": ["book"]})
            >>> pkg.keys()
            ['book']
    """
    package = Package.from_source(
        source,
        engine=engine,
        spec=spec,
        tables=tables,
        format=format,
        credentials=credentials,
        **kwargs,
    )
    if scope:
        package = package.scope(**scope)
    return package


__all__ = [
    "OutOfSyncError",
    "OutOfSyncWarning",
    "Package",
    "Table",
    "read",
]
