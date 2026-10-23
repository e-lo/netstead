"""The project cards plugin's Action handlers, sharing one session's drafts (``state.CardsState``).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network. A handler
nested in an Action that fails later is rolled back after it returned; ``target_network`` reconciles the draft with
the network's pending edits at the start of every handler, so such a change never reaches an undo or a commit. A
commit changes no network, so it registers ``host.on_rollback`` to take its file and the draft back.
"""

from __future__ import annotations

import logging
import weakref
from pathlib import Path
from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import (
    AddChange,
    ApplyCard,
    CheckCard,
    ClosePreview,
    CommitCard,
    EditCard,
    ImportCard,
    OpenCatalog,
    PreviewCard,
    PreviewChange,
    RefreshCatalog,
    RemoveChange,
    SessionSummary,
    UndoChange,
)
from .card import Dependencies, read_card_file
from .catalog import applicability, card_targets, with_options
from .errors import CardError
from .files import undo_write, write_file
from .flow import (
    APPLY_OFF,
    apply_enabled,
    as_action_errors,
    catalog_entry,
    check_card_shape,
    check_entries,
    complete_change,
    load_catalog,
    open_handle,
    refuse_transit,
    selection_networks,
    summary_text,
    target_network,
)
from .lower import Applied, apply_change, describe, few
from .mapping import FieldMap
from .state import AppliedCard, CardsState, Entry, Preview, timestamp
from .types import NetworkChange
from .validate import card_problems

__all__ = ["Handlers"]

logger = logging.getLogger(__name__)


class Handlers:
    """The Action handlers of one plugin instance (one Workbench session)."""

    def __init__(self, state: CardsState, mapping: FieldMap) -> None:
        """Bind the handlers to a session's draft state and the field mapping."""
        self.state, self.mapping = state, mapping

    def specs(self) -> tuple[ActionSpec, ...]:
        """Every Action this plugin registers, paired with its handler."""
        return (
            ActionSpec(AddChange, self.add_change),
            ActionSpec(UndoChange, self.undo_change),
            ActionSpec(RemoveChange, self.remove_change),
            ActionSpec(CommitCard, self.commit),
            ActionSpec(ImportCard, self.import_card),
            ActionSpec(EditCard, self.edit_card),
            ActionSpec(PreviewChange, self.preview),
            ActionSpec(ClosePreview, self.close_preview),
            ActionSpec(OpenCatalog, self.open_catalog),
            ActionSpec(RefreshCatalog, self.refresh_catalog),
            ActionSpec(CheckCard, self.check_card),
            ActionSpec(PreviewCard, self.preview_card),
            ActionSpec(ApplyCard, self.apply_card),
            ActionSpec(SessionSummary, self.session_summary),
        )

    def add_change(self, host: Host, action: AddChange) -> dict[str, Any]:
        """Apply the change to the base network (never a preview) and append it to that network's draft.

        A change that applies nothing (a selection whose ids are all missing and ignored, or every property skipped)
        is refused: the draft holds only changes the network has.
        """
        refuse_transit(action.change)
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            nets = selection_networks(host, self.state, net)
            change, warnings = complete_change(host, nets, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            applied = apply_change(host, net, change)
        if not applied.marks:  # a draft entry with no marks could not be reconciled after a parent's rollback
            raise ActionError(f"nothing on {net} matches this change; nothing was added")
        applied.warnings[:0] = warnings
        count = self.state.extend(net, host.network(net), [applied])
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "count": count, "change": change.model_dump(mode="json"), **applied.summary()}

    def undo_change(self, host: Host, action: UndoChange) -> dict[str, Any]:
        """Undo whichever plugin change is newest on the network: an applied catalog card, or the draft's last change.

        The network's newest pending edit decides which: when it is the last applied catalog card's newest edit
        (``AppliedCard.edit_ids``), the whole card is undone (every edit, newest first) and dropped from
        ``applied``; otherwise the draft's last change is undone as before (as many ``host.undo`` calls as it made
        mutations, LIFO, then dropped). A committed card is never picked this way: its ``source`` is ``"committed"``,
        so this handler falls through to the draft (empty after a commit, so a committed card stays un-undoable
        here, as before). ``host.undo`` refuses (``ActionError``, naming what is on top) when the network's newest
        edit is not this plugin's at all, so a core or another plugin's change on top is refused either way; a
        refusal part way through a two-step change rolls the earlier step back with the Action. Each undone edit
        must be the mutation the card or the draft recorded (its seq), so a plugin edit neither holds is refused
        rather than undone in its place.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        pending = host.pending_edits(net)
        newest = pending.edits[-1] if pending.edits else None
        card = self.state.last_catalog_card(net)
        if card is not None and card.edit_ids and newest is not None and newest["seq"] == card.edit_ids[-1]:
            return self._undo_card(host, net, card)
        entry = self.state.last(net)
        if entry is None:
            if newest is not None and newest["source"] != host.plugin_id:
                host.undo(net)  # refuses, naming what is on top (e.g. a core edit over the applied card)
            raise ActionError("nothing to undo: the draft card has no changes (committed cards are not undone)")
        with self.state.undoing(net):
            for mark in reversed(entry.marks):
                undone = host.undo(net)
                if undone["seq"] != mark.seq:
                    raise ActionError(
                        f"the newest project cards edit on {net} ({undone['note']}) is not the draft's last change "
                        f"({mark.note}); nothing was undone"
                    )
            count = self.state.pop(net, entry)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "undone": describe(entry.change), "count": count}

    def remove_change(self, host: Host, action: RemoveChange) -> dict[str, Any]:
        """Remove change ``index`` of the draft: undo it and every later change, then apply the later ones again.

        ``host.undo`` takes back only the network's newest edit, so a change in the middle is removed by undoing to it
        and re-adding what came after (each through the same lowering as ``add_change``): the network ends as if the
        change had never been made. A later change that no longer applies (it depends on the removed one) fails the
        Action, whose rollback puts every undone edit back; the draft is touched only once everything succeeded.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        entries = self.state.entries(net)
        if action.index >= len(entries):
            n = len(entries)
            raise ActionError(
                f"the draft card on {net} has {n} change{'s' if n != 1 else ''}: there is no change {action.index + 1}"
            )
        removed = entries[action.index :]
        with self.state.undoing(net):
            self._undo_entries(host, net, removed)
            applied = self._reapply(host, net, action.index, [e.change for e in removed[1:]])
            count = self.state.replace_tail(net, host.network(net), action.index, removed, applied)
        host.publish("changed", {"net_id": net, "count": count})
        return {
            "net_id": net,
            "index": action.index,
            "removed": describe(removed[0].change),
            "readded": len(applied),
            "count": count,
        }

    def _undo_entries(self, host: Host, net: str, entries: list[Entry]) -> None:
        """Undo ``entries`` (the draft's last changes, oldest first) newest first, each mutation checked by its seq.

        Call it inside ``state.undoing(net)`` from a handler: a refusal fails the Action, whose rollback redoes them.
        """
        for entry in reversed(entries):
            for mark in reversed(entry.marks):
                undone = host.undo(net)
                if undone["seq"] != mark.seq:
                    raise ActionError(
                        f"the newest project cards edit on {net} ({undone['note']}) is not the draft's "
                        f"({mark.note}); nothing was changed"
                    )

    def _reapply(self, host: Host, net: str, index: int, changes: list[NetworkChange]) -> list[Applied]:
        """Apply the changes that came after the removed change ``index`` again, in order."""
        applied: list[Applied] = []
        for k, change in enumerate(changes, start=index + 2):
            with as_action_errors(f"change {k} depends on the removed change {index + 1} ({describe(change)}): "):
                again = apply_change(host, net, change)
            if not again.marks:
                raise ActionError(
                    f"change {k} depends on the removed change {index + 1}: nothing on {net} matches it any more; "
                    "nothing was removed"
                )
            applied.append(again)
        return applied

    def edit_card(self, host: Host, action: EditCard) -> dict[str, Any]:
        """Load a catalog card into the draft to edit it (``import`` semantics), remembering its file.

        A draft with uncommitted changes is refused unless ``replace_draft``: they are then undone first (network
        edits included). The card's changes are applied all or nothing; a failure rolls back the undos too. The draft
        takes the card's project, tags, dependencies and notes, and its source (``{card_id, path}``), whatever it
        had; an enclosing Action that fails afterwards puts those back (``host.on_rollback``).
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        entry = catalog_entry(self.state, action.card_id)
        path = host.readable(entry.path)
        with as_action_errors():
            incoming = read_card_file(path, self.mapping)
            if not incoming.changes:
                raise CardError(f"{entry.card_id} has no changes to edit")
        current = self.state.entries(net)
        if current and not action.replace_draft:
            n = len(current)
            raise ActionError(
                f"the draft card on {net} has {n} uncommitted change{'s' if n != 1 else ''}: edit with "
                f"replace_draft=True to undo {'them' if n != 1 else 'it'} and load {entry.card_id} instead"
            )
        source = {"card_id": entry.card_id, "path": str(path)}
        with self.state.undoing(net):
            self._undo_entries(host, net, current)
            applied = self._apply_all(host, net, incoming.changes)
            count, undo = self.state.load(net, host.network(net), current, applied, incoming, source)
        host.on_rollback(undo)
        host.publish("changed", {"net_id": net, "count": count})
        return {**source, "net_id": net, "project": incoming.project, "count": count, "replaced": len(current)}

    def _undo_card(self, host: Host, net: str, card: AppliedCard) -> dict[str, Any]:
        """Undo every edit of an applied catalog card (newest first) and drop it from ``applied``.

        Registers a ``host.on_rollback`` that restores the ``AppliedCard`` entry if an enclosing Action fails
        afterwards, the mirror of how :meth:`apply_card` records it.
        """
        for seq in reversed(card.edit_ids):
            undone = host.undo(net)
            if undone["seq"] != seq:
                raise ActionError(
                    f"the newest project cards edit on {net} ({undone['note']}) is not {card.card_id}'s "
                    f"last change; nothing was undone"
                )
        host.on_rollback(self.state.forget_applied(card))
        draft = self.state.card(net)
        count = len(draft.changes) if draft is not None else 0
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "undone": "card", "card_id": card.card_id, "edits": len(card.edit_ids)}

    def commit(self, host: Host, action: CommitCard) -> dict[str, Any]:
        """Check, write and record the draft as a card; dispatch ``catalog.add_card`` when a catalog is installed.

        The file is written only after every check passes, atomically (``files.write_file``: an existing file is
        replaced only with ``overwrite``, and a failed write leaves it intact), and is removed (or its previous
        content restored) with the folders the commit made when the catalog refuses the card, so a failed commit
        leaves the disk and the draft as they were. A restore that fails too is named in the catalog's error.
        When an enclosing Action fails after the commit succeeded, its ``host.on_rollback`` callback does the same
        (a restore that fails is logged) and puts the draft back.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            draft = self.state.card(net)
            if draft is None:
                raise CardError("the draft card has no changes to commit")
            warnings = host.edit_warnings(net)
            if warnings and not action.confirm_warnings:
                raise CardError(
                    f"{net} has {len(warnings)} live edit warning(s); review them in the Edits panel, "
                    "then commit with confirm_warnings=True"
                )
            card = draft.model_copy(
                update={
                    "project": action.project,
                    "tags": list(action.tags),
                    "dependencies": Dependencies(**action.dependencies),
                    "notes": action.notes,
                }
            )
            problems = card_problems(card.to_card(self.mapping))
            if problems:
                raise CardError(f"not a valid ProjectCard: {problems[0]}")
            text = card.to_yaml(self.mapping)
        path = host.writable(action.path)  # PathNotAllowed (an ActionError) outside io.allowed_roots
        with as_action_errors():
            written = write_file(path, text.encode("utf-8"), overwrite=action.overwrite)
        catalog = host.has_action("catalog.add_card")
        if catalog:
            try:
                host.dispatch({"type": "catalog.add_card", "path": str(path)})
            except Exception as exc:  # boundary: another plugin failed; the file must not outlive a failed commit
                failed = undo_write(written)
                if failed is None:
                    raise
                note = f"the file at {path} could not be {failed}"
                if isinstance(exc, ActionError):
                    raise ActionError(f"{exc}; {note}") from exc
                exc.add_note(note)
                raise
        card_id, uncommit = self.state.commit(net, card, str(path))

        def roll_back() -> None:
            if (failed := undo_write(written)) is not None:
                logger.warning("project_cards: a rolled-back commit's file at %s could not be %s", path, failed)
            uncommit()

        host.on_rollback(roll_back)
        listed = self._rescan_for(host, path)
        host.publish("committed", {"net_id": net, "card_id": card_id, "path": str(path)})
        return {
            "card_id": card_id,
            "net_id": net,
            "path": str(path),
            "changes": len(card.changes),
            "warnings": len(warnings),
            "catalog": catalog,
            "listed": listed,
        }

    def _rescan_for(self, host: Host, path: Path) -> bool:
        """Rescan the open catalog when ``path`` (a file just written) is in its folder; whether it was.

        A folder that can no longer be read keeps the catalog as it was (logged): the file is written either way.
        An enclosing Action that fails puts the older listing back (``host.on_rollback``).
        """
        current = self.state.catalog()
        if current is None or not path.is_relative_to(current.path):
            return False
        try:
            catalog = load_catalog(host, current.path, self.mapping)
        except ActionError as exc:
            logger.warning("project_cards: the catalog at %s was not rescanned: %s", current.path, exc)
            return False
        host.on_rollback(self.state.swap_catalog(catalog))
        host.publish("catalog", {"path": catalog.path, "cards": len(catalog.cards)})
        return True

    def import_card(self, host: Host, action: ImportCard) -> dict[str, Any]:
        """Read a card (or an offline edit log) and apply its changes to the draft, all or nothing.

        A failing change fails the Action, whose rollback undoes the changes already applied; the draft is touched
        only after every change has applied. A change that applies nothing (every id missing, all ignored) is kept:
        it is part of the card (see :mod:`~netstead_project_cards.state` for what that costs).
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        path = host.readable(action.path)  # PathNotAllowed outside io.allowed_roots; ActionError when nothing is there
        with as_action_errors():
            if not path.is_file():
                raise CardError(f"{action.path} is not a file")
            incoming = read_card_file(path, self.mapping)
            if not incoming.changes:
                raise CardError(f"{action.path} has no changes to import")
        applied = self._apply_all(host, net, incoming.changes)
        count = self.state.extend(net, host.network(net), applied, name_from=incoming)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "imported": len(applied), "project": self.state.project(net), "count": count}

    def preview(self, host: Host, action: PreviewChange) -> dict[str, Any]:
        """Derive a copy of the base, apply the change to it, and make it active; then close any older preview.

        A failure in this handler rolls back the copy with the Action (the Workbench closes the networks a failed
        handler derived), and the older preview is closed last, so this handler closes nothing when one of its own
        steps fails. An enclosing Action that fails afterwards does not reopen the older preview (see
        :mod:`~netstead_project_cards.state`). The preview is ``stale`` in ``state()`` once its base changes.
        """
        refuse_transit(action.change)
        with as_action_errors():
            base = target_network(host, self.state, action.net_id)
            nets = selection_networks(host, self.state, base)
            change, warnings = complete_change(host, nets, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            text = describe(change)
            base_handle = host.network(base)
            base_version = base_handle.version
            copy_id = host.derive(base, label=f"Preview: {text}"[:120], note=f"preview {change.type}")
            applied = apply_change(host, copy_id, change)
        self._show_preview(
            host,
            Preview(copy_id, weakref.ref(host.network(copy_id)), base, weakref.ref(base_handle), base_version, text),
        )
        return {"preview": copy_id, "base": base, **applied.summary(), "warnings": [*warnings, *applied.warnings]}

    def _show_preview(self, host: Host, preview: Preview) -> None:
        """Make the preview's network active, close any older preview (last: see :meth:`preview`), record it."""
        older = self.state.preview()
        host.dispatch({"type": "set_active_network", "net_id": preview.net_id})
        if older is not None and older.is_copy(open_handle(host, older.net_id)):
            host.dispatch({"type": "close_network", "net_id": older.net_id})
        self.state.set_preview(preview)

    def close_preview(self, host: Host, action: ClosePreview) -> dict[str, Any]:
        """Close the preview network and make its base active again.

        When the base is gone (closed, even if a network with its id was opened since), the preview is closed and
        the active network is left to core: the plugin never switches to an unrelated network.
        """
        preview = self.state.preview()
        copy = open_handle(host, preview.net_id) if preview is not None else None
        if preview is None or not preview.is_copy(copy):
            raise ActionError("no preview is open")
        if preview.stands_for_base(copy, open_handle(host, preview.base)):
            host.dispatch({"type": "set_active_network", "net_id": preview.base})
        host.dispatch({"type": "close_network", "net_id": preview.net_id})
        self.state.clear_preview(preview)
        return {"closed": preview.net_id, "active": host.active}

    # ---------------------------------------------------------------- the catalog

    def open_catalog(self, host: Host, action: OpenCatalog) -> dict[str, Any]:
        """Scan the folder and make it the open catalog (replacing any other); nothing on a network changes."""
        catalog = load_catalog(host, action.path, self.mapping)
        self.state.set_catalog(catalog)
        host.publish("catalog", {"path": catalog.path, "cards": len(catalog.cards)})
        return {"path": catalog.path, "cards": len(catalog.cards), "rejected": len(catalog.rejected)}

    def refresh_catalog(self, host: Host, action: RefreshCatalog) -> dict[str, Any]:
        """Scan the open catalog's folder again; every card's applicability is unknown again."""
        current = self.state.catalog()
        if current is None:
            raise ActionError("no catalog is open: open a folder of cards first (project_cards.open_catalog)")
        catalog = load_catalog(host, current.path, self.mapping)
        self.state.set_catalog(catalog)
        host.publish("catalog", {"path": catalog.path, "cards": len(catalog.cards)})
        return {"path": catalog.path, "cards": len(catalog.cards), "rejected": len(catalog.rejected)}

    def check_card(self, host: Host, action: CheckCard) -> dict[str, Any]:
        """Compute (and keep, for the listing) whether the card applies to the network as it is now."""
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        entry = catalog_entry(self.state, action.card_id)
        return {"card_id": entry.card_id, **check_entries(host, self.state, net, [entry])[0]}

    def preview_card(self, host: Host, action: PreviewCard) -> dict[str, Any]:
        """Derive a copy of the base, apply every change of the card to it, show it, and publish the card's targets.

        As :meth:`preview`: a failing change fails the Action, whose rollback closes the copy. The published
        ``card_selection`` (``{card_id, links, nodes}``) is the union of the ids the card's changes aim at.
        """
        with as_action_errors():
            base = target_network(host, self.state, action.net_id)
        entry = catalog_entry(self.state, action.card_id)
        text = f"card {entry.card_id}: {entry.project}"
        with as_action_errors():
            base_handle = host.network(base)
            base_version = base_handle.version
            copy_id = host.derive(base, label=f"Preview: {text}"[:120], note=f"preview card {entry.card_id}")
        applied = self._apply_all(host, copy_id, entry.card.changes)
        self._show_preview(
            host,
            Preview(copy_id, weakref.ref(host.network(copy_id)), base, weakref.ref(base_handle), base_version, text),
        )
        host.publish("card_selection", {"card_id": entry.card_id, **card_targets(entry.card)})
        return {
            "preview": copy_id,
            "base": base,
            "card_id": entry.card_id,
            "changes": [a.summary() for a in applied],
            "warnings": [w for a in applied for w in a.warnings],
        }

    def apply_card(self, host: Host, action: ApplyCard) -> dict[str, Any]:
        """Apply the card to the base network, all or nothing, and record it as applied (source ``catalog``).

        The card is checked first; it is refused when a change cannot be applied at all, or lacks an id and
        ``ignore_missing`` is not true. A failing change fails the Action, whose rollback undoes the ones already
        applied; the record is made last and taken back by ``host.on_rollback`` if an enclosing Action fails. The
        card's applicability is unknown again afterwards (the network changed). Refused while ``enable_apply`` is off.
        """
        if not apply_enabled(host):
            raise ActionError(APPLY_OFF)
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        entry = catalog_entry(self.state, action.card_id)
        changes = with_options(
            entry.card, existing_value_conflict=action.existing_value_conflict, ignore_missing=action.ignore_missing
        )
        handle = host.network(net)  # checked with the options being applied: a warn or skip is not a conflict
        check = applicability(entry.card.model_copy(update={"changes": changes}), handle.links_df(), handle.nodes_df())
        problems = [
            f"{c['describe']}: {c['reason'] or 'missing ' + few(c['missing'])}"
            for c in check["changes"]
            if c["reason"] or (c["missing"] and not action.ignore_missing)
        ]
        if problems and (not action.ignore_missing or any(c["reason"] for c in check["changes"])):
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            hint = "" if any(c["reason"] for c in check["changes"]) else "; apply with ignore_missing=True to skip them"
            raise ActionError(f"{entry.card_id} does not apply to {net}: {problems[0]}{more}{hint}")
        applied = self._apply_all(host, net, changes)
        seqs = tuple(m.seq for a in applied for m in a.marks if m.seq is not None)
        record = AppliedCard(entry.card_id, net, entry.path, entry.project, timestamp(), len(changes), "catalog", seqs)
        host.on_rollback(self.state.record_applied(record))
        self.state.set_applicability(entry, None)
        host.publish("applied", {"net_id": net, "card_id": entry.card_id})
        return {
            "card_id": entry.card_id,
            "net_id": net,
            "changes": len(changes),
            "edit_ids": list(seqs),
            "warnings": [w for a in applied for w in a.warnings],
            "skipped": [s for a in applied for s in a.skipped],
        }

    def _apply_all(self, host: Host, net: str, changes: list[NetworkChange]) -> list[Applied]:
        """Apply every change to ``net`` in order; a refusal names the change (``change k of n: ...``)."""
        applied: list[Applied] = []
        total = len(changes)
        for k, change in enumerate(changes, start=1):
            with as_action_errors(f"change {k} of {total}: "):
                check_card_shape(change, self.mapping)
                applied.append(apply_change(host, net, change))
        return applied

    def session_summary(self, host: Host, action: SessionSummary) -> dict[str, Any]:
        """The session summary text (see :mod:`~netstead_project_cards.summary`)."""
        return {"text": summary_text(host, self.state, action.net_id)}
