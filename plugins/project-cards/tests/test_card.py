"""Draft project_cards: ProjectCard dicts and YAML in and out, provenance in notes, tolerant reading."""

import re
from collections import namedtuple

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
    assert card_problems(yaml.safe_load(path.read_text(encoding="utf-8"))) == []  # schema-valid since 2026-10-20
    card = read_card_file(path)
    (change,) = card.changes
    assert change.facility == Selection(ids=[7], ignore_missing=False)
    assert change.property_changes["lanes"] == PropertyChange(existing=1, set=2)
    assert "too few" in change.note and "edit_id=e1" in change.note
    assert "netstead.note" not in (card.notes or "")


def test_an_old_edit_log_still_imports(tmp_path):
    """Logs written before the schema-valid writer: facility ids at the top level and a per-change ``notes``."""
    path = tmp_path / "old.yaml"
    path.write_text(
        "project: netstead edits\n"
        "changes:\n"
        "- roadway_property_change:\n"
        "    facility: {model_link_id: [7]}\n"
        "    property_changes: {lanes: {existing: 1, set: 2}}\n"
        "    notes: 'too few · edit_id=e1'\n"
        "- roadway_property_change:\n"
        "    facility: {model_node_id: 3}\n"
        "    property_changes: {y_coord: {set: 47.6}}\n",
        encoding="utf-8",
    )
    links, nodes = read_card_file(path).changes
    assert links.facility == Selection(ids=[7]) and links.note == "too few · edit_id=e1"
    assert nodes.facility == Selection(table="node", ids=[3]) and nodes.note is None


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


def test_a_json_card_is_read(tmp_path):
    json_card = tmp_path / "card.json"
    json_card.write_text(
        '{"project": "j", "roadway_deletion": {"links": {"model_link_id": [4], "ignore_missing": false}}}'
    )
    assert read_card_file(json_card).changes == [RoadwayDeletion(links=[4])]


def test_a_selector_without_ignore_missing_gets_the_schema_default(tmp_path):
    no_flag = tmp_path / "ids.yml"
    no_flag.write_text(
        "project: i\nchanges:\n- roadway_property_change:\n    facility: {links: {model_link_id: [3]}}\n"
        "    property_changes: {lanes: {set: 2}}\n"
    )
    assert read_card_file(no_flag).changes[0].facility == Selection(ids=[3], ignore_missing=True)


def test_a_file_without_a_project_is_refused(tmp_path):
    (tmp_path / "x.yml").write_text("changes: []\n")
    with pytest.raises(CardError, match="expected a top-level 'project'"):
        read_card_file(tmp_path / "x.yml")


def test_an_unsupported_file_type_is_refused(tmp_path):
    (tmp_path / "x.txt").write_text("project: t\n")
    with pytest.raises(CardError, match="Unsupported file type"):
        read_card_file(tmp_path / "x.txt")


def test_a_toml_card_is_not_supported(tmp_path):
    (tmp_path / "card.toml").write_text('project = "t"\n')
    with pytest.raises(
        CardNotSupported, match=re.escape("TOML cards can't be read with projectcard 0.3.3; use YAML or JSON")
    ):
        read_card_file(tmp_path / "card.toml")


def test_a_missing_file_is_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_card_file(tmp_path / "nowhere.yml")


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


def test_a_query_without_provenance_reads_as_unresolved():
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
    (change,) = DraftCard.from_card(card).changes
    assert change.facility.unresolved and change.facility.target_ids() == []
    assert "netstead.resolved" not in DraftCard.from_card(card).to_yaml()  # no provenance is invented


@pytest.mark.parametrize("kind", ["transit_property_change", "pycode", "roadway_managed_lanes"])
def test_other_change_types_are_not_supported_on_read(kind):
    with pytest.raises(CardNotSupported, match=kind):
        DraftCard.from_card({"project": "p", "changes": [{kind: {}}]})


@pytest.mark.parametrize("kind", ["pycode", "roadway_managed_lanes"])
def test_a_top_level_change_of_an_unsupported_type_is_found(kind):
    with pytest.raises(CardNotSupported, match=kind):
        DraftCard.from_card({"project": "p", kind: {}})


def _one(kind, body, **top):
    return {"project": "p", "changes": [{kind: body}], **top}


_DELETE = {"roadway_deletion": {"links": {"model_link_id": [1], "ignore_missing": False}}}
_LANES = {"lanes": {"set": 2}}
_FACILITY = {"links": {"model_link_id": [1], "ignore_missing": False}}


def _noted(line):
    return {"project": "p", "notes": line, "changes": [_DELETE]}


def _deps(value):
    return {"project": "p", "dependencies": value, "changes": [_DELETE]}


#: A card file to read with ``read_card_file`` (a case is otherwise a dict for ``DraftCard.from_card``).
File = namedtuple("File", "name text")

MALFORMED = {
    # C1: shapes that are not a mapping or a list where one is expected
    "changes is a number": ({"project": "p", "changes": 5}, CardError, "changes: expected a list"),
    "a change is a string": ({"project": "p", "changes": ["x"]}, CardError, "change 1: expected exactly one"),
    "a change body is a list": (
        _one("roadway_deletion", [1]),
        CardError,
        "change 1: roadway_deletion: expected a mapping",
    ),
    "a change body is a string": (
        _one("roadway_property_change", "lanes"),
        CardError,
        "change 1: roadway_property_change: expected a mapping",
    ),
    "facility is a list": (
        _one("roadway_property_change", {"facility": [1], "property_changes": _LANES}),
        CardError,
        "change 1: facility: expected a mapping",
    ),
    "facility links is a list": (
        _one("roadway_property_change", {"facility": {"links": [1]}, "property_changes": _LANES}),
        CardError,
        "change 1: facility links: expected a mapping",
    ),
    "property_changes is a list": (
        _one("roadway_property_change", {"facility": _FACILITY, "property_changes": [_LANES]}),
        CardError,
        "change 1: property_changes: expected a mapping",
    ),
    "a property spec is a scalar": (
        _one("roadway_property_change", {"facility": _FACILITY, "property_changes": {"lanes": 2}}),
        CardError,
        "change 1: property lanes: expected a mapping",
    ),
    "addition links hold a string": (
        _one("roadway_addition", {"links": ["x"]}),
        CardError,
        r"change 1: links\[0\]: expected a mapping",
    ),
    "addition links is a mapping": (
        _one("roadway_addition", {"links": {"a": 1}}),
        CardError,
        "change 1: links: expected a list",
    ),
    "deletion links is a string": (
        _one("roadway_deletion", {"links": "model_link_id"}),
        CardError,
        "change 1: links: expected a mapping",
    ),
    "an unknown change type": (_one("foo", {}), CardError, "change 1: unknown change type 'foo'$"),
    "pydantic's prefix is dropped": (
        _one(
            "roadway_property_change", {"facility": _FACILITY, "property_changes": {"lanes": {"set": 1, "change": 1}}}
        ),
        CardError,
        "change 1: give set or change, not both$",
    ),
    # provenance lines in the notes
    "a resolved line without '='": (
        _noted("netstead.resolved[0]: 1, 2"),
        CardError,
        r"notes: 'netstead\.resolved\[0\]",
    ),
    "a resolved line with bad JSON": (
        _noted("netstead.resolved[0]: model_link_id=[1,"),
        CardError,
        r"notes: 'netstead\.resolved\[0\]",
    ),
    "a resolved line that is not a list": (
        _noted("netstead.resolved[0]: model_link_id=5"),
        CardError,
        r"notes: 'netstead\.resolved\[0\]",
    ),
    # dependencies and tags
    "an unknown dependencies key": (_deps({"foo": ["a"]}), CardError, "dependencies: foo"),
    "dependencies is a list": (_deps(["a"]), CardError, "dependencies: "),
    "prerequisites is null": (_deps({"prerequisites": None}), CardError, "dependencies: prerequisites"),
    "prerequisites is a string": (_deps({"prerequisites": "abc"}), CardError, "dependencies: prerequisites"),
    "tags is a string": ({"project": "p", "tags": "fixes", "changes": [_DELETE]}, CardError, "tags: expected a list"),
    "a tag is a number": ({"project": "p", "tags": [1], "changes": [_DELETE]}, CardError, "tags: expected a list"),
    # C2: files projectcard cannot read
    "bad YAML": (File("bad.yml", "project: [unclosed\n"), CardError, r".*bad\.yml: not a readable ProjectCard"),
    "bad JSON": (File("bad.json", '{"project": '), CardError, r".*bad\.json: not a readable ProjectCard"),
    "an empty file": (File("empty.yml", ""), CardError, r".*empty\.yml: not a readable ProjectCard"),
    "a YAML list": (File("list.yml", "- project: p\n"), CardError, r".*list\.yml: not a readable ProjectCard"),
    "a YAML string": (File("str.yml", "hello\n"), CardError, r".*str\.yml: not a readable ProjectCard"),
    "changes is a number in a file": (
        File("five.yml", "project: p\nchanges: 5\n"),
        CardError,
        r".*five\.yml: not a readable ProjectCard",
    ),
    "a change with two types in a file": (
        File("two.yml", "project: p\nchanges:\n- {roadway_deletion: {}, roadway_addition: {}}\n"),
        CardError,
        r".*two\.yml: not a readable ProjectCard",
    ),
    "not UTF-8": (File("latin.yml", "project: \udcff\n"), CardError, r".*latin\.yml: not a readable ProjectCard"),
    "a TOML file": (File("c.toml", 'project = "p"\n'), CardNotSupported, "TOML cards can't be read"),
}


@pytest.mark.parametrize(("case", "error", "pattern"), MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_input_is_a_card_error(tmp_path, case, error, pattern):
    with pytest.raises(CardError) as info:
        if isinstance(case, File):
            path = tmp_path / case.name
            path.write_bytes(case.text.encode("utf-8", "surrogateescape"))
            read_card_file(path)
        else:
            DraftCard.from_card(case)
    assert info.type is error
    assert re.match(pattern, str(info.value)), str(info.value)
    assert "change 1: change 1" not in str(info.value)


def test_provenance_for_a_change_the_card_lacks_stays_in_the_notes():
    card = {
        "project": "p",
        "notes": "mine\nnetstead.note[3]: why\nnetstead.resolved[4]: model_link_id=[1]",
        "changes": [_DELETE],
    }
    read = DraftCard.from_card(card)
    assert read.notes == "mine\nnetstead.note[3]: why\nnetstead.resolved[4]: model_link_id=[1]"
    assert read.changes[0].note is None


def test_a_property_change_s_unsupported_selector_names_no_change_kind():
    card = _one("roadway_property_change", {"facility": {"nodes": {"osm_node_id": [1]}}, "property_changes": _LANES})
    with pytest.raises(CardNotSupported, match=r"^change 1: only model_node_id selections of nodes can be read"):
        DraftCard.from_card(card)


def test_a_transit_change_cannot_be_written():
    with pytest.raises(CardNotSupported, match="not yet supported"):
        draft(TransitPropertyChange()).to_card()


def test_an_incomplete_change_cannot_be_written():
    with pytest.raises(CardError, match="needs a facility"):
        draft(RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=2)})).to_card()


def test_not_a_card():
    with pytest.raises(CardError, match="project"):
        DraftCard.from_card({"changes": []})
    assert yaml.safe_load(draft(PICK).to_yaml())["project"] == "speed fixes"


def test_an_integer_delta_round_trips_as_an_integer():
    card = draft(QUERY).to_card()
    assert card["changes"][0]["roadway_property_change"]["property_changes"]["lanes"] == {"change": 1}
    delta = DraftCard.from_card(card).changes[0].property_changes["lanes"].change
    assert delta == 1 and type(delta) is int


@pytest.mark.parametrize("scope", [{"scoped": [{"timespan": ["7:00", "9:00"], "set": 2}]}, {"overwrite_scoped": "all"}])
def test_a_scoped_property_change_is_not_supported(scope):
    card = _one("roadway_property_change", {"facility": _FACILITY, "property_changes": {"lanes": {"set": 1, **scope}}})
    with pytest.raises(CardNotSupported, match=r"^change 1: property lanes: scoped changes are not supported yet$"):
        DraftCard.from_card(card)


def test_an_unknown_property_key_is_not_dropped():
    card = _one(
        "roadway_property_change", {"facility": _FACILITY, "property_changes": {"lanes": {"set": 1, "sett": 2}}}
    )
    with pytest.raises(CardNotSupported, match=r"^change 1: property lanes: .*'sett'"):
        DraftCard.from_card(card)


WRANGLER_ADDITION = """\
project: wrangler addition
tags: [example]
roadway_addition:
  links:
  - A: 1
    B: 2
    model_link_id: 404982
    name: new neighborhood st
    roadway: residential
    lanes: 1
    walk_access: 1
    bike_access: 1
    drive_access: 0
    bus_only: 0
    rail_only: 0
"""


def test_a_wrangler_style_addition_keeps_its_access_flags(tmp_path):
    path = tmp_path / "add.yml"
    path.write_text(WRANGLER_ADDITION, encoding="utf-8")
    (change,) = read_card_file(path).changes
    link = change.links[0]
    assert (link["walk_access"], link["bike_access"], link["drive_access"]) == (1, 1, 0)


def test_a_wrangler_style_addition_re_exports_valid(tmp_path):
    path = tmp_path / "add.yml"
    path.write_text(WRANGLER_ADDITION, encoding="utf-8")
    card = read_card_file(path).to_card()
    assert card_problems(card) == []
    assert (
        card["changes"][0]["roadway_addition"]["links"][0]
        == yaml.safe_load(WRANGLER_ADDITION)["roadway_addition"]["links"][0]
    )


def test_derived_fields_are_dropped_when_their_source_column_is_present():
    (change,) = DraftCard.from_card(draft(ADD).to_card()).changes
    assert change.links == ADD.links


def test_provenance_uses_the_mapping_s_link_selector(tmp_path):
    from netstead_project_cards.mapping import load_mapping

    path = tmp_path / "m.yaml"
    path.write_text("version: 1\nselectors: {link: {link_id: my_id}, node: {node_id: my_node}}\n")
    card = draft(QUERY).to_card(load_mapping(path))
    assert "netstead.resolved[0]: my_id=[1, 5, 6, 8]" in card["notes"]


SCOPED_LANES = {"default": 2, "timeofday": [{"timespan": ["6:00", "9:00"], "value": 3}]}


def test_a_scoped_addition_value_is_not_supported():
    link = {"model_link_id": 1, "A": 1, "B": 2, "lanes": SCOPED_LANES}
    card = _one("roadway_addition", {"links": [link]})
    with pytest.raises(
        CardNotSupported, match=r"^change 1: link 1 field 'lanes': scoped \(time-of-day\) values are not supported yet$"
    ):
        DraftCard.from_card(card)


def test_a_list_addition_value_is_a_card_error():
    link = {"model_link_id": 1, "A": 1, "B": 2, "lanes": [1, 2]}
    with pytest.raises(CardError, match=r"^change 1: .*cannot set a property to a list"):
        DraftCard.from_card(_one("roadway_addition", {"links": [link]}))


@pytest.mark.parametrize("key", ["existing", "set"])
@pytest.mark.parametrize("value", [SCOPED_LANES, [1, 2]])
def test_an_unhashable_property_value_is_a_card_error(key, value):
    spec = {"set": 2, key: value}
    card = _one("roadway_property_change", {"facility": _FACILITY, "property_changes": {"lanes": spec}})
    with pytest.raises(CardError, match=r"^change 1: a ProjectCard cannot set a property to a list"):
        DraftCard.from_card(card)


def test_writing_an_unhashable_record_value_does_not_raise_type_error():
    from netstead_project_cards.card import _record
    from netstead_project_cards.mapping import load_mapping

    out = _record({"link_id": 1, "lanes": SCOPED_LANES}, "link", load_mapping())
    assert out == {"model_link_id": 1, "lanes": SCOPED_LANES}


@pytest.mark.parametrize(
    "card",
    [
        _one("roadway_deletion", {"nodes": {"model_node_id": [1], "osm_node_id": ["x"]}}),
        _one("roadway_deletion", {"links": {"model_link_id": [1], "name": ["Main"], "ignore_missing": False}}),
        _one(
            "roadway_property_change",
            {"facility": {"nodes": {"model_node_id": [1], "osm_node_id": ["x"]}}, "property_changes": _LANES},
        ),
    ],
)
def test_extra_selector_keys_are_not_dropped(card):
    with pytest.raises(CardNotSupported, match=r"^change 1: only model_\w+ selections of \w+s can be read"):
        DraftCard.from_card(card)


def test_a_deletion_writes_its_links_ignore_missing_and_nodes_false():
    """A deletion made from a query selection carries the query's flag; nodes are always aimed at specific records."""
    card = draft(RoadwayDeletion(links=[1], nodes=[9], ignore_missing=True)).to_card()
    deletion = card["changes"][0]["roadway_deletion"]
    assert deletion["links"] == {"model_link_id": [1], "ignore_missing": True}
    assert deletion["nodes"] == {"model_node_id": [9], "ignore_missing": False}
    assert card_problems(card) == []


def test_importing_a_deletion_keeps_its_links_ignore_missing():
    links = {"model_link_id": [4], "ignore_missing": True}
    card = _one("roadway_deletion", {"links": links, "nodes": {"model_node_id": [9], "ignore_missing": True}})
    (change,) = DraftCard.from_card(card).changes
    assert change == RoadwayDeletion(links=[4], nodes=[9], ignore_missing=True)
    written = DraftCard.from_card(card).to_card()["changes"][0]["roadway_deletion"]
    assert written["links"]["ignore_missing"] is True and written["nodes"]["ignore_missing"] is False


def test_a_node_only_deletion_reads_ignore_missing_false():
    card = _one("roadway_deletion", {"nodes": {"model_node_id": [9], "ignore_missing": True}})
    assert DraftCard.from_card(card).changes == [RoadwayDeletion(nodes=[9])]
