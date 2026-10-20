"""The project cards plugin's Actions: recorded in history, replayable with "Copy as Python", dispatchable over HTTP.

Each one changes a network, registers or closes a preview network, or writes a file, so each sets ``mutates``: the
natural-language assistant drafts them for the user to approve. The ones that need no file path (adding, undoing and
previewing a change, closing the preview) set ``compact``, so a model on this machine is offered them too.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from netstead.workbench.plugins import BaseAction
from pydantic import Field, model_validator

from .types import NetworkChange

__all__ = ["AddChange", "ClosePreview", "CommitCard", "ImportCard", "PreviewChange", "UndoChange"]


class _ChangeAction(BaseAction):
    """Fields an Action that applies one change shares (not an Action itself: it has no ``type``)."""

    mutates: ClassVar[bool] = True
    compact: ClassVar[bool] = True
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
    it previews, while that network is open, and a selection made on the preview targets it). The change is checked
    with ``projectcard`` before it is applied.
    """

    type: Literal["project_cards.add_change"] = "project_cards.add_change"


class UndoChange(BaseAction):
    """Undo the last change of a network's draft ProjectCard (committed cards are not undone)."""

    type: Literal["project_cards.undo_change"] = "project_cards.undo_change"
    mutates: ClassVar[bool] = True
    compact: ClassVar[bool] = True
    net_id: str | None = None


class CommitCard(BaseAction):
    """Write a network's draft card as a ProjectCard YAML file and start a new, empty draft.

    Live edit warnings must be confirmed (``confirm_warnings``), the same rule core applies before saving a copy.
    ``path`` must be inside ``io.allowed_roots``; an existing file is replaced only with ``overwrite``. When the
    ``catalog`` plugin is installed, the card is also added to it (``catalog.add_card``).
    """

    type: Literal["project_cards.commit"] = "project_cards.commit"
    mutates: ClassVar[bool] = True
    # The user confirmed the warnings in the live session. Like core's save_network, a replayed script never
    # replaces a file the user did not ask it to: ``overwrite`` is replayed as given.
    replay_overrides: ClassVar[dict[str, Any]] = {"confirm_warnings": True}
    net_id: str | None = None
    path: str = Field(min_length=1, max_length=1000)
    project: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(default_factory=list)
    dependencies: dict[Literal["prerequisites", "corequisites", "conflicts"], list[str]] = Field(default_factory=dict)
    notes: str | None = Field(default=None, max_length=5000)
    confirm_warnings: bool = False
    overwrite: bool = False


class ImportCard(BaseAction):
    """Apply every change of a ProjectCard YAML or JSON file to a network's draft card, all or nothing.

    Also reads the offline validation report's edit log. A query selection is applied to the link ids recorded in
    the card's notes when the plugin wrote it; a query without them is refused (re-resolving arrives with
    ``apply_card``). ``path`` must be inside ``io.allowed_roots``. An empty draft takes the card's project, tags,
    dependencies and notes.
    """

    type: Literal["project_cards.import"] = "project_cards.import"
    mutates: ClassVar[bool] = True
    net_id: str | None = None
    path: str = Field(min_length=1, max_length=1000)


class PreviewChange(_ChangeAction):
    """Preview one roadway change on a copy of a network, shown on the map; the network itself is untouched.

    Takes the same fields as ``project_cards.add_change``. A second preview replaces the first;
    ``project_cards.close_preview`` returns to the network. The preview is reported ``stale`` once the network
    changes (an ``add_change`` while previewing goes to the network, not the preview).
    """

    type: Literal["project_cards.preview"] = "project_cards.preview"


class ClosePreview(BaseAction):
    """Close the open preview and show the network it previewed again."""

    type: Literal["project_cards.close_preview"] = "project_cards.close_preview"
    mutates: ClassVar[bool] = True  # it closes (unregisters) the preview network
    compact: ClassVar[bool] = True
