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

    def __init__(self, issued: int = 0) -> None:
        """Start empty; the first entry's ``seq`` is ``issued + 1`` (a rolled-back ledger's count, never reused)."""
        self.entries: list[PendingEdit] = []
        self.check = CheckResult()
        self.diverged = False  # an undo reverted a saved edit: the open network differs from the saved copy
        #: The network version the ledger last saw (after its newest add or pop). When the network's version
        #: differs, something changed it without going through the ledger, so its results can't be reversed.
        self.version: int | None = None
        #: Each table a pending edit touched -> the expression it held after the ledger's newest add or pop
        #: (compared by identity). A reverse in place keeps the version but swaps the expression, so this
        #: catches what the version check cannot.
        self.exprs: dict[str, Any] = {}
        self._seq = issued

    @property
    def issued(self) -> int:
        """The newest ``seq`` handed out (``0``: none). Seqs only grow: an entry's ``seq`` is its stable id."""
        return self._seq

    def reserve(self, issued: int) -> None:
        """Never hand out a ``seq`` up to ``issued`` again (a rolled-back Action's edits had them)."""
        self._seq = max(self._seq, issued)

    def copy(self) -> EditLedger:
        """A snapshot (entries copied one level down), for the session's rollback of a failed Action."""
        other = EditLedger()
        other.entries = [replace(e) for e in self.entries]
        other.check, other.diverged, other.version, other._seq = self.check, self.diverged, self.version, self._seq
        other.exprs = dict(self.exprs)
        return other

    def add(
        self,
        source: str,
        note: str,
        version: int,
        results: list[EditResult],
        touched: dict[str, set[Any]],
        exprs: dict[str, Any],
    ) -> PendingEdit:
        """Record one applied mutation and return it; ``exprs`` maps each table it changed to its new expression."""
        self._seq += 1
        entry = PendingEdit(self._seq, source, note, version, list(results), touched)
        self.entries.append(entry)
        self.version = version
        self.exprs.update(exprs)
        return entry

    def last(self) -> PendingEdit | None:
        """The newest entry (``None`` when there is none)."""
        return self.entries[-1] if self.entries else None

    def pop(self, version: int, exprs: dict[str, Any]) -> PendingEdit:
        """Drop the newest entry (after its edits were reverted, leaving the network at ``version``); return it.

        ``exprs`` maps each table the revert changed to its new expression.
        """
        entry = self.entries.pop()
        if entry.saved:
            self.diverged = True
        self.version = version
        self.exprs.update(exprs)
        return entry

    def unseen_change(self, version: int, exprs: dict[str, Any]) -> bool:
        """Whether the network changed without the ledger since its newest add or pop.

        It did if its ``version`` moved, or if one of ``exprs`` (tables to look at, with their expressions now)
        is not the expression the ledger recorded for that table.
        """
        if self.version != version:
            return True
        return any(name in self.exprs and self.exprs[name] is not expr for name, expr in exprs.items())

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
            "check_failed": self.check.failed,
            "last_source": last.source if last else None,
        }
