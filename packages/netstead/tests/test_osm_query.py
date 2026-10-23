"""Tests for netstead.osm.query — area resolution, Overpass query building, HTTP (faked).

HTTP is exercised through an injected fake session (no network). Conventions:
bbox is ``(west, south, east, north)``; a point is ``(lat, lon)``.
"""

from __future__ import annotations

import pytest
from netstead.osm import query


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
        self.calls = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, data))
        return self._responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params))
        return self._responses.pop(0)


_NO_SLEEP = lambda *_: None  # noqa: E731 - test helper


class TestPointBufferBbox:
    def test_equator_one_degree(self):
        assert query.point_buffer_bbox(0.0, 0.0, 111320.0) == pytest.approx((-1.0, -1.0, 1.0, 1.0), abs=0.01)

    def test_latitude_widens_longitude_span(self):
        west, south, east, north = query.point_buffer_bbox(60.0, 0.0, 111320.0)
        assert (east - west) / 2 == pytest.approx(2.0, abs=0.05)  # cos(60)=0.5 -> dlon ~2 deg
        assert (north - south) / 2 == pytest.approx(1.0, abs=0.05)


class TestParseOverpassElements:
    def test_splits_nodes_and_ways(self):
        elements = [
            {"type": "node", "id": 1, "lat": 42.0, "lon": -71.0},
            {"type": "node", "id": 2, "lat": 42.1, "lon": -71.1},
            {"type": "way", "id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}},
        ]
        nodes, ways = query.parse_overpass_elements(elements)
        assert nodes == {1: (-71.0, 42.0), 2: (-71.1, 42.1)}
        assert ways == [{"id": 100, "nodes": [1, 2], "tags": {"highway": "residential"}}]

    def test_way_without_tags_gets_empty_dict(self):
        _, ways = query.parse_overpass_elements([{"type": "way", "id": 1, "nodes": [1]}])
        assert ways[0]["tags"] == {}


class TestBuildOverpassQuery:
    def test_bbox_drive_has_highway_filter_and_bbox(self):
        q = query.build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1), network_type="roads")
        assert "out:json" in q
        assert "highway" in q
        assert "residential" in q  # part of the drive allow-list regex
        assert "42.0,-71.1,42.1,-71.0" in q  # overpass order: south,west,north,east

    def test_every_network_type_has_a_class_regex(self):
        q = query.build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1), network_type=["roads", "paths"])
        assert '["highway"~"^(' in q and '["highway"]' not in q

    def test_polygon_uses_poly_filter(self):
        q = query.build_overpass_query(polygon=[(42.0, -71.1), (42.1, -71.1), (42.1, -71.0)], network_type="roads")
        assert "poly:" in q

    def test_requires_bbox_or_polygon(self):
        with pytest.raises(ValueError):
            query.build_overpass_query(network_type="roads")


class TestFetchOsm:
    def test_returns_elements(self):
        payload = {"elements": [{"type": "node", "id": 1, "lat": 1.0, "lon": 2.0}]}
        sess = _FakeSession([_FakeResponse(200, payload)])
        assert query.fetch_osm("[out:json];", session=sess, sleep=_NO_SLEEP) == payload["elements"]

    def test_retries_on_429_then_succeeds(self):
        sess = _FakeSession([_FakeResponse(429, {}), _FakeResponse(200, {"elements": []})])
        assert query.fetch_osm("q", session=sess, sleep=_NO_SLEEP) == []
        assert len(sess.calls) == 2

    def test_gives_up_after_retries(self):
        sess = _FakeSession([_FakeResponse(429, {})] * 5)
        with pytest.raises(RuntimeError):
            query.fetch_osm("q", session=sess, retries=2, sleep=_NO_SLEEP)


class TestGeocodeArea:
    def test_parses_bbox_and_polygon(self):
        payload = [
            {
                "boundingbox": ["42.0", "42.1", "-71.1", "-71.0"],  # south, north, west, east
                "geojson": {
                    "type": "Polygon",
                    "coordinates": [[[-71.1, 42.0], [-71.0, 42.0], [-71.0, 42.1], [-71.1, 42.0]]],
                },
            }
        ]
        sess = _FakeSession([_FakeResponse(200, payload)])
        result = query.geocode_area("Anytown", session=sess, sleep=_NO_SLEEP)
        assert result["bbox"] == pytest.approx((-71.1, 42.0, -71.0, 42.1))
        assert result["polygon"][0] == (42.0, -71.1)  # (lat, lon)

    def test_no_result_raises(self):
        sess = _FakeSession([_FakeResponse(200, [])])
        with pytest.raises(LookupError):
            query.geocode_area("Nowhereville", session=sess, sleep=_NO_SLEEP)

    def test_result_without_boundingbox_raises_lookuperror(self):
        # Some Nominatim entries omit boundingbox; treat that as "not geocodable"
        # rather than letting a bare KeyError escape.
        sess = _FakeSession([_FakeResponse(200, [{"display_name": "Anytown"}])])
        with pytest.raises(LookupError, match=r"could not geocode"):
            query.geocode_area("Anytown", session=sess, sleep=_NO_SLEEP)


class TestResolveArea:
    def test_four_tuple_is_bbox(self):
        r = query.resolve_area((-71.1, 42.0, -71.0, 42.1))
        assert r["bbox"] == (-71.1, 42.0, -71.0, 42.1)
        assert r["polygon"] is None

    def test_two_tuple_is_point_with_buffer(self):
        r = query.resolve_area((42.0, -71.0), buffer_m=111320.0)  # (lat, lon)
        west, south, east, north = r["bbox"]
        assert south < 42.0 < north
        assert west < -71.0 < east

    def test_string_geocodes(self):
        payload = [
            {
                "boundingbox": ["42.0", "42.1", "-71.1", "-71.0"],
                "geojson": {"type": "Polygon", "coordinates": [[[-71.1, 42.0], [-71.0, 42.1], [-71.1, 42.0]]]},
            }
        ]
        sess = _FakeSession([_FakeResponse(200, payload)])
        r = query.resolve_area("Anytown", session=sess, sleep=_NO_SLEEP)
        assert r["polygon"] is not None

    def test_bad_area_raises(self):
        with pytest.raises(ValueError):
            query.resolve_area((1.0, 2.0, 3.0))  # length-3 is neither point nor bbox


class TestFetchNetworkElements:
    def test_bbox_input_skips_geocode(self):
        payload = {
            "elements": [
                {"type": "node", "id": 1, "lat": 1.0, "lon": 2.0},
                {"type": "node", "id": 2, "lat": 1.1, "lon": 2.1},
                {"type": "way", "id": 9, "nodes": [1, 2], "tags": {"highway": "residential"}},
            ]
        }
        sess = _FakeSession([_FakeResponse(200, payload)])
        nodes, ways = query.fetch_network_elements((2.0, 1.0, 2.1, 1.1), session=sess, sleep=_NO_SLEEP)
        assert set(nodes) == {1, 2}
        assert ways[0]["id"] == 9
        assert all(call[0] == "POST" for call in sess.calls)  # no geocode GET


class TestCountQuery:
    def test_count_query_counts_ways_without_recursing(self):
        q = query.build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1), network_type="roads", out="count")
        assert q.endswith("(42.0,-71.1,42.1,-71.0);out count;")
        assert "(._;>;)" not in q

    def test_body_is_still_the_default(self):
        assert query.build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1)).endswith("(._;>;);out body;")


_DURHAM = {
    "display_name": "Durham, Durham County, North Carolina, United States",
    "type": "administrative",
    "boundingbox": ["35.86", "36.14", "-79.01", "-78.75"],
    "geojson": {"type": "Polygon", "coordinates": [[[-79.0, 35.9], [-78.8, 35.9], [-78.8, 36.1], [-79.0, 35.9]]]},
}
_DURHAM_ST = {"display_name": "Durham Street", "type": "residential", "boundingbox": ["1", "2", "3", "4"]}


class TestGeocodeCandidates:
    def test_returns_every_hit_with_bbox_and_outline(self):
        session = _FakeSession([_FakeResponse(200, [_DURHAM, _DURHAM_ST, {"display_name": "no bbox"}])])
        got = query.geocode_candidates("Durham", session=session, sleep=_NO_SLEEP)
        assert [c["display_name"] for c in got] == [_DURHAM["display_name"], "Durham Street"]
        assert got[0]["bbox"] == (-79.01, 35.86, -78.75, 36.14)
        assert got[0]["polygon"][0] == (35.9, -79.0)  # (lat, lon), like geocode_area
        assert got[1]["polygon"] is None and got[1]["type"] == "residential"

    def test_sends_limit_and_simplification(self):
        session = _FakeSession([_FakeResponse(200, [])])
        assert query.geocode_candidates("nowhere", limit=3, session=session, sleep=_NO_SLEEP) == []
        _method, _url, params = session.calls[0]
        assert params["limit"] == 3 and params["polygon_threshold"] == 0.001 and params["polygon_geojson"] == 1
