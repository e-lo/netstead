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

import pandas as pd

from ._support import bounded_bfs, norm_name, norm_ref, to_py
from .result import AnchorMatch

__all__ = ["Interchanges", "classify_interchanges", "resolve_anchor", "resolve_surface_anchor"]

_MAX_RAMP_HOPS = 8


def _column(links, name: str):
    """``links[name]``, or an all-null column when the network has none.

    ``ref`` and ``name`` are optional GMNS fields, so an anchor must not assume either.
    """
    if name in links.columns:
        return links[name]
    return pd.Series(None, index=links.index, dtype="object")


@dataclass
class Interchanges:
    """Mainline attach nodes on the requested carriageway, by ramp orientation."""

    gore: set  # off-ramp diverge nodes (traffic leaves mainline here)
    merge: set  # on-ramp merge nodes (traffic joins mainline here)


def classify_interchanges(links, directed_nodes: set, mainline_nodes: set) -> Interchanges:
    """Find gore (off-ramp) and merge (on-ramp) nodes on the directed carriageway.

    A ramp (``*_link``) that departs the carriageway (``from_node`` on the
    directed mainline, ``to_node`` off the mainline) marks its ``from_node`` a
    gore node. A ramp that joins the carriageway marks its ``to_node`` a merge
    node.
    """
    ramps = links[links["facility_type"].astype("string").str.endswith("_link").fillna(False)]
    f, t = ramps["from_node_id"], ramps["to_node_id"]
    f_in_d, t_in_m = f.isin(directed_nodes), t.isin(mainline_nodes)
    t_in_d, f_in_m = t.isin(directed_nodes), f.isin(mainline_nodes)
    gore = set(f[f_in_d & ~t_in_m].tolist())
    merge = set(t[t_in_d & ~f_in_m].tolist())
    return Interchanges(gore=gore, merge=merge)


def _surface_links(links, anchor: str):
    """Links whose name OR ref matches the anchor (excluding motorway/ramps).

    Vectorized: name match via pandas string ops; the ``ref`` match (rarer,
    needs set-overlap) runs only when the anchor looks like a route number.
    """
    a_name = norm_name(anchor)
    a_ref = norm_ref(anchor)
    norm = _column(links, "name").fillna("").astype("string").str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    hit = (norm == a_name) if a_name else pd.Series(False, index=links.index)
    if a_ref:
        hit = hit | _column(links, "ref").apply(lambda r: bool(a_ref & norm_ref(r)))
    surf = links[hit.fillna(False)]
    return surf[~surf["facility_type"].isin(["motorway", "motorway_link"])]


def _ramp_adjacency(links, surface_link_ids: set) -> dict:
    """Undirected adjacency over ramp/connector links + the anchor's own links.

    Filters to just those links first so it touches a few thousand rows, not the
    whole network.
    """
    mask = links["facility_type"].astype("string").str.endswith("_link").fillna(False) | links["link_id"].isin(
        surface_link_ids
    )
    sub = links[mask]
    adj = collections.defaultdict(list)
    for a, b in zip(sub["from_node_id"].to_numpy(), sub["to_node_id"].to_numpy(), strict=False):
        adj[a].append(b)
        adj[b].append(a)
    return adj


def resolve_anchor(anchor: str, role: str, links, interchanges: Interchanges) -> AnchorMatch:
    """Resolve ``anchor`` to a mainline node for ``role`` in {"from","to"}.

    ``from`` targets gore (off-ramp diverge) nodes; ``to`` targets merge
    (on-ramp) nodes. Nearest match wins; the rest are ranked candidates
    (ambiguity signal for the caller).
    """
    targets = interchanges.gore if role == "from" else interchanges.merge
    kind = "gore" if role == "from" else "merge"
    detail_kind = "off-ramp diverge" if role == "from" else "on-ramp merge"

    surf = _surface_links(links, anchor)
    if surf.empty:
        return AnchorMatch(
            anchor, None, [], 0.0, "unresolved", f"anchor {anchor!r} not found as a street/route in this network"
        )

    seeds = set(surf["from_node_id"]) | set(surf["to_node_id"])
    adj = _ramp_adjacency(links, set(surf["link_id"]))
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


def _anchor_links(links, anchor: str):
    """Links whose name OR ref matches the anchor (vectorized)."""
    a_name = norm_name(anchor)
    a_ref = norm_ref(anchor)
    nm = _column(links, "name").fillna("").astype("string").str.lower().str.replace(r"[^a-z0-9]", "", regex=True)
    hit = (nm == a_name) if a_name else pd.Series(False, index=links.index)
    if a_ref:
        hit = hit | _column(links, "ref").apply(lambda r: bool(a_ref & norm_ref(r)))
    return links[hit.fillna(False)]


def resolve_surface_anchor(anchor: str, facility_nodes: set, links) -> AnchorMatch:
    """Resolve an anchor to a node ON a (surface) facility.

    First tries an **at-grade intersection** — a node shared by the facility and
    a link named/ref'd by the anchor. Falls back to a **ramp path** (for a
    freeway anchor like "I-40" crossing a surface street): BFS from the anchor's
    nodes over ramp/connector links to the nearest facility node.
    """
    anchor_links = _anchor_links(links, anchor)
    if anchor_links.empty:
        return AnchorMatch(
            anchor, None, [], 0.0, "unresolved", f"anchor {anchor!r} not found as a street/route in this network"
        )
    anchor_nodes = set(anchor_links["from_node_id"]) | set(anchor_links["to_node_id"])

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
    anchor_ids = set(anchor_links["link_id"])
    mask = links["facility_type"].astype("string").str.endswith("_link").fillna(False) | links["link_id"].isin(
        anchor_ids
    )
    sub = links[mask]
    adj = collections.defaultdict(list)
    for a, b in zip(sub["from_node_id"].to_numpy(), sub["to_node_id"].to_numpy(), strict=False):
        adj[a].append(b)
        adj[b].append(a)
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
