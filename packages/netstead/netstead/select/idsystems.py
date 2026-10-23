"""Which id system a selection fragment speaks: GMNS ids, OpenStreetMap ids or Overture GERS ids.

A network always has GMNS ``link_id`` / ``node_id``. An OSM-built network also carries ``osm_way_id`` on its links
and ``osm_node_id`` on its nodes; an Overture-built one carries ``overture_segment_id`` / ``overture_connector_id``
(the GERS ids). :func:`map_ids` translates a resolved selection's link and anchor-node ids into one of those
systems. Several links can share one way or segment id (a way is split at each intersection), so the mapped link
ids are deduplicated and :class:`MappedIds` says which ones were shared.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from .errors import SelectError

__all__ = ["ID_SYSTEMS", "IdSystem", "MappedIds", "availability", "map_ids"]


@dataclass(frozen=True)
class IdSystem:
    """One id system: the table columns that hold it and the keys it is written under.

    ``pc_link_key`` / ``pc_node_key`` are the ProjectCard selection keys, ``None`` when ProjectCard has none.
    """

    key: str
    label: str
    link_col: str
    node_col: str
    pc_link_key: str | None
    pc_node_key: str | None
    #: Singular noun for a link-level id in a caption ("OSM way", "GERS segment").
    link_noun: str


ID_SYSTEMS: dict[str, IdSystem] = {
    s.key: s
    for s in (
        IdSystem("gmns", "GMNS (link_id, node_id)", "link_id", "node_id", "model_link_id", "model_node_id", "link"),
        IdSystem(
            "osm",
            "OpenStreetMap (osm_way_id, osm_node_id)",
            "osm_way_id",
            "osm_node_id",
            "osm_link_id",
            "osm_node_id",
            "OSM way",
        ),
        IdSystem(
            "gers",
            "Overture GERS (gers ids)",
            "overture_segment_id",
            "overture_connector_id",
            None,
            None,
            "GERS segment",
        ),
    )
}


@dataclass(frozen=True)
class MappedIds:
    """A selection's ids in one system.

    ``link_ids`` is deduplicated, in selection order. ``shared`` maps each id used by several selected links to
    that count; ``also_outside`` maps each id to the number of links outside the selection that carry it too (a
    link list in that system would select those as well).
    """

    system: IdSystem
    link_ids: list[Any]
    from_node: Any = None
    to_node: Any = None
    shared: dict[Any, int] = field(default_factory=dict)
    also_outside: dict[Any, int] = field(default_factory=dict)

    def captions(self) -> list[str]:
        """Plain-English notes on shared ids ([] for GMNS, whose ids are unique)."""
        noun = self.system.link_noun
        notes = [f"{n} links share {noun} {i}" for i, n in self.shared.items()]
        notes += [
            f"{noun} {i} also covers {n} {'link' if n == 1 else 'links'} outside this selection"
            for i, n in self.also_outside.items()
        ]
        return notes


def availability(links: pd.DataFrame, nodes: pd.DataFrame) -> dict[str, str | None]:
    """For each id system, ``None`` when this network has its columns, else why not (a tooltip)."""
    out: dict[str, str | None] = {}
    for key, system in ID_SYSTEMS.items():
        missing = [c for c, df in ((system.link_col, links), (system.node_col, nodes)) if c not in df]
        out[key] = None if not missing else f"This network has no {' or '.join(missing)} column"
    return out


def _plain(value: Any) -> Any:
    """A JSON-safe scalar: integral floats and numpy ints become ``int``, other numpy scalars their Python value."""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _has(value: Any) -> bool:
    return value is not None and not pd.isna(value)


def map_ids(
    system_key: str,
    link_ids: Sequence[Any],
    from_node: Any,
    to_node: Any,
    links: pd.DataFrame,
    nodes: pd.DataFrame,
) -> MappedIds:
    """The selection's link ids and anchor nodes in ``system_key``.

    Raises:
        SelectError: for an unknown system, a missing column, or a selected link or anchor node with no id there.
    """
    if system_key not in ID_SYSTEMS:
        raise SelectError(f"unknown id system {system_key!r}; expected one of {', '.join(ID_SYSTEMS)}")
    system = ID_SYSTEMS[system_key]
    reason = availability(links, nodes)[system_key]
    if reason:
        raise SelectError(reason)
    if system_key == "gmns":
        return MappedIds(system, [_plain(i) for i in link_ids], _plain(from_node), _plain(to_node))
    by_link = links.set_index("link_id")[system.link_col]
    found = [by_link.get(i) for i in link_ids]
    lacking = [i for i, m in zip(link_ids, found, strict=True) if not _has(m)]
    if lacking:
        raise SelectError(f"{len(lacking)} selected links have no {system.link_col} (first: link {_plain(lacking[0])})")
    mapped = [_plain(m) for m in found]
    counts = Counter(mapped)
    unique = list(dict.fromkeys(mapped))
    shared = {i: n for i, n in counts.items() if n > 1}
    on_ids = links[links[system.link_col].isin(unique)]
    elsewhere = on_ids.loc[~on_ids["link_id"].isin(set(link_ids)), system.link_col]
    outside = Counter(_plain(v) for v in elsewhere)
    by_node = nodes.set_index("node_id")[system.node_col]

    def node(n: Any) -> Any:
        if n is None:
            return None
        got = by_node.get(n)
        if not _has(got):
            raise SelectError(f"node {_plain(n)} has no {system.node_col}")
        return _plain(got)

    return MappedIds(system, unique, node(from_node), node(to_node), shared, dict(outside))
