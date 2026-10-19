"""The project cards plugin's Action handlers, sharing one session's drafts (``state.CardsState``).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network. A handler
nested in an Action that fails later is rolled back after it returned; ``target_network`` reconciles the draft with
the network at the start of every handler, so such a change never reaches an undo or a commit.
"""

from __future__ import annotations

import weakref
from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, ClosePreview, CommitCard, ImportCard, PreviewChange, UndoChange
from .card import Dependencies, read_card_file
from .errors import CardError
from .files import undo_write, write_file
from .flow import (
    as_action_errors,
    check_card_shape,
    complete_change,
    open_handle,
    refuse_transit,
    selection_networks,
    target_network,
)
from .lower import Applied, apply_change, describe, mark_after
from .mapping import FieldMap
from .state import CardsState, Preview
from .validate import card_problems

__all__ = ["Handlers"]


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
            ActionSpec(CommitCard, self.commit),
            ActionSpec(ImportCard, self.import_card),
            ActionSpec(PreviewChange, self.preview),
            ActionSpec(ClosePreview, self.close_preview),
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
        """Undo the draft's last change: as many ``host.undo`` calls as it made mutations (LIFO), then drop it.

        ``host.undo`` refuses (``ActionError``) when the network's newest edit is not this plugin's; a refusal part
        way through a two-step change rolls the earlier step back with the Action. Each undone edit must be the
        mutation the draft recorded (its ledger note and version), so a plugin edit the draft does not hold is
        refused rather than undone in its place.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        entry = self.state.last(net)
        if entry is None:
            raise ActionError("nothing to undo: the draft card has no changes (committed cards are not undone)")
        undo_marks = []
        for mark in reversed(entry.marks):
            undone = host.undo(net)
            if (undone["note"], undone["version"]) != (mark.note, mark.version):
                raise ActionError(
                    f"the newest project cards edit on {net} ({undone['note']}) is not the draft's last change "
                    f"({mark.note}); nothing was undone"
                )
            undo_marks.append(mark_after(host, net, f"undo: {mark.note}"))
        count = self.state.pop(net, entry, undo_marks)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "undone": describe(entry.change), "count": count}

    def commit(self, host: Host, action: CommitCard) -> dict[str, Any]:
        """Check, write and record the draft as a card; dispatch ``catalog.add_card`` when a catalog is installed.

        The file is written only after every check passes, atomically (``files.write_file``: an existing file is
        replaced only with ``overwrite``, and a failed write leaves it intact), and is removed (or its previous
        content restored) with the folders the commit made when the catalog refuses the card, so a failed commit
        leaves the disk and the draft as they were. A restore that fails too is named in the catalog's error.
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
        card_id = self.state.commit(net, card, str(path))
        host.publish("committed", {"net_id": net, "card_id": card_id, "path": str(path)})
        return {
            "card_id": card_id,
            "net_id": net,
            "path": str(path),
            "changes": len(card.changes),
            "warnings": len(warnings),
            "catalog": catalog,
        }

    def import_card(self, host: Host, action: ImportCard) -> dict[str, Any]:
        """Read a card (or an offline edit log) and apply its changes to the draft, all or nothing.

        A failing change fails the Action, whose rollback undoes the changes already applied; the draft is touched
        only after every change has applied. A change that applies nothing (every id missing, all ignored) is kept:
        it is part of the card (see :mod:`~netstead_project_cards.state` for what that costs).
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        path = host.writable(action.path)  # the allowed-roots check (the Host has no read-only variant)
        with as_action_errors():
            try:
                is_file = path.is_file()
            except OSError as exc:  # a folder on the way that cannot be searched
                raise CardError(f"could not read {path}: {exc.strerror or exc}") from exc
            if not is_file:
                raise CardError(f"{action.path} is not a file")
            incoming = read_card_file(path, self.mapping)
            if not incoming.changes:
                raise CardError(f"{action.path} has no changes to import")
        total = len(incoming.changes)
        applied: list[Applied] = []
        for k, change in enumerate(incoming.changes, start=1):
            with as_action_errors(f"change {k} of {total}: "):
                check_card_shape(change, self.mapping)
                applied.append(apply_change(host, net, change))
        count = self.state.extend(net, host.network(net), applied, name_from=incoming)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "imported": total, "project": self.state.project(net), "count": count}

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
        older = self.state.preview()
        host.dispatch({"type": "set_active_network", "net_id": copy_id})
        if older is not None and older.is_copy(open_handle(host, older.net_id)):
            host.dispatch({"type": "close_network", "net_id": older.net_id})
        preview = Preview(
            net_id=copy_id,
            copy=weakref.ref(host.network(copy_id)),
            base=base,
            base_handle=weakref.ref(base_handle),
            base_version=base_version,
            describe=text,
        )
        self.state.set_preview(preview)
        return {"preview": copy_id, "base": base, **applied.summary(), "warnings": [*warnings, *applied.warnings]}

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
