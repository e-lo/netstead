"""Edit planning: requests become corral edits; only what the storage cannot hold is refused."""

import ibis.expr.datatypes as dt
import pandas as pd
import pytest
from corral.editing.apply import apply_edit, reverse_edit
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.workbench.editing import EditRefused, coerce_value, editable, plan_add, plan_delete, plan_update

PK = {"link": "link_id", "node": "node_id", "lane": "lane_id"}


@pytest.fixture
def net():
    return Network.from_source(leavenworth.parquet_dir())  # per test: plans get applied


def apply(net, plan):
    return [apply_edit(net, e) for e in plan.edits]


def cell(net, table, key, column):
    df = net.tables[table].to_pandas()
    (value,) = df.loc[df[PK[table]] == key, column].tolist()
    return None if pd.isna(value) else value


def counts(net):
    return {name: t.count() for name, t in net.tables.items()}


@pytest.mark.parametrize(
    ("value", "dtype", "want"),
    [
        ("45", dt.float64, 45.0),
        (2.0, dt.int64, 2),
        ("2", dt.int64, 2),
        (None, dt.int64, None),
        ("", dt.float64, None),
        ("yes", dt.boolean, True),
        ("0", dt.boolean, False),
        (7, dt.string, "7"),
        (127, dt.int8, 127),
        (True, dt.string, "True"),
    ],
)
def test_coerce_value_fits_the_storage_type(value, dtype, want):
    assert coerce_value(value, dtype, "c") == want


@pytest.mark.parametrize(
    ("value", "dtype"),
    [
        ("fast", dt.float64),
        (1.5, dt.int64),
        ("maybe", dt.boolean),
        (float("nan"), dt.float64),
        (float("inf"), dt.float64),
        ("-inf", dt.float64),
        (float("inf"), dt.int64),
        (True, dt.int64),
        (False, dt.float64),
        (128, dt.int8),
        ("-1", dt.uint16),
        (2**63, dt.int64),
    ],
)
def test_coerce_value_refuses_what_the_column_cannot_hold(value, dtype):
    with pytest.raises(EditRefused, match="does not fit"):
        coerce_value(value, dtype, "c")


def test_editable_storage_types():
    assert all(editable(t) for t in (dt.float64, dt.int64, dt.string, dt.boolean))
    assert not any(editable(t) for t in (dt.binary, dt.date, dt.time, dt.timestamp))


def test_an_update_is_one_edit_and_reverses(net):
    plan = plan_update(net, "link", [1], {"free_speed": "30"})
    assert plan.summary == "link 1: free_speed = 30.0" and plan.rows == {"link": 1} and len(plan.edits) == 1
    (result,) = apply(net, plan)
    assert cell(net, "link", 1, "free_speed") == 30.0
    reverse_edit(net, result)
    assert cell(net, "link", 1, "free_speed") == 40.0


def test_none_clears_a_cell(net):
    apply(net, plan_update(net, "link", [1], {"name": None}))
    assert cell(net, "link", 1, "name") is None


def test_ids_arrive_as_text_or_numbers(net):
    assert plan_update(net, "link", ["2", 2], {"lanes": 2.0}).rows == {"link": 1}


@pytest.mark.parametrize(
    ("table", "ids", "values", "message"),
    [
        ("link", [1], {"free_speed": "fast"}, "does not fit"),
        ("link", [1], {"lanes": 1.5}, "does not fit"),  # corral would widen the column to float
        ("link", [999999], {"lanes": 2}, "not found"),
        ("link", [1], {"link_id": 2}, "key"),
        ("link", [1], {"colour": "red"}, "no column"),  # corral would add the column
        ("nope", [1], {"x": 1}, "no nope table"),
        ("link", [], {"lanes": 2}, "at least one row"),
        ("link", [1], {}, "at least one column"),
    ],
)
def test_bad_updates_are_refused(net, table, ids, values, message):
    with pytest.raises(EditRefused, match=message):
        plan_update(net, table, ids, values)


def test_a_spec_problem_is_planned_not_refused(net):
    plan_update(net, "node", [1], {"ctrl_type": "bogus"})  # outside the enum: a live warning (Task 4)
    plan_update(net, "link", [1], {"from_node_id": 424242})  # not a node: a live warning too


def test_deleting_a_link_takes_its_lanes_and_reverses(net):
    before = counts(net)
    plan = plan_delete(net, "link", [1])
    assert plan.rows == {"lane": 1, "link": 1} and [e.table for e in plan.edits] == ["lane", "link"]
    assert plan.summary == "delete link 1 (and 1 lane rows that depend on it)"
    results = apply(net, plan)
    after = counts(net)
    assert (after["link"], after["lane"]) == (before["link"] - 1, before["lane"] - 1)
    for result in reversed(results):
        reverse_edit(net, result)
    assert counts(net) == before


def test_a_node_a_link_uses_is_not_deleted(net):
    with pytest.raises(EditRefused, match="still used by link"):
        plan_delete(net, "node", [1])


def test_missing_rows_are_not_deleted(net):
    with pytest.raises(EditRefused, match="not found"):
        plan_delete(net, "link", [999999])


def test_adding_rows(net):
    plan = plan_add(net, "node", [{"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}])
    assert plan.rows == {"node": 1} and plan.summary == "add node 9001"
    apply(net, plan)
    assert cell(net, "node", 9001, "x_coord") == -120.66 and cell(net, "node", 9001, "name") is None


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "at least one row"),
        ([{"link_id": 9001, "colour": "red"}], "no column"),
        ([{"name": "x"}], "needs its key"),
        ([{"link_id": " ", "name": "x"}], "needs its key"),
        ([{"link_id": "", "name": "x"}], "needs its key"),
    ],
)
def test_bad_additions_are_refused(net, rows, message):
    with pytest.raises(EditRefused, match=message):
        plan_add(net, "link", rows)


def test_a_duplicate_key_is_added_not_refused(net):
    apply(net, plan_add(net, "link", [{"link_id": 1, "from_node_id": 1, "to_node_id": 2, "directed": False}]))
    assert net.tables["link"].count() == 340  # the live check reports edit.duplicate_key (Task 4)


def test_a_blank_text_key_is_refused(net):
    link = net.tables["link"]
    link.expr = link.expr.mutate(link_id=link.expr.link_id.cast("string"))  # a text key keeps " " as text
    for blank in (" ", ""):
        with pytest.raises(EditRefused, match="needs its key"):
            plan_add(net, "link", [{"link_id": blank}])
    assert plan_add(net, "link", [{"link_id": "a1"}]).keys == ["a1"]


# ---------------------------------------------------------------- cascades through synthetic foreign keys


def _with_fks(monkeypatch, *extra):
    """Leavenworth's foreign keys plus ``extra`` ``(table, column, ref_table, ref_column)`` ones."""
    from netstead.workbench import editing
    from netstead.workbench.related import ForeignKey

    real = editing.foreign_keys

    def fks(spec, columns, self_refs=False):
        return [*real(spec, columns, self_refs=self_refs), *(ForeignKey(*f) for f in extra)]

    monkeypatch.setattr(editing, "foreign_keys", fks)


def test_a_multi_level_cascade_goes_deepest_first(net, monkeypatch):
    _with_fks(monkeypatch, ("link_tod", "link_tod_id", "lane", "lane_id"))  # link -> lane 1 -> link_tod 1
    plan = plan_delete(net, "link", [1])
    assert plan.rows == {"link_tod": 1, "lane": 1, "link": 1}
    assert [e.table for e in plan.edits] == ["link_tod", "lane", "link"]
    before = counts(net)
    results = apply(net, plan)
    assert counts(net)["link_tod"] == before["link_tod"] - 1
    for result in reversed(results):
        reverse_edit(net, result)
    assert counts(net) == before


def test_a_row_reached_through_two_foreign_keys_counts_once(net, monkeypatch):
    _with_fks(monkeypatch, ("lane", "lane_id", "link", "link_id"))  # lane 1 points at link 1 twice
    plan = plan_delete(net, "link", [1])
    assert plan.rows == {"lane": 1, "link": 1} and plan.summary == "delete link 1 (and 1 lane rows that depend on it)"


def test_a_diamond_counts_each_row_once(net, monkeypatch):
    # link 1 and 3 -> lanes 1 and 3 -> link_tod 1 (by link_tod_id); and link 3 -> link_tod 1 (by link_id).
    _with_fks(monkeypatch, ("link_tod", "link_tod_id", "lane", "lane_id"))
    lanes = net.tables["lane"].to_pandas()
    assert 1 in set(lanes.loc[lanes.link_id.isin([1, 3]), "lane_id"])  # so link_tod 1 is reached both ways
    plan = plan_delete(net, "link", [1, 3])
    assert plan.rows == {"link_tod": 1, "lane": int(lanes.link_id.isin([1, 3]).sum()), "link": 2}
    before = counts(net)
    apply(net, plan)
    assert counts(net)["link_tod"] == before["link_tod"] - 1


def test_rows_left_at_a_cycle_are_rechecked(net, monkeypatch):
    # lane -> link_tod (link_tod_id = lane_id) -> lane (lane_num = link_tod_id): a cycle through lane.
    _with_fks(
        monkeypatch, ("link_tod", "link_tod_id", "lane", "lane_id"), ("lane", "lane_num", "link_tod", "link_tod_id")
    )
    plan = plan_delete(net, "link", [1])
    assert plan.rows == {"link_tod": 1, "lane": 1, "link": 1}
    lanes = net.tables["lane"].to_pandas()
    pointing = set(lanes.loc[lanes.lane_num == 1, "lane_id"]) - {1}  # lanes whose lane_num named link_tod 1
    assert pointing and set(plan.recheck["lane"]) == pointing and 1 not in plan.recheck["lane"]
