"""The project cards plugin's Actions: recorded in history, replayable with "Copy as Python", dispatchable over HTTP.

The ones that change a network, register or close a preview network, or write a file set ``mutates``: the
natural-language assistant drafts them for the user to approve. The ones that need no file path (adding, undoing,
removing and previewing a change, closing the preview, previewing, applying and editing a catalog card) set
``assistant_compact``, so a model on this machine is offered them too. Opening, refreshing and checking the
catalog, and the session summary, read only (the catalog is the plugin's own state, not a network).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from netstead.workbench.plugins import BaseAction
from pydantic import Field, model_validator

from .types import NetworkChange

__all__ = [
    "AddChange",
    "ApplyCard",
    "CheckCard",
    "ClosePreview",
    "CommitCard",
    "DeleteCard",
    "DuplicateCard",
    "EditCard",
    "ImportCard",
    "OpenCatalog",
    "PreviewCard",
    "PreviewChange",
    "RefreshCatalog",
    "RemoveChange",
    "RenameCard",
    "RestoreCard",
    "SessionSummary",
    "UndoChange",
    "UpdateCardMeta",
]


class _ChangeAction(BaseAction):
    """Fields an Action that applies one change shares (not an Action itself: it has no ``type``)."""

    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None
    change: NetworkChange
    use_selection: bool = False
    saved_selection: str | None = Field(default=None, min_length=1, max_length=100)
    facility_form: Literal["segment", "link_list"] | None = None
    id_system: Literal["gmns", "osm", "gers"] = "gmns"

    @model_validator(mode="after")
    def _one_source(self) -> _ChangeAction:
        if self.use_selection and self.saved_selection is not None:
            raise ValueError("use the shared selection or a saved selection, not both")
        if not (self.use_selection or self.saved_selection) and (self.facility_form or self.id_system != "gmns"):
            raise ValueError("facility_form and id_system say how a selection is written: use a selection")
        return self


class AddChange(_ChangeAction):
    """Apply one roadway change to a network and add it to that network's draft ProjectCard.

    ``change`` is a ProjectCard change: ``roadway_property_change``, ``roadway_deletion`` or ``roadway_addition``
    (the transit types answer "not yet supported"). With ``use_selection`` a property change or a deletion targets
    the shared selection: a click-pick by its link ids (``ignore_missing`` false), a query by its query (keeping the
    query's ``ignore_missing``, true when it has none) with the ids it resolved to. With ``saved_selection`` it targets
    that saved selection instead. The facility is written as ``facility_form`` ``segment`` (name or ref with the from
    and to nodes, re-resolvable; the default for a named selection) or ``link_list`` (ids only; the default for a
    click or box), in ``id_system`` ``gmns`` (``model_link_id``) or ``osm`` (``osm_link_id``); ProjectCard has no key
    for ``gers``, which is refused. ``net_id`` defaults to the active network (an open preview stands for the network
    it previews, while that network is open, and a selection made on the preview targets it). The change is checked
    with ``projectcard`` before it is applied.
    """

    type: Literal["project_cards.add_change"] = "project_cards.add_change"


class UndoChange(BaseAction):
    """Undo the last change of a network's draft ProjectCard (committed cards are not undone)."""

    type: Literal["project_cards.undo_change"] = "project_cards.undo_change"
    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None


class RemoveChange(BaseAction):
    """Remove any change of a network's draft card (``index``, from 0), network edits included.

    The draft's changes from ``index`` on are undone, newest first, and the later ones are applied again in order, so
    the network ends as if the removed change had never been made. A later change that depends on the removed one (a
    property change on a link it added, an ``existing`` value it set) no longer applies: the Action is then refused
    and nothing changes. The network's newest edit must be the draft's, as for ``project_cards.undo_change``.
    """

    type: Literal["project_cards.remove_change"] = "project_cards.remove_change"
    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None
    index: int = Field(ge=0)


class EditCard(BaseAction):
    """Load a catalog card into a network's draft to edit it: its changes are applied, as ``project_cards.import`` does.

    The draft takes the card's project, tags, dependencies and notes, and remembers the card's file, so a commit can
    save over it. A draft with uncommitted changes is refused unless ``replace_draft``: its changes are then undone
    first (network edits included). The card is read from its file again; all or nothing.
    """

    type: Literal["project_cards.edit_card"] = "project_cards.edit_card"
    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None
    card_id: str = Field(min_length=1, max_length=1000)
    replace_draft: bool = False


class CommitCard(BaseAction):
    """Write a network's draft card as a ProjectCard YAML file and start a new, empty draft.

    Live edit warnings must be confirmed (``confirm_warnings``), the same rule core applies before saving a copy.
    ``path`` must be inside ``io.allowed_roots``; an existing file is replaced only with ``overwrite`` (a draft loaded
    with ``project_cards.edit_card`` is saved over its card this way). When the ``catalog`` plugin is installed, the
    card is also added to it (``catalog.add_card``); when the path is inside the open catalog's folder, the catalog is
    rescanned so the card is listed.
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
    the card's notes when the plugin wrote it; a query without them is refused ("query without provenance;
    re-resolution not supported"). ``path`` must be inside ``io.allowed_roots``. An empty draft takes the card's
    project, tags, dependencies and notes.
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
    assistant_compact: ClassVar[bool] = True


class OpenCatalog(BaseAction):
    """Open a folder of ProjectCard files (YAML or JSON, one level of sub-folders) as the catalog.

    ``path`` must be inside ``io.allowed_roots``. Each card is listed with its id (the path relative to the folder),
    project, tags and change count; files that are not cards are listed as rejected, with the reason. Whether a card
    applies to the open network is checked on demand (``project_cards.check_card``, or the panel's listing). The
    plugin setting ``catalog_dir`` opens a folder when the session starts.
    """

    type: Literal["project_cards.open_catalog"] = "project_cards.open_catalog"
    path: str = Field(min_length=1, max_length=1000)


class RefreshCatalog(BaseAction):
    """Read the open catalog's folder again (new, changed and removed files); every card is checked afresh."""

    type: Literal["project_cards.refresh_catalog"] = "project_cards.refresh_catalog"


class CheckCard(BaseAction):
    """Check whether a catalog card applies to a network as it is now, without changing anything.

    Reports, per change, how many target records the network has, the ids it lacks, and a reason when the change
    cannot be applied (a query without netstead provenance, a transit change, an added id already taken).
    """

    type: Literal["project_cards.check_card"] = "project_cards.check_card"
    net_id: str | None = None
    card_id: str = Field(min_length=1, max_length=1000)


class PreviewCard(BaseAction):
    """Preview a catalog card on a copy of a network, shown on the map; the network itself is untouched.

    Every change of the card is applied to the copy (a change that fails closes the preview and reports why). The
    links and nodes the card aims at are published as ``project_cards.card_selection`` so the map highlights them.
    A second preview replaces the first; ``project_cards.close_preview`` returns to the network.
    """

    type: Literal["project_cards.preview_card"] = "project_cards.preview_card"
    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None
    card_id: str = Field(min_length=1, max_length=1000)


class ApplyCard(BaseAction):
    """Apply a catalog card to a network, all or nothing, and record it as applied; no file is written.

    Each change goes through the same lowering as a draft change (one pending edit per ``host.mutate``), so core's
    history, Edits panel and live warnings see it. The card is checked first (``project_cards.check_card``): a
    missing id refuses it unless ``ignore_missing`` is true, which skips missing ids in every change; false refuses
    any missing id, and the default (``null``) follows each selection's own ``ignore_missing``. A change that cannot
    be applied at all (a query without provenance) is always refused. ``existing_value_conflict`` overrides every
    property's ``existing_value_conflict`` for this application (``null``: as the card says).

    Applying is off unless ``[plugins.project_cards] enable_apply`` is true: it is refused with "applying cards is
    turned off (plugins.project_cards.enable_apply)". The assistant is never offered it (``assistant = False``): the
    setting is per session, the tool list is per Action class.
    """

    type: Literal["project_cards.apply_card"] = "project_cards.apply_card"
    mutates: ClassVar[bool] = True
    assistant: ClassVar[bool] = False
    assistant_compact: ClassVar[bool] = True
    net_id: str | None = None
    card_id: str = Field(min_length=1, max_length=1000)
    existing_value_conflict: Literal["error", "warn", "skip"] | None = None
    ignore_missing: bool | None = None


class _CatalogFileAction(BaseAction):
    """Fields an Action that changes a catalog card's file shares (not an Action itself: it has no ``type``).

    These change files, not a network (``mutates`` is false), and are kept out of the assistant's tools: a file is
    renamed, copied, trashed or rewritten only when the user asks. Each is recorded in history and listed in the
    session summary; the catalog is rescanned afterwards.
    """

    assistant: ClassVar[bool] = False
    card_id: str = Field(min_length=1, max_length=1000)


#: A new card file name: a file name (``main-v2.yml``) or a path relative to the catalog folder (``sub/main.yml``).
_NAME = Field(min_length=1, max_length=255)


class RenameCard(_CatalogFileAction):
    """Rename a catalog card's file (``new_name``: a name in the catalog folder, or ``sub/name.yml``).

    A name without a suffix keeps the card's (``.yml``, ``.yaml`` or ``.json``). The new path must stay inside the
    catalog folder (one level of sub-folders, nothing hidden) and ``io.allowed_roots``; an existing different file
    there is replaced only with ``overwrite``. The card is validated with ``projectcard`` first.
    """

    type: Literal["project_cards.rename_card"] = "project_cards.rename_card"
    new_name: str = _NAME
    overwrite: bool = False


class DuplicateCard(_CatalogFileAction):
    """Copy a catalog card to a new file: ``new_name``, or ``<name>-copy.yml`` (``-copy-2``, … when taken).

    The same rules as ``project_cards.rename_card``; the copy is byte for byte.
    """

    type: Literal["project_cards.duplicate_card"] = "project_cards.duplicate_card"
    new_name: str | None = Field(default=None, min_length=1, max_length=255)
    overwrite: bool = False


class DeleteCard(_CatalogFileAction):
    """Move a catalog card's file to the ``.trash`` folder inside the catalog folder (never a hard delete).

    ``project_cards.restore_card`` puts it back. A card already in the trash under that name gets ``-2``, ``-3``, …
    """

    type: Literal["project_cards.delete_card"] = "project_cards.delete_card"


class RestoreCard(_CatalogFileAction):
    """Move a card ``project_cards.delete_card`` trashed back to where it was (``card_id``: its id then).

    The newest deletion of that card this session is restored, else ``.trash/<card_id>``. A different file now at
    its place is replaced only with ``overwrite``.
    """

    type: Literal["project_cards.restore_card"] = "project_cards.restore_card"
    overwrite: bool = False


class UpdateCardMeta(_CatalogFileAction):
    """Change a catalog card's project, tags and dependencies, and save the file (``None``: left as it is).

    Only those keys of the file change (the changes and notes are kept as they are; comments are not), and the card
    is validated with ``projectcard`` before it is written.
    """

    type: Literal["project_cards.update_card_meta"] = "project_cards.update_card_meta"
    project: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[str] | None = None
    dependencies: dict[Literal["prerequisites", "corequisites", "conflicts"], list[str]] | None = None


class SessionSummary(BaseAction):
    """The session summary as plain text: the network, the cards applied, the draft, and this plugin's Actions.

    Returns ``{"text": ...}``; ``GET /api/plugins/project_cards/summary.txt`` serves the same text.
    """

    type: Literal["project_cards.session_summary"] = "project_cards.session_summary"
    net_id: str | None = None
