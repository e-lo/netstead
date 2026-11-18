"""Pure OSM-elements -> GMNS node/link records (no network I/O).

The converter turns parsed OSM ways + node coordinates into flat GMNS ``node``
and ``link`` records. The topology rule (per design): a node becomes a GMNS
node when it is a way endpoint or is shared by two or more ways (a real
intersection); intermediate shape points belonging to a single way are dropped
as nodes and retained as the link's WKT geometry (and in the ``osm_node_ids``
property for provenance). Each undirected segment expands to directed links —
two for a two-way street (``directed=True`` on every link), one for a oneway.

Units: ``length`` is geodesic metres; ``free_speed`` is mph (see
:func:`netstead.osm.tags.parse_speed`).

Input contract:
    * ``nodes``: ``{osm_node_id: (lon, lat)}`` (EPSG:4326).
    * ``ways``: sequence of ``{"id": int, "nodes": [osm_node_id, ...],
      "tags": {str: str}}``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from . import tags

__all__ = ["build_node_link_tables"]

# Mean Earth radius (IUGG), metres — used for geodesic (haversine) length.
_EARTH_RADIUS_M = 6371008.8


def build_node_link_tables(
    nodes: Mapping[int, tuple[float, float]],
    ways: Sequence[Mapping[str, Any]],
    *,
    extra_tags: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert OSM nodes + ways into GMNS ``node`` and ``link`` records.

    Args:
        nodes: ``{osm_node_id: (lon, lat)}`` coordinate lookup (EPSG:4326).
        ways: Sequence of way dicts ``{"id", "nodes", "tags"}``. ``nodes`` is
            the ordered list of OSM node ids; ``tags`` is the way's OSM tags.
        extra_tags: Optional OSM tag keys to carry through onto each link as
            verbatim columns.

    Returns:
        A ``(node_records, link_records)`` tuple. ``node_records`` carry
        ``node_id`` (the OSM node id), ``x_coord`` (lon), ``y_coord`` (lat).
        ``link_records`` carry ``link_id``, ``from_node_id``, ``to_node_id``,
        ``directed`` (always ``True``), ``length`` (metres), ``free_speed``,
        ``lanes``, ``facility_type``, ``name``, ``ref`` (OSM's route number, ``None`` without one; never
        copied into ``name``), ``geometry`` (WKT), and the
        provenance fields ``osm_way_id`` + ``osm_node_ids``.

    Examples:
        >>> nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        >>> ways = [{"id": 7, "nodes": [1, 2], "tags": {"highway": "residential"}}]
        >>> node_recs, link_recs = build_node_link_tables(nodes, ways)
        >>> sorted(n["node_id"] for n in node_recs)
        [1, 2]
        >>> len(link_recs)
        2
    """
    # Fail fast on a way that references a node id absent from `nodes` (a
    # malformed Overpass response from a non-default endpoint, or a hand-built
    # input). The lookup at coord-assembly time would otherwise raise a bare
    # KeyError with just the node id.
    for way in ways:
        way_id = way.get("id")
        for n in way["nodes"]:
            if n not in nodes:
                raise ValueError(
                    f"way {way_id} references node {n} which is not in the nodes dict ({len(nodes)} nodes given)"
                )

    kept = _kept_nodes(ways)
    extra = list(extra_tags or [])

    link_records: list[dict[str, Any]] = []
    used_nodes: set[int] = set()
    link_id = 0

    for segment in _split_ways(ways, kept):
        attrs = tags.apply_mapping(segment["tags"], extra)
        direction = tags.oneway_direction(segment["tags"])
        seq = segment["seq"]

        if direction == "both":
            orientations = [seq, list(reversed(seq))]
        elif direction == "backward":
            orientations = [list(reversed(seq))]
        else:  # "forward"
            orientations = [seq]

        for oriented in orientations:
            link_id += 1
            coords = [nodes[n] for n in oriented]
            record: dict[str, Any] = {
                "link_id": link_id,
                "from_node_id": oriented[0],
                "to_node_id": oriented[-1],
                "directed": True,
                "length": round(_line_length_m(coords), 2),
                "free_speed": attrs["free_speed"],
                "lanes": attrs["lanes"],
                "facility_type": attrs["facility_type"],
                "name": attrs["name"],
                "ref": attrs["ref"],
                "geometry": _wkt_linestring(coords),
                "osm_way_id": segment["way_id"],
                "osm_node_ids": ",".join(str(n) for n in oriented),
            }
            for tag in extra:
                record[tag] = attrs[tag]
            link_records.append(record)
            used_nodes.add(oriented[0])
            used_nodes.add(oriented[-1])

    node_records = [{"node_id": nid, "x_coord": nodes[nid][0], "y_coord": nodes[nid][1]} for nid in sorted(used_nodes)]
    return node_records, link_records


def _kept_nodes(ways: Sequence[Mapping[str, Any]]) -> set[int]:
    """Return the OSM node ids that become GMNS nodes.

    A node is kept when it is a way endpoint, repeats within a single way
    (self-loop), or is shared by two or more ways (intersection).
    """
    ways_per_node: Counter[int] = Counter()
    kept: set[int] = set()
    for way in ways:
        seq = way["nodes"]
        if not seq:
            continue
        kept.add(seq[0])
        kept.add(seq[-1])
        for node, count in Counter(seq).items():
            if count > 1:
                kept.add(node)
        for node in set(seq):
            ways_per_node[node] += 1
    kept.update(node for node, count in ways_per_node.items() if count >= 2)
    return kept


def _split_ways(ways: Sequence[Mapping[str, Any]], kept: set[int]):
    """Yield undirected segments by splitting each way at kept nodes.

    Each yielded segment is ``{"seq", "tags", "way_id"}`` where ``seq`` is the
    ordered node-id list from one kept node to the next (inclusive); the
    in-between nodes are intermediate shape points retained for geometry.
    """
    for way in ways:
        seq = way["nodes"]
        way_tags = way.get("tags", {})
        way_id = way["id"]
        start = 0
        for i in range(1, len(seq)):
            if seq[i] in kept:
                sub = seq[start : i + 1]
                if len(sub) >= 2:
                    yield {"seq": sub, "tags": way_tags, "way_id": way_id}
                start = i


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Return the great-circle distance between two lon/lat points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _line_length_m(coords: Sequence[tuple[float, float]]) -> float:
    """Return the geodesic length of a (lon, lat) polyline, in metres."""
    total = 0.0
    for (lon1, lat1), (lon2, lat2) in pairwise(coords):
        total += _haversine_m(lon1, lat1, lon2, lat2)
    return total


def _wkt_linestring(coords: Sequence[tuple[float, float]]) -> str:
    """Format a (lon, lat) coordinate sequence as a WKT ``LINESTRING``."""
    points = ", ".join(f"{lon} {lat}" for lon, lat in coords)
    return f"LINESTRING ({points})"
