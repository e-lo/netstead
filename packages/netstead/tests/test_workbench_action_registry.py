"""Tests for the per-session ActionRegistry (core Actions + plugin Actions)."""

from typing import Literal

import pytest
from netstead.workbench.actions import (
    CORE_ACTIONS,
    ActionRegistry,
    BaseAction,
    OpenNetwork,
    action_json_schema,
    import_line,
    parse_action,
)
from pydantic import ValidationError


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


class NoType(BaseAction):
    name: str


def test_action_type_reads_the_discriminator():
    assert Greet.action_type() == "hello.greet" and OpenNetwork.action_type() == "open_network"
    assert NoType.action_type() is None


def test_default_registry_holds_exactly_the_core_actions():
    reg = ActionRegistry()
    assert reg.types() == [m.action_type() for m in CORE_ACTIONS]
    assert isinstance(reg.parse({"type": "open_network", "source": "/x"}), OpenNetwork)


def test_registering_a_plugin_action_extends_parse_and_schema():
    reg = ActionRegistry()
    reg.register(Greet)
    assert reg.has("hello.greet") and reg.model("hello.greet") is Greet
    assert reg.parse({"type": "hello.greet", "name": "Ada"}) == Greet(name="Ada")
    assert "Greet" in reg.json_schema()["$defs"]


def test_registry_rejects_duplicates_and_non_actions():
    reg = ActionRegistry()
    with pytest.raises(ValueError, match="already registered"):
        reg.register(OpenNetwork)
    with pytest.raises(ValueError, match="type"):
        reg.register(NoType)
    with pytest.raises(TypeError):
        reg.register(dict)  # type: ignore[arg-type]


def test_registries_are_independent():
    a, b = ActionRegistry(), ActionRegistry()
    a.register(Greet)
    assert not b.has("hello.greet")
    with pytest.raises(ValidationError):
        b.parse({"type": "hello.greet", "name": "Ada"})


def test_module_level_parse_and_schema_stay_core_only():
    with pytest.raises(ValidationError):
        parse_action({"type": "hello.greet", "name": "Ada"})
    assert "Greet" not in action_json_schema().get("$defs", {})


def test_import_line_uses_the_public_module_for_core_and_the_class_module_otherwise():
    assert import_line(OpenNetwork(source="/x")) == "from netstead.workbench import OpenNetwork"
    assert import_line(Greet(name="Ada")) == f"from {Greet.__module__} import Greet"


def test_script_imports_keep_the_core_line_and_add_each_plugin_line_once():
    from netstead.workbench.actions import script_imports

    core = (
        "from netstead.workbench import Session, OpenNetwork, BuildNetwork, CloseNetwork, SetActiveNetwork,"
        " Select, ClearSelection, Style, Navigate, SetSetting"
    )
    greet = import_line(Greet(name="Ada"))
    assert script_imports([]) == [core]
    assert script_imports([import_line(OpenNetwork(source="/x")), greet, greet]) == [core, greet]
    from netstead.workbench.actions import EditCells, UndoEdit

    edit, undo = import_line(EditCells(ids=[1], values={"lanes": 2})), import_line(UndoEdit())
    assert edit == "from netstead.workbench import EditCells"
    assert script_imports([edit, greet, undo, edit]) == [core, edit, greet, undo]  # the header line is unchanged


class StrType(BaseAction):
    type: str = "hello.str"


class TwoTypes(BaseAction):
    type: Literal["hello.a", "hello.b"] = "hello.a"


class WrongDefault(BaseAction):
    type: Literal["hello.x"] = "hello.y"  # type: ignore[assignment]


@pytest.mark.parametrize("model", [StrType, TwoTypes, WrongDefault])
def test_only_an_exact_single_literal_type_registers(model):
    """A ``type: str`` or multi-value ``Literal`` would break every later parse (core Actions too)."""
    assert model.action_type() is None
    reg = ActionRegistry()
    with pytest.raises(ValueError, match="one value"):
        reg.register(model)
    assert reg.types() == [m.action_type() for m in CORE_ACTIONS]
    assert isinstance(reg.parse({"type": "open_network", "source": "/x"}), OpenNetwork)


def test_a_model_pydantic_refuses_leaves_the_registry_unchanged(monkeypatch):
    reg = ActionRegistry()
    monkeypatch.setattr(reg, "_build_adapter", lambda: (_ for _ in ()).throw(TypeError("bad union")))
    with pytest.raises(ValueError, match="bad union"):
        reg.register(Greet)
    assert not reg.has("hello.greet")
    monkeypatch.undo()
    assert isinstance(reg.parse({"type": "open_network", "source": "/x"}), OpenNetwork)


def test_registries_with_one_or_no_models():
    one = ActionRegistry([Greet])
    assert one.parse({"type": "hello.greet", "name": "Ada"}) == Greet(name="Ada")
    assert "name" in one.json_schema()["properties"]
    one.unregister("hello.greet")
    with pytest.raises(ValueError, match="no Action types"):
        one.parse({"type": "hello.greet", "name": "Ada"})
    with pytest.raises(ValueError, match="no Action types"):
        ActionRegistry([]).json_schema()


def test_unregister_removes_the_type_from_parsing():
    reg = ActionRegistry()
    reg.register(Greet)
    reg.unregister("hello.greet")
    assert not reg.has("hello.greet")
    with pytest.raises(ValidationError):
        reg.parse({"type": "hello.greet", "name": "Ada"})


def test_action_union_and_core_actions_hold_the_same_models_in_order():
    from typing import get_args

    from netstead.workbench.actions import Action

    union = get_args(Action)[0]
    assert get_args(union) == CORE_ACTIONS
