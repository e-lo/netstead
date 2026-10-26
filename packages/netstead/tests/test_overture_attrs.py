"""Tests for netstead.overture.attrs — transforms, direction, mapping, filters."""

from __future__ import annotations

import pytest
from netstead.overture import attrs


class TestResolvePath:
    def test_nested_lookup(self):
        assert attrs.resolve_path({"names": {"primary": "Main St"}}, "names.primary") == "Main St"

    def test_missing_intermediate_is_none(self):
        assert attrs.resolve_path({"names": None}, "names.primary") is None
        assert attrs.resolve_path({}, "a.b.c") is None


class TestMaxSpeedMph:
    def test_mph_passthrough(self):
        assert attrs.max_speed_mph([{"max_speed": {"value": 30, "unit": "mph"}}]) == 30.0

    def test_kmh_converted(self):
        assert attrs.max_speed_mph([{"max_speed": {"value": 100, "unit": "km/h"}}]) == 62.1

    def test_prefers_unscoped_rule_over_scoped(self):
        rules = [
            {"max_speed": {"value": 20, "unit": "mph"}, "between": [0.0, 0.5]},
            {"max_speed": {"value": 45, "unit": "mph"}},
        ]
        assert attrs.max_speed_mph(rules) == 45.0

    def test_none_and_empty(self):
        assert attrs.max_speed_mph(None) is None
        assert attrs.max_speed_mph([]) is None


class TestPrimaryRef:
    def test_first_ref(self):
        assert attrs.primary_ref([{"ref": "US 1"}, {"ref": "NC 50"}]) == "US 1"

    def test_skips_entries_without_ref(self):
        assert attrs.primary_ref([{"network": "US"}, {"ref": "I-40"}]) == "I-40"

    def test_none_and_empty(self):
        assert attrs.primary_ref(None) is None
        assert attrs.primary_ref([]) is None


class TestLaneCount:
    def test_plain_int(self):
        assert attrs.lane_count(2) == 2

    def test_numeric_string(self):
        assert attrs.lane_count("3") == 3

    def test_scoped_array_is_unknown(self):
        assert attrs.lane_count([{"direction": "forward"}]) is None

    def test_none_and_bool(self):
        assert attrs.lane_count(None) is None
        assert attrs.lane_count(True) is None


class TestOvertureDirection:
    def test_default_both(self):
        assert attrs.overture_direction({}) == "both"

    def test_backward_denied_is_forward(self):
        seg = {"access_restrictions": [{"access_type": "denied", "when": {"heading": "backward"}}]}
        assert attrs.overture_direction(seg) == "forward"

    def test_forward_denied_is_backward(self):
        seg = {"access_restrictions": [{"access_type": "denied", "when": {"heading": "forward"}}]}
        assert attrs.overture_direction(seg) == "backward"

    def test_allowed_restriction_does_not_restrict(self):
        seg = {"access_restrictions": [{"access_type": "allowed", "when": {"heading": "backward"}}]}
        assert attrs.overture_direction(seg) == "both"

    def test_both_denied_falls_back_to_both(self):
        seg = {
            "access_restrictions": [
                {"access_type": "denied", "when": {"heading": "forward"}},
                {"access_type": "denied", "when": {"heading": "backward"}},
            ]
        }
        assert attrs.overture_direction(seg) == "both"


class TestApplyMapping:
    def test_core_fields(self):
        seg = {
            "class": "primary",
            "names": {"primary": "Broadway"},
            "speed_limits": [{"max_speed": {"value": 35, "unit": "mph"}}],
            "routes": [{"ref": "US 9"}],
        }
        out = attrs.apply_mapping(seg)
        assert out["facility_type"] == "primary"
        assert out["name"] == "Broadway"
        assert out["free_speed"] == 35.0
        assert out["ref"] == "US 9"
        assert out["lanes"] is None  # absent -> unknown

    def test_absent_fields_present_as_none(self):
        out = attrs.apply_mapping({"class": "residential"})
        assert out["name"] is None
        assert out["free_speed"] is None

    def test_extra_tags_carried_by_last_path_segment(self):
        out = attrs.apply_mapping({"class": "service", "subclass": "parking_aisle"}, extra_tags=["subclass"])
        assert out["subclass"] == "parking_aisle"


class TestNetworkFilters:
    def test_roads_allow_residential_not_footway(self):
        assert attrs.accepts_class("roads", "residential") is True
        assert attrs.accepts_class("roads", "footway") is False

    def test_paths_allow_footway(self):
        assert attrs.accepts_class("paths", "footway") is True

    def test_no_type_accepts_arbitrary_values(self):
        assert attrs.accepts_class(["roads", "paths"], "whatever") is False

    def test_unknown_network_type_raises(self):
        with pytest.raises(ValueError, match="unknown network_type"):
            attrs.allowed_classes("fly")
