"""Multi-level S2 cell overlay for fast point-to-point shortest paths.

A one-time preprocessing step that partitions a :class:`GMNSGraph` by S2 cells
(:mod:`netstead.graph.s2`) at a few nested levels and replaces each cell's
interior with shortcut edges between its boundary nodes. This is the
partition-based scheme of Customizable Route Planning (Delling et al., "Customizable Route
Planning", 2011), using S2 cells as the partition instead of a graph
partitioner.

Construction, for levels ``L[0] > L[1] > ...`` (fine to coarse)::

    G_0 = the routing graph
    G_j = shortcuts across each level-L[j-1] cell of G_{j-1}
          + the G_{j-1} edges that cross between level-L[j-1] cells

A shortcut runs from each *entry* node of a cell (head of an edge coming in)
to each *exit* node (tail of an edge going out), costed by a Dijkstra search
over that cell's interior. Shortcuts that merely repeat a path through another
boundary node are dropped, which typically removes over half of them.

A query searches upward from the source: the source's level-``L[0]`` cell in
``G_0``, then its level-``L[1]`` cell in ``G_1``, and so on, ending in the whole
of the coarsest ``G_m``. A mirrored backward search climbs from the target.
Each step is a small scipy Dijkstra seeded with the distances found so far;
the answer is the best node where the two sides meet. Shortcuts on the result
are expanded back into links by re-searching the cell they cross.

Costs equal a plain Dijkstra over the whole graph; when several paths tie, a
different one of them may come back.

Examples:
    >>> import pandas as pd
    >>> from netstead.graph import GMNSGraph
    >>> nodes = pd.DataFrame({"node_id": [1, 2, 3], "x_coord": [-122.42, -122.41, -122.30],
    ...                       "y_coord": [37.77, 37.77, 37.80]})
    >>> links = pd.DataFrame({"link_id": [10, 11], "from_node_id": [1, 2], "to_node_id": [2, 3],
    ...                       "length": [1.0, 9.0]})
    >>> g = GMNSGraph.build({"node": nodes, "link": links}, cost="length")
    >>> overlay = g.build_overlay(levels=(13, 12))
    >>> g.shortest_path(1, 3).links
    [10, 11]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.sparse import csr_array
from scipy.sparse.csgraph import dijkstra

from . import s2
from .build import ShortestPathResult

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_LEVELS", "S2Overlay"]

#: Default S2 levels, fine to coarse (level 13 cells are ~1 km across, level 9 ~16 km).
DEFAULT_LEVELS = (13, 11, 9)

# Edge kinds in a level graph.
_ORIGINAL = 0  # ref = position in GMNSGraph.csr.data
_SHORTCUT = 1  # ref = row in that level's shortcut table


@dataclass
class _Block:
    """One cell's slice of a level graph, renumbered to local node indices.

    ``fwd`` searches along edge direction; ``rev`` against it (for the backward
    search). ``fwd_edge`` gives, for each ``fwd`` slot, the edge's row in the
    level graph's edge arrays, and ``fwd_key`` is ``row * size + col`` for that
    slot: strictly increasing, since columns are sorted within rows and level
    graphs have no parallel edges.
    """

    nodes: np.ndarray  # sorted global node indices
    fwd: csr_array
    rev: csr_array
    fwd_edge: np.ndarray
    fwd_key: np.ndarray

    def local(self, node: int) -> int:
        """Local index of global node ``node`` (which must be in the block)."""
        return int(np.searchsorted(self.nodes, node))

    def edges_between(self, tails: np.ndarray, heads: np.ndarray) -> np.ndarray:
        """Level-graph edge rows for local ``tails[i] -> heads[i]`` (each edge must exist)."""
        keys = tails.astype(np.int64) * len(self.nodes) + heads
        return self.fwd_edge[np.searchsorted(self.fwd_key, keys)]


@dataclass
class _LevelGraph:
    """Edges of one level graph ``G_j``: endpoints, cost, and what each edge stands for."""

    u: np.ndarray
    v: np.ndarray
    w: np.ndarray
    kind: np.ndarray
    ref: np.ndarray

    def take(self, mask) -> _LevelGraph:
        """Return the subset of edges selected by ``mask``."""
        return _LevelGraph(self.u[mask], self.v[mask], self.w[mask], self.kind[mask], self.ref[mask])

    @classmethod
    def concat(cls, parts: list[_LevelGraph]) -> _LevelGraph:
        """Stack several edge sets into one."""
        return cls(*(np.concatenate([getattr(p, f) for p in parts]) for f in ("u", "v", "w", "kind", "ref")))


@dataclass
class _Shortcuts:
    """Shortcut table for one level: the cell each shortcut crosses and its endpoints."""

    cell: np.ndarray
    src: np.ndarray
    dst: np.ndarray


@dataclass
class _Run:
    """One step of an upward search: the block searched and its predecessor tree."""

    block: _Block
    pred: np.ndarray
    origin: np.ndarray  # per local node: the step whose distance seeded it (-1 = query endpoint)
    reverse: bool


class S2Overlay:
    """Precomputed multi-level S2 cell overlay over a :class:`GMNSGraph`.

    Build with :meth:`GMNSGraph.build_overlay` (which also attaches it so
    :meth:`GMNSGraph.shortest_path` uses it). Holds no reference to the graph,
    so it pickles on its own for a sidecar cache; :meth:`shortest_path` takes
    the graph it was built from.
    """

    def __init__(self, graph, levels=DEFAULT_LEVELS, *, crs=None):
        """Partition ``graph`` by S2 cells at ``levels`` and build the shortcut graphs.

        Args:
            graph: The :class:`GMNSGraph` to preprocess.
            levels: S2 levels, any order; used fine to coarse. Each must be 1..30.
            crs: CRS of the node coordinates when they are not lon/lat degrees
                (anything ``pyproj`` accepts, e.g. ``"EPSG:2264"``). Needs pyproj.

        Raises:
            ValueError: If ``levels`` is empty or out of range, or the coordinates
                do not look like lon/lat and no ``crs`` was given.
        """
        self.levels = tuple(sorted({int(level) for level in levels}, reverse=True))
        if not self.levels or not all(1 <= level <= s2.MAX_LEVEL for level in self.levels):
            raise ValueError(f"levels must be S2 levels 1..{s2.MAX_LEVEL}, got {levels!r}.")
        self.n_nodes = graph.csr.shape[0]
        self.fingerprint = graph.content_hash()
        self._expanded: dict[tuple[int, int], np.ndarray] = {}

        csr = graph.csr
        n = self.n_nodes
        rows = np.repeat(np.arange(n), np.diff(csr.indptr))
        self._orig_u = rows
        self._orig_v = csr.indices.astype(np.int64)

        leaf = _leaf_cells(graph, crs)
        # cells[j]: compact cell number of every node at levels[j]
        self.cells = [np.unique(s2.parent(leaf, level), return_inverse=True)[1].ravel() for level in self.levels]

        current = _LevelGraph(
            rows,
            self._orig_v,
            csr.data.astype("float64"),
            np.full(len(rows), _ORIGINAL, dtype=np.int8),
            np.arange(len(rows), dtype=np.int64),
        )
        current_nodes = np.arange(n)
        self.graphs: list[_LevelGraph] = [current]
        self.blocks: list[dict[int, _Block]] = []
        self.shortcuts: list[_Shortcuts | None] = [None]
        for cell in self.cells:
            current, current_nodes = self._coarsen(current, current_nodes, cell)
            self.graphs.append(current)
        self.top = _make_block(current_nodes, self.graphs[-1], np.ones(len(current.u), dtype=bool))
        logger.info(
            "Built S2Overlay levels=%s: top graph %d nodes, %d edges (from %d nodes, %d edges)",
            self.levels,
            len(self.top.nodes),
            len(current.u),
            n,
            csr.nnz,
        )

    # -- construction --------------------------------------------------------

    def _coarsen(self, g: _LevelGraph, g_nodes: np.ndarray, cell: np.ndarray):
        """Split ``g`` into per-cell blocks, add shortcuts across each cell; return the next level graph."""
        cu, cv = cell[g.u], cell[g.v]
        internal = cu == cv
        blocks = _make_blocks(g, g_nodes, cell, internal)
        self.blocks.append(blocks)

        cut = ~internal
        if (g.kind[cut] != _ORIGINAL).any():  # pragma: no cover - S2 cells nest, so this cannot happen
            raise AssertionError("a shortcut edge crossed a coarser cell boundary")
        entries = _group_unique(g.v[cut], cv[cut])
        exits = _group_unique(g.u[cut], cu[cut])

        su, sv, sw, scell = [], [], [], []
        for c in entries.keys() & exits.keys():
            block = blocks.get(c)
            if block is None:
                continue
            a, b, cost = _cell_shortcuts(block, entries[c], exits[c])
            su.append(a)
            sv.append(b)
            sw.append(cost)
            scell.append(np.full(len(a), c, dtype=np.int64))
        if su:
            table = _Shortcuts(np.concatenate(scell), np.concatenate(su), np.concatenate(sv))
            sw_all = np.concatenate(sw)
        else:
            table = _Shortcuts(*(np.array([], dtype=np.int64) for _ in range(3)))
            sw_all = np.array([], dtype="float64")
        self.shortcuts.append(table)

        shortcut_edges = _LevelGraph(
            table.src,
            table.dst,
            sw_all,
            np.full(len(table.src), _SHORTCUT, dtype=np.int8),
            np.arange(len(table.src), dtype=np.int64),
        )
        nxt = _LevelGraph.concat([shortcut_edges, g.take(cut)])
        return nxt, np.unique(np.concatenate([nxt.u, nxt.v]))

    # -- queries -------------------------------------------------------------

    def shortest_path(self, graph, source_node, target_node) -> ShortestPathResult:
        """Return the least-cost path between two node ids, like :func:`netstead.graph.paths.shortest_path`."""
        if not self.matches(graph):
            raise ValueError("This S2Overlay was built from a different graph; rebuild it.")
        src = graph.index_of(source_node)
        tgt = graph.index_of(target_node)

        fwd_best, fwd_step, fwd_runs = self._climb(src, reverse=False)
        bwd_best, bwd_step, bwd_runs = self._climb(tgt, reverse=True)
        total = fwd_best + bwd_best
        meet = int(np.argmin(total))
        cost = float(total[meet])
        if not np.isfinite(cost):
            return ShortestPathResult(source_node, target_node, np.inf, [], [])

        to_meet = [(level, edges[::-1]) for level, edges in reversed(self._walk(meet, fwd_step, fwd_runs))]
        from_meet = self._walk(meet, bwd_step, bwd_runs)
        parts = [self._expand(level, edges) for level, edges in to_meet + from_meet]
        positions = np.concatenate(parts) if parts else np.array([], dtype=np.int64)

        path_idx = np.concatenate([self._orig_u[positions[:1]], self._orig_v[positions]]) if len(positions) else [src]
        nodes = [graph.node_at(i) for i in path_idx]
        links = np.asarray(graph.edge_link_id)[positions].tolist()
        return ShortestPathResult(source_node, target_node, cost, nodes, links)

    def matches(self, graph) -> bool:
        """``True`` when ``graph`` has the same edges, costs, and coordinates this overlay was built from."""
        return graph.content_hash() == self.fingerprint

    def _climb(self, start: int, *, reverse: bool):
        """Upward search from ``start``: its cell at each level, then (forward only) the top graph."""
        best = np.full(self.n_nodes, np.inf)
        step_of = np.full(self.n_nodes, -1, dtype=np.int16)
        best[start] = 0.0
        runs: dict[int, _Run] = {}
        steps = [(j, self.blocks[j].get(int(self.cells[j][start]))) for j in range(len(self.levels))]
        if not reverse:  # one side covering the top graph is enough for the two to meet
            steps.append((len(self.levels), self.top))
        for j, block in steps:
            if block is None:
                continue
            seed = best[block.nodes]
            seeded = np.nonzero(np.isfinite(seed))[0]
            if not len(seeded):
                continue
            dist, pred = _seeded_dijkstra(block.rev if reverse else block.fwd, seeded, seed[seeded])
            origin = step_of[block.nodes].copy()
            better = dist < seed
            best[block.nodes[better]] = dist[better]
            step_of[block.nodes[better]] = j
            runs[j] = _Run(block, pred, origin, reverse)
        return best, step_of, runs

    def _walk(self, node: int, step_of: np.ndarray, runs: dict[int, _Run]) -> list[tuple[int, np.ndarray]]:
        """Follow predecessors from ``node`` back to the search's start.

        Returns ``(level, edges)`` segments, one per search step crossed, with
        everything nearest ``node`` first. For the backward search that is
        path order; the forward caller reverses it.
        """
        out: list[tuple[int, np.ndarray]] = []
        j = int(step_of[node])
        while j >= 0:
            run = runs[j]
            size = len(run.block.nodes)
            local = run.block.local(node)
            chain = [local]
            while 0 <= (prev := int(run.pred[chain[-1]])) < size:
                chain.append(prev)
            chain = np.array(chain)
            # In the backward search predecessors run against edge direction.
            tails, heads = (chain[:-1], chain[1:]) if run.reverse else (chain[1:], chain[:-1])
            out.append((j, run.block.edges_between(tails, heads)))
            node = int(run.block.nodes[chain[-1]])
            j = int(run.origin[chain[-1]])  # the step whose distance seeded the chain's end
        return out

    def _expand(self, level: int, edges: np.ndarray) -> np.ndarray:
        """Unpack edges of level graph ``level`` (in path order) into original CSR positions."""
        g = self.graphs[level]
        shortcut = g.kind[edges] == _SHORTCUT
        if not shortcut.any():
            return g.ref[edges]
        return np.concatenate(
            [
                self._expand_shortcut(level, int(g.ref[e])) if is_sc else g.ref[e : e + 1]
                for e, is_sc in zip(edges, shortcut, strict=True)
            ]
        )

    def _expand_shortcut(self, level: int, k: int) -> np.ndarray:
        """Original CSR positions behind shortcut ``k`` of level ``level`` (memoized)."""
        key = (level, k)
        if key in self._expanded:
            return self._expanded[key]
        table = self.shortcuts[level]
        block = self.blocks[level - 1][int(table.cell[k])]
        a, b = block.local(int(table.src[k])), block.local(int(table.dst[k]))
        _, pred = dijkstra(block.fwd, directed=True, indices=a, return_predecessors=True)
        chain = [b]
        while chain[-1] != a:
            chain.append(int(pred[chain[-1]]))
        chain = np.array(chain[::-1])
        out = self._expand(level - 1, block.edges_between(chain[:-1], chain[1:]))
        self._expanded[key] = out
        return out

    def __getstate__(self):
        """Pickle without the shortcut-expansion memo (it refills on use)."""
        state = self.__dict__.copy()
        state["_expanded"] = {}
        return state

    def __repr__(self) -> str:
        """Return a compact representation for shell + log output."""
        return f"S2Overlay(levels={self.levels}, top_nodes={len(self.top.nodes)}, top_edges={self.top.fwd.nnz})"


# -- helpers -----------------------------------------------------------------


def _leaf_cells(graph, crs) -> np.ndarray:
    """Leaf S2 cell of every node; nodes without coordinates borrow a neighbor's (else cell 0)."""
    x, y = graph.coords[:, 0].copy(), graph.coords[:, 1].copy()
    known = np.isfinite(x) & np.isfinite(y)
    if crs is not None:
        try:
            from pyproj import Transformer
        except ImportError as exc:  # pragma: no cover - exercised only without pyproj
            raise ImportError("S2Overlay(crs=...) needs pyproj: pip install 'netstead[clean]'") from exc
        x[known], y[known] = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(x[known], y[known])
    elif known.any() and (np.abs(x[known]).max() > 180 or np.abs(y[known]).max() > 90):
        raise ValueError(
            "Node coordinates are not lon/lat degrees; pass crs= (e.g. crs='EPSG:2264') so they can be reprojected."
        )

    leaf = np.zeros(len(x), dtype=np.uint64)  # 0 is not a valid S2 id: the no-coordinate cell
    leaf[known] = s2.cell_ids(x[known], y[known])
    csr = graph.csr
    rows = np.repeat(np.arange(len(x)), np.diff(csr.indptr))
    u = np.concatenate([rows, csr.indices])
    v = np.concatenate([csr.indices, rows])
    while True:  # spread cells across edges to coordinate-less nodes
        reach = (leaf[u] != 0) & (leaf[v] == 0)
        if not reach.any():
            return leaf
        leaf[v[reach]] = leaf[u[reach]]


def _group_unique(values: np.ndarray, groups: np.ndarray) -> dict[int, np.ndarray]:
    """``{group: sorted unique values}``."""
    if not len(values):
        return {}
    pairs = np.unique(np.stack([groups, values]), axis=1)
    keys, starts = np.unique(pairs[0], return_index=True)
    return {int(k): part for k, part in zip(keys, np.split(pairs[1], starts[1:]), strict=True)}


def _make_blocks(g: _LevelGraph, g_nodes: np.ndarray, cell: np.ndarray, internal: np.ndarray) -> dict[int, _Block]:
    """One :class:`_Block` per cell holding ``g``'s nodes in that cell and its internal edges."""
    node_cell = cell[g_nodes]
    order = np.argsort(node_cell, kind="stable")
    cell_ids, node_starts = np.unique(node_cell[order], return_index=True)
    node_groups = np.split(g_nodes[order], node_starts[1:])

    edge_idx = np.nonzero(internal)[0]
    edge_cell = cell[g.u[edge_idx]]
    eorder = np.argsort(edge_cell, kind="stable")
    edge_idx = edge_idx[eorder]
    bounds = np.searchsorted(edge_cell[eorder], cell_ids, side="left")
    ends = np.searchsorted(edge_cell[eorder], cell_ids, side="right")

    blocks = {}
    for c, nodes, lo, hi in zip(cell_ids, node_groups, bounds, ends, strict=True):
        blocks[int(c)] = _make_block(np.sort(nodes), g, edge_idx[lo:hi])
    return blocks


def _make_block(nodes: np.ndarray, g: _LevelGraph, edges) -> _Block:
    """Build a block over ``nodes`` from the edges of ``g`` selected by ``edges`` (indices or mask)."""
    edges = np.nonzero(edges)[0] if edges.dtype == bool else edges
    lu = np.searchsorted(nodes, g.u[edges])
    lv = np.searchsorted(nodes, g.v[edges])
    w = g.w[edges]
    size = len(nodes)
    fwd, fwd_order = _csr(lu, lv, w, size)
    rev, _ = _csr(lv, lu, w, size)
    fwd_key = lu[fwd_order].astype(np.int64) * size + lv[fwd_order]
    return _Block(nodes, fwd, rev, edges[fwd_order], fwd_key)


def _csr(rows: np.ndarray, cols: np.ndarray, w: np.ndarray, size: int):
    """CSR matrix with rows grouped and columns sorted; also the order applied to the inputs."""
    order = np.lexsort((cols, rows))
    indptr = np.zeros(size + 1, dtype=np.int64)
    np.cumsum(np.bincount(rows, minlength=size), out=indptr[1:])
    return csr_array((w[order], cols[order].astype(np.int32), indptr), shape=(size, size)), order


def _seeded_dijkstra(csr: csr_array, seeds: np.ndarray, seed_cost: np.ndarray):
    """Dijkstra from several nodes at once, each starting at its own cost.

    Adds a virtual source (index ``size``) with an edge to each seed, so a
    predecessor of ``size`` or beyond means "seeded here".
    """
    size = csr.shape[0]
    indptr = np.append(csr.indptr, csr.indptr[-1] + len(seeds))
    aug = csr_array(
        (np.concatenate([csr.data, seed_cost]), np.concatenate([csr.indices, seeds.astype(np.int32)]), indptr),
        shape=(size + 1, size + 1),
    )
    dist, pred = dijkstra(aug, directed=True, indices=size, return_predecessors=True)
    return dist[:size], pred[:size]


def _cell_shortcuts(block: _Block, entries: np.ndarray, exits: np.ndarray):
    """Entry -> exit shortcuts across one cell, minus those implied by a path through another boundary node."""
    ent = np.searchsorted(block.nodes, entries)
    ext = np.searchsorted(block.nodes, exits)
    dist = dijkstra(block.fwd, directed=True, indices=ent)[:, ext]
    keep = np.isfinite(dist) & (entries[:, None] != exits[None, :])

    # a -> c is redundant if some boundary node b (both an exit and an entry)
    # has d(a, b) + d(b, c) <= d(a, c). Each leg must also be strictly shorter
    # than d(a, c): otherwise zero-cost legs, or legs too small to change a
    # float sum, let two shortcuts each justify dropping the other.
    via = np.intersect1d(entries, exits)
    via_col = np.searchsorted(exits, via)
    via_row = np.searchsorted(entries, via)
    for col, row in zip(via_col, via_row, strict=True):
        to_b = dist[:, col : col + 1]
        from_b = dist[row : row + 1, :]
        implied = (to_b < dist) & (from_b < dist) & (to_b + from_b <= dist)
        keep &= ~implied
    a, c = np.nonzero(keep)
    return entries[a], exits[c], dist[a, c]
