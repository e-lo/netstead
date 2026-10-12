"""Live checks: what the spec says about rows an edit touched (warnings, never refusals)."""

import pytest
from corral.editing import Edit
from corral.editing.apply import apply_edit
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.workbench import editcheck
from netstead.workbench.editcheck import check_rows, fits_type, touched_keys
from netstead.workbench.editing import plan_add, plan_delete, plan_update, table_keys


@pytest.fixture
def net():
    return Network.from_source(leavenworth.parquet_dir())


def apply(net, plan):
    return [apply_edit(net, e) for e in plan.edits]


def codes(net, touched):
    return sorted((w.code, w.table, w.key, w.column) for w in check_rows(net, touched).warnings)


def test_leavenworth_rows_start_clean(net):
    keys = net.tables["link"].select("link_id").to_pandas()["link_id"].tolist()
    assert check_rows(net, {"link": keys}).warnings == ()


def test_a_clean_edit_has_no_warnings(net):
    apply(net, plan_update(net, "link", [1], {"free_speed": 30}))
    assert codes(net, {"link": [1]}) == []


def test_a_value_outside_the_enum(net):
    apply(net, plan_update(net, "node", [1], {"ctrl_type": "bogus"}))
    (w,) = check_rows(net, {"node": [1]}).warnings
    assert (w.code, w.column, w.value) == ("edit.enum", "ctrl_type", "bogus") and "not one of" in w.message


def test_a_missing_foreign_key_and_an_empty_required_field(net):
    apply(net, plan_update(net, "link", [1], {"from_node_id": 424242, "directed": None}))
    assert codes(net, {"link": [1]}) == [
        ("edit.fk_missing", "link", 1, "from_node_id"),
        ("edit.required_empty", "link", 1, "directed"),
    ]


def test_fixing_the_foreign_key_clears_its_warning(net):
    apply(net, plan_update(net, "link", [1], {"from_node_id": 424242}))
    apply(net, plan_add(net, "node", [{"node_id": 424242, "x_coord": -120.66, "y_coord": 47.6}]))
    assert codes(net, {"link": [1], "node": [424242]}) == []


def test_a_duplicate_key(net):
    apply(net, plan_add(net, "link", [{"link_id": 1, "from_node_id": 1, "to_node_id": 2, "directed": False}]))
    assert ("edit.duplicate_key", "link", 1, "link_id") in codes(net, {"link": [1]})


def test_a_value_the_spec_type_cannot_hold(net):
    # A plugin's raw corral edit can widen an integer column to float (the planner would refuse 2.5).
    apply_edit(
        net,
        Edit(op="update_rows", table="link", payload={"predicate": lambda t: t.link_id == 1, "set": {"lanes": 2.5}}),
    )
    assert ("edit.type", "link", 1, "lanes") in codes(net, {"link": [1]})


@pytest.mark.parametrize(
    ("value", "spec_type", "fits"),
    [
        (2, "integer", True),
        (2.0, "integer", True),
        (2.5, "integer", False),
        ("2", "number", False),
        (True, "number", False),
        (1, "boolean", True),
        ("x", "string", True),
        (3, "string", False),
        ("x", "any", True),
    ],
)
def test_fits_type(value, spec_type, fits):
    assert fits_type(value, spec_type) is fits


def test_rows_that_no_longer_exist_are_skipped(net):
    apply(net, plan_delete(net, "link", [1]))
    assert codes(net, {"link": [1]}) == []


def test_touched_keys_reads_updates_and_additions_not_deletions(net):
    results = apply(net, plan_update(net, "link", [1, 2], {"lanes": 2}))
    results += apply(net, plan_add(net, "node", [{"node_id": 9001, "x_coord": 0.0, "y_coord": 0.0}]))
    results += apply(net, plan_delete(net, "link", [3]))
    assert touched_keys(results, table_keys(net)) == {"link": {1, 2}, "node": {9001}}


def test_the_check_is_bounded(net, monkeypatch):
    monkeypatch.setattr(editcheck, "MAX_CHECKED_KEYS", 1)
    result = check_rows(net, {"link": [1, 2]})
    assert result.truncated is True and result.warnings == ()


def test_warnings_for_some_rows(net):
    apply(net, plan_update(net, "node", [1, 2], {"ctrl_type": "bogus"}))
    result = check_rows(net, {"node": [1, 2]})
    assert [w.key for w in result.for_rows({"node": {2}})] == [2]
    assert result.to_dict()["warnings"][0]["code"] == "edit.enum"


def test_the_cap_checks_the_newest_keys_first(net, monkeypatch):
    monkeypatch.setattr(editcheck, "MAX_CHECKED_KEYS", 1)
    apply(net, plan_update(net, "node", [1, 2], {"ctrl_type": "bogus"}))
    result = check_rows(net, {"node": [1, 2]}, first={"node": [2]})
    assert result.truncated is True and [w.key for w in result.warnings] == [2]
