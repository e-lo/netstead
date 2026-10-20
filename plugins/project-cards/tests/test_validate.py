"""Cards are checked with projectcard's own validator, strictly (no defaults filled in) and on a copy."""

import copy

from netstead_project_cards.validate import card_problems

GOOD = {
    "project": "fix speeds",
    "tags": ["fixes"],
    "changes": [
        {
            "roadway_property_change": {
                "facility": {"links": {"model_link_id": [1, 2], "ignore_missing": False}},
                "property_changes": {"free_speed": {"existing": 40, "existing_value_conflict": "error", "set": 30}},
            }
        }
    ],
}


def _change(card):
    return card["changes"][0]["roadway_property_change"]


def test_a_valid_card_has_no_problems():
    assert card_problems(GOOD) == []


def test_a_missing_ignore_missing_is_a_problem_and_the_card_is_left_alone():
    bad = copy.deepcopy(GOOD)
    del _change(bad)["facility"]["links"]["ignore_missing"]
    before = copy.deepcopy(bad)
    assert card_problems(bad) == [
        "changes/0/roadway_property_change/facility/links: 'ignore_missing' is a required property"
    ]
    assert bad == before  # projectcard's parse_defaults would have filled it in, in place


def test_a_change_takes_no_extra_fields():
    bad = copy.deepcopy(GOOD)
    _change(bad)["notes"] = "per-change notes are not in the schema"
    (problem,) = card_problems(bad)
    assert problem.startswith("changes/0/roadway_property_change: Additional properties")


def test_a_name_and_a_ref_together_are_not_a_valid_selector():
    bad = copy.deepcopy(GOOD)
    _change(bad)["facility"]["links"] = {"name": ["Main Street"], "ref": ["US 2"], "ignore_missing": True}
    (problem,) = card_problems(bad)
    assert problem.startswith("changes/0/roadway_property_change/facility/links:")


def test_ids_must_be_integers():
    bad = copy.deepcopy(GOOD)
    _change(bad)["facility"]["links"]["model_link_id"] = ["1"]
    assert card_problems(bad) == [
        "changes/0/roadway_property_change/facility/links/model_link_id/0: '1' is not of type 'integer'"
    ]


def test_a_card_needs_a_project():
    assert card_problems({"changes": GOOD["changes"]}) == ["(card): 'project' is a required property"]
