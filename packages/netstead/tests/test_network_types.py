"""Several network types in one build: a way or segment is kept if any chosen type allows it (no network)."""

from pathlib import Path

import pytest
from netstead.network_types import allowed_union, as_types
from netstead.osm import tags
from netstead.osm.local import read_osm_file
from netstead.osm.query import build_overpass_query
from netstead.overture import attrs

OSM_XML = Path(__file__).resolve().parent / "fixtures" / "osm" / "tiny.osm"
FILTERS = {"a": ["x"], "b": ["y", "x"], "all": []}


def test_as_types_dedupes_in_order_and_wraps_a_bare_string():
    assert as_types("drive") == ("drive",)
    assert as_types(["bike", "drive", "bike"]) == ("bike", "drive")


def test_no_type_is_an_error():
    with pytest.raises(ValueError, match="at least one"):
        as_types([])


def test_union_of_allow_lists():
    assert allowed_union(FILTERS, ["a", "b"]) == {"x", "y"}


def test_all_makes_the_union_unrestricted():
    assert allowed_union(FILTERS, ["a", "all"]) == frozenset()


def test_unknown_member_names_itself():
    with pytest.raises(ValueError, match="unknown network_type 'boat'"):
        allowed_union(FILTERS, ["a", "boat"])


def test_osm_highways_union_covers_both_modes():
    both = tags.allowed_highways(["drive", "walk"])
    assert tags.allowed_highways("drive") < both and tags.allowed_highways("walk") < both
    assert tags.accepts_highway(["drive", "walk"], "footway") and tags.accepts_highway(["drive", "bike"], "cycleway")
    assert not tags.accepts_highway(["drive", "bike"], "steps")


def test_overture_classes_union_covers_both_modes():
    both = attrs.allowed_classes(["drive", "walk"])
    assert attrs.allowed_classes("drive") < both and attrs.allowed_classes("walk") < both
    assert attrs.accepts_class(["drive", "walk"], "footway") and not attrs.accepts_class("drive", "footway")


def test_overpass_query_filters_on_the_union():
    q = build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1), network_type=["drive", "walk"])
    assert "motorway" in q and "footway" in q
    assert "footway" not in build_overpass_query(bbox=(-71.1, 42.0, -71.0, 42.1), network_type="drive")


def test_local_osm_file_keeps_ways_from_either_mode():
    assert [w["id"] for w in read_osm_file(OSM_XML, network_type="drive")[1]] == [100]
    assert [w["id"] for w in read_osm_file(OSM_XML, network_type=["drive", "walk"])[1]] == [100, 101]
