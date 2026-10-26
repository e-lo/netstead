"""Network types (roads / paths): allowed sets, union, legacy names and the filters built on them (no network)."""

import textwrap
from pathlib import Path

import pytest
from netstead.network_types import allowed_union, as_types, normalize_types
from netstead.osm import tags
from netstead.osm.local import read_osm_file
from netstead.osm.query import build_overpass_query
from netstead.overture import attrs

OSM_XML = Path(__file__).resolve().parent / "fixtures" / "osm" / "tiny.osm"
FILTERS = {"a": ["x"], "b": ["y", "x"]}
BBOX = (-71.1, 42.0, -71.0, 42.1)

ROADS = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential", "living_street", "service", "road", "busway",
}  # fmt: skip
PATHS = {"bridleway", "footway", "path", "pedestrian", "steps", "track", "cycleway"}
NEVER = {"proposed", "construction", "corridor", "raceway", "elevator", "platform", "abandoned", "disused"}


def test_as_types_dedupes_in_order_and_wraps_a_bare_string():
    assert as_types("roads") == ("roads",)
    assert as_types(["paths", "roads", "paths"]) == ("paths", "roads")


def test_no_type_is_an_error():
    with pytest.raises(ValueError, match="at least one"):
        as_types([])


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [("drive", ["roads"]), ("walk", ["roads", "paths"]), ("bike", ["roads", "paths"]), ("all", ["roads", "paths"])],
)
def test_legacy_names_map_onto_roads_and_paths(legacy, expected):
    assert normalize_types(legacy) == expected
    assert normalize_types([legacy, "paths"]) == (expected if "paths" in expected else [*expected, "paths"])


def test_unknown_name_lists_the_valid_ones():
    with pytest.raises(ValueError, match=r"unknown network type 'boat'.*roads.*paths"):
        normalize_types(["roads", "boat"])


def test_union_of_allow_lists():
    assert allowed_union(FILTERS, ["a", "b"]) == {"x", "y"}


def test_unknown_member_names_itself():
    with pytest.raises(ValueError, match="unknown network_type 'boat'"):
        allowed_union(FILTERS, ["a", "boat"])


def test_osm_highway_sets():
    assert tags.allowed_highways("roads") == ROADS
    assert tags.allowed_highways("paths") == PATHS
    assert "busway" in tags.allowed_highways("roads") and "cycleway" in tags.allowed_highways("paths")
    assert not NEVER & (tags.allowed_highways(["roads", "paths"]))
    assert set(tags.load_network_filters()) == {"roads", "paths"}


def test_osm_highways_union_covers_both_types():
    both = tags.allowed_highways(["roads", "paths"])
    assert both == ROADS | PATHS
    assert tags.accepts_highway(["roads", "paths"], "footway") and not tags.accepts_highway("roads", "footway")
    assert not tags.accepts_highway(["roads", "paths"], "proposed")


def test_legacy_names_filter_like_their_replacements():
    assert tags.allowed_highways("drive") == ROADS
    for legacy in ("walk", "bike", "all"):
        assert tags.allowed_highways(legacy) == ROADS | PATHS
        assert attrs.allowed_classes(legacy) == attrs.allowed_classes(["roads", "paths"])


def test_overture_class_sets():
    roads, paths = attrs.allowed_classes("roads"), attrs.allowed_classes("paths")
    assert {"motorway", "residential", "service"} <= roads and not {"footway", "cycleway"} & roads
    assert {"footway", "sidewalk", "crosswalk", "steps", "cycleway", "bridleway", "pedestrian"} <= paths
    assert not (roads | paths) & (NEVER | {"unknown"})
    assert attrs.allowed_classes(["roads", "paths"]) == roads | paths
    assert attrs.accepts_class(["roads", "paths"], "footway") and not attrs.accepts_class("roads", "footway")
    assert set(attrs.load_network_filters()) == {"roads", "paths"}


def test_overpass_query_filters_on_the_union():
    q = build_overpass_query(bbox=BBOX, network_type=["roads", "paths"])
    regex = q.split('["highway"~"^(')[1].split(")$")[0].split("|")
    assert set(regex) == ROADS | PATHS
    assert not any(bad in q for bad in ("proposed", "construction", "corridor"))
    assert "footway" not in build_overpass_query(bbox=BBOX, network_type="roads")
    assert "busway" in build_overpass_query(bbox=BBOX, network_type="roads")


def test_overpass_query_for_a_legacy_name_matches_its_replacement():
    assert build_overpass_query(bbox=BBOX, network_type="drive") == build_overpass_query(
        bbox=BBOX, network_type="roads"
    )
    assert build_overpass_query(bbox=BBOX, network_type="all") == build_overpass_query(
        bbox=BBOX, network_type=["roads", "paths"]
    )


def test_local_osm_file_keeps_ways_from_either_type():
    assert [w["id"] for w in read_osm_file(OSM_XML, network_type="roads")[1]] == [100]
    assert [w["id"] for w in read_osm_file(OSM_XML, network_type=["roads", "paths"])[1]] == [100, 101]
    assert [w["id"] for w in read_osm_file(OSM_XML, network_type="paths")[1]] == [101]


def test_local_osm_file_keeps_busway_and_footway_and_drops_proposed(tmp_path):
    osm = tmp_path / "mixed.osm"
    osm.write_text(
        textwrap.dedent(
            """\
            <osm version="0.6">
              <node id="1" lat="42.0" lon="-71.0"/><node id="2" lat="42.001" lon="-71.0"/>
              <node id="3" lat="42.002" lon="-71.0"/>
              <way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="busway"/></way>
              <way id="11"><nd ref="2"/><nd ref="3"/><tag k="highway" v="footway"/></way>
              <way id="12"><nd ref="1"/><nd ref="3"/><tag k="highway" v="proposed"/></way>
              <way id="13"><nd ref="1"/><nd ref="3"/><tag k="highway" v="construction"/></way>
              <way id="14"><nd ref="1"/><nd ref="3"/><tag k="highway" v="corridor"/></way>
            </osm>
            """
        )
    )
    assert [w["id"] for w in read_osm_file(osm, network_type=["roads", "paths"])[1]] == [10, 11]
    assert [w["id"] for w in read_osm_file(osm, network_type="all")[1]] == [10, 11]
