"""The project cards plugin's Action handlers, sharing one session's drafts (``state.CardsState``).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network.
"""

from __future__ import annotations

from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, CommitCard, ImportCard, UndoChange
from .card import Dependencies, read_card_file
from .errors import CardError
from .flow import as_action_errors, check_card_shape, complete_change, refuse_transit, target_network
from .lower import Applied, apply_change, describe
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
        count = self.state.add(net, applied)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "count": count, "change": change.model_dump(mode="json"), **applied.summary()}

    def undo_change(self, host: Host, action: UndoChange) -> dict[str, Any]:
        """Undo the draft's last change: as many ``host.undo`` calls as it made mutations (LIFO), then drop it.

        ``host.undo`` refuses (``ActionError``) when the network's newest edit is not this plugin's; a refusal part
        way through a two-step change rolls the earlier step back with the Action.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
        draft = self.state.drafts.get(net)
        if draft is None or not draft.steps:
            raise ActionError("nothing to undo: the draft card has no changes (committed cards are not undone)")
        for _ in range(draft.steps[-1]):
            host.undo(net)
        change = self.state.pop(net)
        host.publish("changed", {"net_id": net, "count": len(draft.steps)})
        return {"net_id": net, "undone": describe(change), "count": len(draft.steps)}

    def commit(self, host: Host, action: CommitCard) -> dict[str, Any]:
        """Check, write and record the draft as a card; dispatch ``catalog.add_card`` when a catalog is installed.

        The file is written only after every check passes, and is removed (or its previous content restored) when
        the catalog refuses the card, so a failed commit leaves the disk and the draft as they were.
        """
        with as_action_errors():
            net = target_network(host, self.state, action.net_id)
            draft = self.state.drafts.get(net)
            if draft is None or not draft.steps:
                raise CardError("the draft card has no changes to commit")
            warnings = host.edit_warnings(net)
            if warnings and not action.confirm_warnings:
                raise CardError(
                    f"{net} has {len(warnings)} live edit warning(s); review them in the Edits panel, "
                    "then commit with confirm_warnings=True"
                )
            card = draft.card.model_copy(
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
        draft = self.state.draft(net)
        if not draft.steps and not draft.card.project:
            self.state.name(
                net,
                project=incoming.project,
                tags=incoming.tags,
                dependencies=incoming.dependencies,
                notes=incoming.notes,
            )
        count = len(draft.steps)
        for one in applied:
            count = self.state.add(net, one)
        host.publish("changed", {"net_id": net, "count": count})
        return {"net_id": net, "imported": total, "project": self.state.draft(net).card.project, "count": count}
