"""Resolve an anchor (cross-street / interchange name) to a mainline node.

Method B (topological, no new GMNS attributes). For a limited-access facility,
an anchor resolves to a mainline node via the *gore/merge* rule:

* the upstream (``from``) anchor -> the **off-ramp diverge (gore)** node, and
* the downstream (``to``) anchor -> the **on-ramp merge** node,

both on the requested-direction carriageway. Surface arterials resolve to a
directly-incident named node. Anchors match on both ``name`` and ``ref``.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

import numpy as np

from ._support import bounded_bfs, to_py
from .result import AnchorMatch
from .streets import StreetIndex

__all__ = ["Interchanges", "classify_interchanges", "pinned_anchor", "resolve_anchor", "resolve_surface_anchor"]

_MAX_RAMP_HOPS = 8


@dataclass
class Interchanges:
    """Mainline attach nodes on the requested carriageway, by ramp orientation."""

    gore: set  # off-ramp diverge nodes (traffic leaves mainline here)
    merge: set  # on-ramp merge nodes (traffic joins mainline here)


def classify_interchanges(
    links, directed_nodes: set, mainline_nodes: set, *, index: StreetIndex | None = None
) -> Interchanges:
    """Find gore (off-ramp) and merge (on-ramp) nodes on the directed carriageway.

    A ramp (``*_link``) that departs the carriageway (``from_node`` on the
    directed mainline, ``to_node`` off the mainline) marks its ``from_node`` a
    gore node. A ramp that joins the carriageway marks its ``to_node`` a merge
    node.
    """
    ramps = links.iloc[(index or StreetIndex.build(links)).ramp_rows]
    f, t = ramps["from_node_id"], ramps["to_node_id"]
    f_in_d, t_in_m = f.isin(directed_nodes), t.isin(mainline_nodes)
    t_in_d, f_in_m = t.isin(directed_nodes), f.isin(mainline_nodes)
    gore = set(f[f_in_d & ~t_in_m].tolist())
    merge = set(t[t_in_d & ~f_in_m].tolist())
    return Interchanges(gore=gore, merge=merge)


def pinned_anchor(anchor: str, node_id, allowed: set) -> AnchorMatch:
    """``anchor`` pinned to ``node_id`` by the user: accepted when the node is one of ``allowed`` (on the facility).

    >>> pinned_anchor("Main St", 7, {7, 8}).kind, pinned_anchor("Main St", 9, {7, 8}).node_id
    ('pinned', None)
    """
    if node_id in allowed:
        return AnchorMatch(anchor, to_py(node_id), [], 1.0, "pinned", f"pinned to node {node_id}")
    return AnchorMatch(anchor, None, [], 0.0, "unresolved", f"node {node_id} is not on the facility for {anchor!r}")


def _surface_links(links, anchor: str, index: StreetIndex):
    """Links whose name OR ref matches the anchor (excluding motorway/ramps)."""
    surf = links.iloc[index.street_rows(anchor)]
    return surf[~surf["facility_type"].isin(["motorway", "motorway_link"])]


def _ramp_adjacency(links, index: StreetIndex, anchor_rows: np.ndarray) -> dict:
    """Undirected adjacency over ramp/connector links + the anchor's own links.

    Only those rows are read, so it touches a few thousand links, not the whole network.
    """
    sub = links.iloc[np.union1d(index.ramp_rows, anchor_rows)]
    adj = collections.defaultdict(list)
    for a, b in zip(sub["from_node_id"].to_numpy(), sub["to_node_id"].to_numpy(), strict=False):
        adj[a].append(b)
        adj[b].append(a)
    return adj


def resolve_anchor(
    anchor: str, role: str, links, interchanges: Interchanges, *, index: StreetIndex | None = None
) -> AnchorMatch:
    """Resolve ``anchor`` to a mainline node for ``role`` in {"from","to"}.

    ``from`` targets gore (off-ramp diverge) nodes; ``to`` targets merge
    (on-ramp) nodes. Nearest match wins; the rest are ranked candidates
    (ambiguity signal for the caller). ``index`` is a :class:`StreetIndex` of ``links`` (built if omitted).
    """
    index = index or StreetIndex.build(links)
    targets = interchanges.gore if role == "from" else interchanges.merge
    kind = "gore" if role == "from" else "merge"
    detail_kind = "off-ramp diverge" if role == "from" else "on-ramp merge"

    surf = _surface_links(links, anchor, index)
    if surf.empty:
        return AnchorMatch(
            anchor, None, [], 0.0, "unresolved", f"anchor {anchor!r} not found as a street/route in this network"
        )

    seeds = set(surf["from_node_id"]) | set(surf["to_node_id"])
    adj = _ramp_adjacency(links, index, index.street_rows(anchor))
    reached = bounded_bfs(adj, seeds, targets, _MAX_RAMP_HOPS)
    if not reached:
        return AnchorMatch(
            anchor,
            None,
            [],
            0.0,
            "unresolved",
            f"anchor {anchor!r} found, but no ramp path to a {detail_kind} node on the {role}-direction carriageway",
        )

    reached.sort(key=lambda x: x[1])
    best_node, hops = reached[0]
    candidates = [to_py(n) for n, _ in reached[1:]]
    confidence = 1.0 / (1 + hops)
    return AnchorMatch(
        anchor, to_py(best_node), candidates, confidence, kind, f"{detail_kind} node, {hops} ramp hop(s)"
    )


def resolve_surface_anchor(anchor: str, facility_nodes: set, links, *, index: StreetIndex | None = None) -> AnchorMatch:
    """Resolve an anchor to a node ON a (surface) facility.

    First tries an **at-grade intersection** — a node shared by the facility and
    a link named/ref'd by the anchor. Falls back to a **ramp path** (for a
    freeway anchor like "I-40" crossing a surface street): BFS from the anchor's
    nodes over ramp/connector links to the nearest facility node. ``index`` is a
    :class:`StreetIndex` of ``links`` (built if omitted).
    """
    index = index or StreetIndex.build(links)
    anchor_rows = index.street_rows(anchor)
    if not anchor_rows.size:
        return AnchorMatch(
            anchor, None, [], 0.0, "unresolved", f"anchor {anchor!r} not found as a street/route in this network"
        )
    anchor_nodes = index.nodes_of(anchor_rows)

    direct = sorted(facility_nodes & anchor_nodes)
    if direct:
        return AnchorMatch(
            anchor,
            to_py(direct[0]),
            [to_py(n) for n in direct[1:]],
            1.0,
            "intersection",
            f"at-grade intersection with {anchor!r}",
        )

    # freeway/ramp fallback: adjacency over ramps + the anchor's own links
    adj = _ramp_adjacency(links, index, anchor_rows)
    reached = bounded_bfs(adj, anchor_nodes, facility_nodes, _MAX_RAMP_HOPS)
    if reached:
        reached.sort(key=lambda x: x[1])
        node, hops = reached[0]
        return AnchorMatch(
            anchor,
            to_py(node),
            [to_py(n) for n, _ in reached[1:]],
            1.0 / (1 + hops),
            "ramp",
            f"reached facility via {anchor!r} ramps ({hops} hop(s))",
        )
    return AnchorMatch(
        anchor, None, [], 0.0, "unresolved", f"anchor {anchor!r} found, but no connection to the facility"
    )
