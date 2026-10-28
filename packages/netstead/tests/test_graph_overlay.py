"""Tests for netstead.graph.overlay — the S2 cell overlay for shortest paths.

Every overlay answer is checked against plain Dijkstra
(:func:`netstead.graph.paths.shortest_path`): same cost, and a connected path
whose links add up to it.
"""

import pickle
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("scipy")

from netstead.fixtures import leavenworth
from netstead.graph import GMNSGraph, S2Overlay
from netstead.graph.paths import shortest_path as plain_shortest_path


def grid_network(side=30, spacing=0.003, seed=0):
    """A lon/lat street grid (~10 km across, many level-13 cells) with mixed one-way links.

    Costs are random so paths have to bend; every fifth street is one-way,
    and a handful of links are dropped so some pairs are unreachable.
    """
    rng = np.random.default_rng(seed)
    ids = np.arange(side * side).reshape(side, side) + 1
    nodes = pd.DataFrame(
        {
            "node_id": ids.ravel(),
            "x_coord": (-122.45 + spacing * np.tile(np.arange(side), side)),
            "y_coord": (37.70 + spacing * np.repeat(np.arange(side), side)),
        }
    )
    pairs = [(ids[r, c], ids[r, c + 1]) for r in range(side) for c in range(side - 1)]
    pairs += [(ids[r, c], ids[r + 1, c]) for r in range(side - 1) for c in range(side)]
    pairs = [p for p in pairs if rng.random() > 0.03]
    links = pd.DataFrame(
        {
            "link_id": np.arange(len(pairs)) + 1000,
            "from_node_id": [a for a, _ in pairs],
            "to_node_id": [b for _, b in pairs],
            "cost": rng.uniform(1.0, 5.0, len(pairs)),
            "directed": [k % 5 == 0 for k in range(len(pairs))],
        }
    )
    return {"node": nodes, "link": links}


@pytest.fixture(scope="module")
def grid():
    return GMNSGraph.build(grid_network(), cost="cost")


def assert_matches_plain(graph, overlay, pairs):
    for source, target in pairs:
        expected = plain_shortest_path(graph, source, target)
        got = overlay.shortest_path(graph, source, target)
        assert got.cost == pytest.approx(expected.cost, rel=1e-9), (source, target)
        if not np.isfinite(expected.cost):
            assert got.nodes == [] and got.links == []
            continue
        assert got.nodes[0] == source and got.nodes[-1] == target
        idx = [graph.index_of(n) for n in got.nodes]
        hops = [graph.csr[[a], [b]][0] for a, b in pairwise(idx)]
        assert sum(hops) == pytest.approx(got.cost, rel=1e-9)
        assert [graph.link_between(a, b) for a, b in pairwise(idx)] == got.links


def random_pairs(graph, count, seed=1):
    rng = np.random.default_rng(seed)
    return [(graph.node_at(int(a)), graph.node_at(int(b))) for a, b in rng.integers(0, graph.csr.shape[0], (count, 2))]


@pytest.mark.parametrize("levels", [(13,), (13, 12), (14, 12, 10), (13, 11, 9)])
def test_overlay_costs_match_dijkstra(grid, levels):
    overlay = S2Overlay(grid, levels)
    assert_matches_plain(grid, overlay, random_pairs(grid, 150))


def test_pruned_shortcuts_stay_fewer_than_edges(grid):
    overlay = S2Overlay(grid, (14,))
    assert 0 < len(overlay.shortcuts[1].src) < overlay.graphs[0].u.size


def test_tiny_links_between_boundary_nodes_keep_their_shortcuts():
    # b <-> c cost less than half an ulp of the a -> b shortcut, so a -> b + b -> c
    # rounds to a -> b: the pruning must not let a -> b and a -> c remove each other.
    base_x, base_y = -122.4194, 37.7749
    offsets = {
        1: (0, 0),
        2: (0.0001, 0),
        3: (0.0002, 0),
        10: (-0.03, 0),
        11: (0.03, 0),
        12: (0.03, 0.03),
        13: (0, 0.03),
    }
    nodes = pd.DataFrame(
        {
            "node_id": list(offsets),
            "x_coord": [base_x + dx for dx, _ in offsets.values()],
            "y_coord": [base_y + dy for _, dy in offsets.values()],
        }
    )
    rows = [(100, 10, 1, 1.0), (101, 1, 2, 1000.0), (102, 2, 3, 1e-14), (103, 3, 2, 1e-14)]
    rows += [(104, 2, 11, 1.0), (105, 3, 12, 1.0), (106, 13, 2, 1.0), (107, 13, 3, 1.0)]
    links = pd.DataFrame(rows, columns=["link_id", "from_node_id", "to_node_id", "cost"])
    g = GMNSGraph.build({"node": nodes, "link": links}, cost="cost")
    assert_matches_plain(g, S2Overlay(g, (13, 11)), [(10, 11), (10, 12)])


def test_same_node_and_unreachable(grid):
    overlay = S2Overlay(grid, (13, 11))
    same = overlay.shortest_path(grid, 1, 1)
    assert same.cost == 0 and same.nodes == [1] and same.links == []

    isolated = {
        "node": pd.DataFrame({"node_id": [1, 2, 3], "x_coord": [-122.4, -122.3, -122.2], "y_coord": [37.7] * 3}),
        "link": pd.DataFrame({"link_id": [1], "from_node_id": [1], "to_node_id": [2], "cost": [1.0]}),
    }
    g = GMNSGraph.build(isolated, cost="cost")
    result = S2Overlay(g, (13,)).shortest_path(g, 1, 3)
    assert not result.reachable and result.nodes == []


def test_leavenworth_matches_dijkstra():
    g = GMNSGraph.build(str(leavenworth.duckdb_path()), cost="length")
    assert_matches_plain(g, S2Overlay(g), random_pairs(g, 200))


def test_nodes_without_coordinates_borrow_a_neighbors_cell():
    net = grid_network(side=12)
    net["node"].loc[[5, 40, 77], ["x_coord", "y_coord"]] = np.nan
    g = GMNSGraph.build(net, cost="cost")
    assert_matches_plain(g, S2Overlay(g, (14, 13)), random_pairs(g, 100))


def test_projected_coordinates_need_a_crs():
    net = grid_network(side=6)
    net["node"]["x_coord"] = net["node"]["x_coord"] * 1e5
    g = GMNSGraph.build(net, cost="cost")
    with pytest.raises(ValueError, match="crs="):
        S2Overlay(g)


def test_projected_coordinates_reproject_with_crs():
    pyproj = pytest.importorskip("pyproj")
    net = grid_network(side=12)
    to_utm = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32610", always_xy=True)
    net["node"]["x_coord"], net["node"]["y_coord"] = to_utm.transform(net["node"]["x_coord"], net["node"]["y_coord"])
    g = GMNSGraph.build(net, cost="cost")
    overlay = S2Overlay(g, (14, 12), crs="EPSG:32610")
    assert len(overlay.blocks[0]) > 1
    assert_matches_plain(g, overlay, random_pairs(g, 60))


def test_rejects_bad_levels(grid):
    with pytest.raises(ValueError, match="levels"):
        S2Overlay(grid, ())
    with pytest.raises(ValueError, match="levels"):
        S2Overlay(grid, (0,))


def test_graph_uses_attached_overlay(grid):
    plain = grid.shortest_path(1, 900)
    grid.build_overlay(levels=(13, 11))
    try:
        assert grid.shortest_path(1, 900).cost == pytest.approx(plain.cost)
    finally:
        grid.attach_overlay(None)


def test_attach_rejects_overlay_from_another_graph(grid):
    other = GMNSGraph.build(grid_network(seed=5), cost="cost")
    overlay = S2Overlay(other, (13,))
    with pytest.raises(ValueError, match="different graph"):
        grid.attach_overlay(overlay)
    with pytest.raises(ValueError, match="different graph"):
        overlay.shortest_path(grid, 1, 2)


def test_overlay_pickles_without_its_memo(grid):
    overlay = S2Overlay(grid, (13, 11))
    overlay.shortest_path(grid, 1, 900)
    assert overlay._expanded
    clone = pickle.loads(pickle.dumps(overlay))
    assert clone._expanded == {}
    assert_matches_plain(grid, clone, random_pairs(grid, 20))


def test_build_overlay_caches_a_sidecar(tmp_path):
    source = tmp_path / "net.duckdb"
    g = GMNSGraph.build(grid_network(side=10), cost="cost")
    first = g.build_overlay(levels=(14, 13), cache=source)
    sidecars = list((tmp_path / "_netstead_indexes").glob("net.s2overlay.*.parquet"))
    assert len(sidecars) == 1

    again = GMNSGraph.build(grid_network(side=10), cost="cost")
    second = again.build_overlay(levels=(14, 13), cache=source)
    assert second is not first and second.fingerprint == first.fingerprint
    assert list((tmp_path / "_netstead_indexes").glob("net.s2overlay.*.parquet")) == sidecars

    again.build_overlay(levels=(13,), cache=source)
    assert len(list((tmp_path / "_netstead_indexes").glob("net.s2overlay.*.parquet"))) == 2
