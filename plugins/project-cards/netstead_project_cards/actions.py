"""The project cards plugin's Actions: recorded in history, replayable with "Copy as Python", dispatchable over HTTP.

Each one changes a network, registers or closes a preview network, or writes a file, so each sets ``mutates``: the
natural-language assistant drafts them for the user to approve.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from netstead.workbench.plugins import BaseAction
from pydantic import Field, model_validator

from .types import NetworkChange

__all__ = ["AddChange", "UndoChange"]


class _ChangeAction(BaseAction):
    """Fields an Action that applies one change shares (not an Action itself: it has no ``type``)."""

    mutates: ClassVar[bool] = True
    net_id: str | None = None
    change: NetworkChange
    use_selection: bool = False
    saved_selection: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def _one_source(self) -> _ChangeAction:
        if self.use_selection and self.saved_selection is not None:
            raise ValueError("use the shared selection or a saved selection, not both")
        return self


class AddChange(_ChangeAction):
    """Apply one roadway change to a network and add it to that network's draft ProjectCard.

    ``change`` is a ProjectCard change: ``roadway_property_change``, ``roadway_deletion`` or ``roadway_addition``
    (the transit types answer "not yet supported"). With ``use_selection`` a property change or a deletion targets
    the shared selection: a click-pick by its link ids (``ignore_missing`` false), a query by its query (keeping the
    query's ``ignore_missing``, true when it has none) with the ids it resolved to. With ``saved_selection`` it targets
    that saved selection instead. ``net_id`` defaults to the active network (an open preview stands for the network
    it previews). The change is checked with ``projectcard`` before it is applied.
    """

    type: Literal["project_cards.add_change"] = "project_cards.add_change"


class UndoChange(BaseAction):
    """Undo the last change of a network's draft ProjectCard (committed cards are not undone)."""

    type: Literal["project_cards.undo_change"] = "project_cards.undo_change"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
