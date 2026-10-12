"""Pending edits: every mutation of an open network, kept until it is closed.

The Edits panel lists them, :meth:`~netstead.workbench.session.Session.undo_last` reverts the newest, and the
dirty badge counts the ones no saved copy holds yet. Each entry records who made it (``source``: the
Workbench, Python, or a plugin's id) so an undo never reverses someone else's change. An entry keeps the corral
results of its edits: they are what :meth:`~netstead.workbench.session.Session.revert` reverses.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from corral.editing import EditResult

from .editcheck import CheckResult

__all__ = ["CORE_SOURCE", "PYTHON_SOURCE", "EditLedger", "PendingEdit"]

#: The source of edits made by a core Action (plugins use their id).
CORE_SOURCE = "workbench"
#: The source of a ``Session.mutate`` called outside any Action (Part 1 records it as ``python.mutate``).
PYTHON_SOURCE = "python"


@dataclass
class PendingEdit:
    """One applied mutation: who made it, why, the version it produced, and the corral results that revert it."""

    seq: int
    source: str
    note: str
    version: int
    results: list[EditResult] = field(repr=False)
    touched: dict[str, set[Any]] = field(default_factory=dict)
    saved: bool = False

    def view(self) -> dict[str, Any]:
        """JSON-safe: what the Edits panel shows (rows per table counts added, removed and changed rows)."""
        rows: dict[str, int] = {}
        for r in self.results:
            n = r.diff.rows_added + r.diff.rows_removed + r.diff.rows_changed
            rows[r.edit.table] = rows.get(r.edit.table, 0) + n
        return {
            "seq": self.seq,
            "source": self.source,
            "note": self.note,
            "version": self.version,
            "rows": rows,
            "saved": self.saved,
        }


class EditLedger:
    """One network's pending edits (newest last) and the live check over every key they touched."""

    def __init__(self) -> None:
        """Start empty."""
        self.entries: list[PendingEdit] = []
        self.check = CheckResult()
        self.diverged = False  # an undo reverted a saved edit: the open network differs from the saved copy
        self._seq = 0

    def copy(self) -> EditLedger:
        """A snapshot (entries copied one level down), for the session's rollback of a failed Action."""
        other = EditLedger()
        other.entries = [replace(e) for e in self.entries]
        other.check, other.diverged, other._seq = self.check, self.diverged, self._seq
        return other

    def add(
        self, source: str, note: str, version: int, results: list[EditResult], touched: dict[str, set[Any]]
    ) -> PendingEdit:
        """Record one applied mutation and return it."""
        self._seq += 1
        entry = PendingEdit(self._seq, source, note, version, list(results), touched)
        self.entries.append(entry)
        return entry

    def last(self) -> PendingEdit | None:
        """The newest entry (``None`` when there is none)."""
        return self.entries[-1] if self.entries else None

    def pop(self) -> PendingEdit:
        """Drop the newest entry (after its edits were reverted) and return it."""
        entry = self.entries.pop()
        if entry.saved:
            self.diverged = True
        return entry

    def touched(self) -> dict[str, set[Any]]:
        """Every key any pending entry touched: what the live check covers."""
        out: dict[str, set[Any]] = {}
        for entry in self.entries:
            for table, keys in entry.touched.items():
                out.setdefault(table, set()).update(keys)
        return out

    def mark_saved(self) -> None:
        """A copy holding every pending edit was written."""
        for entry in self.entries:
            entry.saved = True
        self.diverged = False

    def summary(self) -> dict[str, Any]:
        """JSON-safe counts for the session state (the dirty badges read ``dirty`` and ``unsaved``)."""
        unsaved = sum(not e.saved for e in self.entries)
        last = self.last()
        return {
            "pending": len(self.entries),
            "unsaved": unsaved,
            "dirty": bool(unsaved or self.diverged),
            "warnings": len(self.check.warnings),
            "truncated": self.check.truncated,
            "last_source": last.source if last else None,
        }
