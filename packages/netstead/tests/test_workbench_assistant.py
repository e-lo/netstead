"""The assistant: the echo, the stub planner, asking, drafts, approval, budgets, and what is recorded."""

from types import SimpleNamespace

import pytest
from netstead.workbench.actions import (
    DeleteRows,
    EditCells,
    Filter,
    FilterCond,
    Navigate,
    PinAnchor,
    SaveSelection,
    Select,
    SetSetting,
    Style,
)
from netstead.workbench.assistant.echo import describe, model_echo, result_detail, result_text
from netstead.workbench.assistant.stub import StubPlanner


def test_describe_is_plain_words():
    assert describe(Style(color_by="lanes")) == "colour links by lanes"
    assert describe(Style(color_by="lanes", width_by="none")) == "colour links by lanes and size links by lane count"
    assert describe(Filter(where=[FilterCond(col="lanes", op="gte", val=2)])) == "show only links where lanes ≥ 2"
    assert describe(Filter(where=[FilterCond(col="name", op="isnull")])) == "show only links where name is empty"
    assert describe(Filter()) == "clear the link filter"
    assert describe(Navigate(record={"table": "link", "id": 5})) == "fly to link 5"
    assert describe(Navigate(to_selection=True)) == "zoom to the selection"
    assert describe(EditCells(ids=[1, 2], values={"lanes": 3})) == "set lanes = 3 on 2 links"
    assert describe(DeleteRows(ids=[1])) == "delete 1 link (and the rows that depend on them)"
    assert describe(Select(utterance="Ski Hill", query={"select_all": True})) == "select “Ski Hill”"
    assert describe(Select(link_ids=[1, 2, 3])) == "select 3 links"
    assert describe(PinAnchor(role="to", node_id=60)) == "pin the to anchor to node 60"
    assert describe(PinAnchor(role="from")) == "unpin the from anchor"
    assert describe(SaveSelection(name="cherry")) == "save the selection “cherry”"
    assert describe(SetSetting(key="viz.basemap", value="esri")) == "set the setting viz.basemap"


def _entry(action_type, result, ok=True, error=None):
    return SimpleNamespace(action={"type": action_type}, result=result, ok=ok, error=error)


def test_result_lines_for_the_user():
    assert result_text(_entry("select", {"status": "resolved", "link_ids": [1, 2]})) == "resolved: 2 links"
    assert result_text(_entry("select", {"status": "ambiguous", "link_ids": [1]})).startswith("ambiguous: 1 link;")
    assert result_text(_entry("select", {"status": "not_found", "link_ids": []})) == "not found: 0 links"
    assert result_text(_entry("filter", {"count": 7, "where": []})) == "7 links match"
    assert result_text(_entry("style", {"color_by": "lanes"})) == "done"
    assert result_text(_entry("style", None, ok=False, error="no column x")) == "failed: no column x"
    assert result_detail(_entry("select", None, ok=False, error="boom")) == {}


def test_the_model_echo_carries_no_values():
    steps = [
        {"type": "select", "status": "done", "detail": {"status": "resolved", "links": 20}},
        {"type": "edit_cells", "status": "drafted", "detail": {}},
    ]
    assert model_echo(steps) == "done: select (resolved, 20 links); drafted for approval: edit_cells"
    failed = [{"type": "style", "status": "failed", "detail": {}, "error": "no column 'secret_col'"}]
    assert model_echo(failed) == "failed: style"  # an error message never goes back to the model


@pytest.mark.parametrize(
    ("text", "calls"),
    [
        (
            "select Ski Hill Drive",
            [("select", {"utterance": "Ski Hill Drive", "facility": {"name": "Ski Hill Drive"}})],
        ),
        ("colour by lanes", [("style", {"color_by": "lanes"})]),
        ("Color by lanes.", [("style", {"color_by": "lanes"})]),
        ("width by lanes", [("style", {"width_by": "lanes"})]),
        ("filter lanes >= 2", [("filter", {"where": [{"col": "lanes", "op": "gte", "val": 2}]})]),
        (
            "filter facility_type = primary",
            [("filter", {"where": [{"col": "facility_type", "op": "eq", "val": "primary"}]})],
        ),
        ("clear the filter", [("filter", {})]),
        ("zoom to the selection", [("navigate", {"to_selection": True})]),
        ("zoom to link 170", [("navigate", {"record": {"table": "link", "id": 170}})]),
        ("save the selection as cherry", [("save_selection", {"name": "cherry"})]),
        ("set lanes to 3 on the selection", [("edit_cells", {"on_selection": True, "values": {"lanes": 3}})]),
        (
            "colour by lanes and zoom to the selection",
            [("style", {"color_by": "lanes"}), ("navigate", {"to_selection": True})],
        ),
        (
            "select Cherry Street between Prospect Street and West Center Street then zoom to the selection",
            [
                (
                    "select",
                    {
                        "utterance": "Cherry Street between Prospect Street and West Center Street",
                        "facility": {"name": "Cherry Street"},
                        "from_anchor": "Prospect Street",
                        "to_anchor": "West Center Street",
                    },
                ),
                ("navigate", {"to_selection": True}),
            ],
        ),
        (
            "select Lewis and Clark Way and colour by lanes",
            [
                ("select", {"utterance": "Lewis and Clark Way", "facility": {"name": "Lewis and Clark Way"}}),
                ("style", {"color_by": "lanes"}),
            ],
        ),
    ],
)
def test_the_stub_planner_grammar(text, calls):
    plan = StubPlanner().plan(text)
    assert [(c.name, c.arguments) for c in plan.calls] == calls and plan.text == ""


@pytest.mark.parametrize("text", ["make me a sandwich", "", "colour by lanes and make me a sandwich"])
def test_the_stub_planner_says_what_it_understands(text):
    plan = StubPlanner().plan(text)
    assert plan.calls == () and "offline" in plan.text


def test_every_stub_call_fits_its_tool_and_becomes_an_action(tmp_path, isolated_env):
    """The stub's calls take the same path as a model's: the tool's schema, then ``to_action``."""
    from netstead.fixtures import leavenworth
    from netstead.llm.structured import _check_schema
    from netstead.select.parse import StubParser
    from netstead.workbench import Session
    from netstead.workbench.actions import OpenNetwork
    from netstead.workbench.assistant.tools import to_action, tool_specs

    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir())))
    session.dispatch(Select(link_ids=[170, 182]))
    specs = {s.tool.name: s for s in tool_specs(session)}
    text = (
        "select Cherry Street between Prospect Street and West Center Street and colour by lanes and width by lanes "
        "and filter lanes >= 2 and clear the filter and zoom to link 170 and zoom to the selection "
        "and save the selection as cherry and set lanes to 3 on the selection"
    )
    calls = StubPlanner().plan(text).calls
    assert len(calls) == 9
    actions = []
    for call in calls:
        _check_schema(call.arguments, specs[call.name].tool.input_schema)
        actions.append(to_action(session, specs[call.name], call.arguments))
    assert actions[0].query["to_anchor"] == "West Center Street" and actions[-1].ids == [170, 182]
    assert [a.type for a in actions] == [
        "select", "style", "style", "filter", "filter", "navigate", "navigate", "save_selection", "edit_cells"
    ]  # fmt: skip
