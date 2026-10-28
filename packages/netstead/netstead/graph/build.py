"""Build a compact in-memory routing graph from a GMNS network.

The graph is a Compressed Sparse Row (CSR) matrix of generalized cost. It is
built from the ``node`` and ``link`` tables of any :class:`NetworkSource`, holds
its node-id mapping and an edge->link_id mapping, and is meant to be built once
per session and reused for many queries (connectivity, isochrones, shortest
paths). Rebuilding is cheap (~seconds even for a metro network), so there is no
persistent cache or staleness tracking by default.
"""

from __future__ import annotations

import ast
import hashlib
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pandas as pd

from .source import NetworkSource, as_source

if TYPE_CHECKING:
    from .overlay import S2Overlay

logger = logging.getLogger(__name__)


def _require_extras():
    try:
        import scipy.sparse  # noqa: F401
    except ImportError as exc:  # pragma: no cover - exercised only without extras
        raise ImportError(
            "netstead graph features require the optional 'graph' extra. Install with: pip install 'netstead[graph]'"
        ) from exc


def _expr_names(expr: str) -> set:
    """Identifier names referenced by a Python expression (for column pruning)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _to_float_array(values) -> np.ndarray:
    return pd.to_numeric(pd.Series(values), errors="coerce").to_numpy(dtype="float64", na_value=np.nan)


class ShortestPathResult:
    """Result of a one-to-one shortest path query."""

    def __init__(self, source, target, cost, nodes, links):
        """Store the result of a one-to-one shortest-path query.

        Args:
            source: Source node id (in source units, not graph index).
            target: Target node id.
            cost: Total path cost (``inf`` when unreachable).
            nodes: Ordered list of node ids on the path (empty when unreachable).
            links: Ordered list of link ids on the path (empty when unreachable).
        """
        self.source = source
        self.target = target
        self.cost = cost
        self.nodes = nodes
        self.links = links

    @property
    def reachable(self) -> bool:
        """``True`` when the target was reachable from the source (finite cost)."""
        return np.isfinite(self.cost)

    def __repr__(self) -> str:
        """Return a compact representation for shell + log output."""
        return (
            f"ShortestPathResult(source={self.source!r}, target={self.target!r}, "
            f"cost={self.cost}, n_links={len(self.links)})"
        )


class GMNSGraph:
    """An in-memory CSR routing graph derived from a GMNS network."""

    def __init__(self, *, node_index, coords, csr, edge_link_id, meta):
        """Wrap the assembled CSR + node/edge mappings.

        Construct via :meth:`build` or :meth:`from_network`; this constructor
        takes the already-built arrays and is not intended for direct use.
        """
        self.node_index = node_index  # pd.Index, position == graph node index
        self.node_ids = node_index.to_numpy()
        # Python-native mirror used by set-returning methods so callers receive
        # `int`/`str` ids (matching GraphIndex / GMNS schema), not numpy scalars
        # which JSON serialisers stringify and == int comparisons trip on.
        self._py_node_ids = node_index.tolist()
        self.coords = coords  # (n, 2) float array, NaN where unknown
        self.csr = csr
        self.edge_link_id = edge_link_id  # aligned with csr.data slots
        self.meta = meta
        self._source = None  # retained for lazy geometry access in viz
        self._reverse_csr = None
        self._undirected = None  # cached symmetric adjacency for neighbors()
        self._kdtree = None
        self._kdtree_index = None  # graph indices that the kdtree covers
        self._overlay = None  # S2Overlay used by shortest_path once built
        self._content_hash = None

    # -- construction --------------------------------------------------------

    @classmethod
    def build(
        cls,
        source: NetworkSource | dict | str,
        cost: str,
        barrier: str | None = None,
        directed: None | bool | str = None,
        *,
        node_id_col: str = "node_id",
        from_col: str = "from_node_id",
        to_col: str = "to_node_id",
        link_id_col: str = "link_id",
        x_col: str = "x_coord",
        y_col: str = "y_coord",
        keep_missing_cost: bool = False,
    ) -> GMNSGraph:
        """Build a routing graph.

        Args:
            source: A NetworkSource, a ``{table: DataFrame}`` dict, a DuckDB
                connection, or a path (``.duckdb`` file or directory of
                Parquet/CSV).
            cost: Build-time expression over link columns giving the generalized
                cost / edge weight (e.g. ``"length / free_speed"``). May also be a
                bare column name.
            barrier: Optional boolean expression over link columns; matching links
                are removed from the graph (e.g. ``"lts > 2"``).
            directed: ``None`` uses a per-link ``directed`` column if present else
                treats every link row as a one-way edge; ``True``/``False`` forces
                all links one-way/two-way; a string names a boolean column.
            node_id_col: Column name on the node table holding the node id.
            from_col: Column name on the link table holding the from-node id.
            to_col: Column name on the link table holding the to-node id.
            link_id_col: Column name on the link table holding the link id.
            x_col: Column name on the node table holding the x coordinate.
            y_col: Column name on the node table holding the y coordinate.
            keep_missing_cost: When ``True``, give links with a non-finite cost
                a unit weight instead of dropping them — for connectivity /
                scope analysis where the topology matters even if length is
                missing. Routing callers should leave this ``False``.
        """
        _require_extras()
        src = as_source(source)

        # Pull only the link columns the build actually needs (column pruning).
        needed = {from_col, to_col, link_id_col} | _expr_names(cost)
        if barrier is not None:
            needed |= _expr_names(barrier)
        if directed is None:
            needed.add("directed")
        elif isinstance(directed, str):
            needed.add(directed)

        link_tbl = src.table("link", sorted(needed))
        if link_tbl is None:
            raise ValueError("Source has no 'link' table; cannot build a graph.")
        link = link_tbl.to_pandas()

        for col in (from_col, to_col):
            if col not in link.columns:
                raise ValueError(f"link table is missing required column {col!r}.")

        # Cost and barrier are evaluated over the link columns.
        try:
            weight = _to_float_array(link.eval(cost) if not _is_bare_column(cost, link) else link[cost])
        except Exception as exc:
            raise ValueError(f"Could not evaluate cost expression {cost!r}: {exc}") from exc

        keep = np.ones(len(link), dtype=bool)
        if barrier is not None:
            try:
                barrier_mask = pd.Series(link.eval(barrier)).fillna(False).to_numpy(dtype=bool)
            except Exception as exc:
                raise ValueError(f"Could not evaluate barrier expression {barrier!r}: {exc}") from exc
            keep &= ~barrier_mask

        if keep_missing_cost:
            # Topology use (connectivity/scope): keep edges whose cost is
            # missing by giving them unit cost, rather than dropping them
            # (which would fragment the network). Mirrors GraphIndex's
            # null-length -> 1.0 behavior. Routing callers leave this False.
            weight = np.where(np.isfinite(weight), weight, 1.0)

        keep &= np.isfinite(weight)

        # Directedness per link row (True == one-way from->to).
        is_directed = cls._resolve_directed(link, directed)

        # Node-id universe: node table first, then any endpoints only seen on links.
        node_ids, coords, nodes_only_in_links = cls._build_node_universe(
            src, link, from_col, to_col, node_id_col, x_col, y_col
        )
        node_index = pd.Index(node_ids)

        from_idx = node_index.get_indexer(link[from_col].to_numpy())
        to_idx = node_index.get_indexer(link[to_col].to_numpy())
        # get_indexer returns -1 for unknown; the universe includes all endpoints
        # so this should not happen, but guard anyway.
        keep &= (from_idx >= 0) & (to_idx >= 0)

        link_ids = (link[link_id_col] if link_id_col in link.columns else pd.Series(link.index)).to_numpy()

        u = from_idx[keep]
        v = to_idx[keep]
        w = weight[keep]
        lid = link_ids[keep]
        directed_keep = is_directed[keep]

        if np.any(w < 0):
            raise ValueError(
                "Negative edge costs are not supported (Dijkstra requires "
                "non-negative weights). Check the cost expression."
            )

        # Add reverse edges for two-way links.
        rev = ~directed_keep
        U = np.concatenate([u, v[rev]])
        V = np.concatenate([v, u[rev]])
        W = np.concatenate([w, w[rev]])
        LID = np.concatenate([lid, lid[rev]])

        n = len(node_index)
        csr, edge_link_id = cls._assemble_csr(U, V, W, LID, n)

        meta = {
            "cost": cost,
            "barrier": barrier,
            "directed": directed,
            "n_nodes": n,
            "n_edges": int(csr.nnz),
            "nodes_only_in_links": list(nodes_only_in_links),
        }
        logger.info("Built GMNSGraph: %d nodes, %d edges", n, csr.nnz)
        graph = cls(node_index=node_index, coords=coords, csr=csr, edge_link_id=edge_link_id, meta=meta)
        graph._source = src
        return graph

    @classmethod
    def from_network(cls, net, *, cost: str = "length", **kwargs) -> GMNSGraph:
        """Build a graph from a :class:`netstead.network.Network` (corral package).

        Materializes the ``node`` and ``link`` tables to pandas and delegates to
        :meth:`build`. ``cost`` defaults to ``"length"`` so distance-based ops
        (:meth:`network_buffer`) and connectivity work without extra arguments.

        Args:
            net (Network): A :class:`~netstead.network.Network`.
            cost: Build-time cost expression / column (default ``"length"``).
            **kwargs (Any): Forwarded to :meth:`build` (``barrier``, ``directed``, …).
        """
        nodes_df = net.nodes.to_pandas()
        links_df = net.links.to_pandas()
        return cls.build({"node": nodes_df, "link": links_df}, cost=cost, **kwargs)

    @staticmethod
    def _resolve_directed(link: pd.DataFrame, directed) -> np.ndarray:
        n = len(link)
        if directed is None:
            if "directed" in link.columns:
                return pd.Series(link["directed"]).fillna(True).to_numpy(dtype=bool)
            return np.ones(n, dtype=bool)
        if isinstance(directed, str):
            if directed not in link.columns:
                raise ValueError(f"directed column {directed!r} not found in link table.")
            return pd.Series(link[directed]).fillna(True).to_numpy(dtype=bool)
        return np.full(n, bool(directed))

    @staticmethod
    def _build_node_universe(src, link, from_col, to_col, node_id_col, x_col, y_col):
        node_tbl = src.table("node", [node_id_col, x_col, y_col])
        endpoints = pd.unique(pd.concat([link[from_col], link[to_col]], ignore_index=True))
        if node_tbl is None:
            # No node table: derive nodes purely from link endpoints.
            coords = np.full((len(endpoints), 2), np.nan, dtype="float64")
            return endpoints, coords, list(endpoints.tolist())

        node = node_tbl.to_pandas()
        declared = node[node_id_col].to_numpy()
        declared_set = set(declared.tolist())
        extra = [e for e in endpoints.tolist() if e not in declared_set]
        node_ids = np.concatenate([declared.astype(object), np.array(extra, dtype=object)]) if extra else declared
        coords = np.full((len(node_ids), 2), np.nan, dtype="float64")
        if x_col in node.columns and y_col in node.columns:
            coords[: len(declared), 0] = _to_float_array(node[x_col])
            coords[: len(declared), 1] = _to_float_array(node[y_col])
        return node_ids, coords, extra

    @staticmethod
    def _assemble_csr(U, V, W, LID, n):
        from scipy.sparse import csr_array

        # Sort by (row, col, cost), then drop parallel duplicates keeping the
        # minimum-cost edge (and its link_id) per (u, v). Without this, scipy
        # SUMS duplicate weights — so parallel links (e.g. dual carriageways
        # collapsed to one node pair) would be mis-costed — and edge_link_id
        # could misalign with csr.data if the matrix is later canonicalized.
        order = np.lexsort((W, V, U))
        U = U[order]
        V = V[order].astype(np.int32, copy=False)
        W = W[order].astype("float64", copy=False)
        LID = LID[order]
        if len(U) > 1:
            dup = np.zeros(len(U), dtype=bool)
            dup[1:] = (U[1:] == U[:-1]) & (V[1:] == V[:-1])  # min-cost is first in each (u,v) run
            if dup.any():
                keep = ~dup
                U, V, W, LID = U[keep], V[keep], W[keep], LID[keep]
        counts = np.bincount(U, minlength=n)
        indptr = np.zeros(n + 1, dtype=np.int64)
        np.cumsum(counts, out=indptr[1:])
        csr = csr_array((W, V, indptr), shape=(n, n))
        return csr, LID

    # -- accessors -----------------------------------------------------------

    def content_hash(self) -> str:
        """SHA-256 of the graph's edges, costs, and node coordinates (computed once)."""
        if self._content_hash is None:
            digest = hashlib.sha256()
            for array in (self.csr.indptr, self.csr.indices, self.csr.data, self.coords):
                digest.update(np.ascontiguousarray(array).tobytes())
            self._content_hash = digest.hexdigest()
        return self._content_hash

    def index_of(self, node_id) -> int:
        """Return the graph (matrix-row) index for ``node_id``, or raise :class:`KeyError`."""
        i = self.node_index.get_indexer([node_id])[0]
        if i < 0:
            raise KeyError(f"node_id {node_id!r} is not in the graph.")
        return int(i)

    def node_at(self, index: int):
        """Return the source node id at graph (matrix-row) ``index``."""
        return self.node_ids[index]

    @property
    def reverse_csr(self):
        """Cached transpose of :attr:`csr` (built lazily on first access)."""
        if self._reverse_csr is None:
            self._reverse_csr = self.csr.T.tocsr()
        return self._reverse_csr

    def link_between(self, u_idx: int, v_idx: int):
        """The (min-cost) link_id of the edge from graph index ``u_idx`` to ``v_idx``."""
        start, end = self.csr.indptr[u_idx], self.csr.indptr[u_idx + 1]
        cols = self.csr.indices[start:end]
        hits = np.nonzero(cols == v_idx)[0]
        if len(hits) == 0:
            return None
        best = hits[np.argmin(self.csr.data[start:end][hits])]
        return self.edge_link_id[start + best]

    def _index_or_none(self, node_id) -> int | None:
        i = self.node_index.get_indexer([node_id])[0]
        return int(i) if i >= 0 else None

    def _undirected_adjacency(self):
        if self._undirected is None:
            self._undirected = (self.csr + self.csr.T).tocsr()
        return self._undirected

    # -- network analysis (GraphIndex-parity surface) ------------------------

    def neighbors(self, node_id, *, hops: int = 1) -> set:
        """Return node_ids within ``hops`` graph-distance of ``node_id`` (undirected, excludes seed)."""
        start = self._index_or_none(node_id)
        if start is None:
            return set()
        adj = self._undirected_adjacency()
        visited = {start}
        frontier = {start}
        for _ in range(hops):
            nxt: set[int] = set()
            for u in frontier:
                lo, hi = int(adj.indptr[u]), int(adj.indptr[u + 1])
                nxt.update(int(v) for v in adj.indices[lo:hi])
            nxt -= visited
            if not nxt:
                break
            visited |= nxt
            frontier = nxt
        visited.discard(start)
        return {self._py_node_ids[p] for p in visited}

    def network_buffer(self, seed_node_ids, distance: float) -> set:
        """Return node_ids reachable within ``distance`` (cost units) of any seed (directed; includes seeds)."""
        from scipy.sparse.csgraph import dijkstra

        seeds = [p for n in seed_node_ids if (p := self._index_or_none(n)) is not None]
        if not seeds:
            return set()
        dist = np.atleast_2d(dijkstra(self.csr, directed=True, indices=seeds, limit=distance))
        reachable = np.where(np.isfinite(dist).any(axis=0))[0]
        return {self._py_node_ids[j] for j in reachable}

    def connected_component(self, seed_node_id) -> set:
        """Return node_ids in the same weakly-connected component as ``seed_node_id``."""
        from scipy.sparse.csgraph import connected_components

        pos = self._index_or_none(seed_node_id)
        if pos is None:
            return set()
        _, labels = connected_components(self.csr, directed=True, connection="weak", return_labels=True)
        return {self._py_node_ids[j] for j in np.nonzero(labels == labels[pos])[0]}

    def reachable_from(self, node_id) -> set:
        """Return node_ids reachable from ``node_id`` by directed traversal (includes the seed)."""
        from scipy.sparse.csgraph import breadth_first_order

        pos = self._index_or_none(node_id)
        if pos is None:
            return set()
        order, _ = breadth_first_order(self.csr, pos, directed=True, return_predecessors=True)
        return {self._py_node_ids[p] for p in order}

    # -- queries (delegated to sibling modules) ------------------------------

    def connectivity(self, connection: str = "weak"):
        """Return per-node component labels — see :func:`netstead.graph.connectivity.connectivity`."""
        from .connectivity import connectivity

        return connectivity(self, connection=connection)

    def isochrone(self, source_node, cutoff: float):
        """Return the isochrone from ``source_node`` — see :func:`netstead.graph.paths.isochrone`."""
        from .paths import isochrone

        return isochrone(self, source_node, cutoff)

    def shortest_path(self, source_node, target_node) -> ShortestPathResult:
        """Return the least-cost path — see :func:`netstead.graph.paths.shortest_path`.

        Uses the S2 cell overlay when one is attached (:meth:`build_overlay` /
        :meth:`attach_overlay`); the cost is the same either way.
        """
        if self._overlay is not None:
            return self._overlay.shortest_path(self, source_node, target_node)
        from .paths import shortest_path

        return shortest_path(self, source_node, target_node)

    def build_overlay(
        self, levels: Iterable[int] | None = None, *, crs: Any = None, cache: str | Path | None = None
    ) -> S2Overlay:
        """Precompute an S2 cell overlay and attach it so :meth:`shortest_path` uses it.

        A one-time cost (about a second for a 130k-node metro network) that makes
        each point-to-point query several times faster. See
        :class:`netstead.graph.overlay.S2Overlay`.

        Args:
            levels: S2 levels to partition at (default
                :data:`netstead.graph.overlay.DEFAULT_LEVELS`).
            crs: CRS of the node coordinates if they are not lon/lat degrees.
            cache: Path of the network this graph was built from. The overlay is
                then loaded from, or saved to, a sidecar under
                ``_netstead_indexes/`` next to it, keyed on the graph's content
                and ``levels`` (``netstead index drop`` removes it).
        """
        from .overlay import DEFAULT_LEVELS, S2Overlay

        levels = DEFAULT_LEVELS if levels is None else levels
        if cache is None:
            return self.attach_overlay(S2Overlay(self, levels, crs=crs))

        from netstead.indexes.cache import cache_path, load_cached, save_cached

        level_key = sorted({int(level) for level in levels})
        key = hashlib.sha256(f"{self.content_hash()}:{level_key}:{crs}".encode()).hexdigest()
        path = cache_path(str(cache), "s2overlay", key)
        overlay = load_cached(path)
        if overlay is None:
            overlay = S2Overlay(self, levels, crs=crs)
            save_cached(path, overlay)
        return self.attach_overlay(overlay)

    def attach_overlay(self, overlay):
        """Attach a previously built (e.g. cached) :class:`~netstead.graph.overlay.S2Overlay`.

        Pass ``None`` to detach and go back to plain Dijkstra.

        Raises:
            ValueError: If ``overlay`` was built from a different graph.
        """
        if overlay is not None and not overlay.matches(self):
            raise ValueError("This S2Overlay was built from a different graph; rebuild it.")
        self._overlay = overlay
        return overlay

    def snap(self, x: float, y: float):
        """Return the node id nearest to ``(x, y)`` — see :func:`netstead.graph.paths.snap`."""
        from .paths import snap

        return snap(self, x, y)

    def to_geodataframe(self, **kwargs):
        """Return the graph as a geopandas GeoDataFrame (lazy-imports the viz module).

        Args:
            **kwargs (Any): Forwarded to :func:`netstead.graph.viz.to_geodataframe`.

        Raises:
            ImportError: If ``geopandas``/``shapely`` are not installed
                (install ``netstead[clean]`` to pick them up).
        """
        try:
            from .viz import to_geodataframe
        except ImportError as e:  # pragma: no cover - exercised only without geopandas
            raise ImportError(
                "GMNSGraph.to_geodataframe requires geopandas + shapely; install them with "
                "pip install 'netstead[clean]' (or `pip install geopandas shapely`)."
            ) from e
        return to_geodataframe(self, **kwargs)

    @property
    def nbytes(self) -> int:
        """Exact in-memory size of the graph's backing arrays, in bytes.

        Covers the CSR (``data``/``indices``/``indptr``), the edge->link_id map,
        node coordinates, and the node-id array. This is deterministic and
        independent of process noise, so it is the preferred memory metric for
        benchmarking how the graph scales.
        """
        arrays = [
            self.csr.data,
            self.csr.indices,
            self.csr.indptr,
            self.coords,
            np.asarray(self.edge_link_id),
            np.asarray(self.node_ids),
        ]
        return int(sum(a.nbytes for a in arrays))

    def __repr__(self) -> str:
        """Return a compact representation for shell + log output."""
        return f"GMNSGraph(nodes={self.meta['n_nodes']}, edges={self.meta['n_edges']})"


def _is_bare_column(expr: str, df: pd.DataFrame) -> bool:
    return expr in df.columns
