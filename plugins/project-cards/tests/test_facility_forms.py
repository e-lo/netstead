"""A card change's facility as a Segment or a Link list, in GMNS or OpenStreetMap ids (never mixed, never GERS)."""

from __future__ import annotations

import copy

import pytest
import yaml
from netstead.workbench.actions import ClearSelection, SaveSelection, Select
from netstead.workbench.errors import ActionError
from netstead_project_cards.actions import AddChange, CommitCard, ImportCard, PreviewChange
from netstead_project_cards.card import DraftCard, read_card_file
from netstead_project_cards.catalog import applicability
from netstead_project_cards.facility import facility_text, query_id_system, resolve_osm
from netstead_project_cards.lower import describe
from netstead_project_cards.types import PropertyChange, RoadwayDeletion, RoadwayPropertyChange, Selection
from netstead_project_cards.validate import card_problems
from pydantic import ValidationError

NET = "leavenworth"
PINE = "Pine Street between Orchard Street and Central Avenue"  # link 60: node 33 -> 19, OSM way 5846369
PINE_WAY = "5846369"  # OSM way 5846369 covers 11 more links than link 60
BENTON = "Benton Street"  # links 1, 5, 6, 8: one OSM way, 5836396

SEGMENT_GMNS = {"links": {"name": ["Pine Street"], "ignore_missing": True}, "from": {"model_node_id": 33}}
LANES = RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=4)})


def lanes_of(session, ids):
    links = session.network(NET).links_df()
    return links.loc[links.link_id.isin(ids), "lanes"].tolist()


def add(session, form=None, system="gmns", **extra):
    return session.dispatch(AddChange(use_selection=True, facility_form=form, id_system=system, change=LANES, **extra))


def facility(session):
    draft = session.state()["plugins"]["project_cards"]["drafts"][NET]
    return draft["changes"][-1]["describe"]


def card_facility(session, tmp_path, name="c.yml"):
    """Commit the draft, return (path, the first change's facility dict as written, the whole card dict)."""
    path = tmp_path / name
    session.dispatch(CommitCard(path=str(path), project="Forms"))
    card = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert card_problems(card) == []
    return path, card["changes"][0]["roadway_property_change"]["facility"], card


# ---------------------------------------------------------------- the Host API: old keys unchanged, strict keys added


def test_host_selection_keeps_its_old_keys_and_adds_the_strict_forms(session, host):
    session.dispatch(Select(utterance=PINE))
    forms = host.selection["projectcard"]
    assert forms["picked"] is False
    assert forms["resolved"] == {
        "links": {"model_link_id": [60], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    assert forms["query"]["links"] == {"name": ["Pine Street"], "ignore_missing": True}
    assert forms["segment"]["gmns"] == {
        "links": {"name": ["Pine Street"], "ignore_missing": True},
        "from": {"model_node_id": 33},
        "to": {"model_node_id": 19},
    }
    assert forms["segment"]["osm"]["from"].keys() == {"osm_node_id"}
    assert forms["link_list"]["gmns"] == {"links": {"model_link_id": [60], "ignore_missing": True}}
    assert forms["link_list"]["osm"] == {"links": {"osm_link_id": [PINE_WAY], "ignore_missing": True}}
    assert forms["segment_problem"] is None
    assert forms["id_systems"]["gmns"] is None and forms["id_systems"]["osm"] is None
    assert "GERS" in forms["id_systems"]["gers"] or "overture" in forms["id_systems"]["gers"]
    assert forms["id_captions"]["osm"] == [f"OSM way {PINE_WAY} also covers 11 links outside this selection"]


def test_a_picked_selection_has_no_segment(session, host):
    session.dispatch(Select(link_ids=[60]))
    forms = host.selection["projectcard"]
    assert forms["picked"] is True and forms["segment"] == {}
    assert "no named segment" in forms["segment_problem"]
    assert set(forms["link_list"]) == {"gmns", "osm"}


# ---------------------------------------------------------------- building each form x id system


@pytest.mark.parametrize(
    ("form", "system", "written"),
    [
        (
            "segment",
            "gmns",
            {
                "links": {"name": ["Pine Street"], "ignore_missing": True},
                "from": {"model_node_id": 33},
                "to": {"model_node_id": 19},
            },
        ),
        ("link_list", "gmns", {"links": {"model_link_id": [60], "ignore_missing": False}}),
    ],
)
def test_a_named_selection_in_each_form_in_gmns_ids(session, tmp_path, form, system, written):
    session.dispatch(Select(utterance=PINE))
    add(session, form, system)
    _, got, _ = card_facility(session, tmp_path)
    assert got == written
    assert "notes" not in got  # strict: nothing from the other form, no utterance


def test_a_named_selection_as_a_segment_in_osm_ids(session, tmp_path):
    session.dispatch(Select(utterance=PINE))
    result = add(session, "segment", "osm")
    assert lanes_of(session, [60]) == [4]  # applied to the links the segment resolved to
    _, got, card = card_facility(session, tmp_path)
    assert got["links"] == {"name": ["Pine Street"], "ignore_missing": True}
    assert got["from"].keys() == {"osm_node_id"} and got["to"].keys() == {"osm_node_id"}
    assert all(isinstance(v, str) for v in (got["from"]["osm_node_id"], got["to"]["osm_node_id"]))
    assert "netstead.resolved[0]: model_link_id=[60]" in card["notes"]  # provenance for a re-import
    assert result["change"]["facility"]["id_system"] == "osm"


def test_clicked_links_as_a_link_list_in_osm_ids_cover_every_link_of_the_way(session, tmp_path):
    session.dispatch(Select(link_ids=[60]))
    result = add(session, None, "osm")  # a pick defaults to a link list
    covered = result["change"]["facility"]["resolved"]
    assert len(covered) == 12 and 60 in covered
    assert any("also covers 11 links outside this selection" in w for w in result["warnings"])
    assert set(lanes_of(session, covered)) == {4}
    _, got, card = card_facility(session, tmp_path)
    assert got == {"links": {"osm_link_id": [PINE_WAY], "ignore_missing": False}}
    assert "netstead.resolved" not in (card.get("notes") or "")  # an OSM list is resolved again on import


def test_defaults_are_a_link_list_for_clicks_and_a_segment_for_named_selections(session):
    session.dispatch(Select(link_ids=[60]))
    assert add(session)["change"]["facility"]["ids"] == [60]
    session.dispatch(ClearSelection())
    session.dispatch(Select(utterance=BENTON))
    got = add(session)["change"]["facility"]
    assert got["query"]["links"]["name"] == ["Benton Street"] and got["id_system"] == "gmns"


def test_a_saved_selection_chooses_its_form_too(session, tmp_path):
    session.dispatch(Select(utterance=PINE))
    session.dispatch(SaveSelection(name="pine"))
    session.dispatch(ClearSelection())
    session.dispatch(AddChange(saved_selection="pine", facility_form="link_list", id_system="osm", change=LANES))
    assert card_facility(session, tmp_path)[1]["links"]["osm_link_id"] == [PINE_WAY]


# ---------------------------------------------------------------- refusals


def test_segment_is_refused_for_a_selection_with_no_named_segment(session):
    session.dispatch(Select(link_ids=[60]))
    with pytest.raises(ActionError, match="no named segment; use Link list"):
        add(session, "segment")


@pytest.mark.parametrize("form", [None, "link_list", "segment"])
def test_gers_ids_are_refused_with_the_reason(session, form):
    session.dispatch(Select(utterance=PINE))
    with pytest.raises(ActionError, match="ProjectCard has no key for GERS ids"):
        add(session, form, "gers")


def test_the_form_and_id_system_need_a_selection():
    with pytest.raises(ValidationError, match="use a selection"):
        AddChange(change=LANES, facility_form="segment")


def test_a_deletion_takes_gmns_link_ids_only(session):
    session.dispatch(Select(link_ids=[60]))
    with pytest.raises(ActionError, match="GMNS link_id"):
        session.dispatch(AddChange(use_selection=True, id_system="osm", change=RoadwayDeletion()))


def test_the_form_route_offers_the_choices_and_reads_the_form_for_one(session):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    session.dispatch(Select(utterance=PINE))
    client = TestClient(build_app(session))
    got = client.get(
        "/api/plugins/project_cards/form", params={"type": "roadway_property_change", "use_selection": "true"}
    )
    facility = got.json()["targets"]["facility"]
    assert facility["form"] == "segment" and facility["id_system"] == "gmns"
    assert facility["forms"] == {"segment": None, "link_list": None}
    assert facility["id_systems"]["osm"] is None and "GERS" in facility["id_systems"]["gers"]
    assert got.json()["targets"]["label"] == "Segment: Pine Street, node 33 → 19 (GMNS), 1 link"
    other = client.get(
        "/api/plugins/project_cards/form",
        params={
            "type": "roadway_property_change",
            "use_selection": "true",
            "facility_form": "link_list",
            "id_system": "osm",
        },
    ).json()["targets"]
    assert other["count"] == 12 and other["facility"]["form"] == "link_list" and other["facility"]["id_system"] == "osm"
    assert other["label"] == "Link list: 1 OSM way (OpenStreetMap), covering 12 links"
    assert other["warnings"] == [f"OSM way {PINE_WAY} also covers 11 links outside this selection"]
    unnamed = client.get(
        "/api/plugins/project_cards/form",
        params={
            "type": "roadway_property_change",
            "use_selection": "true",
            "facility_form": "segment",
            "id_system": "gers",
        },
    )
    assert unnamed.status_code == 400 and "GERS" in unnamed.text


def test_the_form_route_marks_segment_unavailable_for_a_pick(session):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    session.dispatch(Select(link_ids=[60]))
    got = TestClient(build_app(session)).get(
        "/api/plugins/project_cards/form", params={"type": "roadway_property_change", "use_selection": "true"}
    )
    facility = got.json()["targets"]["facility"]
    assert facility["form"] == "link_list" and "no named segment" in facility["forms"]["segment"]
    refused = TestClient(build_app(session)).get(
        "/api/plugins/project_cards/form",
        params={"type": "roadway_property_change", "use_selection": "true", "facility_form": "segment"},
    )
    assert refused.status_code == 400 and "no named segment" in refused.text


# ---------------------------------------------------------------- validity and import round trips


@pytest.mark.parametrize("system", ["gmns", "osm"])
@pytest.mark.parametrize("form", ["segment", "link_list"])
def test_every_emitted_card_is_a_valid_projectcard_and_imports_back(session, make_session, tmp_path, form, system):
    session.dispatch(Select(utterance=PINE))
    add(session, form, system)
    path, _, _ = card_facility(session, tmp_path)  # validity: projectcard.validate.validate_card
    from projectcard.validate import validate_card

    validate_card(yaml.safe_load(path.read_text(encoding="utf-8")), parse_defaults=False)
    other = make_session()
    other.dispatch(ImportCard(path=str(path)))
    change = read_card_file(str(path)).changes[0]
    assert change.facility.form == form and change.facility.id_system == system
    expected = [60] if not (form == "link_list" and system == "osm") else None
    links = other.network(NET).links_df()
    changed = links.loc[links.lanes == 4, "link_id"].tolist()
    if expected:
        assert changed == expected
    else:  # an OSM list is every link of the way on the network the card is imported into
        assert len(changed) == 12 and 60 in changed
    # the draft shows the form and the id system
    text = other.state()["plugins"]["project_cards"]["drafts"][NET]["changes"][0]["describe"]
    assert text.startswith(
        "roadway_property_change " + ("Segment: Pine Street, node " if form == "segment" else "Link list: ")
    )
    assert text.count(f"({'GMNS' if system == 'gmns' else 'OpenStreetMap'})") == 1


def test_a_card_in_osm_ids_reads_back_the_form_and_ids(tmp_path):
    card = {
        "project": "OSM",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"osm_link_id": ["11", "12"], "ignore_missing": False}},
                    "property_changes": {"lanes": {"set": 2}},
                }
            },
            {
                "roadway_property_change": {
                    "facility": {"nodes": {"osm_node_id": ["900"], "ignore_missing": True}},
                    "property_changes": {"ctrl_type": {"set": "signal"}},
                }
            },
            {
                "roadway_property_change": {
                    "facility": {
                        "links": {"name": ["Main"], "ignore_missing": True},
                        "from": {"osm_node_id": "1"},
                        "to": {"osm_node_id": "2"},
                    },
                    "property_changes": {"lanes": {"set": 3}},
                }
            },
        ],
    }
    path = tmp_path / "osm.yml"
    path.write_text(yaml.safe_dump(card), encoding="utf-8")
    first, second, third = read_card_file(str(path)).changes
    assert (first.facility.ids, first.facility.id_system, first.facility.needs_network) == (["11", "12"], "osm", True)
    assert first.facility.target_ids() == [] and not first.facility.unresolved
    assert (second.facility.table, second.facility.ids, second.facility.id_system) == ("node", ["900"], "osm")
    assert (third.facility.form, third.facility.id_system, third.facility.unresolved) == ("segment", "osm", True)
    again = yaml.safe_load(DraftCard(changes=[first, second]).to_yaml())
    assert (
        again["changes"][0]["roadway_property_change"]["facility"]
        == card["changes"][0]["roadway_property_change"]["facility"]
    )
    assert (
        again["changes"][1]["roadway_property_change"]["facility"]
        == card["changes"][1]["roadway_property_change"]["facility"]
    )


def test_an_osm_list_card_is_applied_by_resolving_on_the_open_network(session, tmp_path):
    card = {
        "project": "OSM",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"osm_link_id": ["5836396"], "ignore_missing": False}},
                    "property_changes": {"lanes": {"set": 3}},
                }
            }
        ],
    }
    path = tmp_path / "osm.yml"
    path.write_text(yaml.safe_dump(card), encoding="utf-8")
    session.dispatch(ImportCard(path=str(path)))
    assert lanes_of(session, [1, 5, 6, 8]) == [3, 3, 3, 3]


def test_an_osm_list_with_an_unknown_way_is_refused_unless_missing_ids_are_ignored(session, tmp_path):
    def write(flag):
        card = {
            "project": "OSM",
            "changes": [
                {
                    "roadway_property_change": {
                        "facility": {"links": {"osm_link_id": ["5836396", "999"], "ignore_missing": flag}},
                        "property_changes": {"lanes": {"set": 3}},
                    }
                }
            ],
        }
        path = tmp_path / f"osm-{flag}.yml"
        path.write_text(yaml.safe_dump(card), encoding="utf-8")
        return str(path)

    with pytest.raises(ActionError, match="OSM way 999 not found"):
        session.dispatch(ImportCard(path=write(False)))
    session.dispatch(ImportCard(path=write(True)))
    assert lanes_of(session, [1]) == [3]


# ---------------------------------------------------------------- applicability with OSM ids


def frames(session):
    handle = session.network(NET)
    return handle.links_df(), handle.nodes_df()


def osm_card(ids, *, ignore_missing=False, table="link"):
    selection = Selection(table=table, ids=ids, id_system="osm", ignore_missing=ignore_missing)
    prop = {"lanes": PropertyChange(set=2)} if table == "link" else {"ctrl_type": PropertyChange(set="signal")}
    return DraftCard(changes=[RoadwayPropertyChange(facility=selection, property_changes=prop)])


def test_applicability_resolves_osm_way_ids(session):
    got = applicability(osm_card(["5836396"]), *frames(session))
    assert got["ok"] is True and got["changes"][0]["resolved"] == 4 and got["changes"][0]["missing"] == []


def test_applicability_names_the_osm_ids_the_network_lacks(session):
    got = applicability(osm_card(["5836396", "999"]), *frames(session))
    change = got["changes"][0]
    assert got["ok"] is False and change["missing"] == ["999"] and change["lacks"] == ["OSM way 999"]
    skipped = applicability(osm_card(["5836396", "999"], ignore_missing=True), *frames(session))
    assert skipped["ok"] is True and skipped["changes"][0]["missing"] == ["999"]


def test_applicability_resolves_osm_node_ids(session):
    nodes = session.network(NET).nodes_df()
    osm = str(int(nodes.osm_node_id.dropna().iloc[0]))
    got = applicability(osm_card([osm], table="node"), *frames(session))
    assert got["ok"] is True and got["changes"][0]["resolved"] == 1


def test_applicability_refuses_osm_ids_on_a_network_without_the_column(session):
    links, nodes = frames(session)
    got = applicability(osm_card(["5836396"]), links.drop(columns=["osm_way_id"]), nodes)
    assert got["ok"] is False and "no osm_way_id column" in got["changes"][0]["reason"]


def test_resolve_osm_matches_text_and_float_ids(session):
    links, _ = frames(session)
    keys, absent = resolve_osm(links, "link", [5836396, "5836396", "7"])
    assert keys == [1, 5, 6, 8] and absent == ["7"]
    as_float = links.assign(osm_way_id=links.osm_way_id.astype("float64"))
    assert resolve_osm(as_float, "link", ["5836396"])[0] == [1, 5, 6, 8]
    assert resolve_osm(links.assign(osm_way_id=links.osm_way_id.mask(links.link_id == 1)), "link", ["5836396"])[0] == [
        5,
        6,
        8,
    ]  # Int64 NA
    with_gap = links.assign(osm_way_id=links.osm_way_id.astype(object).where(links.link_id != 1, None))
    assert resolve_osm(with_gap, "link", ["5836396"])[0] == [5, 6, 8]


# ---------------------------------------------------------------- the draft and inspect text


def test_describe_names_the_form_and_the_id_system():
    segment = Selection(query=copy.deepcopy(SEGMENT_GMNS), resolved=[60])
    assert facility_text(segment) == "Segment: Pine Street, node 33 → ? (GMNS)"
    both = Selection(
        query={"links": {"name": ["Pine Street"]}, "from": {"model_node_id": 33}, "to": {"model_node_id": 19}},
        resolved=[60],
    )
    assert facility_text(both) == "Segment: Pine Street, node 33 → 19 (GMNS)"
    assert facility_text(Selection(ids=[1])) == "Link list: 1 link (GMNS)"
    assert facility_text(Selection(table="node", ids=[1, 2])) == "Node list: 2 nodes (GMNS)"
    assert facility_text(Selection(ids=["5", "6"], id_system="osm")) == "Link list: 2 OSM ways (OpenStreetMap)"
    assert facility_text(Selection(query={"links": {"name": ["Main"]}})) == "Segment: Main (GMNS), no provenance"
    assert facility_text(Selection(query={"links": {"all": True, "modes": ["bike"]}}, resolved=[1])) == (
        'Links matching {"all": true, "modes": ["bike"]} (GMNS)'
    )
    named = LANES.model_copy(update={"facility": both})
    assert describe(named) == "roadway_property_change Segment: Pine Street, node 33 → 19 (GMNS): lanes → 4"
    assert describe(LANES) == "roadway_property_change the selection: lanes → 4"


def test_the_inspect_view_carries_the_form_and_the_id_system(session, tmp_path):
    session.dispatch(Select(utterance=PINE))
    add(session, "segment", "osm")
    session.dispatch(ClearSelection())
    session.dispatch(Select(link_ids=[60]))
    add(session, "link_list", "osm")
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    got = TestClient(build_app(session)).get("/api/plugins/project_cards/card", params={"draft": "true"}).json()
    first, second = (c["facility"] for c in got["changes"])
    assert (first["id_system"], first["query"]["links"]["name"]) == ("osm", ["Pine Street"])
    assert (second["id_system"], second["ids"]) == ("osm", [PINE_WAY])
    assert got["changes"][0]["targets"]["links"] == [60] and len(got["changes"][1]["targets"]["links"]) == 12
    assert query_id_system(first["query"]) == "osm"


def test_a_segment_preview_and_a_link_list_preview_both_work(session):
    session.dispatch(Select(utterance=PINE))
    session.dispatch(PreviewChange(use_selection=True, facility_form="link_list", id_system="osm", change=LANES))
    assert session.active != NET
