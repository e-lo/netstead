"""Filter: a recorded, per-network predicate over link fields; ids computed per version."""

import json
from typing import Literal, get_args

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.viz.tables import FILTER_OPS
from netstead.workbench import Session, build_app
from netstead.workbench.actions import BaseAction, CloseNetwork, Filter, FilterCond, FilterOp, OpenNetwork, to_python
from netstead.workbench.editing import plan_update
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def session(tmp_path):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([SRC, str(tmp_path)])}
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def test_the_operator_set_is_the_tables_one():
    assert set(get_args(FilterOp)) == set(FILTER_OPS)


def test_filter_records_counts_and_clears(session):
    out = session.dispatch(Filter(where=[FilterCond(col="lanes", op="gte", val=2)]))
    links = session.network().links_df()
    assert out["count"] == int((links["lanes"] >= 2).sum()) > 0
    assert session.state()["filters"]["leavenworth"]["count"] == out["count"]
    session.dispatch(Filter(where=None))
    assert session.state()["filters"] == {}


def test_unknown_columns_and_bad_values_are_refused(session):
    with pytest.raises(ActionError, match="unknown column 'nope'"):
        session.dispatch(Filter(where=[FilterCond(col="nope", op="eq", val=1)]))
    with pytest.raises(ActionError, match="cannot filter 'lanes' by gt: 'many' is not a number"):
        session.dispatch(Filter(where=[FilterCond(col="lanes", op="gt", val="many")]))
    assert session.state()["filters"] == {}  # a refused Filter stores nothing
    with pytest.raises(ValueError):
        FilterCond(col="lanes", op="like", val="%")  # not an operator: never free-form


def test_the_ids_follow_an_edit(session):
    session.dispatch(Filter(where=[FilterCond(col="lanes", op="eq", val=7)]))
    assert session.filter_ids("leavenworth") == []
    plan = plan_update(session.network().roadway, "link", [1], {"lanes": 7})
    session.mutate("leavenworth", plan.edits, note="seven")
    assert session.filter_ids("leavenworth") == [1]


def test_closing_the_network_drops_its_filter(session):
    session.dispatch(Filter(where=[FilterCond(col="lanes", op="eq", val=1)]))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    assert session.state()["filters"] == {} and session.filter_ids("leavenworth") is None


def test_the_route_and_the_snippet(session):
    client = TestClient(build_app(session))
    assert client.get("/api/n/leavenworth/roadway/filter").json()["where"] is None
    session.dispatch(Filter(where=[FilterCond(col="lanes", op="in", val=[1, 2])]))
    j = client.get("/api/n/leavenworth/roadway/filter").json()
    assert j["count"] == len(j["link_ids"]) > 0 and j["where"][0]["op"] == "in"
    assert to_python(Filter(where=[FilterCond(col="lanes", op="eq", val=2)])) == (
        "app.do(Filter(where=[{'col': 'lanes', 'op': 'eq', 'val': 2}]))"
    )


def test_a_value_is_read_as_its_columns_type(session):
    as_text = session.dispatch(Filter(where=[FilterCond(col="lanes", op="gte", val="2")]))
    assert as_text["where"][0]["val"] == 2  # stored typed, so the table's Filtered scope compares numbers
    as_number = session.dispatch(Filter(where=[FilterCond(col="lanes", op="gte", val=2)]))
    assert as_text["count"] == as_number["count"] > 0
    names = session.dispatch(Filter(where=[FilterCond(col="name", op="in", val=["Cherry Street", 7])]))
    assert names["where"][0]["val"] == ["Cherry Street", "7"] and names["count"] > 0


def test_a_condition_takes_the_value_its_operator_needs():
    with pytest.raises(ValueError, match="only 'in' takes a list"):
        FilterCond(col="lanes", op="eq", val=[1, 2])
    with pytest.raises(ValueError, match="'isnull' takes no value"):
        FilterCond(col="lanes", op="isnull", val=1)
    with pytest.raises(ValueError, match="use 'isnull'"):
        FilterCond(col="lanes", op="eq")
    assert FilterCond(col="lanes", op="notnull").val is None and FilterCond(col="lanes", op="in", val=[1]).val == [1]


def test_contains_is_a_plain_substring_never_a_regex(session):
    bad = session.dispatch(Filter(where=[FilterCond(col="name", op="contains", val="(")]))
    assert bad["count"] == 0  # an unbalanced bracket is just a character
    slow = session.dispatch(Filter(where=[FilterCond(col="name", op="contains", val="(a+)+$")]))
    assert slow["count"] == 0  # a catastrophic pattern is never run as one
    assert session.dispatch(Filter(where=[FilterCond(col="name", op="contains", val="cherry")]))["count"] > 0


def test_the_filter_cache_is_bounded_per_version(session):
    from netstead.workbench.session import FILTER_CACHE_SIZE

    for n in range(FILTER_CACHE_SIZE + 5):  # a slider dragged across many values
        session.dispatch(Filter(where=[FilterCond(col="lanes", op="gte", val=n)]))
    recent = session.network().cached("filter-ids", dict)
    assert len(recent) == FILTER_CACHE_SIZE
    assert session.filter_ids("leavenworth") == []  # the stored (last) filter is still answered


class FilterThenFail(BaseAction):
    type: Literal["probe.filter_and_fail"] = "probe.filter_and_fail"


def _filter_then_fail(host, action):
    host.dispatch(Filter(where=[FilterCond(col="lanes", op="gte", val=2)]))
    raise RuntimeError("boom")


def test_a_failed_action_rolls_back_the_filter_it_set(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(FilterThenFail, _filter_then_fail),),
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(Filter(where=[FilterCond(col="lanes", op="eq", val=1)]))
    assert not s.dispatch_recorded(FilterThenFail()).ok
    assert s.filter_entry("leavenworth")["where"] == [{"col": "lanes", "op": "eq", "val": 1}]
