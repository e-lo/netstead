"""The assistant's tools: the allowlist, the schemas the model sees, and turning a call back into an Action."""

import copy
from typing import ClassVar, Literal

import pytest
from netstead.config import DEFAULT_ASSISTANT_ACTIONS
from netstead.fixtures import leavenworth
from netstead.select.parse import INTENT_TOOL, StubParser
from netstead.workbench import Session
from netstead.workbench.actions import BaseAction, OpenNetwork, Select
from netstead.workbench.assistant.tools import NEVER, action_type_of, lenient_arguments, to_action, tool_specs
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


class Shout(BaseAction):
    """Shout some text."""

    type: Literal["loud.shout"] = "loud.shout"
    text: str
    title: str | None = None


class Wipe(BaseAction):
    """Wipe everything."""

    type: Literal["loud.wipe"] = "loud.wipe"
    mutates: ClassVar[bool] = True


class Secret(BaseAction):
    """Never from natural language."""

    type: Literal["loud.secret"] = "loud.secret"
    assistant: ClassVar[bool] = False


def _loud():
    return WorkbenchPlugin(
        id="loud",
        name="Loud",
        version="0",
        requires_api=HOST_API,
        actions=tuple(ActionSpec(m, lambda host, a: None) for m in (Shout, Wipe, Secret)),
    )


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[_loud()])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _by_type(session):
    return {s.action_type: s for s in tool_specs(session)}


def _allow(session, *extra):
    session.dispatch({"type": "set_setting", "key": "assistant.actions", "value": [*DEFAULT_ASSISTANT_ACTIONS, *extra]})


def test_the_default_allowlist(session):
    specs = _by_type(session)
    assert set(specs) == set(DEFAULT_ASSISTANT_ACTIONS)  # no plugin until opted in; never open/close
    assert specs["edit_cells"].mutates and not specs["style"].mutates and specs["style"].plugin is None
    drafted = {t for t, s in specs.items() if s.mutates}
    assert drafted == {"edit_cells", "delete_rows", "undo_edit", "save_network"}


def test_opt_ins_are_drafted_and_open_close_are_never_offered(session, tmp_path, isolated_env):
    _allow(session, "add_rows", "set_setting", "build_network", "delete_selection")
    specs = _by_type(session)
    assert {"add_rows", "set_setting", "build_network", "delete_selection"} <= set(specs)
    assert all(specs[t].mutates for t in ("add_rows", "set_setting", "build_network", "delete_selection"))
    with pytest.raises(ActionError, match="never offered"):  # refused when set
        _allow(session, *NEVER)
    listed = {"assistant.actions": [*DEFAULT_ASSISTANT_ACTIONS, *NEVER]}  # from a file: warned about, never offered
    other = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[], overrides=listed)
    assert not NEVER & {s.action_type for s in tool_specs(other)}


def test_a_plugin_opt_in_respects_assistant_false(session):
    session.dispatch({"type": "set_setting", "key": "assistant.plugins", "value": ["loud"]})
    specs = _by_type(session)
    assert {"loud.shout", "loud.wipe"} <= set(specs) and "loud.secret" not in specs
    assert specs["loud.shout"].tool.name == "loud__shout" and specs["loud.wipe"].mutates
    assert specs["loud.shout"].plugin == "loud" and action_type_of("loud__shout") == "loud.shout"
    assert specs["loud.shout"].tool.description == "Shout some text."
    assert "title" in specs["loud.shout"].tool.input_schema["properties"]  # a field named title is kept


def test_schemas_are_flat_and_hide_what_the_model_must_not_set(session):
    _allow(session, "build_network")
    specs = _by_type(session)
    style = specs["style"].tool.input_schema
    assert "$defs" not in str(style) and "$ref" not in str(style) and "type" not in style["properties"]
    assert "'title'" not in str(style) and style["additionalProperties"] is False
    assert "expected_version" not in specs["delete_rows"].tool.input_schema["properties"]
    assert "accept_warnings" not in specs["save_network"].tool.input_schema["properties"]
    build = specs["build_network"].tool.input_schema
    assert "approved" not in build["properties"] and "$ref" not in str(build)
    assert "(drafted for the user to approve)" in specs["edit_cells"].tool.description
    assert "drafted" not in specs["style"].tool.description
    edit = specs["edit_cells"].tool.input_schema
    assert "on_selection" in edit["properties"] and "ids" not in edit["required"]
    assert "required" not in specs["clear_selection"].tool.input_schema


def test_the_select_tool_is_the_parsers_schema_plus_an_utterance(session):
    before = copy.deepcopy(INTENT_TOOL)
    schema = _by_type(session)["select"].tool.input_schema
    assert schema["required"] == ["utterance"]
    rest = {k: v for k, v in schema["properties"].items() if k != "utterance"}
    assert rest == INTENT_TOOL["input_schema"]["properties"]
    assert schema["additionalProperties"] is False and before == INTENT_TOOL  # never mutated


def test_a_select_call_becomes_a_select_with_a_query(session):
    spec = _by_type(session)["select"]
    action = to_action(session, spec, {"utterance": "Ski Hill", "facility": {"name": "Ski Hill Drive"}})
    assert action == Select(utterance="Ski Hill", query={"facility": {"name": "Ski Hill Drive"}})
    with pytest.raises(ValueError, match="selection requires one of"):
        to_action(session, spec, {"utterance": "x"})
    with pytest.raises(ValueError, match="needs a short utterance"):
        to_action(session, spec, {"facility": {"name": "Ski Hill Drive"}})
    with pytest.raises(ValueError, match="wrong shape"):
        to_action(session, spec, {"utterance": "x", "facility": "Ski Hill Drive"})


def test_on_selection_fills_the_ids_and_never_invents_them(session):
    spec = _by_type(session)["edit_cells"]
    with pytest.raises(ValueError, match="nothing is selected"):
        to_action(session, spec, {"on_selection": True, "values": {"lanes": 2}})
    session.dispatch(Select(link_ids=[1, 2]))
    action = to_action(session, spec, {"on_selection": True, "values": {"lanes": 2}})
    assert action.ids == [1, 2] and action.table == "link"
    with pytest.raises(ValueError, match="ids or on_selection"):
        to_action(session, spec, {"on_selection": True, "ids": [3], "values": {"lanes": 2}})
    with pytest.raises(ValueError, match="link table only"):
        to_action(session, spec, {"on_selection": True, "table": "node", "values": {"zone_id": 2}})
    with pytest.raises(ValueError, match="no open network is called 'other'"):  # repaired, never drafted
        to_action(session, spec, {"on_selection": True, "net_id": "other", "values": {"lanes": 2}})
    with pytest.raises(ValueError, match="no open network is called 'nope'"):
        to_action(session, _by_type(session)["delete_rows"], {"ids": [5], "net_id": "nope"})
    deleted = to_action(session, _by_type(session)["delete_rows"], {"on_selection": True})
    assert deleted.ids == [1, 2]


def test_protected_settings_are_refused(session):
    _allow(session, "set_setting")
    spec = _by_type(session)["set_setting"]
    with pytest.raises(ValueError, match=r"can.t change llm\.ollama\.base_url"):
        to_action(session, spec, {"key": "llm.ollama.base_url", "value": "http://evil.example"})
    protected = ("select.provider", "Assistant.actions", " app.host", "io.allowed_roots", "credentials", "plugins.x")
    # Endpoints that would receive a bbox or the viewport: the basemap, Overpass/Nominatim, Overture's release.
    protected += ("viz.basemap", "osm.overpass_url", "overture.release")
    for key in protected:
        with pytest.raises(ValueError, match="protected setting"):
            to_action(session, spec, {"key": key, "value": "x"})
    assert to_action(session, spec, {"key": "build.buffer_m", "value": 500}).key == "build.buffer_m"


def test_a_protected_key_shaped_like_a_secret_is_never_echoed(session):
    _allow(session, "set_setting")
    spec = _by_type(session)["set_setting"]
    key = "llm.sk-ant-api03-" + "A" * 40
    with pytest.raises(ValueError) as err:
        to_action(session, spec, {"key": key, "value": "x"})
    assert key not in str(err.value) and "sk-ant" not in str(err.value) and "llm.* settings" in str(err.value)


def test_select_cannot_pin_nodes(session):
    spec = _by_type(session)["select"]
    for pin in ({"from_node": 53}, {"to_node": 60}):
        with pytest.raises(ValueError, match="select can't pin nodes; use pin_anchor"):
            to_action(session, spec, {"utterance": "Cherry", "facility": {"name": "Cherry Street"}, **pin})


def test_invalid_arguments_become_a_value_error_without_the_input(session):
    with pytest.raises(ValueError) as err:
        to_action(session, _by_type(session)["style"], {"ramp": "sk-ant-not-a-ramp"})
    assert "sk-ant" not in str(err.value) and str(err.value).startswith("ramp: ")


def test_the_catalog_says_which_actions_may_be_offered(session):
    session.dispatch({"type": "set_setting", "key": "assistant.plugins", "value": ["loud"]})
    flags = {a["type"]: a["assistant"] for a in session.action_catalog()}
    assert flags["style"] and flags["edit_cells"] and flags["loud.shout"]
    assert not flags["open_network"] and not flags["close_network"] and not flags["loud.secret"]
    assert {t for t, ok in flags.items() if ok} == {s.action_type for s in tool_specs(session)} | {
        "add_rows",
        "set_setting",
        "build_network",
        "delete_selection",
        "redo_edit",
    }  # offerable = offered once allowed


def test_a_core_action_marked_not_for_the_assistant_is_flagged_in_the_allowlist(session, monkeypatch):
    from netstead.workbench.actions import Style
    from netstead.workbench.assistant.tools import allowlist_problems

    monkeypatch.setattr(Style, "assistant", False)
    assert allowlist_problems(session, ["style", "open_network"], []) == [
        "assistant.actions: 'style' is never offered to the assistant",
        "assistant.actions: 'open_network' is never offered to the assistant",
    ]
    assert "style" not in {s.action_type for s in tool_specs(session)}


#: Every mutating Action the assistant can be allowed: leniency never applies to any of them.
_MUTATING = ("edit_cells", "delete_rows", "add_rows", "save_network", "set_setting", "build_network", "undo_edit")


def test_leniency_never_touches_a_mutating_tool(session):
    _allow(session, "add_rows", "set_setting", "build_network", "delete_selection")
    specs = _by_type(session)
    for action_type in (*_MUTATING, "delete_selection"):
        spec = specs[action_type]
        assert spec.mutates
        args = {"invented": 1, "net_id": {"bad": "shape"}}
        assert lenient_arguments(spec, args) == (args, []), action_type  # unchanged: the repair sees it


def test_leniency_on_read_only_tools_drops_unknown_keys_and_only_style_drops_malformed_ones(session):
    specs = _by_type(session)
    assert lenient_arguments(specs["navigate"], {"to_selection": True, "zoom": 12}) == (
        {"to_selection": True},
        ["zoom (not a field of navigate)"],
    )
    assert lenient_arguments(specs["style"], {"color_by": "lanes", "show": {"lanes": True}}) == (
        {"color_by": "lanes"},
        ["show (not valid)"],
    )
    bad_where = {"where": "lanes >= 2"}  # filter: leaving where out would clear the filter, so it's repaired
    assert lenient_arguments(specs["filter"], bad_where) == (bad_where, [])
    assert lenient_arguments(specs["style"], {"glow": 1}) == ({"glow": 1}, [])  # nothing left: repaired instead
    assert lenient_arguments(specs["clear_selection"], {}) == ({}, [])


def test_leniency_applies_to_a_read_only_plugin_action_and_never_to_a_mutating_one(session):
    session.dispatch({"type": "set_setting", "key": "assistant.plugins", "value": ["loud"]})
    specs = _by_type(session)
    assert lenient_arguments(specs["loud.shout"], {"text": "hi", "volume": 11})[0] == {"text": "hi"}
    assert lenient_arguments(specs["loud.wipe"], {"volume": 11}) == ({"volume": 11}, [])


# ---------------------------------------------------------------------- the compact tool set (assistant.compact_tools)


def test_a_compact_tool_set_keeps_the_read_only_tools_that_apply_and_the_everyday_edits(session):
    _allow(session, "add_rows", "set_setting", "build_network", "delete_selection")
    full = {s.action_type for s in tool_specs(session)}
    compact = {s.action_type for s in tool_specs(session, compact=True)}
    assert compact < full
    assert full - compact == {
        "set_active_network",  # one network open
        "use_selection",  # nothing saved
        "pin_anchor",  # nothing selected
        "save_network",  # drafted, and not an everyday edit
        "add_rows",
        "set_setting",
        "build_network",
        "delete_selection",
    }
    assert {"edit_cells", "delete_rows", "undo_edit", "select", "style", "filter", "navigate"} <= compact


def test_the_compact_set_offers_a_tool_again_once_it_applies(session):
    session.dispatch(OpenNetwork(source=SRC))
    session.dispatch(Select(utterance="Ski Hill Drive"))
    session.dispatch({"type": "save_selection", "name": "ski"})
    compact = {s.action_type for s in tool_specs(session, compact=True)}
    assert {"set_active_network", "use_selection", "pin_anchor"} <= compact


def test_the_compact_set_never_offers_more_than_the_allowlist(session):
    session.dispatch({"type": "set_setting", "key": "assistant.actions", "value": ["style", "set_active_network"]})
    session.dispatch({"type": "set_setting", "key": "assistant.plugins", "value": ["loud"]})
    assert {s.action_type for s in tool_specs(session, compact=True)} == {"style", "loud.shout"}  # wipe: mutates


def test_the_tools_small_models_get_wrong_carry_one_example(session):
    specs = _by_type(session)
    assert '{"record": {"table": "link", "id": 170}}' in specs["navigate"].tool.description
    assert '{"color_by": "lanes"}' in specs["style"].tool.description
    assert "then save it as" in specs["save_selection"].tool.description
