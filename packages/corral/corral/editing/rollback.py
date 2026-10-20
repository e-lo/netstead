"""Replay a persisted rollback log to reverse past edits.

Companion to :class:`~corral.editing.session.Session`. Reads the
parquet sidecar the session wrote, filters by the ``to`` selector
(session_id / timestamp / all), and reverses the selected edits in
LIFO order.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow.parquet as pq

from .apply import reverse_edit
from .errors import RollbackError
from .types import Diff, Edit, EditResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.dataset.package import Package


__all__ = ["rollback"]


def rollback(
    package: Package,
    log_path: Path | str,
    *,
    to: str | datetime | None = None,
) -> list[EditResult]:
    r"""Reverse every edit in ``log_path`` matching the ``to`` selector.

    Reads the parquet sidecar written by :class:`Session`, filters per
    ``to``, and reverses the selected edits in LIFO order so dependent
    edits unwind cleanly.

    Args:
        package: The :class:`~corral.dataset.Package` to mutate.
        log_path: Path to the parquet log written by a :class:`Session`.
        to: Selector.

            * ``None`` (default) — reverse every edit.
            * ``str`` — treat as ``session_id``; reverse only that session. Edits
              applied outside a session (logged with a null ``session_id``) are
              never selected this way; an empty string is refused.
            * :class:`datetime` — reverse edits applied strictly after this time.

    Returns:
        The :class:`EditResult`\ s that were reversed (LIFO order).

    Raises:
        RollbackError: ``log_path`` missing or unparseable, or ``to`` of
            the wrong type.

    Examples:
        >>> import tempfile, pathlib
        >>> from corral.dataset import Package, Table
        >>> from corral.editing import Edit, Session, rollback
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> e = IbisEngine()
        >>> pkg = Package.from_tables({"x": Table(name="x", expr=e.from_records([{"a": 1}]), engine=e)})
        >>> with tempfile.TemporaryDirectory() as tmp:
        ...     log = pathlib.Path(tmp) / "history.parquet"
        ...     with Session(pkg, log_path=log) as s:
        ...         _ = s.add_edit(Edit(op="add_rows", table="x", payload={"rows": [{"a": 2}]}))
        ...     _ = rollback(pkg, log)
        ...     pkg["x"].count()
        1
    """
    if to == "":
        raise RollbackError("`to` must be a non-empty session_id (or None for every edit, or a datetime)")
    path = Path(log_path)
    if not path.exists():
        raise RollbackError(f"Rollback log not found at {path!r}")
    try:
        arrow = pq.read_table(path)
    except Exception as exc:  # pragma: no cover - parquet read failure
        raise RollbackError(f"Failed to read rollback log {path!r}: {exc}") from exc

    rows = arrow.to_pylist()
    if not rows:
        return []

    materialised = [_inflate_result(row) for row in rows]
    if to is None:
        selected = materialised
    elif isinstance(to, datetime):
        selected = [r for r in materialised if r.applied_at > to]
    elif isinstance(to, str):
        selected = [r for r in materialised if r.session_id == to]
    else:
        raise RollbackError(f"`to` must be None, a session_id str, or a datetime; got {type(to).__name__}")

    if not selected:
        return []

    reversed_edits: list[EditResult] = []
    for result in sorted(selected, key=lambda r: r.applied_at, reverse=True):
        reverse_edit(package, result)
        reversed_edits.append(result)
    return reversed_edits


# ---------------------------------------------------------------------------
# Helpers — internal per §9
# ---------------------------------------------------------------------------


def _inflate_result(row: dict) -> EditResult:
    """Rebuild an :class:`EditResult` from one log row dict."""
    edit = Edit(
        op=row["op"],
        table=row["table"],
        payload=_decode(row.get("payload_json")),
        metadata=_decode(row.get("metadata_json")) or {},
    )
    diff = Diff(
        edit=edit,
        rows_added=int(row.get("rows_added") or 0),
        rows_removed=int(row.get("rows_removed") or 0),
        rows_changed=int(row.get("rows_changed") or 0),
    )
    return EditResult(
        edit=edit,
        diff=diff,
        rollback_data=_decode(row.get("rollback_json")) or {},
        applied_at=_parse_dt(row.get("applied_at")),
        session_id=row.get("session_id") or None,  # logs written before #147 stored no session as ""
    )


def _decode(blob: str | None) -> dict | list | None:
    """JSON-decode a log column; tolerate None / empty / parse errors."""
    if not blob:
        return None
    try:
        return json.loads(blob)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_dt(value: str | None) -> datetime:
    """Parse an ISO-format timestamp; fall back to ``datetime.min``."""
    if not value:
        return datetime.min
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min
