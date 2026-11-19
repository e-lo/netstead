"""Cards made from the Changes pane's changes (design 2e): core's pending edits as ProjectCard changes.

``host.edit_changes`` (Host API 1.11) says what each pending edit did (``ops``: ``update``, ``add``, ``delete``).
:func:`entry_changes` turns one edit into card changes: an update of links or nodes is a ``roadway_property_change``
whose facility is a **Link list** (the edit names its rows by id, so there is no Segment) in GMNS or, for links,
OpenStreetMap ids (core's strict forms, ``op["projectcard"]``), with each field's ``existing`` value when the rows
agreed; a deletion is a ``roadway_deletion`` (the rows that went with a link, its lanes, are not listed: they go
with it); an addition is a ``roadway_addition``. :func:`year_tags` writes the Year field as a ``year:2030`` tag.

Where the card goes is a :class:`CardDestination`: a folder (:class:`FolderDestination`, the open catalog or another
one, made if new), a download or the clipboard (:class:`DownloadDestination`, :class:`CopyDestination`: the browser
takes the YAML from the Action's result). A card registry is one more destination (design 2f).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .card import Dependencies, DraftCard
from .errors import CardError
from .facility import GERS_REFUSAL, OSM_KEY
from .flow import facility_options
from .lower import describe
from .types import NetworkChange, PropertyChange, RoadwayAddition, RoadwayDeletion, RoadwayPropertyChange, Selection

__all__ = [
    "CardDestination",
    "CardFile",
    "CopyDestination",
    "Delivered",
    "Destination",
    "DownloadDestination",
    "FacilityChoice",
    "FolderDestination",
    "card_file_name",
    "changes_card",
    "entry_changes",
    "entry_options",
    "entry_rows",
    "year_of",
    "year_tags",
]

#: The tables a card holds changes to.
CARD_TABLES = ("link", "node")
NODE_OSM = "Node changes are written in GMNS ids"
NO_FORMS = "These links can't be written in other ids"
_YEAR = re.compile(r"^(?:year:)?(\d{4})$")


class FacilityChoice(BaseModel):
    """How an edit's facility is written: its form and its id system (the dialog's choice per change)."""

    model_config = ConfigDict(extra="forbid")
    form: Literal["segment", "link_list"] = "link_list"
    id_system: Literal["gmns", "osm", "gers"] = "gmns"


# ---------------------------------------------------------------- the year tag


def year_of(tags: Sequence[str]) -> int | None:
    """The year a card's tags give: a ``year:2030`` tag, or a bare four-digit one (``2030``); ``None`` without one.

    >>> year_of(["bike", "2030"]), year_of(["year:2035"]), year_of(["phase 2"])
    (2030, 2035, None)
    """
    for tag in tags:
        match = _YEAR.match(str(tag).strip())
        if match:
            return int(match[1])
    return None


def year_tags(tags: Sequence[str], year: int | None) -> list[str]:
    """``tags`` with the year written as ``year:<year>``, replacing any year tag they had (``None``: none added).

    >>> year_tags(["bike", "2030"], 2035)
    ['bike', 'year:2035']
    """
    kept = [t for t in tags if not _YEAR.match(str(t).strip())]
    return [*kept, f"year:{year}"] if year is not None else kept


# ---------------------------------------------------------------- edits as card changes


def _node_options() -> dict[str, Any]:
    """A node change's choices: a Link list in GMNS ids."""
    return {
        "forms": {"segment": "Nodes have no segment form; use Link list", "link_list": None},
        "id_systems": {"gmns": None, "osm": NODE_OSM, "gers": GERS_REFUSAL},
        "form": "link_list",
        "id_system": "gmns",
        "picked": True,
    }


def _op_options(op: Mapping[str, Any]) -> dict[str, Any]:
    """What an update's facility may be written as (``flow.facility_options``'s shape)."""
    forms = op.get("projectcard")
    if op["table"] == "link" and forms:
        return facility_options(forms)
    options = _node_options()
    if op["table"] == "link":
        options["id_systems"]["osm"] = NO_FORMS
    return options


def entry_options(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    """The facility choices of an edit's first update of links or nodes (``None``: it has none to choose)."""
    op = next((o for o in entry["ops"] if o["op"] == "update" and o["table"] in CARD_TABLES), None)
    return _op_options(op) if op is not None else None


def _choose(options: dict[str, Any], choice: FacilityChoice) -> None:
    """Refuse a form or id system the options say is unavailable, with the reason."""
    for reason in (options["forms"][choice.form], options["id_systems"][choice.id_system]):
        if reason:
            raise CardError(reason)


def _facility(op: Mapping[str, Any], choice: FacilityChoice) -> Selection:
    _choose(_op_options(op), choice)
    table, keys = op["table"], list(op["keys"])
    if choice.id_system == "osm":  # links only (_choose refused nodes): the ways core wrote them as
        ways = [str(w) for w in op["projectcard"]["link_list"]["osm"]["links"][OSM_KEY["link"]]]
        return Selection(ids=ways, id_system="osm", resolved=keys, ignore_missing=False)
    return Selection(table=table, ids=keys, ignore_missing=False)


def _update(op: Mapping[str, Any], choice: FacilityChoice, note: str | None) -> RoadwayPropertyChange:
    existing = op.get("existing") or {}
    props = {}
    for name, value in op["set"].items():
        if value is None:
            raise CardError(f"{op['table']} {name} was cleared: a ProjectCard cannot set a property to empty")
        props[name] = PropertyChange(set=value, existing=existing.get(name))
    return RoadwayPropertyChange(facility=_facility(op, choice), property_changes=props, note=note)


def entry_changes(entry: Mapping[str, Any], choice: FacilityChoice | None = None) -> list[NetworkChange]:
    """The card changes for one pending edit (an ``host.edit_changes`` entry), its facility written as ``choice``.

    Raises ``CardError`` (naming the edit) for an edit a card can't hold: a change to a table other than links and
    nodes, a cleared value, or a facility choice that is unavailable.
    """
    choice = choice or FacilityChoice()
    note = " ".join(str(entry.get("note") or "").split())[:500] or None
    where = f"#{entry['seq']}"
    ops = list(entry["ops"])
    deleted = {t: [k for o in ops if o["op"] == "delete" and o["table"] == t for k in o["keys"]] for t in CARD_TABLES}
    out: list[NetworkChange] = []
    try:
        for op in ops:
            table = op["table"]
            if op["op"] == "delete" and (deleted["link"] or deleted["node"]):
                if not any(isinstance(c, RoadwayDeletion) for c in out):
                    out.append(RoadwayDeletion(links=deleted["link"], nodes=deleted["node"], note=note))
                continue  # a deleted link's lanes and time-of-day rows go with it
            if table not in CARD_TABLES:
                raise CardError(f"a change to the {table} table can't go in a card: cards hold link and node changes")
            if op["op"] == "update":
                out.append(_update(op, choice, note))
            elif op["op"] == "add" and not any(isinstance(c, RoadwayAddition) for c in out):
                added = {
                    t: [_record(r) for o in ops if o["op"] == "add" and o["table"] == t for r in o["rows"]]
                    for t in CARD_TABLES
                }
                out.append(RoadwayAddition(links=added["link"], nodes=added["node"], note=note))
            elif op["op"] not in ("add", "update", "delete"):
                raise CardError(f"a {op['op']} of {table} can't go in a card")
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False)
        raise CardError(f"{where}: " + errors[0]["msg"].removeprefix("Value error, ")) from exc
    except CardError as exc:
        raise CardError(f"{where}: {exc}") from exc
    return out


def _record(row: Mapping[str, Any]) -> dict[str, Any]:
    """An added row as a card record: its filled fields."""
    return {k: v for k, v in row.items() if v is not None and v != ""}


def entry_rows(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The card dialog's list: each edit with what it becomes in a card, its facility choices, or why it can't go in.

    Each row: ``seq``, ``note``, ``source``, ``saved``, ``saved_to``, ``card``, ``rows``, ``changes`` (described as the
    draft card lists them), ``options`` (``None`` when there is no facility to choose) and ``problem``.
    """
    rows = []
    for entry in entries:
        options = entry_options(entry)
        default = FacilityChoice(form=options["form"], id_system=options["id_system"]) if options else None
        try:
            described, problem = [describe(c) for c in entry_changes(entry, default)], None
        except CardError as exc:
            described, problem = [], str(exc)
        keep = ("seq", "note", "source", "saved", "saved_to", "card", "rows")
        rows.append({**{k: entry.get(k) for k in keep}, "changes": described, "options": options, "problem": problem})
    return rows


def changes_card(
    entries: Sequence[Mapping[str, Any]],
    choices: Mapping[int, FacilityChoice],
    *,
    project: str,
    year: int | None = None,
    tags: Sequence[str] = (),
    dependencies: Mapping[str, list[str]] | None = None,
    notes: str | None = None,
) -> DraftCard:
    """The card the edits make, in their order, with the dialog's About fields (``CardError`` when one can't go in)."""
    changes = [c for e in entries for c in entry_changes(e, choices.get(int(e["seq"])))]
    if not changes:
        raise CardError("these changes make no card changes")
    return DraftCard(
        project=project,
        tags=year_tags([t.strip() for t in tags if t.strip()], year),
        dependencies=Dependencies(**(dependencies or {})),
        notes=notes or None,
        changes=changes,
    )


def card_file_name(project: str, file: str | None = None) -> str:
    """The card's file name: ``file`` (``.yml`` added when it has no card suffix), else the project's slug.

    >>> card_file_name("Main Street road diet"), card_file_name("x", "diet")
    ('main-street-road-diet.yml', 'diet.yml')
    """
    name = (file or "").strip() or re.sub(r"[^a-z0-9]+", "-", project.lower()).strip("-") or "card"
    return name if Path(name).suffix.lower() in (".yml", ".yaml", ".json") else f"{name}.yml"


# ---------------------------------------------------------------- where the card goes


@dataclass(frozen=True)
class CardFile:
    """A card ready to go: its YAML, its file name, and how to write it at a path (the plugin's commit code)."""

    text: str
    name: str
    #: ``write(path, overwrite) -> {path, catalog, listed}``: writes the file, adds it to the catalog, rolls back.
    write: Callable[[str, bool], dict[str, Any]] = field(repr=False)


@dataclass(frozen=True)
class Delivered:
    """Where a card went: ``where`` is what the Changes pane says the changes are in; ``result`` joins the answer."""

    where: str
    result: dict[str, Any]


class CardDestination(Protocol):
    """Where a card made from changes goes (design 2e, "Where"); a card registry (2f) is one more.

    ``kind`` names it in the Action (``destination.kind``). ``deliver`` runs inside the Action, after every check:
    it writes the card (or hands its YAML back) and says where it went. A failure raises ``ActionError`` or
    ``CardError``; anything it wrote is taken back by the rollback ``card.write`` registers.
    """

    kind: str

    def deliver(self, card: CardFile) -> Delivered:
        """Send ``card`` here; say where it went."""
        ...


class FolderDestination(BaseModel):
    """A catalog folder (the open one, another, or a new one: made when missing), inside ``io.allowed_roots``.

    A bundled sample catalog is read-only, so it is refused. The folder becomes the open catalog unless it is.
    """

    model_config = ConfigDict(extra="forbid")
    kind: Literal["folder"] = "folder"
    folder: str = Field(min_length=1, max_length=1000)
    overwrite: bool = False

    def deliver(self, card: CardFile) -> Delivered:
        """Write the card into the folder."""
        result = card.write(str(Path(self.folder) / card.name), self.overwrite)
        return Delivered(result["path"], result)


class DownloadDestination(BaseModel):
    """Download the ``.yml``: the browser saves the YAML the Action returns."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["download"] = "download"

    def deliver(self, card: CardFile) -> Delivered:
        """Hand the YAML back to download."""
        return Delivered(f"{card.name} (downloaded)", {"yaml": card.text, "file": card.name})


class CopyDestination(BaseModel):
    """Copy the YAML to the clipboard: the browser copies the YAML the Action returns."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["copy"] = "copy"

    def deliver(self, card: CardFile) -> Delivered:
        """Hand the YAML back to copy."""
        return Delivered("copied YAML", {"yaml": card.text, "file": card.name})


Destination = Annotated[FolderDestination | DownloadDestination | CopyDestination, Field(discriminator="kind")]
