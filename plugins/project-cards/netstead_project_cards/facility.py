"""A change's facility in one of the two strict forms, and the id systems it is written in.

**Segment** names the facility (``name`` / ``ref``) with ``from`` / ``to`` anchors, so a re-import finds it again;
**Link list** has only ids. Either is written in GMNS ids (``model_link_id`` / ``model_node_id``) or OpenStreetMap
ids (``osm_link_id`` / ``osm_node_id``, text). ProjectCard has no key for Overture GERS ids: those are refused.
Core builds the card forms (``host.selection["projectcard"]``); this module reads them back, resolves an OpenStreetMap
list against a network's ``osm_way_id`` / ``osm_node_id`` columns, and says in words what a selection is.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import CardError
from .mapping import load_mapping
from .types import Selection

__all__ = [
    "FORMS",
    "GERS_REFUSAL",
    "OSM_COLUMN",
    "OSM_KEY",
    "OSM_NOUN",
    "SYSTEM_LABELS",
    "facility_text",
    "query_id_system",
    "resolve_osm",
    "resolve_selection",
]

#: The two forms a change's facility is written in.
FORMS = ("segment", "link_list")
#: How each id system is named for a person.
SYSTEM_LABELS = {"gmns": "GMNS", "osm": "OpenStreetMap"}
GERS_REFUSAL = "ProjectCard has no key for GERS ids; use GMNS or OpenStreetMap ids"
#: The network column that holds the OpenStreetMap id, per table, and the card key the ids are written under.
OSM_COLUMN = {"link": "osm_way_id", "node": "osm_node_id"}
OSM_KEY = {"link": "osm_link_id", "node": "osm_node_id"}
#: What an OpenStreetMap id of each table is called.
OSM_NOUN = {"link": "OSM way", "node": "OSM node"}


def query_id_system(query: Mapping[str, Any]) -> str:
    """The id system a segment query's anchors are written in (``osm`` when one is an ``osm_node_id``).

    >>> query_id_system({"links": {"name": ["Main"]}, "from": {"osm_node_id": "7"}})
    'osm'
    >>> query_id_system({"links": {"name": ["Main"]}})
    'gmns'
    """
    anchors = [query.get("from"), query.get("to")]
    return "osm" if any(isinstance(a, Mapping) and "osm_node_id" in a for a in anchors) else "gmns"


def _osm_text(value: Any) -> str:
    """An OpenStreetMap id as text: ``5836396.0`` and ``"5836396"`` are the same id; a missing cell is ``""``."""
    try:
        if value is None or bool(value != value):  # None, NaN
            return ""
    except TypeError:  # pandas' NA has no truth value
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def resolve_osm(frame: Any, table: str, osm_ids: Sequence[Any]) -> tuple[list[Any], list[Any]]:
    """``(the GMNS keys of the rows carrying these OpenStreetMap ids, the ids no row carries)``.

    A way is split at each intersection, so one ``osm_link_id`` can give several links (all of them, in table order).

    Raises:
        CardError: the table has no ``osm_way_id`` / ``osm_node_id`` column.
    """
    column = OSM_COLUMN[table]
    if column not in frame.columns:
        raise CardError(f"the {table} table has no {column} column, so OpenStreetMap ids cannot be resolved")
    key = load_mapping().selector(table)[0]
    wanted = {str(i) for i in osm_ids}
    text = frame[column].map(_osm_text)
    hit = text.isin(wanted)
    keys = [k.item() if hasattr(k, "item") else k for k in frame.loc[hit, key]]
    present = set(text[hit])
    return keys, [i for i in osm_ids if str(i) not in present]


def resolve_selection(sel: Selection, frame: Any) -> tuple[Selection, list[Any]]:
    """``sel`` with its OpenStreetMap ids resolved on ``frame`` (its table), and the ids not found.

    A selection that does not need the network comes back as it is.
    """
    if not sel.needs_network or sel.ids is None:
        return sel, []
    keys, absent = resolve_osm(frame, sel.table, sel.ids)
    return sel.model_copy(update={"resolved": keys or None}), absent


def _anchors(query: Mapping[str, Any]) -> str:
    """``", node 33 → 19"`` for a segment's anchors (either may be missing), ``""`` without any."""
    ends = []
    for role in ("from", "to"):
        anchor = query.get(role)
        value = next(iter(anchor.values()), None) if isinstance(anchor, Mapping) else None
        ends.append("?" if value is None else str(value))
    return "" if ends == ["?", "?"] else f", node {ends[0]} → {ends[1]}"


def facility_text(sel: Selection) -> str:
    """What a selection is, in words: ``Segment: Pine Street, node 33 → 19 (GMNS)``, ``Link list: 3 links (GMNS)``.

    >>> facility_text(Selection(ids=[4]))
    'Link list: 1 link (GMNS)'
    >>> facility_text(Selection(ids=["77", "78"], id_system="osm"))
    'Link list: 2 OSM ways (OpenStreetMap)'
    """
    system = f"({SYSTEM_LABELS[sel.id_system]})"
    if sel.ids is not None:
        n = len(sel.ids)
        noun = OSM_NOUN[sel.table] if sel.id_system == "osm" else sel.table
        head = "Link list" if sel.table == "link" else "Node list"
        return f"{head}: {n} {noun}{'' if n == 1 else 's'} {system}"
    query = sel.query or {}
    links = {k: v for k, v in (query.get("links") or {}).items() if k != "ignore_missing"}
    named = [str(n) for key in ("name", "ref") for n in links.get(key) or []]
    if not named:
        return f"Links matching {json.dumps(links, sort_keys=True)} {system}"
    flag = "" if sel.resolved is not None else ", no provenance"
    return f"Segment: {' and '.join(named)}{_anchors(query)} {system}{flag}"
