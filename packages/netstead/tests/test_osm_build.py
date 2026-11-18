"""Tests for netstead.osm.build — records -> Network and the full OSM build pipeline.

The pipeline test injects a fake HTTP session (no network).
"""

from __future__ import annotations

from corral.engines.ibis_engine import IbisEngine
from netstead.osm import build


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)

    def post(self, url, data=None, headers=None, timeout=None):
        return self._responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._responses.pop(0)


def _two_node_street_payload():
    return {
        "elements": [
            {"type": "node", "id": 1, "lat": 42.000, "lon": -71.000},
            {"type": "node", "id": 2, "lat": 42.001, "lon": -71.000},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential", "name": "Main St"}},
        ]
    }


_NODE_RECS = [
    {"node_id": 1, "x_coord": -71.0, "y_coord": 42.0},
    {"node_id": 2, "x_coord": -71.0, "y_coord": 42.001},
]
_LINK_RECS = [
    {
        "link_id": 1,
        "from_node_id": 1,
        "to_node_id": 2,
        "directed": True,
        "length": 111.2,
        "free_speed": None,
        "lanes": None,
        "facility_type": "residential",
        "name": "Main St",
        "geometry": "LINESTRING (-71.0 42.0, -71.0 42.001)",
        "osm_way_id": 100,
        "osm_node_ids": "1,2",
    },
    {
        "link_id": 2,
        "from_node_id": 2,
        "to_node_id": 1,
        "directed": True,
        "length": 111.2,
        "free_speed": None,
        "lanes": None,
        "facility_type": "residential",
        "name": "Main St",
        "geometry": "LINESTRING (-71.0 42.001, -71.0 42.0)",
        "osm_way_id": 100,
        "osm_node_ids": "2,1",
    },
]


class TestNetworkFromRecords:
    def test_builds_network_with_named_accessors(self):
        net = build.network_from_records(_NODE_RECS, _LINK_RECS, engine=IbisEngine())
        assert net.spec_version == "0.97"
        assert net.nodes.count() == 2
        assert net.links.count() == 2

    def test_links_reference_existing_nodes(self):
        net = build.network_from_records(_NODE_RECS, _LINK_RECS, engine=IbisEngine())
        node_ids = set(net.nodes.to_pandas()["node_id"])
        links = net.links.to_pandas()
        assert set(links["from_node_id"]) <= node_ids
        assert set(links["to_node_id"]) <= node_ids

    def test_provenance_columns_preserved(self):
        net = build.network_from_records(_NODE_RECS, _LINK_RECS, engine=IbisEngine())
        assert "osm_way_id" in net.links.to_pandas().columns

    def test_engine_override_respected(self):
        net = build.network_from_records(_NODE_RECS, _LINK_RECS, engine=IbisEngine())
        assert net.engine.name == "ibis"


class TestBuildNetworkFromOsm:
    def test_full_pipeline_with_faked_http(self):
        sess = _FakeSession([_FakeResponse(200, _two_node_street_payload())])
        net = build.build_network_from_osm(
            (-71.1, 41.9, -70.9, 42.1),
            engine=IbisEngine(),
            session=sess,
            sleep=lambda *_: None,
        )
        assert net.nodes.count() == 2
        assert net.links.count() == 2  # two-way residential -> two directed links
        assert "Main St" in set(net.links.to_pandas()["name"])

    def test_ref_column_is_kept_through_the_pipeline_and_leaves_name_empty(self):
        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 42.000, "lon": -71.000},
                {"type": "node", "id": 2, "lat": 42.001, "lon": -71.000},
                {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "trunk", "ref": "US 2"}},
            ]
        }
        net = build.build_network_from_osm(
            (-71.1, 41.9, -70.9, 42.1),
            engine=IbisEngine(),
            session=_FakeSession([_FakeResponse(200, payload)]),
            sleep=lambda *_: None,
        )
        links = net.links.to_pandas()
        assert set(links["ref"]) == {"US 2"}
        assert links["name"].isna().all()
