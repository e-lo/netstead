"""The project cards plugin's Action handlers, sharing one session's drafts (``state.CardsState``).

Each handler does every fallible step (Host calls included) before it touches the draft state: when a handler
fails, the Workbench rolls back its network changes, and the drafts must still describe the network.
"""

from __future__ import annotations

from typing import Any

from netstead.workbench.plugins import ActionError, ActionSpec, Host

from .actions import AddChange, UndoChange
from .flow import as_action_errors, check_card_shape, complete_change, refuse_transit, target_network
from .lower import apply_change, describe
from .mapping import FieldMap
from .state import CardsState

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
