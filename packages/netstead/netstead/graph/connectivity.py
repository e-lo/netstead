"""Connectivity analysis for a :class:`GMNSGraph` (data-quality QA)."""

from __future__ import annotations

import numpy as np
import pandas as pd


class ConnectivityResult:
    """Per-node component labels plus QA helpers."""

    def __init__(self, table: pd.DataFrame, n_components: int, connection: str, nodes_only_in_links):
        """Store the per-node component labels + counts produced by :func:`connectivity`.

        Args:
            table: Per-node frame with columns ``node_id``, ``component``, ``component_size``.
            n_components: Total number of components found.
            connection: ``"weak"`` or ``"strong"`` (the option used to build this result).
            nodes_only_in_links: Node ids that appeared only on link endpoints (no row in
                the node table) — surfaced as a data-quality hint.
        """
        self.table = table  # columns: node_id, component, component_size
        self.n_components = n_components
        self.connection = connection
        self.nodes_only_in_links = list(nodes_only_in_links)

    def component_sizes(self) -> pd.Series:
        """Component id -> node count, largest first."""
        return (
            self.table.drop_duplicates("component")
            .set_index("component")["component_size"]
            .sort_values(ascending=False)
        )

    def small_components(self, max_size: int = 1) -> pd.DataFrame:
        """Nodes belonging to components no larger than ``max_size`` (likely data errors)."""
        return self.table[self.table["component_size"] <= max_size]

    def summary(self) -> dict:
        """Return a small dict summarizing the connectivity result for logs / JSON output."""
        sizes = self.component_sizes()
        return {
            "connection": self.connection,
            "n_components": self.n_components,
            "n_nodes": len(self.table),
            "largest_component_size": int(sizes.iloc[0]) if len(sizes) else 0,
            "n_singleton_components": int((sizes == 1).sum()),
            "n_nodes_only_in_links": len(self.nodes_only_in_links),
        }

    def __repr__(self) -> str:
        """Return a compact representation for shell + log output."""
        return f"ConnectivityResult(n_components={self.n_components}, connection={self.connection!r})"


def connectivity(graph, connection: str = "weak") -> ConnectivityResult:
    """Label each node with its connected component.

    Args:
        graph: a :class:`GMNSGraph`.
        connection: ``"weak"`` (ignore edge direction) or ``"strong"`` (mutual
            reachability). Weak components are the usual check for "unconnected
            parts" / dangling subgraphs.
    """
    from scipy.sparse.csgraph import connected_components

    if connection not in ("weak", "strong"):
        raise ValueError(f"connection must be 'weak' or 'strong', got {connection!r}.")

    n_components, labels = connected_components(graph.csr, directed=True, connection=connection, return_labels=True)
    sizes = np.bincount(labels, minlength=n_components)
    table = pd.DataFrame(
        {
            "node_id": graph.node_ids,
            "component": labels,
            "component_size": sizes[labels],
        }
    )
    return ConnectivityResult(table, n_components, connection, graph.meta.get("nodes_only_in_links", []))
