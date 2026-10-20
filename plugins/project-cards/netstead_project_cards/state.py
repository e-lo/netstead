"""The plugin's own nouns: one draft card per network, the cards committed this session, and the open preview.

It lives in the plugin factory's closure (one per Workbench session) and is changed only by the plugin's Action
handlers, after every Host call they make has succeeded: a failing handler's network changes are rolled back by the
Workbench, and this state must not have moved. A handler nested in another Action can succeed and still be rolled
back when its parent fails, which core does not tell the plugin; so each draft change records the marks its
mutations left (:class:`~netstead_project_cards.lower.Mark`), and every handler and ``state()`` first reconciles the
draft with its network (:meth:`CardsState.sync`). It has its own lock, so that ``state()`` and the routes
(:mod:`~netstead_project_cards.routes`) can read it outside the session lock.

The open preview (:class:`Preview`) is checked the same way, by handle: it stands for its base only while both
networks are the ones it was made on.

Known limits, each after an enclosing Action fails once a nested ``project_cards`` Action has succeeded:

- a preview that closes an older one (a second preview) cannot reopen it, so the new copy is rolled back and the
  older preview stays closed;
- a ``project_cards.commit`` is not reconciled: the file stays written and the draft stays reset (the network still
  holds the edits). Core would need a rollback hook for plugins;
- an imported change that applied nothing cannot be reconciled after a parent Action's rollback: it has no marks, so
  it stays in the draft. (``project_cards.add_change`` refuses such a change; ``project_cards.import`` keeps it, as
  part of the card.)
"""

from __future__ import annotations

import re
import threading
import weakref
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .card import DraftCard
from .lower import Applied, Mark, describe
from .types import CHANGE_TYPES, ROADWAY_TYPES, NetworkChange

__all__ = ["CardsState", "Draft", "Entry", "Preview", "Undone", "slug"]


def slug(text: str) -> str:
    """A card id from a project name: lower case, words joined by ``-``.

    >>> slug("I-90 HOV lanes, 2035")
    'i-90-hov-lanes-2035'
    """
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "card"


@dataclass(frozen=True)
class Entry:
    """One change of a draft and the marks of the ``host.mutate`` calls that applied it (undone together, LIFO)."""

    change: NetworkChange
    marks: tuple[Mark, ...]

    def holds(self, lineage: Sequence[str], version: int) -> bool:
        """Whether the network still has every mutation of this change."""
        return all(m.holds(lineage, version) for m in self.marks)


@dataclass(frozen=True)
class Undone:
    """A change ``project_cards.undo_change`` took out of the draft, and the marks its ``host.undo`` calls left."""

    entry: Entry
    marks: tuple[Mark, ...]


@dataclass
class Draft:
    """A network's draft card, its changes (``entries``, in step with ``card.changes``) and the changes undone.

    ``handle`` is a weak reference to the network handle the draft was made on: core reuses a closed network's id,
    and a reopened network is a new handle. ``undone`` keeps undone changes so that a rolled-back undo (an
    ``undo_change`` nested in an Action that failed) puts its change back. A later change does not clear it: an
    enclosing Action that undid a change and then added one rolls both back together, and the undone change must
    come back.
    """

    handle: weakref.ref[Any]
    card: DraftCard = field(default_factory=DraftCard)
    entries: list[Entry] = field(default_factory=list)
    undone: list[Undone] = field(default_factory=list)

    def reconcile(self, lineage: Sequence[str], version: int) -> None:
        """Make the draft describe the network: put back changes whose undo was rolled back, drop changes that were.

        A failed Action's rollback cuts its lines off the lineage, so the undos it rolled back are the newest ones.
        """
        restored = False
        while self.undone and not all(m.holds(lineage, version) for m in self.undone[-1].marks):
            self.entries.append(self.undone.pop().entry)
            restored = True
        kept = [e for e in self.entries if e.holds(lineage, version)]
        if restored or len(kept) != len(self.entries):
            self.entries = kept
            self.undone = [u for u in self.undone if u.entry.holds(lineage, version)]
            self.card = self.card.model_copy(update={"changes": [e.change for e in kept]})


@dataclass(frozen=True)
class Preview:
    """The open preview: its network, the base it was derived from, and the base's version when it was made.

    ``copy`` and ``base_handle`` are weak references to the two network handles (core reuses a closed network's id,
    and a reopened network is a new handle). The preview is ``stale`` once the base's version differs from
    ``base_version``: a change applied to the base since (``project_cards.add_change`` while previewing, an undo, an
    outside edit) is not on the copy, so the UI can offer to preview again.
    """

    net_id: str
    copy: weakref.ref[Any]
    base: str
    base_handle: weakref.ref[Any]
    base_version: int
    describe: str

    def is_copy(self, handle: Any | None) -> bool:
        """Whether ``handle`` (the open network with id ``net_id``) is this preview's network."""
        return handle is not None and handle is self.copy()

    def stands_for_base(self, copy: Any | None, base: Any | None) -> bool:
        """Whether the preview network ``copy`` still stands for ``base`` (the open networks with those ids).

        Both must be the handles the preview was made on, and the copy must still name the base as ``derived_from``
        (core renames it when the base closes).
        """
        return (
            self.is_copy(copy)
            and base is not None
            and base is self.base_handle()
            and getattr(copy, "derived_from", None) == self.base
        )

    def report(self, base: Any) -> dict[str, Any]:
        """JSON-safe for ``state()``; ``base`` is the base's open handle."""
        stale = base.version != self.base_version
        return {"net_id": self.net_id, "base": self.base, "describe": self.describe, "stale": stale}


class CardsState:
    """Drafts per network, committed cards and the open preview, behind one lock.

    Every read and write of the drafts and the preview goes through a method that holds the lock; none of them calls
    into the session (a handler holds the session lock and then this one, so the other order could deadlock).
    """

    def __init__(self) -> None:
        """Start with no drafts, no committed cards and no preview."""
        self._lock = threading.RLock()
        self._drafts: dict[str, Draft] = {}
        self.committed: list[dict[str, Any]] = []
        self._preview: Preview | None = None

    def preview(self) -> Preview | None:
        """The open preview (``None`` when there is none); a frozen record, safe to read outside the lock."""
        with self._lock:
            return self._preview

    def set_preview(self, preview: Preview) -> None:
        """Record ``preview`` as the open preview (it replaces any older one, which the caller has closed)."""
        with self._lock:
            self._preview = preview

    def clear_preview(self, preview: Preview) -> None:
        """Forget ``preview``, unless another preview has replaced it meanwhile."""
        with self._lock:
            if self._preview is preview:
                self._preview = None

    def sync(self, net_id: str, handle: Any) -> None:
        """Bring ``net_id``'s draft in line with its network ``handle`` (read before the call, outside the lock).

        A draft made on another handle (a closed network whose id was reused) is dropped; otherwise changes a
        rolled-back Action left behind are dropped, and changes whose undo was rolled back are put back.
        """
        lineage, version = list(handle.lineage), handle.version
        with self._lock:
            d = self._drafts.get(net_id)
            if d is None:
                return
            if d.handle() is not handle:
                del self._drafts[net_id]
                return
            d.reconcile(lineage, version)

    def last(self, net_id: str) -> Entry | None:
        """The last change of ``net_id``'s draft (``None`` when it has none)."""
        with self._lock:
            d = self._drafts.get(net_id)
            return d.entries[-1] if d is not None and d.entries else None

    def card(self, net_id: str) -> DraftCard | None:
        """A copy of ``net_id``'s draft card (``None`` when it has no changes)."""
        with self._lock:
            d = self._drafts.get(net_id)
            return d.card.model_copy(deep=True) if d is not None and d.entries else None

    def extend(
        self, net_id: str, handle: Any, applied: Sequence[Applied], *, name_from: DraftCard | None = None
    ) -> int:
        """Append applied changes to ``net_id``'s draft (made on ``handle``); return its change count.

        With ``name_from``, an empty, unnamed draft takes that card's project, tags, dependencies and notes first.
        """
        with self._lock:
            d = self._drafts.get(net_id)
            if d is None or d.handle() is not handle:
                d = self._drafts[net_id] = Draft(weakref.ref(handle))
            if name_from is not None and not d.entries and not d.card.project:
                d.card = d.card.model_copy(
                    update={
                        "project": name_from.project,
                        "tags": list(name_from.tags),
                        "dependencies": name_from.dependencies,
                        "notes": name_from.notes,
                    }
                )
            d.entries.extend(Entry(a.change, tuple(a.marks)) for a in applied)
            d.card = d.card.model_copy(update={"changes": [e.change for e in d.entries]})
            return len(d.entries)

    def pop(self, net_id: str, entry: Entry, undo_marks: Sequence[Mark]) -> int:
        """Take ``entry`` (the draft's last change, which the caller has undone) out; return the change count."""
        with self._lock:
            d = self._drafts[net_id]
            if not d.entries or d.entries[-1] is not entry:
                raise RuntimeError(f"{net_id}'s draft changed while its last change was being undone")
            d.entries.pop()
            d.undone.append(Undone(entry, tuple(undo_marks)))
            d.card = d.card.model_copy(update={"changes": [e.change for e in d.entries]})
            return len(d.entries)

    def project(self, net_id: str) -> str:
        """The project name of ``net_id``'s draft (``""`` when unnamed)."""
        with self._lock:
            d = self._drafts.get(net_id)
            return d.card.project if d is not None else ""

    def commit(self, net_id: str, card: DraftCard, path: str) -> str:
        """Record ``card`` as committed to ``path``, start a new empty draft, and return the card's id."""
        with self._lock:
            taken = {c["card_id"] for c in self.committed}
            base = card_id = slug(card.project)
            n = 2
            while card_id in taken:
                card_id, n = f"{base}-{n}", n + 1
            self.committed.append(
                {
                    "card_id": card_id,
                    "net_id": net_id,
                    "path": path,
                    "project": card.project,
                    "changes": len(card.changes),
                }
            )
            self._drafts.pop(net_id, None)
            return card_id

    def snapshot(self, network: Callable[[str], Any | None]) -> dict[str, Any]:
        """JSON-safe state for ``/api/state`` (``plugins.project_cards``), each draft reconciled with its network.

        ``network(net_id)`` is the open network's handle, or ``None``: the drafts of a closed network are dropped (its
        pending edits went with it, so its draft can never be committed or undone), and so is a preview whose network
        or base is gone (:meth:`Preview.stands_for_base`). It is called before the lock is taken, never under it.
        """
        with self._lock:
            ids = list(self._drafts)
            preview = self._preview
        handles = {net_id: network(net_id) for net_id in ids}
        base = network(preview.base) if preview is not None else None
        standing = preview is not None and preview.stands_for_base(network(preview.net_id), base)
        for net_id, handle in handles.items():
            if handle is not None:
                self.sync(net_id, handle)
        with self._lock:
            for net_id, handle in handles.items():
                if handle is None:
                    self._drafts.pop(net_id, None)
            if preview is not None and self._preview is preview and not standing:
                self._preview = None
            reported = preview.report(base) if standing and self._preview is preview else None
            drafts = {
                net_id: {
                    "project": d.card.project,
                    "count": len(d.entries),
                    "changes": [
                        {"type": e.change.type, "describe": describe(e.change), "steps": len(e.marks)}
                        for e in d.entries
                    ],
                }
                for net_id, d in self._drafts.items()
                if d.entries or d.card.project
            }
            return {
                "drafts": drafts,
                "committed": [dict(c) for c in self.committed],
                "preview": reported,
                "change_types": list(CHANGE_TYPES),
                "supported": sorted(ROADWAY_TYPES),
            }
