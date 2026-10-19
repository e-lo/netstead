"""Draft project_cards: ProjectCard dicts and YAML in and out, provenance in notes, tolerant reading."""

import pytest
import yaml
from netstead_project_cards.card import DraftCard, read_card_file
from netstead_project_cards.errors import CardError, CardNotSupported
from netstead_project_cards.types import (
    EMPTY_VALUE,
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)
from netstead_project_cards.validate import card_problems

PICK = RoadwayPropertyChange(
    facility=Selection(ids=[1]),
    property_changes={
        "free_speed": PropertyChange(existing=40, set=30),
        "facility_type": PropertyChange(set="tertiary"),
    },
    note="quality.high_speed_residential: link 1",
)
QUERY = RoadwayPropertyChange(
    facility=Selection(query={"links": {"name": ["Benton Street"], "ignore_missing": True}}, resolved=[1, 5, 6, 8]),
    property_changes={"lanes": PropertyChange(change=1)},
)
ADD = RoadwayAddition(
    nodes=[{"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}],
    links=[
        {
            "link_id": 9001,
            "from_node_id": 1,
            "to_node_id": 9001,
            "name": "New Street",
            "facility_type": "residential",
            "lanes": 1,
            "allowed_uses": "auto,walk",
        }
    ],
)


def draft(*changes):
    return DraftCard(project="speed fixes", tags=["fixes"], changes=list(changes))


def test_a_pick_writes_wrangler_names_and_validates():
    card = draft(PICK).to_card()
    assert card["changes"] == [
        {
            "roadway_property_change": {
                "facility": {"links": {"model_link_id": [1], "ignore_missing": False}},
                "property_changes": {
                    "free_speed": {"existing": 40, "existing_value_conflict": "error", "set": 30},
                    "roadway": {"set": "tertiary"},
                },
            }
        }
    ]
    assert "netstead.note[0]: quality.high_speed_residential: link 1" in card["notes"]
    assert card_problems(card) == []


def test_a_query_keeps_its_query_and_records_what_it_resolved_to():
    card = draft(QUERY).to_card()
    assert card["changes"][0]["roadway_property_change"]["facility"] == QUERY.facility.query
    assert "netstead.resolved[0]: model_link_id=[1, 5, 6, 8]" in card["notes"]
    assert card_problems(card) == []
    assert DraftCard.from_card(card).changes[0].facility == QUERY.facility


def test_deletion_and_addition_cards_validate():
    card = draft(RoadwayDeletion(links=[1], clean_nodes=True), ADD).to_card()
    deletion, addition = (c.popitem()[1] for c in card["changes"])
    assert deletion == {"links": {"model_link_id": [1], "ignore_missing": False}, "clean_nodes": True}
    link = addition["links"][0]
    assert (link["A"], link["B"], link["roadway"]) == (1, 9001, "residential")
    assert (link["walk_access"], link["bike_access"], link["drive_access"]) == (1, 0, 1)
    assert addition["nodes"][0] == {"model_node_id": 9001, "X": -120.66, "Y": 47.6}
    assert card_problems(draft(RoadwayDeletion(links=[1], clean_nodes=True), ADD).to_card()) == []


def test_yaml_round_trip(tmp_path):
    original = DraftCard(
        project="speed fixes",
        tags=["fixes"],
        dependencies={"prerequisites": ["widen-main"]},
        notes="Reviewed with the city.",
        changes=[PICK, QUERY, RoadwayDeletion(links=[2]), ADD],
    )
    path = tmp_path / "card.yml"
    path.write_text(original.to_yaml(), encoding="utf-8")
    assert read_card_file(path) == original


def test_a_card_we_write_loads_with_projectcard(tmp_path):
    import projectcard

    path = tmp_path / "card.yml"
    path.write_text(draft(PICK, QUERY, RoadwayDeletion(links=[2]), ADD).to_yaml(), encoding="utf-8")
    assert projectcard.read_card(path, validate=True).project == "speed fixes"


def test_the_offline_reports_edit_log_imports(tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log  # the real writer: its format is what we read

    path = tmp_path / "edits.yaml"
    log = EditLog(
        edits=[
            Edit(
                id="e1",
                kind="fix",
                table="link",
                pk={"link_id": 7},
                column="lanes",
                from_value=1,
                to_value=2,
                reason="too few",
            ),
        ]
    )
    dump_edit_log(log, path)
    (change,) = read_card_file(path).changes
    assert change.facility == Selection(ids=[7]) and change.property_changes["lanes"] == PropertyChange(
        existing=1, set=2
    )
    assert "too few" in change.note and "edit_id=e1" in change.note


def test_an_edit_log_that_clears_a_cell_is_refused(tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log

    path = tmp_path / "edits.yaml"
    dump_edit_log(
        EditLog(
            edits=[
                Edit(
                    id="e1",
                    kind="fix",
                    table="link",
                    pk={"link_id": 7},
                    column="name",
                    from_value="Main",
                    to_value=None,
                )
            ]
        ),
        path,
    )
    with pytest.raises(CardError, match=f"change 1: .*{EMPTY_VALUE}"):
        read_card_file(path)


def test_files_are_read_with_projectcard(tmp_path):
    json_card = tmp_path / "card.json"
    json_card.write_text(
        '{"project": "j", "roadway_deletion": {"links": {"model_link_id": [4], "ignore_missing": false}}}'
    )
    assert read_card_file(json_card).changes == [RoadwayDeletion(links=[4])]
    no_flag = tmp_path / "ids.yml"
    no_flag.write_text(
        "project: i\nchanges:\n- roadway_property_change:\n    facility: {links: {model_link_id: [3]}}\n"
        "    property_changes: {lanes: {set: 2}}\n"
    )
    assert read_card_file(no_flag).changes[0].facility == Selection(ids=[3], ignore_missing=True)  # schema default
    (tmp_path / "x.yml").write_text("changes: []\n")
    with pytest.raises(CardError, match="expected a top-level 'project'"):
        read_card_file(tmp_path / "x.yml")
    (tmp_path / "x.txt").write_text("project: t\n")
    with pytest.raises(CardError, match="Unsupported file type"):
        read_card_file(tmp_path / "x.txt")


def test_an_id_card_keeps_its_ignore_missing():
    card = {
        "project": "p",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"model_link_id": [3], "ignore_missing": True}},
                    "property_changes": {"lanes": {"set": 2}},
                }
            }
        ],
    }
    assert DraftCard.from_card(card).changes[0].facility == Selection(ids=[3], ignore_missing=True)


def test_a_single_change_card_without_a_changes_list_reads():
    card = {"project": "p", "roadway_deletion": {"links": {"model_link_id": [4], "ignore_missing": False}}}
    assert DraftCard.from_card(card).changes == [RoadwayDeletion(links=[4])]


def test_a_query_without_provenance_is_not_supported_yet():
    card = {
        "project": "p",
        "changes": [
            {
                "roadway_property_change": {
                    "facility": {"links": {"name": ["Main"], "ignore_missing": True}},
                    "property_changes": {"lanes": {"set": 2}},
                }
            }
        ],
    }
    with pytest.raises(CardNotSupported, match="apply_card"):
        DraftCard.from_card(card)


@pytest.mark.parametrize("kind", ["transit_property_change", "pycode", "roadway_managed_lanes"])
def test_other_change_types_are_not_supported_on_read(kind):
    with pytest.raises(CardNotSupported, match=kind):
        DraftCard.from_card({"project": "p", "changes": [{kind: {}}]})


def test_transit_and_incomplete_changes_cannot_be_written():
    with pytest.raises(CardNotSupported, match="not yet supported"):
        draft(TransitPropertyChange()).to_card()
    with pytest.raises(CardError, match="needs a facility"):
        draft(RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)})).to_card()


def test_not_a_card():
    with pytest.raises(CardError, match="project"):
        DraftCard.from_card({"changes": []})
    assert yaml.safe_load(draft(PICK).to_yaml())["project"] == "speed fixes"
