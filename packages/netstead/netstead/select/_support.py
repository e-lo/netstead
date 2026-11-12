"""Internal helpers for the resolver: normalization, bearing, tiny graph ops.

The graph ops here run on the *post-filter* subnet (a few hundred edges), so a
hand-rolled BFS/Dijkstra is cheap and engine-independent. Kept isolated so a
future refactor can delegate to :mod:`netstead.graph` if warranted.
"""

from __future__ import annotations

import collections
import heapq
import math
import re
from collections.abc import Iterable

_CARDINAL = {"EB": 90.0, "WB": 270.0, "NB": 0.0, "SB": 180.0}


def to_py(value):
    """Coerce a numpy scalar id to a native Python int/str; pass others through."""
    return value.item() if hasattr(value, "item") else value


# Spelled-out route prefixes -> the short form a ref carries ("US Highway 2" -> "US 2", "Interstate 5" -> "I 5").
_ROUTE_WORDS = re.compile(
    r"^\s*(?:(?:united\s+states|u\.?\s?s\.?)\s+(?:highway|hwy|route|rte)|interstate)\s*(?=\d)",
    re.IGNORECASE,
)


def route_alias(part: str) -> str:
    """``part`` with a spelled-out US route or interstate prefix shortened (``"US Highway 2"`` -> ``"US 2"``)."""
    match = _ROUTE_WORDS.match(part)
    if not match:
        return part
    prefix = "I" if match.group(0).strip().lower().startswith("interstate") else "US"
    return f"{prefix} {part[match.end() :]}"


def norm_ref(value) -> set[str]:
    """Normalize an OSM/GMNS ref into a set of comparable tokens.

    ``"I 40"`` -> ``{"I40"}``; multi-refs ``"I 40;US 1"`` -> ``{"I40", "US1"}``; ``"US-2"``, ``"U.S. Route 2"``
    and ``"US Highway 2"`` are all ``{"US2"}``, ``"Interstate 5"`` is ``{"I5"}``.
    Prevents the substring collision where ``"40"`` matches ``"I 540"``.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return set()
    out = set()
    for part in re.split(r"[;,]", str(value)):
        token = re.sub(r"[^A-Za-z0-9]", "", route_alias(part)).upper()
        if token:
            out.add(token)
    return out


def norm_name(value) -> str:
    """Normalize a street name for exact comparison (lowercase, alnum only)."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def bearing_deg(nx: dict, ny: dict, u, v) -> float:
    """Compass bearing (0=N, 90=E) of the segment u->v using node coords."""
    dx = nx[v] - nx[u]
    dy = ny[v] - ny[u]
    return math.degrees(math.atan2(dx, dy)) % 360


def matches_direction(bearing: float, direction: str, tol: float = 75.0) -> bool:
    """True if ``bearing`` is within ``tol`` degrees of the cardinal for ``direction``."""
    want = _CARDINAL[direction]
    diff = min((bearing - want) % 360, (want - bearing) % 360)
    return diff <= tol


def is_link_class(facility_type) -> bool:
    """True for ramp/connector links (OSM ``*_link``)."""
    return isinstance(facility_type, str) and facility_type.endswith("_link")


def link_length(nx: dict, ny: dict, row) -> float:
    """Link length in metres; fall back to node-coord distance if absent."""
    length = row.get("length")
    if length is not None and not (isinstance(length, float) and math.isnan(length)) and length > 0:
        return float(length)
    u, v = row["from_node_id"], row["to_node_id"]
    return math.hypot(nx[v] - nx[u], ny[v] - ny[u]) * 111_000


def bounded_bfs(adj: dict, seeds: Iterable, targets: set, max_hops: int) -> list[tuple]:
    """Undirected BFS from ``seeds``, nearest first.

    Returns ``(node, hops)`` for every node in ``targets`` reached within ``max_hops``.
    """
    seen = set(seeds)
    queue = collections.deque((n, 0) for n in seeds)
    found = []
    while queue:
        node, hops = queue.popleft()
        if node in targets:
            found.append((node, hops))
            continue
        if hops >= max_hops:
            continue
        for nbr in adj.get(node, ()):
            if nbr not in seen:
                seen.add(nbr)
                queue.append((nbr, hops + 1))
    return found


def dijkstra_links(directed_adj: dict, src, dst) -> tuple[list, list] | None:
    """Shortest path over a directed adjacency of ``(to_node, weight, link_id)``.

    Returns ``(link_ids, node_path)`` or ``None`` if unreachable.
    """
    dist = {src: 0.0}
    prev: dict = {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, w, lid in directed_adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, lid)
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None
    links, nodes = [], [dst]
    cur = dst
    while cur != src:
        u, lid = prev[cur]
        links.append(lid)
        nodes.append(u)
        cur = u
    return list(reversed(links)), list(reversed(nodes))
