"""Graph / network analysis for GMNS networks.

Requires the optional ``graph`` extra (``pip install 'netstead[graph]'``).

Build a routing graph once and reuse it for connectivity QA, isochrones, and
shortest paths::

    from netstead.graph import GMNSGraph

    g = GMNSGraph.build(source="network.duckdb", cost="length / free_speed")
    g.connectivity()              # unconnected parts (data quality)
    g.isochrone(source_node=1, cutoff=20)
    g.shortest_path(1, 9)

    g.build_overlay()             # optional: S2 cell overlay for faster shortest paths
"""

# Guard the optional [graph] extra up front so an import-time error points the
# user at the install command (scipy is the gating dependency).
try:
    import scipy  # noqa: F401
except ImportError as e:  # pragma: no cover - defensive
    raise ImportError("netstead.graph requires the [graph] extra: pip install 'netstead[graph]'") from e

from .build import GMNSGraph, ShortestPathResult
from .connectivity import ConnectivityResult, connectivity
from .overlay import S2Overlay
from .paths import IsochroneResult, isochrone, shortest_path, snap
from .source import (
    DuckDBSource,
    InMemorySource,
    NetworkSource,
    ParquetSource,
    PolarsSource,
    as_source,
)

__all__ = [
    "ConnectivityResult",
    "DuckDBSource",
    "GMNSGraph",
    "InMemorySource",
    "IsochroneResult",
    "NetworkSource",
    "ParquetSource",
    "PolarsSource",
    "S2Overlay",
    "ShortestPathResult",
    "as_source",
    "connectivity",
    "isochrone",
    "shortest_path",
    "snap",
]
