"""Id systems for selection fragments: GMNS / OSM / GERS ids, and the strict Segment and Link list forms."""

import pandas as pd
import pytest
from netstead.select.emit import segment_problem, to_fragment, to_projectcard, validate_fragment
from netstead.select.errors import SelectError
from netstead.select.idsystems import availability, map_ids
from netstead.select.intent import Facility, SelectionIntent
from netstead.select.result import AnchorMatch, SelectionResult

PINE = Facility(name="Pine Street")
LINKS = pd.DataFrame(
    {
        "link_id": [60, 61, 62, 63],
        "osm_way_id": [900, 900, 900, 901],
        "overture_segment_id": ["gers-a", "gers-a", "gers-b", "gers-c"],
    }
)
NODES = pd.DataFrame(
    {"node_id": [33, 19, 20], "osm_node_id": [7001, 7002, 7003], "overture_connector_id": ["c-33", "c-19", "c-20"]}
)


def _result(selected=(60, 61), *, facility=PINE, **intent_kw):
    intent = SelectionIntent(
        facility=facility, from_anchor="Orchard", to_anchor="Central", utterance="pine", **intent_kw
    )
    return SelectionResult(
        status="resolved",
        intent=intent,
        link_ids=list(selected),
        node_path=[33, 19],
        from_match=AnchorMatch("Orchard", 33, [], 1.0, "intersection", ""),
        to_match=AnchorMatch("Central", 19, [], 1.0, "intersection", ""),
        diagnostics=[],
    )


def _ids(system, result=None):
    result = result or _result()
    return map_ids(system, result.link_ids, 33, 19, LINKS, NODES)


def test_segment_is_the_facility_name_and_anchors_only():
    frag = to_fragment(_result(), form="segment")
    assert frag == {"links": {"name": ["Pine Street"]}, "from": {"node_id": 33}, "to": {"node_id": 19}}
    validate_fragment(frag)


def test_link_list_is_only_the_resolved_ids():
    frag = to_fragment(_result(), form="link_list")
    assert frag == {"links": {"link_id": [60, 61]}}
    validate_fragment(frag)


def test_segment_keeps_ref_modes_and_conditions():
    frag = to_fragment(
        _result(facility=Facility(ref="I 40"), modes=("drive",), conditions={"lanes": [4]}), form="segment"
    )
    assert frag["links"] == {"ref": ["I 40"], "modes": ["drive"], "lanes": [4]}


@pytest.mark.parametrize("kw", [{"select_all": True}, {"link_ids": [60]}])
def test_a_selection_with_no_named_facility_has_no_segment(kw):
    result = _result(facility=None, **kw)
    assert segment_problem(result) == "This selection has no named segment; use Link list"
    with pytest.raises(SelectError, match="no named segment"):
        to_fragment(result, form="segment")
    with pytest.raises(SelectError, match="no named segment"):
        to_projectcard(result, form="segment")
    assert to_fragment(result, form="link_list") == {"links": {"link_id": [60, 61]}}


def test_osm_ids_are_deduplicated_and_say_which_ways_are_shared():
    ids = _ids("osm")
    assert ids.link_ids == [900] and ids.shared == {900: 2}
    assert ids.captions() == ["2 links share OSM way 900", "OSM way 900 also covers 1 link outside this selection"]
    assert to_fragment(_result(), form="link_list", ids=ids) == {"links": {"osm_way_id": [900]}}
    segment = to_fragment(_result(), form="segment", ids=ids)
    assert segment == {"links": {"name": ["Pine Street"]}, "from": {"osm_node_id": 7001}, "to": {"osm_node_id": 7002}}
    validate_fragment(segment)
    validate_fragment(to_fragment(_result(), form="link_list", ids=ids))


def test_gers_ids_use_the_overture_columns():
    ids = _ids("gers")
    assert ids.link_ids == ["gers-a"] and ids.shared == {"gers-a": 2}
    link_list = to_fragment(_result(), form="link_list", ids=ids)
    assert link_list == {"links": {"overture_segment_id": ["gers-a"]}}
    validate_fragment(link_list)
    segment = to_fragment(_result(), form="segment", ids=ids)
    assert segment["from"] == {"overture_connector_id": "c-33"}
    validate_fragment(segment)


def test_gmns_needs_no_columns_and_has_no_captions():
    ids = map_ids("gmns", [60], 33, 19, LINKS[["link_id"]], NODES[["node_id"]])
    assert ids.link_ids == [60] and ids.captions() == []


def test_a_system_whose_columns_are_missing_is_unavailable_with_the_reason():
    plain_links, plain_nodes = LINKS[["link_id"]], NODES[["node_id"]]
    got = availability(plain_links, plain_nodes)
    assert got["gmns"] is None
    assert "osm_way_id" in got["osm"] and "overture_segment_id" in got["gers"]
    assert availability(LINKS, NODES) == {"gmns": None, "osm": None, "gers": None}
    with pytest.raises(SelectError, match="osm_way_id"):
        map_ids("osm", [60], 33, 19, plain_links, plain_nodes)


def test_a_link_or_node_with_no_id_in_the_system_is_refused():
    gaps = LINKS.assign(osm_way_id=[900, None, 900, 901])
    with pytest.raises(SelectError, match="1 selected links have no osm_way_id"):
        map_ids("osm", [60, 61], 33, 19, gaps, NODES)
    with pytest.raises(SelectError, match="node 33 has no osm_node_id"):
        map_ids("osm", [60], 33, 19, LINKS, NODES.assign(osm_node_id=[None, 7002, 7003]))
    with pytest.raises(SelectError, match="unknown id system"):
        map_ids("nope", [60], 33, 19, LINKS, NODES)


def test_projectcard_segment_and_link_list_use_their_own_keys():
    result = _result()
    assert to_projectcard(result, form="segment") == {
        "links": {"name": ["Pine Street"], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    assert to_projectcard(result, form="link_list") == {"links": {"model_link_id": [60, 61], "ignore_missing": True}}
    osm = _ids("osm")
    assert to_projectcard(result, form="link_list", ids=osm) == {
        "links": {"osm_link_id": ["900"], "ignore_missing": True}
    }
    assert to_projectcard(result, form="segment", ids=osm)["from"] == {"osm_node_id": "7001"}


@pytest.mark.parametrize("form", ["segment", "link_list"])
def test_projectcard_has_no_key_for_gers_ids(form):
    with pytest.raises(SelectError, match="no key for GERS"):
        to_projectcard(_result(), form=form, ids=_ids("gers"))


@pytest.mark.parametrize("form", ["segment", "link_list"])
@pytest.mark.parametrize("system", ["gmns", "osm"])
def test_every_projectcard_form_is_a_valid_facility(form, system):
    validate = pytest.importorskip("projectcard.validate")
    facility = to_projectcard(_result(), form=form, ids=None if system == "gmns" else _ids(system))
    change = {"facility": facility, "property_changes": {"lanes": {"set": 2}}}
    assert validate.validate_card({"project": "pine", "changes": [{"roadway_property_change": change}]})
