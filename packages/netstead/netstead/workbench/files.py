"""Server-side file browser for the Open / Import wizard: list a folder, tag what each entry can be opened as.

Listing never leaves ``io.allowed_roots`` (see :mod:`netstead.workbench.paths`), skips hidden entries, and
reads no file contents: a kind is decided from names alone, so browsing a large folder stays cheap.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from netstead.config import Settings
from netstead.overture.layout import is_local_snapshot

from .paths import allowed_roots, resolve_allowed

__all__ = ["MAX_ENTRIES", "detect_kind", "list_dir", "open_target"]

#: Cap on entries returned for one folder (the response says ``truncated`` when it is hit).
MAX_ENTRIES = 2000

_GMNS_TABLE_FILES = ("link.csv", "link.parquet")


def detect_kind(path: Path) -> str | None:
    """What ``path`` can be opened as, or ``None`` for a plain folder / unrecognized file.

    Kinds: ``gmns`` (a folder with ``link.csv``/``link.parquet`` or a ``datapackage.json``),
    ``overture`` (a folder holding ``segment.parquet`` + ``connector.parquet``), ``zip``,
    ``duckdb``, ``datapackage`` (the ``datapackage.json`` file itself), ``osm`` (``.osm`` XML),
    and ``json`` (a candidate Overpass JSON export).
    """
    if path.is_dir():
        if is_local_snapshot(path):
            return "overture"
        if (path / "datapackage.json").is_file() or any((path / n).is_file() for n in _GMNS_TABLE_FILES):
            return "gmns"
        return None
    name = path.name.lower()
    if name == "datapackage.json":
        return "datapackage"
    return {".zip": "zip", ".duckdb": "duckdb", ".osm": "osm", ".json": "json"}.get(path.suffix.lower())


def open_target(path: Path, kind: str | None) -> str:
    """The source string to hand to ``OpenNetwork`` / ``BuildNetwork`` for an entry of ``kind``."""
    return str(path.parent if kind == "datapackage" else path)


def _entry(path: Path) -> dict[str, Any]:
    kind = detect_kind(path)
    return {
        "name": path.name,
        "path": str(path),
        "is_dir": path.is_dir(),
        "kind": kind,
        "target": open_target(path, kind),
    }


def list_dir(path: str | None, settings: Settings) -> dict[str, Any]:
    """List ``path`` (or, when ``None``, the allowed roots themselves) as tagged entries, folders first.

    Raises:
        PathNotAllowed: ``path`` resolves outside ``io.allowed_roots``.
        FileNotFoundError: ``path`` does not exist.
        NotADirectoryError: ``path`` is a file.
    """
    if path is None:
        roots = [r for r in allowed_roots(settings) if r.is_dir()]
        return {"path": None, "parent": None, "entries": [_entry(r) for r in roots], "truncated": False}
    folder = resolve_allowed(path, settings)
    if not folder.exists():
        raise FileNotFoundError(f"no such folder: {path}")
    if not folder.is_dir():
        raise NotADirectoryError(f"not a folder: {path}")
    children = sorted(
        (p for p in folder.iterdir() if not p.name.startswith(".")),
        key=lambda p: (not p.is_dir(), p.name.lower()),
    )
    parent = folder.parent if folder not in allowed_roots(settings) else None
    return {
        "path": str(folder),
        "parent": str(parent) if parent is not None else None,
        "entries": [_entry(p) for p in children[:MAX_ENTRIES]],
        "truncated": len(children) > MAX_ENTRIES,
    }
