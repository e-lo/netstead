"""GMNS-specific Python toolkit.

Builds on corral with network semantics, quality rules, editing, and
surfaces (CLI / notebook / API / MCP).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from netstead.network import Network, NetworkError
from netstead.spec import DEFAULT_SPEC, SUPPORTED_SPECS, get_spec_path, load_gmns_spec

if TYPE_CHECKING:
    from corral.engines.base import Engine
    from corral.reports import ValidationReport


def validate(
    source: str | Path | Network,
    *,
    engine: Engine | None = None,
    spec_version: str | None = None,
    schema: bool = True,
    structural: bool = True,
    foreign_keys: bool = True,
    sync_state: bool = True,
    strict: bool = False,
) -> ValidationReport:
    """Validate a GMNS network. Accepts a path/URL or an already-loaded Network.

    Thin wrapper around :meth:`Network.validate` — exists so the
    documented top-level form ``netstead.validate(...)`` works without
    callers needing to load a :class:`Network` first.

    If ``source`` is already a :class:`Network`, calls ``.validate()``
    on it directly. Otherwise loads via :func:`netstead.read` first.

    Args:
        source: A :class:`Network`, or anything :func:`read` accepts
            (local path, URL, package directory).
        engine: Engine to materialize through. Only consulted when
            ``source`` is a path — ignored when it's already a Network.
        spec_version: GMNS spec version to validate against. Only
            consulted when ``source`` is a path.
        schema: Run the per-field schema check pass (types, enums,
            constraints). Default ``True``.
        structural: Run the package-structure pass (required tables,
            file presence). Default ``True``.
        foreign_keys: Run the FK referential-integrity pass. Default
            ``True``.
        sync_state: Stamp + check FK staleness against the
            :class:`~corral.validation.sync_state.DirtyTracker`.
            Default ``True``.
        strict: Treat warnings as errors — caller can ``raise`` on
            any non-clean report. Default ``False``.

    Returns:
        A :class:`~corral.reports.ValidationReport` whose
        ``spec_version`` field is stamped with the GMNS version the
        data was checked against.

    Examples:
        >>> import netstead
        >>> from netstead.fixtures import leavenworth
        >>> report = netstead.validate(leavenworth.csv_dir())
        >>> report.spec_version
        '0.97'
    """
    pass_kwargs = dict(
        schema=schema,
        structural=structural,
        foreign_keys=foreign_keys,
        sync_state=sync_state,
        strict=strict,
    )
    if isinstance(source, Network):
        return source.validate(**pass_kwargs)
    net = read(source, engine=engine, spec_version=spec_version)
    return net.validate(**pass_kwargs)


def read(
    source: str | Path,
    *,
    engine: Engine | None = None,
    spec_version: str | None = None,
    tables: Iterable[str] | None = None,
) -> Network:
    """Load a GMNS network from ``source``. The I/O front door.

    Thin convenience wrapper around :meth:`Network.from_source` — exists
    so the documented top-level form ``netstead.read(...)`` works without
    callers needing to know which class to import.

    Args:
        source: Local path, ``s3://`` / ``https://`` / ``duckdb://`` URL,
            or directory of CSV / Parquet / DuckDB / zipped-CSV files
            containing the GMNS package.
        engine: Engine to materialize through. Defaults to the
            corral default (typically the ibis + DuckDB engine).
        spec_version: GMNS spec version to validate against. Defaults
            to :data:`DEFAULT_SPEC` (currently ``"0.97"``); must be one
            of :data:`SUPPORTED_SPECS`.
        tables: Optional iterable of table names to partial-load.

    Returns:
        A :class:`Network` whose tables are lazy ibis expressions.

    Examples:
        >>> import netstead
        >>> from netstead.fixtures import leavenworth
        >>> net = netstead.read(leavenworth.csv_dir())
        >>> net.links.count()
        339
    """
    return Network.from_source(
        source,
        engine=engine,
        spec_version=spec_version,
        tables=tables,
    )


__all__ = [
    "DEFAULT_SPEC",
    "SUPPORTED_SPECS",
    "Network",
    "NetworkError",
    "get_spec_path",
    "load_gmns_spec",
    "read",
    "validate",
]
