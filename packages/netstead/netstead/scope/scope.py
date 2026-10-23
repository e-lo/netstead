"""Network-aware scope ops for GMNS :class:`netstead.Network` (task 3.10).

A :class:`NetworkScope` is a (frozen) pair of node id + link id sets
plus a reference to the source :class:`Network`. Each constructor
function builds one from a seed (nodes, a link, a point, a zone, …);
each composition method returns a new scope; calling :meth:`apply`
returns a fresh :class:`Network` whose tables are filtered to the
scope's id sets via the GMNS FK chain.

Cross-references for the reader:

* :mod:`netstead.graph` — the underlying :class:`GMNSGraph` (scipy CSR)
  routing engine. The graph is built (or reused via the
  :data:`netstead.semantics.connectivity._GRAPH_CACHE_KEY` cache) on first
  use and shared with :mod:`netstead.semantics.connectivity`.
* :mod:`netstead.indexes` — the :class:`SpatialIndex` (STRtree) primitive
  for geometric scope ops; built/cached independently.
* :mod:`netstead.semantics.connectivity` — connected-component logic.
  :func:`connected_component` here reuses ``GMNSGraph.connected_component``
  directly rather than going through ``semantics.connected_components``,
  because we only need one component, not the full partition.
* :mod:`corral.dataset.view` — the generic spatial scopes (bbox,
  polygon, geometry-buffer). Spatial-only scopes already work via
  ``net.scope.from_bbox(...)``; this module adds the GMNS-aware
  network-distance and topology-aware constructors.

Design decisions for the reader:

* **Single file.** All constructors + composition + apply live here.
  Splitting per-op into N files would obscure the shared FK-pushdown
  pattern; each function is short and reads top-to-bottom.
* **Frozen scope.** :class:`NetworkScope` is immutable so composition
  ops are pure (no aliasing surprises when chaining).
* **Index cache key.** We stash both spatial + graph indexes on
  ``Network.metadata`` under the same key used by
  :mod:`netstead.semantics.connectivity` so the two modules share a
  build.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from corral.dataset import Table

from .errors import ScopeError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netstead.graph import GMNSGraph
    from netstead.indexes import SpatialIndex
    from netstead.network import Network

__all__ = [
    "AUTO_INDEX_THRESHOLD_DEFAULT",
    "NetworkScope",
    "connected_component",
    "from_link",
    "from_node",
    "from_nodes",
    "from_point",
    "from_zone",
]

logger = logging.getLogger(__name__)

# Default node-count threshold above which network-aware scope ops
# emit an info-level "building graph" warning the first time they
# build the routing graph. Callers who don't want the surprise can
# either pre-build (``net.build_indexes(graph=True)``) or raise the
# threshold via ``NETSTEAD_AUTO_INDEX_THRESHOLD`` env var.
AUTO_INDEX_THRESHOLD_DEFAULT: int = 50_000

# Same cache key as netstead.semantics.connectivity so the two modules share a
# single GMNSGraph build per network. Duplicated rather than imported so
# semantics imports don't pull the scope module into memory just for the
# constants. The spatial index is independent and lives under its own key.
_GRAPH_CACHE_KEY = "_cached_gmnsgraph"
_SPATIAL_INDEX_KEY = "_cached_spatial_index"

# Back-compat alias for callers / network.py that still reference the old name
# during the graph-unification transition. New code should use _GRAPH_CACHE_KEY.
_GRAPH_INDEX_KEY = _GRAPH_CACHE_KEY


# ---------------------------------------------------------------------------
# Distance parsing — keep it small + inline; no `pint` dep.
# ---------------------------------------------------------------------------
#
# Bare numbers are treated as **meters** (the GMNS spec uses meters
# for link.length). String forms accept the common transportation
# units the project's modeler audience writes by hand.

_UNIT_TO_METERS: dict[str, float] = {
    "m": 1.0,
    "meter": 1.0,
    "meters": 1.0,
    "km": 1000.0,
    "ft": 0.3048,
    "feet": 0.3048,
    "mi": 1609.344,
    "mile": 1609.344,
    "miles": 1609.344,
}

_DISTANCE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([a-zA-Z]+)\s*$")


def _parse_distance(value: str | float | int) -> float:
    """Return ``value`` as meters.

    Numeric inputs (``0.5``, ``800``) are already meters. String inputs
    must match ``<number><unit>`` where unit is one of m/km/ft/mi (and
    spelled-out variants). Unknown units raise :class:`ScopeError`.

    Examples:
        >>> _parse_distance(800)
        800.0
        >>> _parse_distance("0.5mi")
        804.672
        >>> round(_parse_distance("1km"), 2)
        1000.0
    """
    if isinstance(value, (int, float)):
        return float(value)
    match = _DISTANCE_RE.match(str(value))
    if not match:
        raise ScopeError(f"Unparseable distance {value!r}: expected '<number><unit>' (e.g. '0.5mi', '800m').")
    number, unit = match.group(1), match.group(2).lower()
    factor = _UNIT_TO_METERS.get(unit)
    if factor is None:
        supported = ", ".join(sorted(set(_UNIT_TO_METERS)))
        raise ScopeError(f"Unknown distance unit {unit!r} in {value!r}; supported: {supported}.")
    return float(number) * factor


# ---------------------------------------------------------------------------
# NetworkScope value type + composition + apply
# ---------------------------------------------------------------------------
#
# GMNS-table → (column, id-source) mapping for FK pushdown inside
# :meth:`NetworkScope.apply`. Format:
#   resource_name: list of (column, "link" | "node")
# A row is kept iff at least one of its (column, source) pairs matches
# the scope's id set. Tables not in this dict pass through unchanged
# (documented in the docstring) — keeps the table easy to extend.
_FK_PUSHDOWN: dict[str, list[tuple[str, str]]] = {
    "link": [("link_id", "link")],
    "node": [("node_id", "node")],
    "geometry": [],  # filtered separately via geometry_id from kept links
    "lane": [("link_id", "link")],
    "segment": [("link_id", "link")],
    "segment_lane": [("link_id", "link")],
    "link_tod": [("link_id", "link")],
    "lane_tod": [("link_id", "link")],
    "segment_tod": [("link_id", "link")],
    "segment_lane_tod": [("link_id", "link")],
    "movement": [
        ("ib_link_id", "link"),
        ("ob_link_id", "link"),
        ("node_id", "node"),
    ],
    "movement_tod": [
        ("ib_link_id", "link"),
        ("ob_link_id", "link"),
        ("node_id", "node"),
    ],
    "signal_controller": [("node_id", "node")],
    "signal_coordination": [("node_id", "node")],
    "signal_detector": [("node_id", "node")],
    "signal_phase_mvmt": [("node_id", "node")],
    "signal_timing_phase": [("node_id", "node")],
    "signal_timing_plan": [("node_id", "node")],
}


@dataclass(frozen=True)
class NetworkScope:
    """An immutable subset of a :class:`Network`'s links + nodes.

    Built by the module-level constructors (:func:`from_nodes`,
    :func:`from_node`, :func:`from_link`, :func:`from_point`,
    :func:`connected_component`, :func:`from_zone`) and composed via
    :meth:`union` / :meth:`intersect` / :meth:`subtract` /
    :meth:`buffer_network` / :meth:`buffer_spatial`. :meth:`apply`
    materializes the scope as a fresh :class:`Network` with all
    FK-related tables pre-filtered.

    Attributes:
        node_ids: frozenset of node ids in the scope.
        link_ids: frozenset of link ids in the scope.
        network: the source :class:`Network` (used by composition ops
            to access the cached indexes; not re-validated on the
            return path of :meth:`apply`).
        provenance: short human-readable trail of how this scope was
            built (e.g. ``"from_nodes([1,2,3], path_between=True)"``).
            Used in error messages and scope.__repr__.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("scipy")
        >>> from netstead import Network
        >>> from netstead.fixtures import leavenworth
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> from netstead.scope import from_nodes
        >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
        >>> scope = from_nodes(net, [1, 2], path_between=True)
        >>> isinstance(scope, NetworkScope)
        True
        >>> sub = scope.apply()
        >>> sub.links.count() <= net.links.count()
        True
    """

    node_ids: frozenset[int]
    link_ids: frozenset[int]
    network: Network = field(repr=False, hash=False, compare=False)
    provenance: str = ""

    def __post_init__(self) -> None:
        """Normalize id sets to plain Python ``int``.

        Constructors collect ids from numpy / scipy results (e.g.
        ``GMNSGraph.shortest_path`` yields ``numpy.int64``), and numpy
        scalars are rejected by ``json.dumps`` and by FastMCP's result
        serializer. Enforcing the invariant here means every constructor
        and composition op (which go through ``dataclasses.replace``)
        produces JSON-safe ids without each call site remembering to.
        """
        object.__setattr__(self, "node_ids", _as_int_frozenset(self.node_ids))
        object.__setattr__(self, "link_ids", _as_int_frozenset(self.link_ids))

    def __len__(self) -> int:
        """Total ids in the scope (links + nodes) — useful for sanity-checking sizes."""
        return len(self.node_ids) + len(self.link_ids)

    def __repr__(self) -> str:
        """Compact repr for shell + log output."""
        return f"NetworkScope(nodes={len(self.node_ids)}, links={len(self.link_ids)}, via={self.provenance!r})"

    # -- composition --------------------------------------------------------

    def union(self, other: NetworkScope) -> NetworkScope:
        """Return the union of two scopes (same network)."""
        _require_same_network(self, other, "union")
        return replace(
            self,
            node_ids=self.node_ids | other.node_ids,
            link_ids=self.link_ids | other.link_ids,
            provenance=f"({self.provenance}) | ({other.provenance})",
        )

    def intersect(self, other: NetworkScope) -> NetworkScope:
        """Return the intersection of two scopes (same network)."""
        _require_same_network(self, other, "intersect")
        return replace(
            self,
            node_ids=self.node_ids & other.node_ids,
            link_ids=self.link_ids & other.link_ids,
            provenance=f"({self.provenance}) & ({other.provenance})",
        )

    def subtract(self, other: NetworkScope) -> NetworkScope:
        """Return the set-difference (self minus other) of two scopes (same network)."""
        _require_same_network(self, other, "subtract")
        return replace(
            self,
            node_ids=self.node_ids - other.node_ids,
            link_ids=self.link_ids - other.link_ids,
            provenance=f"({self.provenance}) - ({other.provenance})",
        )

    def buffer_network(self, distance: str | float) -> NetworkScope:
        """Extend the scope by all nodes within ``distance`` network-distance of any current node.

        Adds the induced links (any link whose endpoints are both in
        the expanded node set) so the result remains a proper subgraph.
        """
        meters = _parse_distance(distance)
        graph = _get_or_build_graph(self.network)
        expanded_nodes = graph.network_buffer(list(self.node_ids), meters)
        new_nodes = self.node_ids | expanded_nodes
        new_links = self.link_ids | _induced_link_ids(self.network, new_nodes)
        return replace(
            self,
            node_ids=frozenset(new_nodes),
            link_ids=frozenset(new_links),
            provenance=f"{self.provenance} + buffer_network({distance!r})",
        )

    def buffer_spatial(self, distance_m: float) -> NetworkScope:
        """Extend the scope by all links within ``distance_m`` of any current link's geometry.

        Implementation note: builds a shapely union of the current
        scope's link geometries, then queries the SpatialIndex with
        ``distance_m`` as a buffer. Endpoint nodes of any newly added
        links are folded in too.
        """
        spatial = _get_or_build_spatial_index(self.network)
        if spatial is None or len(spatial) == 0:
            return self  # No geometry available — silent no-op.
        # Pull the WKT of each current link from the network for the union.
        from shapely import unary_union

        link_geoms = _link_geometries(self.network, self.link_ids)
        if not link_geoms:
            return self
        merged = unary_union(link_geoms)
        new_link_ids = set(spatial.query_geometry(merged, distance_m=distance_m))
        all_links = self.link_ids | new_link_ids
        # Recompute node set from final link set so endpoints get included.
        all_nodes = self.node_ids | _link_endpoint_nodes(self.network, new_link_ids)
        return replace(
            self,
            node_ids=frozenset(all_nodes),
            link_ids=frozenset(all_links),
            provenance=f"{self.provenance} + buffer_spatial({distance_m})",
        )

    # -- materialization ----------------------------------------------------

    def apply(self) -> Network:
        """Return a new :class:`Network` whose tables are filtered to the scope.

        Pushdown rules:

        * ``link`` -> rows where ``link_id`` is in :attr:`link_ids`.
        * ``node`` -> rows where ``node_id`` is in :attr:`node_ids`.
        * ``geometry`` -> rows whose ``geometry_id`` is referenced by
          any surviving link.
        * Other tables in :data:`_FK_PUSHDOWN` -> rows where any of
          their FK columns match the scope's id sets.
        * Tables outside :data:`_FK_PUSHDOWN` (e.g. ``time_set_definitions``,
          ``use_definition``) pass through unfiltered — they are
          dimension tables, not network-keyed.
        """
        # Local import to keep cold-import cheap.
        from netstead.network import Network

        node_set, link_set = set(self.node_ids), set(self.link_ids)
        new_tables: dict[str, Table] = {}

        # First pass: link, node, and any other FK-pushdown table.
        for name, table in self.network.tables.items():
            mapping = _FK_PUSHDOWN.get(name)
            if mapping is None or name == "geometry":
                # Pass-through or handled below.
                new_tables[name] = table
                continue
            new_tables[name] = _filter_table_by_id_columns(table, mapping, node_set, link_set)

        # Geometry filter requires the surviving link rows' geometry_id.
        if "geometry" in self.network.tables:
            new_tables["geometry"] = _filter_geometry_by_links(
                self.network.tables["geometry"],
                new_tables.get("link"),
            )

        return Network(
            spec=self.network.spec,
            tables=new_tables,
            engine=self.network.engine,
            source=self.network.source,
            metadata={
                **self.network.metadata,
                "_scope_provenance": self.provenance,
            },
            spec_version=self.network.spec_version,
        )


def _as_int_frozenset(ids: Iterable[int]) -> frozenset[int]:
    """Return ``ids`` as a frozenset of plain ``int`` (drops numpy scalar types)."""
    return frozenset(int(i) for i in ids)


def _require_same_network(left: NetworkScope, right: NetworkScope, op: str) -> None:
    """Composition only makes sense within one network — raise if not."""
    if left.network is not right.network:
        raise ScopeError(f"Cannot {op} scopes from different networks ({left.network!r} vs {right.network!r}).")


# ---------------------------------------------------------------------------
# Scope constructors
# ---------------------------------------------------------------------------


def from_nodes(
    net: Network,
    node_ids: Iterable[int],
    *,
    path_between: bool = True,
) -> NetworkScope:
    """Scope built from a set of seed nodes.

    Args:
        net: The source :class:`Network`.
        node_ids: Seed node ids. Unknown ids are silently dropped (the
            scope just gets smaller) so callers can pass loose lists
            without pre-validating.
        path_between: If ``True`` (default), expand the scope to
            include nodes + links on the shortest path between every
            pair of seeds (BFS-equivalent on uniformly weighted
            graphs; weighted by ``link.length`` on GMNS networks). If
            ``False``, the scope is just the seed nodes plus the
            links directly incident on them.

    Returns:
        A :class:`NetworkScope`.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("scipy")
        >>> from netstead import Network
        >>> from netstead.fixtures import leavenworth
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
        >>> scope = from_nodes(net, [1, 2, 3], path_between=False)
        >>> 1 in scope.node_ids
        True
    """
    _maybe_warn_auto_index(net, "from_nodes")
    graph = _get_or_build_graph(net)
    seeds = {int(n) for n in node_ids if graph._index_or_none(int(n)) is not None}
    nodes: set[int] = set(seeds)

    if path_between and len(seeds) > 1:
        seed_list = sorted(seeds)
        for i, src in enumerate(seed_list):
            for dst in seed_list[i + 1 :]:
                # GMNSGraph.shortest_path returns a ShortestPathResult; .nodes is
                # the ordered node-id list (empty when unreachable). Its ids are
                # numpy scalars (indexed off the graph's node array) — coerce so
                # _induced_link_ids and the scope hold plain ints.
                nodes.update(int(n) for n in graph.shortest_path(src, dst).nodes)

    link_ids = _induced_link_ids(net, nodes)
    return NetworkScope(
        node_ids=frozenset(nodes),
        link_ids=frozenset(link_ids),
        network=net,
        provenance=f"from_nodes({sorted(seeds)!r}, path_between={path_between})",
    )


def from_node(
    net: Network,
    node_id: int,
    *,
    network_buffer: str | float = "0.5mi",
) -> NetworkScope:
    """Scope = all nodes within ``network_buffer`` network-distance of ``node_id``.

    Dijkstra-bounded on the underlying :class:`GMNSGraph`; the result
    includes the seed node, all reachable nodes within distance, and
    every link whose endpoints are both in the resulting node set.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("scipy")
        >>> from netstead import Network
        >>> from netstead.fixtures import leavenworth
        >>> from corral.engines.ibis_engine import IbisEngine
        >>> net = Network.from_source(leavenworth.csv_dir(), engine=IbisEngine())
        >>> scope = from_node(net, 1, network_buffer="200m")
        >>> 1 in scope.node_ids
        True
    """
    _maybe_warn_auto_index(net, "from_node")
    meters = _parse_distance(network_buffer)
    graph = _get_or_build_graph(net)
    if graph._index_or_none(int(node_id)) is None:
        raise ScopeError(f"node_id={node_id!r} not present in network.nodes.")
    nodes = graph.network_buffer([int(node_id)], meters) | {int(node_id)}
    link_ids = _induced_link_ids(net, nodes)
    return NetworkScope(
        node_ids=frozenset(nodes),
        link_ids=frozenset(link_ids),
        network=net,
        provenance=f"from_node({node_id!r}, network_buffer={network_buffer!r})",
    )


def from_link(
    net: Network,
    link_id: int,
    *,
    spatial_buffer_m: float | None = None,
    network_buffer: str | float | None = None,
) -> NetworkScope:
    """Scope seeded by one link, expanded by either a spatial or network buffer.

    Exactly one of ``spatial_buffer_m`` / ``network_buffer`` must be
    provided. The seed link is always included regardless.
    """
    if (spatial_buffer_m is None) == (network_buffer is None):
        raise ScopeError("from_link requires exactly one of spatial_buffer_m or network_buffer.")
    _maybe_warn_auto_index(net, "from_link")

    if spatial_buffer_m is not None:
        spatial = _get_or_build_spatial_index(net)
        if spatial is None:
            raise ScopeError("from_link(spatial_buffer_m=...) requires a geometry column on links.")
        geoms = _link_geometries(net, {int(link_id)})
        if not geoms:
            raise ScopeError(f"link_id={link_id!r} has no parseable geometry.")
        # geoms[0] is already a shapely geometry — query the tree with it directly.
        new_link_ids = set(spatial.query_geometry(geoms[0], distance_m=spatial_buffer_m))
        new_link_ids.add(int(link_id))
        nodes = _link_endpoint_nodes(net, new_link_ids)
        provenance = f"from_link({link_id!r}, spatial_buffer_m={spatial_buffer_m})"
    else:
        # Network buffer: seed nodes = the link's two endpoints; then expand.
        endpoints = _link_endpoint_nodes(net, {int(link_id)})
        if not endpoints:
            raise ScopeError(f"link_id={link_id!r} not found in network.links.")
        graph = _get_or_build_graph(net)
        meters = _parse_distance(network_buffer)
        nodes = graph.network_buffer(list(endpoints), meters) | endpoints
        new_link_ids = _induced_link_ids(net, nodes) | {int(link_id)}
        provenance = f"from_link({link_id!r}, network_buffer={network_buffer!r})"

    return NetworkScope(
        node_ids=frozenset(nodes),
        link_ids=frozenset(new_link_ids),
        network=net,
        provenance=provenance,
    )


def from_point(
    net: Network,
    xy: tuple[float, float],
    *,
    spatial_buffer_m: float = 100.0,
) -> NetworkScope:
    """Scope = links within ``spatial_buffer_m`` of ``(x, y)`` (CRS of the geometry column).

    Snaps to the nearest link via :class:`SpatialIndex` first (which
    needs no actual snapping — STRtree handles the point-buffer query
    directly), then includes endpoint nodes of every matching link.
    """
    _maybe_warn_auto_index(net, "from_point")
    spatial = _get_or_build_spatial_index(net)
    if spatial is None:
        raise ScopeError("from_point requires a geometry column on links.")
    x, y = xy
    link_ids = set(spatial.query_point(float(x), float(y), distance_m=spatial_buffer_m))
    nodes = _link_endpoint_nodes(net, link_ids)
    return NetworkScope(
        node_ids=frozenset(nodes),
        link_ids=frozenset(link_ids),
        network=net,
        provenance=f"from_point({xy!r}, spatial_buffer_m={spatial_buffer_m})",
    )


def connected_component(net: Network, seed_node_id: int) -> NetworkScope:
    """Scope = the entire weakly-connected component containing ``seed_node_id``."""
    _maybe_warn_auto_index(net, "connected_component")
    graph = _get_or_build_graph(net)
    nodes = graph.connected_component(int(seed_node_id))
    if not nodes:
        raise ScopeError(f"seed_node_id={seed_node_id!r} not present in network.nodes.")
    link_ids = _induced_link_ids(net, nodes)
    return NetworkScope(
        node_ids=frozenset(nodes),
        link_ids=frozenset(link_ids),
        network=net,
        provenance=f"connected_component({seed_node_id!r})",
    )


def from_zone(net: Network, zone_ids: Iterable[int]) -> NetworkScope:
    """Scope = all nodes (and their incident links) whose ``zone_id`` is in ``zone_ids``.

    Requires ``net.nodes`` to carry a ``zone_id`` column; raises
    :class:`ScopeError` otherwise.
    """
    if "zone_id" not in net.nodes.columns():
        raise ScopeError("from_zone requires a 'zone_id' column on the node table.")
    target_zones = {int(z) for z in zone_ids}
    nodes_arrow = _to_arrow(net.nodes)
    nids = nodes_arrow.column("node_id").to_pylist()
    zids = nodes_arrow.column("zone_id").to_pylist()
    matching_nodes = {int(n) for n, z in zip(nids, zids, strict=True) if z is not None and int(z) in target_zones}
    link_ids = _induced_link_ids(net, matching_nodes)
    return NetworkScope(
        node_ids=frozenset(matching_nodes),
        link_ids=frozenset(link_ids),
        network=net,
        provenance=f"from_zone({sorted(target_zones)!r})",
    )


# ---------------------------------------------------------------------------
# Index access + auto-build heuristic
# ---------------------------------------------------------------------------


def _get_or_build_graph(net: Network) -> GMNSGraph:
    """Return the cached :class:`GMNSGraph`, or build + cache one.

    Same cache key as :mod:`netstead.semantics.connectivity` — the two
    modules deliberately share a single GMNSGraph (scipy CSR) build per
    network. Built with ``keep_missing_cost=True`` so links with a null
    ``length`` remain topological connections.
    """
    cached = net.metadata.get(_GRAPH_CACHE_KEY)
    if cached is not None:
        return cached
    from netstead.graph import GMNSGraph

    graph = GMNSGraph.from_network(net, cost="length", keep_missing_cost=True)
    net.metadata[_GRAPH_CACHE_KEY] = graph
    return graph


def _get_or_build_spatial_index(net: Network) -> SpatialIndex | None:
    """Return the cached :class:`SpatialIndex`, or build + cache (None if no geometry)."""
    cached = net.metadata.get(_SPATIAL_INDEX_KEY)
    if cached is not None:
        return cached
    if "geometry" not in net.links.columns():
        # No inline link.geometry; we don't auto-resolve geometry_id
        # here (that's :func:`netstead.semantics.assemble_link_geometry`'s
        # job) — callers wanting spatial scope on geometry-table-backed
        # networks should call ``net.build_indexes(spatial=True)``
        # after assembling first.
        return None
    from netstead.indexes import SpatialIndex

    index = SpatialIndex.build(net.links)
    net.metadata[_SPATIAL_INDEX_KEY] = index
    return index


def _maybe_warn_auto_index(net: Network, op_name: str) -> None:
    """Emit an info log if we're about to auto-build an index over a large network.

    Only fires if (a) no graph index is cached, AND (b) the node table
    exceeds the threshold. Callers who care can avoid the surprise by
    pre-building or raising ``NETSTEAD_AUTO_INDEX_THRESHOLD``.
    """
    if _GRAPH_INDEX_KEY in net.metadata:
        return
    try:
        threshold = int(os.environ.get("NETSTEAD_AUTO_INDEX_THRESHOLD", AUTO_INDEX_THRESHOLD_DEFAULT))
    except ValueError:
        threshold = AUTO_INDEX_THRESHOLD_DEFAULT
    node_count = net.nodes.count()
    if node_count > threshold:
        logger.info(
            "%s: auto-building graph index over %d nodes (>%d threshold). "
            "Call net.build_indexes(graph=True) up front or raise "
            "NETSTEAD_AUTO_INDEX_THRESHOLD to avoid this on cold starts.",
            op_name,
            node_count,
            threshold,
        )


# ---------------------------------------------------------------------------
# Pyarrow-level helpers (shared with semantics layer)
# ---------------------------------------------------------------------------


def _induced_link_ids(net: Network, node_ids: set[int]) -> set[int]:
    """Return link ids whose from/to endpoints are BOTH in ``node_ids``."""
    if not node_ids:
        return set()
    arrow = _to_arrow(net.links)
    lid_col = arrow.column("link_id").to_pylist()
    from_col = arrow.column("from_node_id").to_pylist()
    to_col = arrow.column("to_node_id").to_pylist()
    return {
        int(lid)
        for lid, f, t in zip(lid_col, from_col, to_col, strict=True)
        if f is not None and t is not None and int(f) in node_ids and int(t) in node_ids
    }


def _link_endpoint_nodes(net: Network, link_ids: set[int]) -> set[int]:
    """Return the union of from_node_id + to_node_id over ``link_ids``."""
    if not link_ids:
        return set()
    arrow = _to_arrow(net.links)
    lid_col = arrow.column("link_id").to_pylist()
    from_col = arrow.column("from_node_id").to_pylist()
    to_col = arrow.column("to_node_id").to_pylist()
    nodes: set[int] = set()
    for lid, f, t in zip(lid_col, from_col, to_col, strict=True):
        if lid is not None and int(lid) in link_ids:
            if f is not None:
                nodes.add(int(f))
            if t is not None:
                nodes.add(int(t))
    return nodes


def _link_geometries(net: Network, link_ids: set[int]):
    """Return the parsed shapely geometries for ``link_ids`` (drops null/unparseable)."""
    from netstead._geom import shapely_from_any

    if not link_ids or "geometry" not in net.links.columns():
        return []
    arrow = _to_arrow(net.links)
    lid_col = arrow.column("link_id").to_pylist()
    geom_col = arrow.column("geometry").to_pylist()
    geoms = []
    for lid, geom in zip(lid_col, geom_col, strict=True):
        if lid is None or int(lid) not in link_ids:
            continue
        g = shapely_from_any(geom)
        if g is not None and not g.is_empty:
            geoms.append(g)
    return geoms


def _expr_from_kept_rows(engine, source_arrow, kept: list[dict]):
    """Rebuild a table expression from ``kept`` rows, preserving schema when empty.

    ``from_records([])`` builds a columnless table that the ibis/duckdb
    backend rejects ("must have at least one column"). When a scope filters
    out every row of a table, slice the source arrow to zero rows instead so
    the empty table keeps its columns and the package's table contract holds.
    """
    if kept:
        return engine.from_records(kept)
    return engine.from_arrow(source_arrow.slice(0, 0))


def _filter_table_by_id_columns(
    table: Table,
    mapping: list[tuple[str, str]],
    node_set: set[int],
    link_set: set[int],
) -> Table:
    """Return ``table`` with rows kept where any FK column matches the right id set.

    Materializes through pyarrow + rebuilds via ``engine.from_records``
    — same pattern used by :mod:`corral.dataset.view` for non-ibis
    backends. Tables with no rows pass through.
    """
    arrow = _to_arrow(table)
    if arrow.num_rows == 0 or not mapping:
        return table
    keep_mask: list[bool] = [False] * arrow.num_rows
    for col_name, kind in mapping:
        if col_name not in arrow.column_names:
            continue
        values = arrow.column(col_name).to_pylist()
        target = link_set if kind == "link" else node_set
        for i, v in enumerate(values):
            if v is not None and int(v) in target:
                keep_mask[i] = True
    rows = arrow.to_pylist()
    kept = [r for r, m in zip(rows, keep_mask, strict=True) if m]
    new_expr = _expr_from_kept_rows(table.engine, arrow, kept)
    return Table(name=table.name, expr=new_expr, engine=table.engine, schema=table.schema, source=table.source)


def _filter_geometry_by_links(geometry_table: Table, link_table: Table | None) -> Table:
    """Filter the geometry table to ``geometry_id``s referenced by the kept links."""
    if link_table is None:
        return geometry_table
    link_arrow = _to_arrow(link_table)
    if "geometry_id" not in link_arrow.column_names:
        return geometry_table
    referenced = {int(g) for g in link_arrow.column("geometry_id").to_pylist() if g is not None}
    geom_arrow = _to_arrow(geometry_table)
    rows = geom_arrow.to_pylist()
    kept = [r for r in rows if r.get("geometry_id") is not None and int(r["geometry_id"]) in referenced]
    new_expr = _expr_from_kept_rows(geometry_table.engine, geom_arrow, kept)
    return Table(
        name=geometry_table.name,
        expr=new_expr,
        engine=geometry_table.engine,
        schema=geometry_table.schema,
        source=geometry_table.source,
    )


def _to_arrow(table: Table):
    """Materialize a :class:`~corral.dataset.Table` to pyarrow."""
    import pyarrow as pa

    return pa.Table.from_pandas(table.to_pandas(), preserve_index=False)
