"""The plugin's own nouns: drafts, committed and applied cards, the open catalog and the open preview.

One draft card per network; the cards committed or applied this session; the open catalog
(:mod:`~netstead_project_cards.catalog`); the open preview.

It lives in the plugin factory's closure (one per Workbench session) and is changed only by the plugin's Action
handlers, after every Host call they make has succeeded: a failing handler's network changes are rolled back by the
Workbench, and this state must not have moved. A handler nested in another Action can succeed and still be rolled
back when its parent fails. So each draft change records the pending edits its mutations made (their seqs,
:class:`~netstead_project_cards.lower.Mark`), and every handler and ``state()`` first reconciles the draft with its
network's pending edits (``host.pending_edits``, :meth:`CardsState.sync`). A commit, which changes no network, puts
its file and the draft back from a ``host.on_rollback`` callback instead (:meth:`CardsState.commit`). It has its own
lock, so that ``state()`` and the routes (:mod:`~netstead_project_cards.routes`) can read it outside the session lock.

The open preview (:class:`Preview`) is checked the same way, by handle: it stands for its base only while both
networks are the ones it was made on.

Known limits, each after an enclosing Action fails once a nested ``project_cards`` Action has succeeded:

- a preview that closes an older one (a second preview) cannot reopen it, so the new copy is rolled back and the
  older preview stays closed;
- an imported change that applied nothing cannot be reconciled after a parent Action's rollback: it has no marks, so
  it stays in the draft. (``project_cards.add_change`` refuses such a change; ``project_cards.import`` keeps it, as
  part of the card.)
"""

from __future__ import annotations

import re
import threading
import weakref
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, Literal

from .card import DraftCard
from .catalog import Catalog, CatalogEntry
from .lower import Applied, Mark, Pending, describe
from .types import CHANGE_TYPES, ROADWAY_TYPES, NetworkChange

__all__ = [
    "FILE_VERBS",
    "AppliedCard",
    "CardsState",
    "Draft",
    "Entry",
    "Preview",
    "Undone",
    "session_row",
    "slug",
    "timestamp",
]

#: What the session list says for each catalog file Action (its record's ``action``): the verb and how ``to`` reads.
FILE_VERBS = {
    "renamed": ("Renamed", "{card_id} to {to}"),
    "duplicated": ("Duplicated", "{card_id} as {to}"),
    "deleted": ("Deleted", "{card_id} (moved to {to})"),
    "restored": ("Restored", "{card_id}"),
    "updated": ("Edited the metadata of", "{card_id} ({to})"),
}


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

    def holds(self, pending: Pending) -> bool:
        """Whether the network still has every mutation of this change (see :meth:`Mark.holds`)."""
        return all(m.holds(pending) for m in self.marks)

    def back(self, pending: Pending) -> bool:
        """Whether an undone change is back on the network: the undo that took it out was rolled back."""
        return any(m.back(pending) for m in self.marks)


@dataclass(frozen=True)
class Undone:
    """A change ``project_cards.undo_change`` took out of the draft, and the drafts' generation its undo was given."""

    entry: Entry
    popped: int  # ``CardsState.generation`` after this undo

    def back(self, pending: Pending) -> bool:
        """Whether its undo was rolled back, by ``pending`` (a read from before the undo lists its edits)."""
        return self.popped <= pending.generation and self.entry.back(pending)


@dataclass
class Draft:
    """A network's draft card, its changes (``entries``, in step with ``card.changes``) and the changes undone.

    ``handle`` is a weak reference to the network handle the draft was made on: core reuses a closed network's id,
    and a reopened network is a new handle (whose edit seqs start again). ``undone`` keeps undone changes (newest
    last) so that a rolled-back undo (an ``undo_change`` nested in an Action that failed) puts its change back. A
    later change does not clear it: an enclosing Action that undid a change and then added one rolls both back
    together, and the undone change must come back.
    """

    handle: weakref.ref[Any]
    card: DraftCard = field(default_factory=DraftCard)
    entries: list[Entry] = field(default_factory=list)
    undone: list[Undone] = field(default_factory=list)
    source: dict[str, str] | None = None  # ``{card_id, path}``: the catalog card the draft was loaded from (Edit)
    detached: dict[str, str] | None = None  # the ``source`` a deleted file left behind, which a restore gives back

    def reconcile(self, pending: Pending) -> None:
        """Make the draft describe the network: put back changes whose undo was rolled back, drop changes that were.

        ``pending`` is this plugin's pending edits on the network, read at one moment. Core never reuses a seq, so an
        undone change whose edits are pending again had its undo rolled back; one whose edits stay gone was undone, or
        was itself rolled back with its undo, and never comes back. A change whose edits are newer than the read
        (``pending.issued``) is left as it is, and so is an undo newer than the read (``pending.generation``). Undos are
        taken newest first, so putting the changes back newest first restores the draft's order.
        """
        back = [u for u in reversed(self.undone) if u.back(pending)]
        kept = [e for e in [*self.entries, *(u.entry for u in back)] if e.holds(pending)]
        if back or len(kept) != len(self.entries):
            self.entries = kept
            self.undone = [u for u in self.undone if not any(u is b for b in back)]
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


@dataclass(frozen=True)
class AppliedCard:
    """A card this session put on a network: committed from the draft, or applied from the catalog.

    ``edit_ids`` are the seqs of the ``host.mutate`` calls that applied its changes (a committed card's are its
    draft's); ``applied_at`` is an ISO timestamp (UTC).
    """

    card_id: str
    net_id: str
    path: str
    project: str
    applied_at: str
    changes: int
    source: Literal["committed", "catalog"]
    edit_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe, for ``state()`` and the summary."""
        return {**asdict(self), "edit_ids": list(self.edit_ids)}


def session_row(entry: dict[str, Any]) -> dict[str, Any]:
    """One session-log entry as the panel's "This session" list shows it: ``{kind, verb, text, detail, card_id, at}``.

    ``kind`` is ``saved``, ``applied``, or the catalog file Action (``renamed``, ``duplicated``, ``deleted``,
    ``restored``, ``updated``); ``text`` starts with the verb.

    >>> session_row({"kind": "saved", "card_id": "q", "path": "/cards/q.yml", "net_id": "n", "changes": 2, "at": "t"})
    {'kind': 'saved', 'verb': 'Saved', 'text': 'Saved q.yml', 'detail': '2 change(s) from n', 'card_id': 'q', 'at': 't'}
    >>> session_row({"kind": "file", "action": "renamed", "card_id": "a.yml", "to": "b.yml", "at": "t"})["text"]
    'Renamed a.yml to b.yml'
    """
    kind = entry["kind"]
    if kind == "saved":
        verb, text = "Saved", f"Saved {PurePath(entry['path']).name}"
        detail = f"{entry['changes']} change(s) from {entry['net_id']}"
    elif kind == "applied":
        verb, text = "Applied", f"Applied {entry['card_id']} to {entry['net_id']}"
        detail = f"{entry['changes']} change(s)"
    else:
        kind = entry["action"]
        verb, pattern = FILE_VERBS.get(kind, (kind.capitalize(), "{card_id}"))
        text, detail = f"{verb} {pattern.format(card_id=entry['card_id'], to=entry['to'])}", ""
    return {"kind": kind, "verb": verb, "text": text, "detail": detail, "card_id": entry["card_id"], "at": entry["at"]}


def timestamp() -> str:
    """Now, as an ISO timestamp in UTC without microseconds (an :class:`AppliedCard`'s ``applied_at``)."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class CardsState:
    """Drafts per network, committed and applied cards, the open catalog and the open preview, behind one lock.

    Every read and write of the drafts and the preview goes through a method that holds the lock; none of them calls
    into the session (a handler holds the session lock and then this one, so the other order could deadlock).
    """

    def __init__(self) -> None:
        """Start with no drafts, no committed or applied cards, no catalog and no preview."""
        self._lock = threading.RLock()
        self._drafts: dict[str, Draft] = {}
        self.committed: list[dict[str, Any]] = []
        self.applied: list[AppliedCard] = []
        self._catalog: Catalog | None = None
        self._preview: Preview | None = None
        self.files: list[dict[str, Any]] = []  # catalog files changed this session: ``{action, card_id, to, at}``
        self.trash: list[dict[str, str]] = []  # cards deleted this session, newest last: ``{card_id, path, trash}``
        #: What this session did with cards, oldest first: a card saved (committed), applied (from the catalog) or a
        #: catalog file changed. Each is ``{kind, card_id, at, ...}``; :meth:`session_rows` reads it newest first.
        self._log: list[dict[str, Any]] = []
        self._applied_log: dict[int, dict[str, Any]] = {}  # ``id(AppliedCard)`` -> its log entry (catalog cards)
        self._undoing: set[str] = set()  # networks whose draft's last change is being undone right now
        self._generation = 0  # undos so far (:meth:`pop`): a pending-edits read is trusted only for undos before it

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

    def catalog(self) -> Catalog | None:
        """The open catalog (``None`` when none is open). Its entries are shared: read them, change them here."""
        with self._lock:
            return self._catalog

    def set_catalog(self, catalog: Catalog | None) -> None:
        """Make ``catalog`` the open one (``None`` closes it); every entry's applicability starts unknown."""
        with self._lock:
            self._catalog = catalog

    def set_applicability(self, entry: CatalogEntry, result: dict[str, Any] | None) -> None:
        """Record ``result`` on ``entry`` (``None``: unknown again), unless the catalog was replaced meanwhile."""
        with self._lock:
            if self._catalog is not None and any(c is entry for c in self._catalog.cards):
                entry.applicability = result

    def unchecked(self, limit: int) -> list[CatalogEntry]:
        """Up to ``limit`` catalog entries whose applicability is not known yet."""
        with self._lock:
            if self._catalog is None:
                return []
            return [c for c in self._catalog.cards if c.applicability is None][:limit]

    def record_applied(self, applied: AppliedCard) -> Callable[[], None]:
        """Append ``applied`` to the applied cards; return the undo (for ``host.on_rollback``) that forgets it."""
        with self._lock:
            self.applied.append(applied)
            if applied.source == "catalog":
                entry = {
                    "kind": "applied",
                    "card_id": applied.card_id,
                    "net_id": applied.net_id,
                    "path": applied.path,
                    "changes": applied.changes,
                    "at": applied.applied_at,
                }
                self._log.append(entry)
                self._applied_log[id(applied)] = entry

        def undo() -> None:
            with self._lock:
                self.applied = [a for a in self.applied if a is not applied]
                self._unlog(self._applied_log.pop(id(applied), None))

        return undo

    def session_rows(self, apply_enabled: bool = False) -> list[dict[str, Any]]:
        """What this session did with cards, newest first (:func:`session_row`); applied cards only with apply on."""
        with self._lock:
            return self._session_rows(apply_enabled)

    def _session_rows(self, apply_enabled: bool) -> list[dict[str, Any]]:
        return [session_row(e) for e in reversed(self._log) if apply_enabled or e["kind"] != "applied"]

    def _unlog(self, entry: dict[str, Any] | None) -> None:
        """Take ``entry`` out of the session log (nothing for ``None``). Call it under the lock."""
        if entry is not None:
            self._log = [e for e in self._log if e is not entry]

    def applied_card(self, net_id: str, path: str) -> AppliedCard | None:
        """The newest card from the file at ``path`` that this session put on ``net_id`` (and still has there)."""
        with self._lock:
            return next((a for a in reversed(self.applied) if a.net_id == net_id and a.path == path), None)

    def last_catalog_card(self, net_id: str) -> AppliedCard | None:
        """The most recently applied catalog card (source ``"catalog"``) still recorded on ``net_id``, if any.

        A committed card (source ``"committed"``) is never returned: :meth:`~netstead_project_cards.handlers.
        Handlers.undo_change` only takes back a catalog card this way, and keeps the draft-undo behavior for
        everything else.
        """
        with self._lock:
            for applied in reversed(self.applied):
                if applied.net_id == net_id and applied.source == "catalog":
                    return applied
            return None

    def forget_applied(self, applied: AppliedCard) -> Callable[[], None]:
        """Remove ``applied`` from the applied cards; return the undo (for ``host.on_rollback``) that restores it.

        Used by :meth:`~netstead_project_cards.handlers.Handlers.undo_change` when it undoes a whole applied
        catalog card, the mirror of :meth:`record_applied`.
        """
        with self._lock:
            self.applied = [a for a in self.applied if a is not applied]
            entry = self._applied_log.get(id(applied))
            self._unlog(entry)

        def undo() -> None:
            with self._lock:
                if not any(a is applied for a in self.applied):
                    self.applied.append(applied)
                if entry is not None and not any(e is entry for e in self._log):
                    self._log.append(entry)

        return undo

    def sync(self, net_id: str, handle: Any, pending: Pending) -> None:
        """Bring ``net_id``'s draft in line with its network ``handle`` and this plugin's ``pending`` edits on it.

        Both are read before the call, outside the lock. A draft made on another handle (a closed network whose id
        was reused) is dropped; otherwise changes a rolled-back Action left behind are dropped, and changes whose
        undo was rolled back are put back.
        """
        with self._lock:
            d = self._drafts.get(net_id)
            if d is None:
                return
            if d.handle() is not handle:
                del self._drafts[net_id]
                return
            if net_id not in self._undoing:
                d.reconcile(pending)

    @contextmanager
    def undoing(self, net_id: str) -> Iterator[None]:
        """Hold off reconciling ``net_id``'s draft while its last change is being undone (``host.undo`` calls).

        Each ``host.undo`` publishes the session state, and with it ``state()``; between the first undo and
        :meth:`pop`, the change's edits are no longer pending, and reconciling would drop it as if rolled back.
        """
        with self._lock:
            self._undoing.add(net_id)
        try:
            yield
        finally:
            with self._lock:
                self._undoing.discard(net_id)

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

    def generation(self) -> int:
        """How many undos the drafts have had: take it before reading the pending edits (``flow.pending_seqs``)."""
        with self._lock:
            return self._generation

    def pop(self, net_id: str, entry: Entry) -> int:
        """Take ``entry`` (the draft's last change, which the caller has undone) out; return the change count."""
        with self._lock:
            d = self._drafts[net_id]
            if not d.entries or d.entries[-1] is not entry:
                raise RuntimeError(f"{net_id}'s draft changed while its last change was being undone")
            d.entries.pop()
            self._generation += 1
            d.undone.append(Undone(entry, self._generation))
            d.card = d.card.model_copy(update={"changes": [e.change for e in d.entries]})
            return len(d.entries)

    def source(self, net_id: str) -> dict[str, str] | None:
        """The catalog card (``{card_id, path}``) ``net_id``'s draft was loaded from to edit, if any."""
        with self._lock:
            d = self._drafts.get(net_id)
            return dict(d.source) if d is not None and d.source is not None else None

    def follow_file(self, path: str, to: dict[str, str] | None, *, restore: bool = False) -> Callable[[], None]:
        """Point every draft whose source is the file at ``path`` at ``to`` (``{card_id, path}``); return the undo.

        A rename passes the new place; a delete passes ``None`` (the draft then has no source, and its commit offers
        only "Save as new file") and the drafts remember what they lost; a restore (``restore=True``) gives that back
        to the drafts that lost the file at ``path``, now at ``to``.
        """
        with self._lock:
            before = [(d, d.source, d.detached) for d in self._drafts.values()]
            for d in self._drafts.values():
                if restore and d.source is None and d.detached is not None and d.detached["path"] == path:
                    d.source, d.detached = (dict(to) if to else None), None
                elif not restore and d.source is not None and d.source["path"] == path:
                    d.detached = dict(d.source) if to is None else d.detached
                    d.source = dict(to) if to is not None else None

        def undo() -> None:
            with self._lock:
                for d, source, detached in before:
                    d.source, d.detached = source, detached

        return undo

    def entries(self, net_id: str) -> list[Entry]:
        """``net_id``'s draft changes, oldest first (a new list; the entries are shared and frozen)."""
        with self._lock:
            d = self._drafts.get(net_id)
            return list(d.entries) if d is not None else []

    def _take(self, d: Draft, index: int, removed: Sequence[Entry]) -> None:
        """Take ``d``'s changes from ``index`` on (``removed``, which the caller has undone) out, as undone ones.

        Each counts as one undo (newest first), so a rolled-back removal puts them back in order (see
        :meth:`Draft.reconcile`). Call it under the lock.
        """
        current = d.entries[index:]
        if len(current) != len(removed) or any(a is not b for a, b in zip(current, removed, strict=True)):
            raise RuntimeError("the draft changed while its changes were being undone")
        for entry in reversed(removed):
            self._generation += 1
            d.undone.append(Undone(entry, self._generation))
        del d.entries[index:]

    def replace_tail(
        self, net_id: str, handle: Any, index: int, removed: Sequence[Entry], applied: Sequence[Applied]
    ) -> int:
        """Replace ``net_id``'s draft changes from ``index`` on: ``removed`` (undone by the caller) by ``applied``.

        :meth:`~netstead_project_cards.handlers.Handlers.remove_change` undoes a change and the ones after it, then
        applies the later ones again: their new marks replace the old. Returns the change count.
        """
        with self._lock:
            d = self._drafts.get(net_id)
            if d is None or d.handle() is not handle:
                raise RuntimeError(f"{net_id}'s draft changed while a change was being removed")
            self._take(d, index, removed)
            d.entries.extend(Entry(a.change, tuple(a.marks)) for a in applied)
            d.card = d.card.model_copy(update={"changes": [e.change for e in d.entries]})
            return len(d.entries)

    def load(
        self,
        net_id: str,
        handle: Any,
        removed: Sequence[Entry],
        applied: Sequence[Applied],
        card: DraftCard,
        source: dict[str, str],
    ) -> tuple[int, Callable[[], None]]:
        """Make ``net_id``'s draft the card ``card`` from ``source`` (``{card_id, path}``), to edit it.

        ``removed`` are the draft's changes, which the caller has undone; ``applied`` the card's changes, applied.
        The draft takes the card's project, tags, dependencies and notes. Returns the change count and an undo (for
        ``host.on_rollback``) that puts the draft's metadata and source back; its changes are reconciled by marks.
        """
        with self._lock:
            d = self._drafts.get(net_id)
            if d is None or d.handle() is not handle:
                if removed:
                    raise RuntimeError(f"{net_id}'s draft changed while it was being replaced")
                d = self._drafts[net_id] = Draft(weakref.ref(handle))
            self._take(d, 0, removed)
            before = {k: getattr(d.card, k) for k in ("project", "tags", "dependencies", "notes")}
            was = d.source
            d.entries.extend(Entry(a.change, tuple(a.marks)) for a in applied)
            meta = {"project": card.project, "tags": list(card.tags), "dependencies": card.dependencies}
            d.card = d.card.model_copy(update={**meta, "notes": card.notes, "changes": [e.change for e in d.entries]})
            d.source, d.detached = dict(source), None
            count = len(d.entries)

        def undo() -> None:
            with self._lock:
                d.card = d.card.model_copy(update=before)
                d.source = was

        return count, undo

    def record_file(self, record: dict[str, Any]) -> Callable[[], None]:
        """Note a catalog file change (``{action, card_id, to}``; ``at`` is added); return the undo that forgets it."""
        entry = {**record, "at": timestamp()}
        logged = {"kind": "file", **entry}
        with self._lock:
            self.files.append(entry)
            self._log.append(logged)

        def undo() -> None:
            with self._lock:
                self.files = [f for f in self.files if f is not entry]
                self._unlog(logged)

        return undo

    def trashed(self, record: dict[str, str]) -> Callable[[], None]:
        """Note a card moved to the trash (``{card_id, path, trash}``); return the undo that forgets it."""
        entry = dict(record)
        with self._lock:
            self.trash.append(entry)

        def undo() -> None:
            with self._lock:
                self.trash = [t for t in self.trash if t is not entry]

        return undo

    def last_trashed(self, card_id: str) -> dict[str, str] | None:
        """The newest deletion of ``card_id`` this session (``None``: none)."""
        with self._lock:
            return next((dict(t) for t in reversed(self.trash) if t["card_id"] == card_id), None)

    def restored(self, card_id: str) -> Callable[[], None]:
        """Forget the newest deletion of ``card_id`` (it was restored); return the undo that remembers it again."""
        with self._lock:
            entry = next((t for t in reversed(self.trash) if t["card_id"] == card_id), None)
            if entry is not None:
                self.trash = [t for t in self.trash if t is not entry]

        def undo() -> None:
            with self._lock:
                if entry is not None and not any(t is entry for t in self.trash):
                    self.trash.append(entry)

        return undo

    def swap_catalog(self, catalog: Catalog) -> Callable[[], None]:
        """Make ``catalog`` the open one; return the undo (for ``host.on_rollback``) that puts the older one back.

        The undo does nothing once another catalog has replaced ``catalog`` (opened or rescanned since).
        """
        with self._lock:
            older, self._catalog = self._catalog, catalog

        def undo() -> None:
            with self._lock:
                if self._catalog is catalog:
                    self._catalog = older

        return undo

    def project(self, net_id: str) -> str:
        """The project name of ``net_id``'s draft (``""`` when unnamed)."""
        with self._lock:
            d = self._drafts.get(net_id)
            return d.card.project if d is not None else ""

    def commit(self, net_id: str, card: DraftCard, path: str) -> tuple[str, Callable[[], None]]:
        """Record ``card`` as committed to ``path`` (and as applied, source ``committed``) and start a new empty draft.

        Returns the card's id and an undo (for ``host.on_rollback``) that forgets the committed card and puts the
        draft back as it was. Anything done after the commit in the same rolled-back Action has been undone before
        it runs (rollback runs newest first), so the draft it replaces is one that Action started.
        """
        with self._lock:
            taken = {c["card_id"] for c in self.committed}
            base = card_id = slug(card.project)
            n = 2
            while card_id in taken:
                card_id, n = f"{base}-{n}", n + 1
            record = {
                "card_id": card_id,
                "net_id": net_id,
                "path": path,
                "project": card.project,
                "changes": len(card.changes),
                "at": timestamp(),
            }
            self.committed.append(record)
            saved = {"kind": "saved", **record}
            self._log.append(saved)
            draft = self._drafts.pop(net_id, None)
            seqs = tuple(
                m.seq for e in (draft.entries if draft is not None else []) for m in e.marks if m.seq is not None
            )
            applied = AppliedCard(
                card_id, net_id, path, card.project, timestamp(), len(card.changes), "committed", seqs
            )
            forget = self.record_applied(applied)

        def undo() -> None:
            forget()
            with self._lock:
                self.committed = [c for c in self.committed if c is not record]
                self._unlog(saved)
                if draft is None:
                    self._drafts.pop(net_id, None)
                else:
                    self._drafts[net_id] = draft

        return card_id, undo

    def snapshot(
        self, network: Callable[[str], Any | None], pending: Callable[[str], Pending], *, apply_enabled: bool = False
    ) -> dict[str, Any]:
        """JSON-safe state for ``/api/state`` (``plugins.project_cards``), each draft reconciled with its network.

        ``network(net_id)`` is the open network's handle, or ``None``: the drafts of a closed network are dropped (its
        pending edits went with it, so its draft can never be committed or undone), and so is a preview whose network
        or base is gone (:meth:`Preview.stands_for_base`). ``pending(net_id)`` is the plugin's pending edits on an
        open network (:class:`~netstead_project_cards.lower.Pending`). Both are called before the lock is taken, never
        under it. ``apply_enabled`` is the ``enable_apply`` setting, reported so the panel offers Apply only when on.
        """
        with self._lock:
            ids = list(self._drafts)
            preview = self._preview
        handles = {net_id: network(net_id) for net_id in ids}
        base = network(preview.base) if preview is not None else None
        standing = preview is not None and preview.stands_for_base(network(preview.net_id), base)
        for net_id, handle in handles.items():
            if handle is not None:
                self.sync(net_id, handle, pending(net_id))
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
                    "tags": list(d.card.tags),
                    "dependencies": d.card.dependencies.model_dump(),
                    "notes": d.card.notes,
                    "source": dict(d.source) if d.source is not None else None,
                    "count": len(d.entries),
                    "changes": [
                        {"index": i, "type": e.change.type, "describe": describe(e.change), "steps": len(e.marks)}
                        for i, e in enumerate(d.entries)
                    ],
                }
                for net_id, d in self._drafts.items()
                if d.entries or d.card.project
            }
            return {
                "drafts": drafts,
                "committed": [dict(c) for c in self.committed],
                "applied": [a.to_dict() for a in self.applied],
                "catalog": self._catalog.to_dict() if self._catalog is not None else None,
                "files": [dict(f) for f in self.files],
                "session": self._session_rows(apply_enabled),
                "trash": [dict(t) for t in self.trash],
                "preview": reported,
                "change_types": list(CHANGE_TYPES),
                "supported": sorted(ROADWAY_TYPES),
                "enable_apply": apply_enabled,
            }
