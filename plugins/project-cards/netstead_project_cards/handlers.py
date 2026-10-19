"""The project cards plugin's Action handlers, sharing one session's drafts (``state.CardsState``).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network. A handler
nested in an Action that fails later is rolled back after it returned; ``target_network`` reconciles the draft with
the network at the start of every handler, so such a change never reaches an undo or a commit.
"""

from __future__ import annotations

from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, ClosePreview, CommitCard, ImportCard, PreviewChange, UndoChange
from .card import Dependencies, read_card_file
from .errors import CardError
from .flow import as_action_errors, check_card_shape, complete_change, is_open, refuse_transit, target_network
from .lower import Applied, apply_change, describe, mark_after
from .mapping import FieldMap
from .state import CardsState
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
        """Apply the change to the base network (never a preview) and append it to that network's draft."""
        refuse_transit(action.change)
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            change, warnings = complete_change(host, net, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            applied = apply_change(host, net, change)
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

        The file is written only after every check passes, and is removed (or its previous content restored) when
        the catalog refuses the card, so a failed commit leaves the disk and the draft as they were.
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
        if path.exists() and not action.overwrite:
            raise ActionError(f"{path} already exists; commit with overwrite=True to replace it")
        try:
            previous = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise ActionError(f"could not write {path}: {exc.strerror or exc}") from exc
        catalog = host.has_action("catalog.add_card")
        if catalog:
            try:
                host.dispatch({"type": "catalog.add_card", "path": str(path)})
            except Exception:  # boundary: another plugin failed; the file must not outlive a failed commit
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
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
        only after every change has applied.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        path = host.writable(action.path)  # the allowed-roots check (the Host has no read-only variant)
        with as_action_errors():
            if not path.is_file():
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

        A failure rolls back the copy with the Action (the Workbench closes the networks a failed handler derived).
        The older preview is closed last, so nothing is closed when an earlier step fails.
        """
        refuse_transit(action.change)
        with as_action_errors():
            base = target_network(host, self.state, action.net_id)
            change, warnings = complete_change(host, base, action.change, action.use_selection, action.saved_selection)
            check_card_shape(change, self.mapping)
            text = describe(change)
            copy_id = host.derive(base, label=f"Preview: {text}"[:120], note=f"preview {change.type}")
            applied = apply_change(host, copy_id, change)
        older = self.state.preview
        host.dispatch({"type": "set_active_network", "net_id": copy_id})
        if older is not None and is_open(host, older["net_id"]):
            host.dispatch({"type": "close_network", "net_id": older["net_id"]})
        self.state.preview = {"net_id": copy_id, "base": base, "describe": text}
        return {"preview": copy_id, "base": base, **applied.summary(), "warnings": [*warnings, *applied.warnings]}

    def close_preview(self, host: Host, action: ClosePreview) -> dict[str, Any]:
        """Close the preview network and make its base active again."""
        preview = self.state.preview
        if preview is None or not is_open(host, preview["net_id"]):
            raise ActionError("no preview is open")
        if is_open(host, preview["base"]):
            host.dispatch({"type": "set_active_network", "net_id": preview["base"]})
        host.dispatch({"type": "close_network", "net_id": preview["net_id"]})
        self.state.preview = None
        return {"closed": preview["net_id"], "active": host.active}
