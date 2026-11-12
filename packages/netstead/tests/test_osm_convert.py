"""Tests for netstead.osm.convert — pure OSM elements -> GMNS node/link records.

Node coordinates are passed as ``{osm_node_id: (lon, lat)}``; ways as
``{"id": int, "nodes": [osm_node_id, ...], "tags": {...}}``.
"""

from __future__ import annotations

import pytest
from netstead.osm import convert


class TestIntermediateNodesDissolved:
    def test_single_two_way_keeps_only_endpoints(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 100, "nodes": [1, 2, 3], "tags": {"highway": "residential", "name": "A St"}}]

        node_recs, _ = convert.build_node_link_tables(nodes, ways)

        kept = {n["node_id"] for n in node_recs}
        assert kept == {1, 3}  # node 2 is an intermediate shape point -> dropped

    def test_intermediate_node_id_retained_on_link_property(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 100, "nodes": [1, 2, 3], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        forward = next(r for r in link_recs if r["from_node_id"] == 1 and r["to_node_id"] == 3)
        assert forward["osm_node_ids"] == "1,2,3"
        assert forward["osm_way_id"] == 100


class TestDirectedExpansion:
    def test_two_way_emits_two_directed_links(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)

        assert len(link_recs) == 2
        pairs = {(r["from_node_id"], r["to_node_id"]) for r in link_recs}
        assert pairs == {(1, 2), (2, 1)}
        assert all(r["directed"] is True for r in link_recs)

    def test_oneway_yes_emits_single_forward_link(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "primary", "oneway": "yes"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)

        assert len(link_recs) == 1
        assert (link_recs[0]["from_node_id"], link_recs[0]["to_node_id"]) == (1, 2)

    def test_oneway_reverse_emits_single_backward_link(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "primary", "oneway": "-1"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)

        assert len(link_recs) == 1
        assert (link_recs[0]["from_node_id"], link_recs[0]["to_node_id"]) == (2, 1)

    def test_unique_link_ids(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 1, "nodes": [1, 2, 3], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        ids = [r["link_id"] for r in link_recs]
        assert len(ids) == len(set(ids))


class TestIntersectionSplitting:
    def test_shared_node_is_kept_and_ways_split(self):
        # Way 10 runs 1-2-3 horizontally; way 20 runs 4-2 into node 2.
        nodes = {1: (0.0, 0.0), 2: (1.0, 0.0), 3: (2.0, 0.0), 4: (1.0, 1.0)}
        ways = [
            {"id": 10, "nodes": [1, 2, 3], "tags": {"highway": "residential"}},
            {"id": 20, "nodes": [4, 2], "tags": {"highway": "residential"}},
        ]

        node_recs, link_recs = convert.build_node_link_tables(nodes, ways)

        kept = {n["node_id"] for n in node_recs}
        assert kept == {1, 2, 3, 4}  # node 2 shared by 2 ways -> intersection, kept
        # 3 undirected segments (1-2, 2-3, 4-2), each two-way -> 6 directed links
        assert len(link_recs) == 6


class TestGeometryAndLength:
    def test_wkt_linestring_in_lon_lat_order(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 1, "nodes": [1, 2, 3], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        forward = next(r for r in link_recs if r["from_node_id"] == 1)
        assert forward["geometry"] == "LINESTRING (0.0 0.0, 0.0 1.0, 0.0 2.0)"

    def test_reverse_link_reverses_geometry(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 1, "nodes": [1, 2, 3], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        backward = next(r for r in link_recs if r["from_node_id"] == 3)
        assert backward["geometry"] == "LINESTRING (0.0 2.0, 0.0 1.0, 0.0 0.0)"
        assert backward["osm_node_ids"] == "3,2,1"

    def test_length_is_geodesic_meters(self):
        # 0,0 -> 0,1 (deg lat) -> 0,2: ~111.2 km per degree latitude.
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 3: (0.0, 2.0)}
        ways = [{"id": 1, "nodes": [1, 2, 3], "tags": {"highway": "residential"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        forward = next(r for r in link_recs if r["from_node_id"] == 1)
        assert forward["length"] == pytest.approx(222390, rel=0.01)


class TestAttributeMapping:
    def test_core_attributes_mapped(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [
            {
                "id": 1,
                "nodes": [1, 2],
                "tags": {"highway": "residential", "name": "Main", "lanes": "2", "maxspeed": "25 mph"},
            }
        ]

        _, link_recs = convert.build_node_link_tables(nodes, ways)
        r = link_recs[0]
        assert r["name"] == "Main"
        assert r["lanes"] == 2
        assert r["free_speed"] == 25.0
        assert r["facility_type"] == "residential"

    def test_extra_tags_carried(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "residential", "surface": "asphalt"}}]

        _, link_recs = convert.build_node_link_tables(nodes, ways, extra_tags=["surface"])
        assert link_recs[0]["surface"] == "asphalt"


class TestNodeRecords:
    def test_node_records_have_lon_x_lat_y(self):
        nodes = {1: (-71.09, 42.36), 2: (-71.08, 42.37)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "residential"}}]

        node_recs, _ = convert.build_node_link_tables(nodes, ways)
        n1 = next(n for n in node_recs if n["node_id"] == 1)
        assert n1["x_coord"] == -71.09
        assert n1["y_coord"] == 42.36

    def test_isolated_nodes_not_emitted(self):
        # node 9 has coords but is referenced by no way -> not a GMNS node.
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0), 9: (5.0, 5.0)}
        ways = [{"id": 1, "nodes": [1, 2], "tags": {"highway": "residential"}}]

        node_recs, _ = convert.build_node_link_tables(nodes, ways)
        assert 9 not in {n["node_id"] for n in node_recs}


class TestMalformedInput:
    def test_unknown_node_in_way_raises_helpful_error(self):
        # A way references node 99 which is not in the nodes dict (malformed
        # Overpass response from a custom endpoint, or a manual build).
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [{"id": 42, "nodes": [1, 99, 2], "tags": {"highway": "residential"}}]
        with pytest.raises(ValueError, match=r"way 42 references node 99"):
            convert.build_node_link_tables(nodes, ways)


class TestRouteRef:
    def test_ref_is_kept_as_its_own_column_and_never_copied_into_name(self):
        nodes = {1: (0.0, 0.0), 2: (0.0, 1.0)}
        ways = [
            {"id": 1, "nodes": [1, 2], "tags": {"highway": "trunk", "ref": "US 2"}},
            {"id": 2, "nodes": [1, 2], "tags": {"highway": "trunk", "ref": "US 2;WA 207", "name": "Front Street"}},
            {"id": 3, "nodes": [1, 2], "tags": {"highway": "residential"}},
        ]

        _, link_recs = convert.build_node_link_tables(nodes, ways)

        by_way = {r["osm_way_id"]: r for r in link_recs}
        assert by_way[1]["ref"] == "US 2" and by_way[1]["name"] is None  # numbered, still unnamed
        assert by_way[2]["ref"] == "US 2;WA 207" and by_way[2]["name"] == "Front Street"
        assert by_way[3]["ref"] is None
