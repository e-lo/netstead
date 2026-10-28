"""Spatial (STRtree) + graph (scipy CSR via :mod:`netstead.graph`) build/cache/load.

Indexes are **opt-in** (per architecture §6.2): build them when the
caller knows they'll run network-aware scope ops repeatedly. The
auto-build heuristic (threshold-based) lives in :mod:`netstead.scope`
(task 3.10) — this package only ships the primitives.

The graph slot returns a :class:`netstead.graph.GMNSGraph` (scipy CSR routing
engine), which both :mod:`netstead.scope` and :mod:`netstead.semantics.connectivity`
consume.

Examples:
    >>> import pytest
    >>> _ = pytest.importorskip("shapely")
    >>> _ = pytest.importorskip("scipy")
    >>> from netstead.indexes import SpatialIndex, build_indexes, cache_path
    >>> cache_path("/tmp/net.duckdb", "spatial", "deadbeefcafef00d").name
    'net.spatial.deadbeef.parquet'
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from .cache import cache_path, load_cached, save_cached

# SpatialIndex needs shapely ([clean]); without it the cache helpers still work.
with contextlib.suppress(ImportError):
    from .spatial import SpatialIndex

if TYPE_CHECKING:  # pragma: no cover - typing only
    from corral.dataset import Table

__all__ = [
    "SpatialIndex",
    "build_indexes",
    "cache_path",
    "load_cached",
    "save_cached",
]


def build_indexes(
    *,
    links: Table,
    nodes: Table | None = None,
    spatial: bool = True,
    graph: bool = True,
) -> tuple[SpatialIndex | None, Any]:
    """Build both indexes from a links + nodes pair, returning ``(spatial, graph)``.

    Either flag can be ``False`` to skip the corresponding build. The graph
    slot returns a :class:`netstead.graph.GMNSGraph` (scipy CSR) suitable for
    network-aware scope + connectivity ops. ``graph=True`` requires ``nodes``.

    Raises:
        ValueError: If ``graph=True`` but ``nodes`` is ``None``.

    Examples:
        >>> import pytest
        >>> _ = pytest.importorskip("shapely")
        >>> _ = pytest.importorskip("scipy")
        >>> build_indexes  # doctest: +ELLIPSIS
        <function build_indexes at ...>
    """
    if graph and nodes is None:
        raise ValueError("build_indexes(graph=True) requires a nodes Table")
    spatial_idx = None
    if spatial:
        from .spatial import SpatialIndex  # ImportError names shapely when [clean] is missing

        spatial_idx = SpatialIndex.build(links)
    graph_idx: Any = None
    if graph and nodes is not None:
        from netstead.graph import GMNSGraph

        graph_idx = GMNSGraph.build(
            {"node": nodes.to_pandas(), "link": links.to_pandas()},
            cost="length",
            keep_missing_cost=True,
        )
    return spatial_idx, graph_idx
