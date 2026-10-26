"""Shared helpers for the per-command modules in :mod:`netstead.cli.commands`.

Kept thin on purpose: each helper here is used by 2+ command modules. Per-command
helpers live with the command (e.g. doctor's ``_check_*`` functions are in
``commands/doctor.py``, the spec-diff field walker lives in ``commands/spec.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from types import ModuleType

    from corral.editing import Session
    from corral.engines import Engine

    from netstead import Network
    from netstead.scope import NetworkScope

__all__ = [
    "list_index_sidecars",
    "load_network_and_session_cls",
    "resolve_engine",
    "save_index_sidecar",
    "summarise_results",
    "summarise_scope",
    "write_network",
]


def resolve_engine(name: str | None) -> Engine:
    """Thin CLI wrapper around :func:`corral.engines.resolve_engine`.

    Converts the public resolver's :class:`ValueError` (raised on an
    unknown engine name) into :class:`typer.BadParameter` so the CLI
    exits with a clean non-zero + help message instead of a traceback.
    The actual ``name → Engine`` logic lives in :mod:`corral.engines`
    so both CLIs share it.
    """
    from corral.engines import resolve_engine as _resolve

    try:
        return _resolve(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def load_network_and_session_cls(source: Path, engine_name: str | None = None) -> tuple[Network, type[Session]]:
    """Return ``(Network, Session class)``.

    :class:`Session` lives in corral, imported here so each ``clean``
    command stays one expression long.

    ``engine_name`` follows the resolves to the DuckDB engine, same as
    :func:`resolve_engine`. Callers can override the default ibis engine
    to dodge backend-specific edge cases (e.g. duckdb refusing null-typed
    columns when re-materializing via ``engine.from_records`` during a
    ``replace_table`` edit on a fixture that carries empty string
    columns typed as null).
    """
    from corral.editing import Session

    from netstead import Network

    return Network.from_source(source, engine=resolve_engine(engine_name)), Session


def summarise_results(
    results,
    *,
    op: str,
    source: Path,
    dest: Path | None,
    dry_run: bool,
) -> dict[str, object]:
    """Render an :class:`EditResult` (or list thereof) into the CLI's standard summary dict."""
    if not isinstance(results, list):
        results = [results]
    return {
        "op": op,
        "source": str(source),
        "dest": str(dest) if dest is not None else str(source),
        "dry_run": dry_run,
        "edits": [
            {
                "table": r.edit.table,
                "op": r.edit.op,
                "rows_added": r.diff.rows_added,
                "rows_removed": r.diff.rows_removed,
                "rows_changed": r.diff.rows_changed,
            }
            for r in results
        ],
    }


def summarise_scope(scope: NetworkScope) -> dict[str, object]:
    """Render a :class:`NetworkScope` into the CLI's standard summary dict."""
    return {
        "node_count": len(scope.node_ids),
        "link_count": len(scope.link_ids),
        "node_ids": sorted(scope.node_ids),
        "link_ids": sorted(scope.link_ids),
        "provenance": scope.provenance,
    }


def write_network(net: Network, *, source: Path, dest: Path | None) -> None:
    """Write ``net`` to ``dest``, or back over ``source`` when ``dest`` is None.

    Uses :meth:`Package.write`; the format is inferred from the
    target's extension, falling back to ``parquet`` for directory
    targets — matches the read-path convention so a round-trip lands
    the same logical package. The one exception is an in-place write
    of a directory source, which keeps the format the tables were read
    from (a CSV directory stays CSV) instead of silently becoming
    parquet. :meth:`Package.write` stages the new files beside the
    target and swaps them in once complete, so the tables, which still
    read from ``source``, are never read after their files are gone.

    Suppresses :class:`OutOfSyncWarning` (raised by the dirty tracker
    on tables we just edited). The whole point of these CLI ops is
    "edit then write" — the dirty flag is expected, and the warning
    would be promoted to an error under the project's
    ``filterwarnings = ["error"]`` pytest config.
    """
    import warnings

    from corral.dataset.package import OutOfSyncWarning

    target = dest if dest is not None else source
    format = _directory_source_format(net) if dest is None and target.is_dir() else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", OutOfSyncWarning)
        net.write(target, format=format, overwrite=target.exists())


def _directory_source_format(net: Network) -> str | None:
    """The directory format every on-disk table of ``net`` was read from, else ``None``.

    Tables built in memory (no source) carry no format and are ignored;
    an edit keeps the format of the table it changes. ``None`` (unknown,
    mixed, or not a directory format) lets :meth:`Package.write` apply
    its default.
    """
    formats = {table.format for table in net.tables.values()} - {None}
    if len(formats) == 1 and (only := formats.pop()) in {"csv", "parquet"}:
        return only
    return None


def list_index_sidecars(source: Path) -> list[Path]:
    """Return sorted sidecar parquet files for the network at ``source`` (empty list when none)."""
    sidecar_dir = source.parent / "_netstead_indexes"
    if not sidecar_dir.is_dir():
        return []
    return sorted(sidecar_dir.glob(f"{source.stem}.*.parquet"))


def save_index_sidecar(
    indexes: ModuleType,
    source: Path,
    net: Network,
    kind: str,
    index_obj,
    *,
    kind_target: str,
) -> Path:
    """Hash the relevant source table(s), compute the sidecar path, write the index."""
    from corral.validation import hash_table

    engine = net.engine
    assert engine is not None, "a network read from a source always carries its engine"
    if kind_target == "link":
        content_hash = hash_table(net.links.expr, engine)
    else:
        # graph index keys off both links + nodes — combine the digests
        # by hashing the concatenation; only the first 8 chars land in
        # the filename anyway (see cache_path docstring).
        import hashlib

        link_h = hash_table(net.links.expr, engine)
        node_h = hash_table(net.nodes.expr, engine)
        content_hash = hashlib.sha256(f"{link_h}::{node_h}".encode()).hexdigest()
    path = indexes.cache_path(str(source), kind, content_hash)
    indexes.save_cached(path, index_obj)
    return path
