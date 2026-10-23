"""GMNSGraph.from_network + the scope/semantics-facing primitives.

These back :mod:`netstead.semantics.connectivity` and :mod:`netstead.scope`: neighbors
(undirected, excludes seed), network_buffer (directed-out, multi-seed, includes
seeds), connected_component (weak).
"""

from __future__ import annotations

import pandas as pd
from netstead.graph import GMNSGraph
from netstead.osm import network_from_records


def _path_dict(n: int, length: float = 10.0):
    """A directed path 1->2->...->n with uniform link length."""
    nodes = pd.DataFrame(
        {"node_id": list(range(1, n + 1)), "x_coord": [float(i) for i in range(n)], "y_coord": [0.0] * n}
    )
    links = pd.DataFrame(
        {
            "link_id": [f"L{i}{i + 1}" for i in range(1, n)],
            "from_node_id": list(range(1, n)),
            "to_node_id": list(range(2, n + 1)),
            "length": [length] * (n - 1),
        }
    )
    return {"node": nodes, "link": links}


class TestFromNetwork:
    def test_builds_routable_graph_from_a_network(self):
        node_recs = [
            {"node_id": 1, "x_coord": 0.0, "y_coord": 0.0},
            {"node_id": 2, "x_coord": 1.0, "y_coord": 0.0},
            {"node_id": 3, "x_coord": 2.0, "y_coord": 0.0},
        ]
        link_recs = [
            {"link_id": 1, "from_node_id": 1, "to_node_id": 2, "directed": True, "length": 10.0},
            {"link_id": 2, "from_node_id": 2, "to_node_id": 3, "directed": True, "length": 10.0},
        ]
        net = network_from_records(node_recs, link_recs)

        g = GMNSGraph.from_network(net, cost="length")

        assert g.shortest_path(1, 3).nodes == [1, 2, 3]
        assert g.shortest_path(1, 3).cost == 20.0


class TestNeighbors:
    def test_undirected_one_hop_excludes_seed(self):
        g = GMNSGraph.build(_path_dict(4), cost="length", directed=True)
        assert g.neighbors(2, hops=1) == {1, 3}  # undirected, seed excluded
        assert g.neighbors(1, hops=1) == {2}

    def test_two_hops(self):
        g = GMNSGraph.build(_path_dict(4), cost="length", directed=True)
        assert g.neighbors(1, hops=2) == {2, 3}

    def test_unknown_node_returns_empty(self):
        g = GMNSGraph.build(_path_dict(4), cost="length", directed=True)
        assert g.neighbors(999) == set()


class TestNetworkBuffer:
    def test_distance_bounded_includes_seed(self):
        g = GMNSGraph.build(_path_dict(4, length=10.0), cost="length", directed=True)
        # From node 1, a 10m budget reaches node 2 only (1->2 = 10).
        assert g.network_buffer([1], 10.0) == {1, 2}
        assert g.network_buffer([1], 25.0) == {1, 2, 3}

    def test_multi_seed_union(self):
        g = GMNSGraph.build(_path_dict(4, length=10.0), cost="length", directed=True)
        assert g.network_buffer([1, 3], 10.0) == {1, 2, 3, 4}

    def test_unknown_seeds_empty(self):
        g = GMNSGraph.build(_path_dict(4), cost="length", directed=True)
        assert g.network_buffer([999], 100.0) == set()


class TestConnectedComponent:
    def test_weak_component_of_seed(self):
        nodes = pd.DataFrame({"node_id": [1, 2, 3, 7, 8], "x_coord": [0.0] * 5, "y_coord": [0.0] * 5})
        links = pd.DataFrame(
            {
                "link_id": ["a", "b", "c"],
                "from_node_id": [1, 2, 7],
                "to_node_id": [2, 3, 8],
                "length": [1.0, 1.0, 1.0],
            }
        )
        g = GMNSGraph.build({"node": nodes, "link": links}, cost="length", directed=True)
        assert g.connected_component(1) == {1, 2, 3}
        assert g.connected_component(7) == {7, 8}

    def test_unknown_node_empty(self):
        g = GMNSGraph.build(_path_dict(3), cost="length", directed=True)
        assert g.connected_component(999) == set()


class TestReachableFrom:
    def test_directed_reachability_includes_seed(self):
        g = GMNSGraph.build(_path_dict(4), cost="length", directed=True)
        assert g.reachable_from(1) == {1, 2, 3, 4}
        assert g.reachable_from(3) == {3, 4}

    def test_unknown_seed_empty(self):
        g = GMNSGraph.build(_path_dict(3), cost="length", directed=True)
        assert g.reachable_from(999) == set()


class TestKeepMissingCost:
    def _null_length_pair(self):
        nodes = pd.DataFrame({"node_id": [1, 2], "x_coord": [0.0, 1.0], "y_coord": [0.0, 0.0]})
        links = pd.DataFrame({"link_id": ["a"], "from_node_id": [1], "to_node_id": [2], "length": [None]})
        return {"node": nodes, "link": links}

    def test_null_cost_edge_dropped_by_default(self):
        g = GMNSGraph.build(self._null_length_pair(), cost="length")
        assert g.meta["n_edges"] == 0  # routing default: can't route on unknown cost

    def test_keep_missing_cost_preserves_topology(self):
        g = GMNSGraph.build(self._null_length_pair(), cost="length", keep_missing_cost=True)
        assert g.meta["n_edges"] == 1
        assert g.connected_component(1) == {1, 2}


# GraphIndex parity test removed: netstead.indexes.graph (igraph) has been
# retired now that scope + semantics share the GMNSGraph backend. The
# behavior it locked in is now covered by the corresponding GMNSGraph tests
# above and the scope + semantics integration tests against the Leavenworth
# fixture.
