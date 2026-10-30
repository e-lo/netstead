"""Tests for netstead.select.streets — the street-name index and the grammar-first fast path."""

import pandas as pd
import pytest
from netstead.select.intent import Facility, SelectionIntent
from netstead.select.resolve import resolve_frames
from netstead.select.streets import StreetIndex, grammar_first, spelling_key


@pytest.fixture
def net():
    """Glenwood Avenue (1-2-3-4) crossed by West/East Peace Street at 2 and Wade Avenue at 4."""
    rows = [
        (1, 1, 2, "Glenwood Avenue", None, "primary"),
        (2, 2, 3, "Glenwood Avenue", None, "primary"),
        (3, 3, 4, "Glenwood Avenue", None, "primary"),
        (4, 2, 10, "West Peace Street", None, "secondary"),
        (5, 11, 2, "East Peace Street", None, "secondary"),
        (6, 4, 12, "Wade Avenue", None, "secondary"),
        (7, 20, 21, "Six Forks Road", None, "secondary"),
        (8, 21, 22, "East Six Forks Road", None, "secondary"),
        (9, 30, 31, "St Marys Street", None, "residential"),
        (10, 40, 41, None, "I 40;US 64", "motorway"),
        (11, 41, 4, None, None, "motorway_link"),
    ]
    links = pd.DataFrame(rows, columns=["link_id", "from_node_id", "to_node_id", "name", "ref", "facility_type"])
    ids = sorted(set(links["from_node_id"]) | set(links["to_node_id"]))
    nodes = pd.DataFrame({"node_id": ids, "x_coord": [-78.6 + 0.001 * i for i in ids], "y_coord": [35.8] * len(ids)})
    return links, nodes


@pytest.fixture
def index(net):
    return StreetIndex.build(net[0])


@pytest.mark.parametrize(
    ("text", "names", "how"),
    [
        ("glenwood avenue", ("Glenwood Avenue",), "exact"),
        ("Glenwood Ave", ("Glenwood Avenue",), "spelling"),
        ("W. Peace St", ("West Peace Street",), "spelling"),
        ("Peace Street", ("East Peace Street", "West Peace Street"), "directional"),
        ("Wade Ave North", ("Wade Avenue",), "directional"),
        ("Six Forks Road", ("Six Forks Road",), "exact"),  # exact wins: East Six Forks Road is another street
        ("W Six Forks Rd", ("Six Forks Road",), "directional"),  # never the other side's East Six Forks Road
        ("St Marys St", ("St Marys Street",), "spelling"),  # the leading "St" is not a street type
        ("Glenwod Avenue", ("Glenwood Avenue",), "fuzzy"),
        ("Glenwod Ave", ("Glenwood Avenue",), "fuzzy"),
        ("Peece Street", ("East Peace Street", "West Peace Street"), "fuzzy"),
        ("W Peece St", ("West Peace Street",), "fuzzy"),  # a misspelling still keeps to its side
        ("Glenwood", ("Glenwood Avenue",), "bare"),
        ("Peace", ("East Peace Street", "West Peace Street"), "bare"),
        ("W Peace", ("West Peace Street",), "bare"),
        ("Six Forks", ("East Six Forks Road", "Six Forks Road"), "bare"),
        ("St Marys", ("St Marys Street",), "bare"),
        ("Glenwood Road", (), None),  # a type the request does name has to agree
        ("Wde", (), None),  # too short to guess at
        ("Oak Road", (), None),
        ("", (), None),
    ],
)
def test_lookup_name(index, text, names, how):
    assert index.lookup_name(text) == (names, how)


def test_spelling_key_expands_only_types_and_directions_at_the_ends():
    assert spelling_key("N Main St") == spelling_key("North Main Street")
    assert spelling_key("St Marys St") == "stmarysstreet"
    assert spelling_key("Main St N") == "mainstreetnorth"


def test_ref_rows_match_any_token(net, index):
    links = net[0]
    assert links.iloc[index.ref_rows("I-40")]["link_id"].tolist() == [10]
    assert links.iloc[index.ref_rows("US 64")]["link_id"].tolist() == [10]
    assert index.ref_rows("I 4").size == 0  # no substring collision with I 40


def test_resolve_matches_spelling_variants_and_says_so(net, index):
    links, nodes = net
    intent = SelectionIntent(facility=Facility(name="Glenwood Ave"), from_anchor="Peace St", to_anchor="Wade Ave")
    result = resolve_frames(intent, links, nodes, index=index)
    assert result.status == "resolved"
    assert result.link_ids == [2, 3] and result.node_path == [2, 3, 4]
    assert "matched 'Glenwood Ave' to 'Glenwood Avenue'" in result.diagnostics


def test_cross_streets_may_leave_out_their_type(net, index):
    links, nodes = net
    intent = SelectionIntent(facility=Facility(name="Glenwood Avenue"), from_anchor="Peace", to_anchor="Wade")
    result = resolve_frames(intent, links, nodes, index=index)
    assert result.status == "resolved" and result.link_ids == [2, 3]
    assert "matched 'Wade' to 'Wade Avenue'" in result.diagnostics


def test_resolve_builds_its_own_index_and_refuses_a_stale_one(net, index):
    links, nodes = net
    intent = SelectionIntent(facility=Facility(name="Wade Avenue"))
    assert resolve_frames(intent, links, nodes).link_ids == [6]
    with pytest.raises(ValueError, match="different links frame"):
        resolve_frames(intent, links.iloc[:-1], nodes, index=index)


def test_cross_streets(index):
    assert index.cross_streets("glenwood avenue") == ["East Peace Street", "Wade Avenue", "West Peace Street"]
    assert index.cross_streets("Oak Road") == []


def test_a_network_without_name_or_ref_columns(net):
    links = net[0].drop(columns=["name", "ref"])
    index = StreetIndex.build(links)
    assert index.lookup_name("Glenwood Avenue") == ((), None)
    assert not index.knows("I-40")


@pytest.mark.parametrize(
    "utterance",
    [
        "Glenwood Avenue",
        "Glenwood Ave northbound",
        "Glenwood Ave between Peace St and Wade Avenue",
        "Glenwood from Peace to Wade",
        "I-40 EB",
        "all links",
        "all secondary links",
        "links 1, 2",
    ],
)
def test_grammar_first_accepts_requests_whose_names_are_all_in_the_network(index, utterance):
    assert grammar_first(utterance, index) is not None


@pytest.mark.parametrize(
    "utterance",
    [
        "bike lanes on Glenwood Avenue",  # the grammar would read it all as one street name
        "Glenwood Avenue between Peace Street and Oak Road",  # an anchor the network doesn't have
        "Glenwood Avenue where there are 2 lanes",
        "all cycleway links",  # a facility_type that never occurs
        "???",  # the grammar can't read it at all
    ],
)
def test_grammar_first_leaves_the_rest_to_the_model(index, utterance):
    assert grammar_first(utterance, index) is None


def test_resolve_notes_a_close_spelling(net, index):
    links, nodes = net
    intent = SelectionIntent(facility=Facility(name="Glenwod Avenue"), from_anchor="Peece St", to_anchor="Wade Avenue")
    result = resolve_frames(intent, links, nodes, index=index)
    assert result.status == "resolved" and result.link_ids == [2, 3]
    assert "matched 'Glenwod Avenue' to 'Glenwood Avenue' (a close spelling)" in result.diagnostics
    assert "matched 'Peece St' to 'East Peace Street', 'West Peace Street' (a close spelling)" in result.diagnostics


def chain(gap_name, gap_type, gap_length, *, main_type="primary"):
    """Main Street 1-2, a gap link 2-3, Main Street 3-4; First Street meets it at 1, Second Street at 4."""
    rows = [
        (1, 1, 2, "Main Street", main_type, 100.0),
        (2, 2, 3, gap_name, gap_type, gap_length),
        (3, 3, 4, "Main Street", "primary", 100.0),
        (4, 1, 10, "First Street", "residential", 100.0),
        (5, 4, 11, "Second Street", "residential", 100.0),
    ]
    links = pd.DataFrame(rows, columns=["link_id", "from_node_id", "to_node_id", "name", "facility_type", "length"])
    ids = [1, 2, 3, 4, 10, 11]
    nodes = pd.DataFrame({"node_id": ids, "x_coord": [-78.6 + 0.001 * i for i in ids], "y_coord": [35.8] * 6})
    intent = SelectionIntent(
        facility=Facility(name="Main Street"), from_anchor="First Street", to_anchor="Second Street"
    )
    return resolve_frames(intent, links, nodes)


@pytest.mark.parametrize(
    ("gap_name", "gap_type", "gap_length"),
    [(None, "primary", 30.0), ("Cross Street", "secondary", 12.0)],  # a nameless roundabout; a median crossing
)
def test_a_gap_in_a_street_is_bridged(gap_name, gap_type, gap_length):
    result = chain(gap_name, gap_type, gap_length)
    assert result.status == "resolved" and result.link_ids == [1, 2, 3]
    assert "bridged gaps in the facility with 1 short or unnamed link(s)" in result.diagnostics


@pytest.mark.parametrize(
    ("gap_name", "gap_type", "gap_length"),
    [(None, "service", 30.0), ("Cross Street", "secondary", 40.0)],  # a parking aisle; a whole block of another street
)
def test_a_gap_is_not_bridged_by_service_roads_or_long_named_links(gap_name, gap_type, gap_length):
    result = chain(gap_name, gap_type, gap_length)
    assert result.status == "not_found" and "no path between resolved anchor nodes" in result.diagnostics


def test_a_partly_trunk_street_falls_back_to_at_grade_intersections():
    result = chain("Main Street", "primary", 100.0, main_type="trunk")
    assert result.status == "resolved" and result.link_ids == [1, 2, 3]
    assert "no interchange path; tried at-grade intersections" in result.diagnostics
