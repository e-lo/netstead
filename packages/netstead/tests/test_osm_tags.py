"""Tests for netstead.osm.tags — OSM tag transforms, field mapping, and network filters."""

from __future__ import annotations

import pytest
from netstead.osm import tags


class TestParseInt:
    def test_plain_integer(self):
        assert tags.parse_int("2") == 2

    def test_none_passthrough(self):
        assert tags.parse_int(None) is None

    def test_ambiguous_semicolon_list_is_none(self):
        assert tags.parse_int("3;2") is None

    def test_non_numeric_is_none(self):
        assert tags.parse_int("two") is None


class TestParseSpeed:
    def test_mph_suffix_kept_as_value(self):
        assert tags.parse_speed("30 mph") == 30.0

    def test_bare_number_is_kmh_converted_to_mph(self):
        assert tags.parse_speed("50") == pytest.approx(50 / 1.60934, abs=0.1)

    def test_kmh_suffix_converted_to_mph(self):
        assert tags.parse_speed("50 km/h") == pytest.approx(50 / 1.60934, abs=0.1)

    def test_none_passthrough(self):
        assert tags.parse_speed(None) is None

    def test_non_numeric_is_none(self):
        assert tags.parse_speed("walk") is None


class TestDirect:
    def test_passthrough_string(self):
        assert tags.direct("Main St") == "Main St"

    def test_none_passthrough(self):
        assert tags.direct(None) is None


class TestOnewayDirection:
    def test_yes_is_forward(self):
        assert tags.oneway_direction({"oneway": "yes"}) == "forward"

    def test_minus_one_is_backward(self):
        assert tags.oneway_direction({"oneway": "-1"}) == "backward"

    def test_no_is_both(self):
        assert tags.oneway_direction({"oneway": "no"}) == "both"

    def test_absent_is_both(self):
        assert tags.oneway_direction({}) == "both"

    def test_roundabout_implies_forward(self):
        assert tags.oneway_direction({"junction": "roundabout"}) == "forward"

    def test_motorway_implies_forward(self):
        assert tags.oneway_direction({"highway": "motorway"}) == "forward"

    def test_explicit_no_overrides_implied(self):
        assert tags.oneway_direction({"highway": "motorway", "oneway": "no"}) == "both"


class TestAllowedHighways:
    def test_roads_include_motorway_excludes_footway(self):
        allowed = tags.allowed_highways("roads")
        assert "motorway" in allowed
        assert "footway" not in allowed

    def test_paths_include_footway(self):
        assert "footway" in tags.allowed_highways("paths")

    def test_no_type_accepts_arbitrary_values(self):
        assert tags.accepts_highway(["roads", "paths"], "anything_at_all") is False

    def test_drive_accepts_helper(self):
        assert tags.accepts_highway("roads", "residential") is True
        assert tags.accepts_highway("roads", "footway") is False

    def test_unknown_network_type_raises(self):
        with pytest.raises(ValueError):
            tags.allowed_highways("hovercraft")


class TestApplyMapping:
    def test_maps_core_fields_with_transforms(self):
        osm = {
            "name": "Main St",
            "lanes": "2",
            "maxspeed": "30 mph",
            "highway": "residential",
            "surface": "asphalt",
        }
        result = tags.apply_mapping(osm)
        assert result == {
            "name": "Main St",
            "lanes": 2,
            "free_speed": 30.0,
            "facility_type": "residential",
        }

    def test_missing_tags_become_none_with_uniform_keys(self):
        result = tags.apply_mapping({"highway": "primary"})
        assert result == {
            "name": None,
            "lanes": None,
            "free_speed": None,
            "facility_type": "primary",
        }

    def test_extra_tags_carried_verbatim_when_present(self):
        result = tags.apply_mapping({"highway": "primary", "surface": "asphalt"}, extra_tags=["surface"])
        assert result["surface"] == "asphalt"

    def test_extra_tags_present_as_none_when_absent(self):
        result = tags.apply_mapping({"highway": "primary"}, extra_tags=["surface", "bridge"])
        assert result["surface"] is None
        assert result["bridge"] is None


class TestMappingFilesLoad:
    def test_field_mappings_have_expected_keys(self):
        mapping = tags.load_field_mappings()
        assert {"name", "lanes", "free_speed", "facility_type"} <= set(mapping)
        # each entry names an osm_tag + a registered transform
        for spec in mapping.values():
            assert "osm_tag" in spec
            assert spec["transform"] in tags.TRANSFORMS

    def test_network_filters_cover_all_network_types(self):
        filters = tags.load_network_filters()
        assert set(filters) == {"roads", "paths"}
