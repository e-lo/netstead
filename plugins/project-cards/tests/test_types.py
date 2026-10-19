"""The change model: ProjectCard change types, card-shaped values, selections."""

import json
import typing

import pytest
from netstead_project_cards.types import (
    CHANGE_TYPES,
    EMPTY_VALUE,
    ROADWAY_TYPES,
    TRANSIT_TYPES,
    NetworkChange,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
    incomplete,
    parse_change,
)
from pydantic import ValidationError

QUERY = {"links": {"name": ["Benton Street"], "ignore_missing": True}}


def test_the_change_types_are_projectcard_s_and_its_schema_s():
    from projectcard.validate import PROJECTCARD_SCHEMA

    schema = json.loads(PROJECTCARD_SCHEMA.read_text(encoding="utf-8"))
    assert set(CHANGE_TYPES) == set(schema["$defs"]["change"]["properties"]) - {"pycode"}
    assert set(CHANGE_TYPES) == ROADWAY_TYPES | TRANSIT_TYPES and not ROADWAY_TYPES & TRANSIT_TYPES


def test_the_union_has_one_model_per_change_type():
    members = typing.get_args(typing.get_args(NetworkChange)[0])
    assert sorted(m.model_fields["type"].default for m in members) == sorted(CHANGE_TYPES)


def test_parse_picks_the_type():
    c = parse_change(
        {"type": "roadway_property_change", "facility": {"ids": [1]}, "property_changes": {"lanes": {"set": 2}}}
    )
    assert isinstance(c, RoadwayPropertyChange) and c.facility.table == "link"
    assert isinstance(parse_change({"type": "roadway_deletion", "links": [1]}), RoadwayDeletion)
    assert isinstance(parse_change({"type": "transit_property_change"}), TransitPropertyChange)


@pytest.mark.parametrize("bad", [{}, {"set": 1, "change": 1}, {"existing": 1}, {"set": None}])
def test_a_property_change_is_set_or_change(bad):
    with pytest.raises(ValidationError):
        PropertyChange(**bad)


def test_an_empty_value_is_refused_with_the_card_rule():
    with pytest.raises(ValidationError, match=EMPTY_VALUE):
        PropertyChange(set="  ")
    with pytest.raises(ValidationError, match=EMPTY_VALUE):
        PropertyChange(existing=1)


def test_a_list_value_is_refused():
    with pytest.raises(ValidationError, match="to a list"):
        PropertyChange(set=[1, 2])


def test_booleans_become_one_and_zero():
    assert PropertyChange(set=True).set == 1 and type(PropertyChange(set=True).set) is int
    assert PropertyChange(existing=False, set=1).existing == 0


def test_an_integer_delta_stays_an_integer():
    assert type(PropertyChange(change=1).change) is int and type(PropertyChange(change="2").change) is int
    assert PropertyChange(change=0.5).change == 0.5 and PropertyChange(change="-1.5").change == -1.5


def test_a_transit_note_is_bounded_like_a_roadway_note():
    with pytest.raises(ValidationError, match="500"):
        TransitPropertyChange(note="x" * 501)


def test_a_delta_is_a_number():
    assert PropertyChange(change="2").change == 2.0
    with pytest.raises(ValidationError, match="delta"):
        PropertyChange(change="fast")
    with pytest.raises(ValidationError, match="delta"):
        PropertyChange(change=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"ids": [1], "query": QUERY, "resolved": [1]},
        {"ids": []},
        {"query": QUERY},  # a query must record what it resolved to
        {"query": QUERY, "resolved": []},
        {"table": "node", "query": QUERY, "resolved": [1]},
        {"ids": [1], "resolved": [1]},  # resolved is a query's provenance
        {"query": {"nodes": {"name": ["x"]}}, "resolved": [1]},  # a query selects links
        {"query": {"links": ["Benton Street"]}, "resolved": [1]},
    ],
)
def test_a_selection_is_ids_or_a_resolved_query(kwargs):
    with pytest.raises(ValidationError):
        Selection(**kwargs)


def test_target_ids_and_the_pick_default():
    assert Selection(ids=[3, 4]).target_ids() == [3, 4] and Selection(ids=[3]).ignore_missing is False
    assert Selection(query=QUERY, resolved=[7]).target_ids() == [7]


def test_incomplete_names_what_is_missing():
    assert "facility" in incomplete(RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)}))
    assert "links" in incomplete(RoadwayDeletion())
    assert incomplete(RoadwayDeletion(links=[1])) is None
    assert incomplete(TransitPropertyChange()) is None


def test_an_addition_needs_its_keys():
    with pytest.raises(ValidationError):
        RoadwayAddition()
    with pytest.raises(ValidationError, match="link_id"):
        RoadwayAddition(links=[{"from_node_id": 1, "to_node_id": 2}])


@pytest.mark.parametrize(
    ("record", "kind"),
    [
        ({"link_id": 9, "from_node_id": 1, "to_node_id": 2, "name": {"a": 1}}, "links"),
        ({"link_id": 9, "from_node_id": 1, "to_node_id": 2, "lanes": [1, 2]}, "links"),
        ({"node_id": 9, "x_coord": 0, "y_coord": 0, "ctrl_type": {"a": 1}}, "nodes"),
    ],
)
def test_an_added_record_holds_card_scalars(record, kind):
    with pytest.raises(ValidationError, match="cannot set a property to a list"):
        RoadwayAddition(**{kind: [record]})
