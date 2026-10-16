"""Filter: a recorded, per-network predicate over link fields; ids computed per version."""

import json
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.viz.tables import FILTER_OPS
from netstead.workbench import Session, build_app
from netstead.workbench.actions import CloseNetwork, Filter, FilterCond, FilterOp, OpenNetwork, to_python
from netstead.workbench.editing import plan_update
from netstead.workbench.errors import ActionError

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
    with pytest.raises(ActionError, match="does not fit its column"):
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
