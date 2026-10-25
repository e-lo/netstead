"""The change model: one record per ProjectCard change type, discriminated on ``type``.

Records hold GMNS names (``link_id``, ``from_node_id``); :mod:`netstead_project_cards.card` translates to and from a
card's Wrangler names through the mapping file. The four transit types are declared so the union, the Action schema
and the assistant's tool vocabulary are stable; applying or writing one answers "not yet supported" until the transit
component lands (P6).

Values follow ProjectCard: a property's ``existing`` / ``set`` is one number or string. Booleans become ``1`` / ``0``;
an empty value or a list is refused ("a ProjectCard cannot set a property to empty"). Clearing a cell is a core edit,
never a card change. (``projectcard``'s validator does not check property values itself: this model is the check.)

The change-type names come from ``projectcard`` itself. Its own models (``projectcard.models``) are not reused: the
package's ``ProjectModel`` does not import (an absolute ``from changes import``), its submodules emit pydantic
deprecation warnings at import (fatal under this repo's warnings-as-errors tests), they enforce neither set-or-change
nor scalar values, ``SelectRoadNode`` rejects a ``model_node_id``, and they hold Wrangler names where the Actions,
forms and assistant work in GMNS names.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from projectcard.projectcard import CHANGE_TYPES as _PROJECTCARD_TYPES
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

__all__ = [
    "CHANGE_TYPES",
    "EMPTY_VALUE",
    "LIST_VALUE",
    "ROADWAY_TYPES",
    "TRANSIT_TYPES",
    "UNRESOLVED_QUERY",
    "Id",
    "NetworkChange",
    "PropertyChange",
    "RoadwayAddition",
    "RoadwayDeletion",
    "RoadwayPropertyChange",
    "Scalar",
    "Selection",
    "TransitPropertyChange",
    "TransitRouteAddition",
    "TransitRoutingChange",
    "TransitServiceDeletion",
    "card_scalar",
    "incomplete",
    "parse_change",
]

#: A value a ProjectCard property holds.
Scalar = int | float | str
#: A record id (GMNS ``link_id`` / ``node_id``).
Id = int | str

EMPTY_VALUE = "a ProjectCard cannot set a property to empty"
#: Why a query selection read from a card without netstead provenance cannot be applied in this version.
UNRESOLVED_QUERY = "query without provenance; re-resolution not supported"
LIST_VALUE = "a ProjectCard cannot set a property to a list: give one number or string"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def card_scalar(value: Any) -> Any:
    """``value`` as a card holds it: a bool becomes 1/0; an empty string or a list raises ``ValueError``.

    >>> card_scalar(True), card_scalar("tertiary"), card_scalar(30)
    (1, 'tertiary', 30)
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, list | tuple | set | dict):
        raise ValueError(LIST_VALUE)
    if isinstance(value, str) and not value.strip():
        raise ValueError(EMPTY_VALUE)
    return value


class PropertyChange(_Model):
    """One property: ``set`` it, or ``change`` it by a numeric delta; ``existing`` is checked first when given."""

    existing: Scalar | None = None
    set: Scalar | None = None
    change: int | float | None = None  # an int delta stays an int (``change: 1`` round-trips as ``1``)
    existing_value_conflict: Literal["error", "warn", "skip"] = "error"

    @field_validator("existing", "set", mode="before")
    @classmethod
    def _card_value(cls, value: Any) -> Any:
        return None if value is None else card_scalar(value)

    @field_validator("change", mode="before")
    @classmethod
    def _delta(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("change is a number: the delta to add")
        if isinstance(value, int | float):
            return value
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError("change is a number: the delta to add") from None

    @model_validator(mode="after")
    def _set_or_change(self) -> PropertyChange:
        if self.set is None and self.change is None:
            raise ValueError(f"{EMPTY_VALUE} (give set or change)")
        if self.set is not None and self.change is not None:
            raise ValueError("give set or change, not both")
        return self


class Selection(_Model):
    """Which records a property change applies to: one of two strict forms, in one id system.

    **Link list** (``ids``): the records by id only. In GMNS ids (``id_system="gmns"``, the default) ``ids`` are the
    ``link_id`` / ``node_id`` values themselves, written with ``ignore_missing`` (``False`` for a pick: a fix aimed at
    a specific link should fail loudly when that link is gone). In OpenStreetMap ids (``id_system="osm"``) ``ids``
    are ``osm_way_id`` / ``osm_node_id`` values (text, as ProjectCard has them) and ``resolved`` the GMNS ids they
    cover on the network (several links can share a way): it is filled when the change is made, or by
    :func:`~netstead_project_cards.facility.resolve_selection` on the open network when a card is read from a file.

    **Segment** (``query``): the facility by ``name`` / ``ref`` with the ``from`` / ``to`` anchors, in card form
    (``host.selection["projectcard"]["segment"][id_system]``, which carries its own ``ignore_missing``: the
    selection's ``ignore_missing`` follows it, true when absent). ``resolved`` is the link ids it resolved to when the
    change was made (provenance). A segment applies to its ``resolved`` ids. One without them (a card written by
    another tool) is :attr:`unresolved`: it reads and writes, but applying it is refused with :data:`UNRESOLVED_QUERY`
    until re-resolving a query on the open network is supported.
    """

    table: Literal["link", "node"] = "link"
    ids: list[Id] | None = None
    query: dict[str, Any] | None = None
    resolved: list[Id] | None = None
    ignore_missing: bool = False
    id_system: Literal["gmns", "osm"] = "gmns"

    @model_validator(mode="before")
    @classmethod
    def _query_flag(cls, data: Any) -> Any:
        """A query's ``ignore_missing`` is its ``links.ignore_missing`` (true, Wrangler's default, when absent)."""
        query = data.get("query") if isinstance(data, dict) else None
        if not isinstance(query, dict) or not isinstance(query.get("links"), dict):
            return data
        flag = bool(query["links"].get("ignore_missing", True))
        given = data.get("ignore_missing")
        if given is not None and bool(given) != flag:
            raise ValueError(f"ignore_missing={given!r} contradicts the query's ignore_missing ({flag})")
        return {**data, "ignore_missing": flag}

    @model_validator(mode="after")
    def _ids_or_query(self) -> Selection:
        if (self.ids is None) == (self.query is None):
            raise ValueError("give exactly one of ids or query")
        if self.ids is not None and not self.ids:
            raise ValueError("ids must not be empty")
        if self.ids is not None and self.resolved is not None and self.id_system == "gmns":
            raise ValueError("resolved records what a query or OpenStreetMap ids matched: give it with those")
        if self.query is not None and (self.table != "link" or not isinstance(self.query.get("links"), dict)):
            raise ValueError("a query selects links: its 'links' must be a mapping")
        if self.resolved is not None and not self.resolved:
            raise ValueError("resolved records the ids a selection matched: give some, or leave it out")
        return self

    @property
    def form(self) -> Literal["link_list", "segment", "query"]:
        """``link_list`` (ids), ``segment`` (a query naming its facility), or ``query`` (another kind of query)."""
        if self.ids is not None:
            return "link_list"
        links = (self.query or {}).get("links") or {}
        return "segment" if links.get("name") or links.get("ref") else "query"

    @property
    def unresolved(self) -> bool:
        """Whether this is a query with no provenance (no ``resolved`` ids), which cannot be applied yet."""
        return self.query is not None and self.resolved is None

    @property
    def needs_network(self) -> bool:
        """Whether this is a list of OpenStreetMap ids not yet resolved to GMNS ids (the open network does that)."""
        return self.ids is not None and self.id_system == "osm" and self.resolved is None

    def target_ids(self) -> list[Id]:
        """The GMNS ids this selection applies to now (none for an :attr:`unresolved` one or an unresolved OSM list)."""
        if self.ids is not None and self.id_system == "gmns":
            return list(self.ids)
        return list(self.resolved or [])


class RoadwayPropertyChange(_Model):
    """Change properties of selected links or nodes (ProjectCard ``roadway_property_change``).

    ``facility`` may be left out when ``project_cards.add_change`` takes it from a selection (``use_selection`` /
    ``saved_selection``).
    """

    type: Literal["roadway_property_change"] = "roadway_property_change"
    facility: Selection | None = None
    property_changes: dict[str, PropertyChange] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=500)  # why (an issue code, a reason): written in the notes


class RoadwayDeletion(_Model):
    """Delete links and/or nodes by id (ProjectCard ``roadway_deletion``).

    A deleted link takes the rows that depend on it (lanes, time-of-day rows), as core plans it. ``clean_nodes`` also
    deletes the deleted links' end nodes that no remaining link uses. ``links`` may be left out when
    ``project_cards.add_change`` takes them from a selection.

    ``ignore_missing`` is the ``links`` selector's: ``True`` skips a link id the network does not have (listed as
    skipped), ``False`` refuses the change. ``False`` is the default; ``project_cards.add_change`` sets it ``False``
    for a picked selection and takes the query's own flag for a query selection, and import reads it from the card's
    ``links``. Nodes are always aimed at specific records, so a missing node always refuses the change (and the card
    writes ``ignore_missing: false`` for them).
    """

    type: Literal["roadway_deletion"] = "roadway_deletion"
    links: list[Id] = Field(default_factory=list)
    nodes: list[Id] = Field(default_factory=list)
    clean_nodes: bool = False
    ignore_missing: bool = False
    note: str | None = Field(default=None, max_length=500)


_REQUIRED = {"links": ("link_id", "from_node_id", "to_node_id"), "nodes": ("node_id", "x_coord", "y_coord")}


class RoadwayAddition(_Model):
    """Add GMNS link and/or node records (ProjectCard ``roadway_addition``); nodes are added before links."""

    type: Literal["roadway_addition"] = "roadway_addition"
    links: list[dict[str, Any]] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _records(self) -> RoadwayAddition:
        if not self.links and not self.nodes:
            raise ValueError("give links and/or nodes to add")
        for kind, required in _REQUIRED.items():
            for record in getattr(self, kind):
                missing = [k for k in required if record.get(k) is None]
                if missing:
                    raise ValueError(f"every added {kind[:-1]} needs {', '.join(missing)}")
                for name, value in record.items():
                    if value is not None:
                        try:
                            card_scalar(value)
                        except ValueError as exc:
                            raise ValueError(f"added {kind[:-1]} field {name!r}: {exc}") from None
        return self


class _Transit(_Model):
    """A transit change: declared so the union is stable; its fields arrive with the transit component (P6)."""

    body: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=500)


class TransitPropertyChange(_Transit):
    """ProjectCard ``transit_property_change`` (not yet supported)."""

    type: Literal["transit_property_change"] = "transit_property_change"


class TransitRoutingChange(_Transit):
    """ProjectCard ``transit_routing_change`` (not yet supported)."""

    type: Literal["transit_routing_change"] = "transit_routing_change"


class TransitRouteAddition(_Transit):
    """ProjectCard ``transit_route_addition`` (not yet supported)."""

    type: Literal["transit_route_addition"] = "transit_route_addition"


class TransitServiceDeletion(_Transit):
    """ProjectCard ``transit_service_deletion`` (not yet supported)."""

    type: Literal["transit_service_deletion"] = "transit_service_deletion"


NetworkChange = Annotated[
    RoadwayPropertyChange
    | RoadwayDeletion
    | RoadwayAddition
    | TransitPropertyChange
    | TransitRoutingChange
    | TransitRouteAddition
    | TransitServiceDeletion,
    Field(discriminator="type"),
]
_ADAPTER: TypeAdapter[NetworkChange] = TypeAdapter(NetworkChange)

#: Every change type, as the installed projectcard lists them, without ``pycode`` (not supported).
#: ``roadway_managed_lanes`` reuses the property change and is out of scope.
CHANGE_TYPES: tuple[str, ...] = tuple(t for t in _PROJECTCARD_TYPES if t != "pycode")
ROADWAY_TYPES = frozenset(t for t in CHANGE_TYPES if t.startswith("roadway_"))
TRANSIT_TYPES = frozenset(t for t in CHANGE_TYPES if t.startswith("transit_"))


def parse_change(data: dict[str, Any]) -> NetworkChange:
    """Validate a JSON dict into a :data:`NetworkChange` (raises ``pydantic.ValidationError``)."""
    return _ADAPTER.validate_python(data)


def incomplete(change: Any) -> str | None:
    """Why ``change`` cannot be applied or written as it stands (``None`` when it can)."""
    if isinstance(change, RoadwayPropertyChange) and change.facility is None:
        return "a roadway_property_change needs a facility (or use_selection / saved_selection)"
    if isinstance(change, RoadwayDeletion) and not change.links and not change.nodes:
        return "a roadway_deletion needs links and/or nodes (or use_selection / saved_selection)"
    return None
