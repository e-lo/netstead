"""Generic Edit/Diff/Session/Rollback framework — no domain semantics here.

This package is the framework half of architecture §6.4: ``Edit`` /
``Diff`` / ``EditResult`` value types, the :class:`Session` context
manager (atomic batch + chronological rollback log), and the
:func:`rollback` log-replay function. Domain ops
(``simplify_geometry``, ``merge_close_nodes``, ...) live in
``netstead.clean`` and compose with this framework by emitting
:class:`Edit` objects against an open :class:`Session`.

Public surface:

* :class:`Edit` — one atomic mutation (op + table + payload).
* :class:`Diff` — before/after delta with bounded row samples.
* :class:`EditResult` — applied edit + rollback blob + timestamp.
* :class:`Session` — context manager; atomic on exception; persists
  the log to a parquet sidecar on commit.
* :func:`rollback` — replay a persisted log to undo past edits.
* :func:`affected_rows` — the rows an applied edit touched.
* :class:`EditingError` (+ subclasses) — typed exceptions.
"""

from .apply import affected_rows
from .errors import (
    EditingError,
    InvalidPayload,
    RollbackError,
    UnknownTable,
    UnsupportedEditOp,
)
from .rollback import rollback
from .session import Session
from .types import Diff, Edit, EditResult

__all__ = [
    "Diff",
    "Edit",
    "EditResult",
    "EditingError",
    "InvalidPayload",
    "RollbackError",
    "Session",
    "UnknownTable",
    "UnsupportedEditOp",
    "affected_rows",
    "rollback",
]
