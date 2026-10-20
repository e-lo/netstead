"""Tests for netstead.viz.buffers — Parquet frames -> packed binary + attrs."""

from importlib import resources

import pandas as pd
import pytest
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.viz.buffers import network_attrs, pack_network, unpack_network


@pytest.fixture(scope="module")
def rdu():
    base = resources.files("netstead.fixtures.rdu_i40").joinpath("parquet")
    return (pd.read_parquet(base.joinpath("link.parquet")), pd.read_parquet(base.joinpath("node.parquet")))


def test_pack_unpack_roundtrip_counts(rdu):
    links, nodes = rdu
    blob = pack_network(links, nodes)
    assert isinstance(blob, (bytes, bytearray))
    net = unpack_network(blob)
    assert net["links"]["count"] == len(links)
    assert net["nodes"]["count"] == len(nodes)
    # startIndices has count+1 entries, monotonic non-decreasing
    si = net["links"]["startIndices"]
    assert len(si) == len(links) + 1
    assert all(si[i] <= si[i + 1] for i in range(len(si) - 1))
    # positions are flat lon,lat pairs; last startIndex = number of vertices
    assert len(net["links"]["positions"]) == si[-1] * 2
    assert net["links"]["ids"][0] == links["link_id"].iloc[0]


def test_positions_are_wgs84ish(rdu):
    links, nodes = rdu
    net = unpack_network(pack_network(links, nodes))
    xs = net["links"]["positions"][0::2]
    ys = net["links"]["positions"][1::2]
    assert all(-79 < x < -78 for x in xs[:50])
    assert all(35 < y < 36 for y in ys[:50])


def test_straight_fallback_when_no_geometry(rdu):
    links, nodes = rdu
    links = links.copy()
    links.loc[links.index[0], "geometry"] = None  # force fallback on first link
    net = unpack_network(pack_network(links, nodes))
    # first link now has exactly 2 vertices (straight from->to)
    si = net["links"]["startIndices"]
    assert si[1] - si[0] == 2


@pytest.fixture(scope="module")
def leavenworth_frames():
    net = Network.from_source(leavenworth.csv_dir())
    return net.links.to_pandas(), net.nodes.to_pandas(), net.geometry.to_pandas()


def _vertex_counts(blob) -> list[int]:
    si = unpack_network(blob)["links"]["startIndices"]
    return [si[i + 1] - si[i] for i in range(len(si) - 1)]


def test_geometry_table_shapes_links(leavenworth_frames):
    """Leavenworth keeps shapes in the ``geometry`` table; without it every link is a straight segment."""
    links, nodes, geometry = leavenworth_frames
    assert "geometry" not in links.columns
    first = links.index[links["link_id"] == 1][0]
    assert links.loc[first, "geometry_id"] == 1

    without = _vertex_counts(pack_network(links, nodes))
    with_table = _vertex_counts(pack_network(links, nodes, geometry=geometry))
    assert without[first] == 2
    assert with_table[first] == 8  # geometry_id 1 has 8 vertices in geometry.csv
    assert sum(with_table) > sum(without)


def test_inline_geometry_wins_over_geometry_table():
    nodes = pd.DataFrame({"node_id": [1, 2], "x_coord": [0.0, 1.0], "y_coord": [0.0, 1.0]})
    geometry = pd.DataFrame({"geometry_id": [10], "geometry": ["LINESTRING (0 0, 0.5 0.2, 0.7 0.4, 1 1)"]})
    links = pd.DataFrame(
        {
            "link_id": [1, 2, 3],
            "from_node_id": [1, 1, 1],
            "to_node_id": [2, 2, 2],
            "geometry_id": [10, 10, 99],  # 99 is not in the geometry table
            "geometry": ["LINESTRING (0 0, 0.1 0.9, 1 1)", None, None],
        }
    )
    counts = _vertex_counts(pack_network(links, nodes, geometry=geometry))
    assert counts == [3, 4, 2]  # inline, geometry table, straight from/to


def test_lanes_present_and_freeway_thicker(rdu):
    links, nodes = rdu
    net = unpack_network(pack_network(links, nodes))
    lanes = net["links"]["lanes"]
    assert len(lanes) == len(links) and all(x >= 1 for x in lanes)
    # motorway links should default to more lanes than residential when untagged
    ids = net["links"]["ids"]
    ft = dict(zip(links["link_id"], links["facility_type"], strict=False))
    mot = [lanes[i] for i, lid in enumerate(ids) if ft.get(lid) == "motorway"]
    res = [lanes[i] for i, lid in enumerate(ids) if ft.get(lid) == "residential"]
    if mot and res:
        assert max(mot) > min(res)


def test_network_attrs_index_aligned(rdu):
    links, _nodes = rdu
    attrs = network_attrs(links)
    assert attrs["link_id"] == list(links["link_id"])
    assert len(attrs["name"]) == len(links)
    assert len(attrs["facility_type"]) == len(links)
