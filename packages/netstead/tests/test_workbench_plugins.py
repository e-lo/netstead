"""Tests for Workbench plugins: spec checks, discovery, install, Host, settings, state."""

from __future__ import annotations

import json
from typing import Any, ClassVar, Literal

import pytest
from netstead.workbench.actions import ActionRegistry, BaseAction
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin
from netstead.workbench.plugins.spec import api_compatible, problems
from pydantic import BaseModel, ConfigDict


class Greet(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"
    name: str


class HelloSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prefix: str = "Hello"


def make_hello(**overrides: Any) -> WorkbenchPlugin:
    """A fresh ``hello`` plugin: ``hello.greet`` returns ``"<prefix>, <name>!"`` and remembers names in state."""
    greeted: list[str] = []

    def greet(host, action: Greet) -> str:
        greeted.append(action.name)
        return f"{host.settings.prefix}, {action.name}!"

    fields: dict[str, Any] = {
        "id": "hello",
        "name": "Hello",
        "version": "0.1",
        "requires_api": HOST_API,
        "actions": (ActionSpec(Greet, greet),),
        "settings_model": HelloSettings,
        "state": lambda host: {"greeted": list(greeted)},
    }
    fields.update(overrides)
    return WorkbenchPlugin(**fields)


# ---------------------------------------------------------------------------- spec


def test_api_compatible_same_major_and_new_enough_minor():
    assert api_compatible("1.0", "1.2") and api_compatible("1.2", "1.2")
    assert not api_compatible("1.3", "1.2") and not api_compatible("2.0", "1.2")
    assert not api_compatible("one", "1.0") and not api_compatible("1", "1.0")


def test_problems_none_for_a_good_plugin():
    assert problems(make_hello(), ActionRegistry(), taken=()) == []


class Unprefixed(BaseAction):
    type: Literal["greet"] = "greet"


class JobAction(BaseAction):
    type: Literal["hello.slow"] = "hello.slow"
    runs_as_job: ClassVar[bool] = True


@pytest.mark.parametrize(
    ("overrides", "taken", "match"),
    [
        ({"id": "Hello-World"}, (), "must match"),
        ({}, ("hello",), "already uses"),
        ({"actions": (ActionSpec(Unprefixed, lambda h, a: None),)}, (), "must start with 'hello.'"),
        ({"actions": (ActionSpec(JobAction, lambda h, a: None),)}, (), "job actions"),
        ({"actions": (ActionSpec(dict, lambda h, a: None),)}, (), "not a BaseAction"),  # type: ignore[arg-type]
    ],
)
def test_problems_reports_each_violation(overrides, taken, match):
    found = problems(make_hello(**overrides), ActionRegistry(), taken=taken)
    assert any(match in p for p in found), found


class NamedSelect(BaseAction):
    type: Literal["hello.select"] = "hello.select"


class NamedSession(BaseAction):
    type: Literal["hello.session"] = "hello.session"


# Named like the core ``Select`` and the ``Session`` a replayed script constructs: importing either would rebind it.
NamedSelect.__name__ = NamedSelect.__qualname__ = "Select"
NamedSession.__name__ = NamedSession.__qualname__ = "Session"


class EmptySuffix(BaseAction):
    type: Literal["hello."] = "hello."


class Greet2(BaseAction):
    type: Literal["hello.greet"] = "hello.greet"


def _nested_action() -> type[BaseAction]:
    class Nested(BaseAction):
        type: Literal["hello.nested"] = "hello.nested"

    return Nested


def _main_action() -> type[BaseAction]:
    class Main(BaseAction):
        type: Literal["hello.main"] = "hello.main"

    Main.__qualname__, Main.__module__ = "Main", "__main__"
    return Main


class StrTyped(BaseAction):
    type: str = "hello.str"


class MultiTyped(BaseAction):
    type: Literal["hello.a", "hello.b"] = "hello.a"


def _spec(model: Any) -> ActionSpec:
    return ActionSpec(model, lambda h, a: None)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"id": "hello\n"}, "must match"),
        ({"actions": (_spec(EmptySuffix),)}, "then name the action"),
        ({"actions": (_spec(Greet), _spec(Greet))}, "declared twice"),
        ({"actions": (_spec(Greet), _spec(Greet2))}, "declared twice"),
        ({"actions": (_spec(NamedSelect),)}, "class name 'Select'"),
        ({"actions": (_spec(NamedSession),)}, "class name 'Session'"),
        ({"actions": (_spec(_nested_action()),)}, "top level of an importable module"),
        ({"actions": (_spec(_main_action()),)}, "top level of an importable module"),
        ({"actions": (_spec(StrTyped),)}, "one value"),
        ({"actions": (_spec(MultiTyped),)}, "one value"),
    ],
)
def test_problems_reports_what_would_break_parsing_or_replay(overrides, match):
    found = problems(make_hello(**overrides), ActionRegistry(), taken=())
    assert any(match in p for p in found), found


class OtherGreet(BaseAction):
    type: Literal["other.greet"] = "other.greet"


OtherGreet.__name__ = OtherGreet.__qualname__ = "Greet"  # another plugin's class with the same name


def test_problems_reports_a_class_name_another_plugin_registered():
    registry = ActionRegistry()
    registry.register(Greet)
    other = WorkbenchPlugin(id="other", name="O", version="0", requires_api=HOST_API, actions=(_spec(OtherGreet),))
    assert any("class name 'Greet'" in p for p in problems(other, registry, taken=()))


def test_problems_reports_a_type_already_registered():
    registry = ActionRegistry()
    registry.register(Greet)
    assert any("already registered" in p for p in problems(make_hello(), registry, taken=()))


# ---------------------------------------------------------------------------- discovery

from dataclasses import dataclass  # noqa: E402

from netstead.workbench.plugins.discovery import PluginStatus, discover  # noqa: E402


@dataclass
class FakeEntryPoint:
    name: str
    target: Any  # the factory, or an exception ``load`` raises

    def load(self) -> Any:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target


def test_discover_loads_factories_and_isolates_failures():
    eps = [
        FakeEntryPoint("hello", make_hello),
        FakeEntryPoint("broken", ImportError("no module named broken")),
        FakeEntryPoint("not_a_plugin", lambda: 42),
        FakeEntryPoint("misnamed", make_hello),  # factory's plugin id is "hello"
    ]
    plugins, statuses = discover(eps=eps)
    assert [p.id for p in plugins] == ["hello"]
    by_id = {s.id: s for s in statuses}
    assert by_id["broken"].state == "error" and "no module named broken" in by_id["broken"].error
    assert by_id["not_a_plugin"].state == "error" and "WorkbenchPlugin" in by_id["not_a_plugin"].error
    assert by_id["misnamed"].state == "error" and "must equal" in by_id["misnamed"].error


def test_discover_skips_disabled_without_importing_them():
    plugins, statuses = discover(disabled={"broken"}, eps=[FakeEntryPoint("broken", ImportError("x"))])
    assert plugins == [] and statuses == [
        PluginStatus(id="broken", name="broken", version="", requires_api=None, state="disabled")
    ]


# ---------------------------------------------------------------------------- host settings

from netstead.workbench.plugins.host import PluginSettingsError, validate_plugin_settings  # noqa: E402


def test_validate_plugin_settings_defaults_and_values():
    assert validate_plugin_settings(HelloSettings, {}, "hello").prefix == "Hello"
    assert validate_plugin_settings(HelloSettings, {"prefix": "Hi"}, "hello").prefix == "Hi"


def test_validate_plugin_settings_never_echoes_values():
    with pytest.raises(PluginSettingsError) as info:
        validate_plugin_settings(HelloSettings, {"prefix": "s3cr3t-value", "bogus": "s3cr3t-value"}, "hello")
    assert "plugins.hello.bogus" in str(info.value) and "s3cr3t" not in str(info.value)
    assert info.value.__context__ is None and info.value.__cause__ is None


# ---------------------------------------------------------------------------- session install

from netstead.select.parse import StubParser  # noqa: E402
from netstead.workbench.actions import SetSetting  # noqa: E402
from netstead.workbench.session import ActionError, Session  # noqa: E402
from pydantic import ValidationError  # noqa: E402


@pytest.fixture
def make_session(tmp_path, isolated_env):
    def build(*plugins: WorkbenchPlugin, **overrides: Any) -> Session:
        return Session(
            project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=list(plugins), overrides=overrides
        )

    return build


def _status(session: Session, plugin_id: str):
    return next(s for s in session.plugin_status if s.id == plugin_id)


def session_script(session: Session) -> tuple[str, list[str]]:
    """``(script, imports)``: "copy session as Python", assembled exactly as ``sessionScript`` in history.js does."""
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    history = TestClient(build_app(session)).get("/api/history").json()
    return _script_lines(history), history["imports"]


def _script_lines(history: dict[str, Any]) -> str:
    by_seq = {e["seq"]: e for e in history["entries"]}
    lines = [*history["imports"], "", "app = Session()  # or reuse a live session"]
    for e in history["entries"]:
        if e["parent_seq"] is not None:
            failed = "" if e["ok"] else f"  # failed: {e['error']}"
            lines.append(f"# via {by_seq[e['parent_seq']]['action']['type']}: {e['python']}{failed}")
        else:
            lines.append(e["python"] if e["ok"] else f"# failed: {e['python']}  # {e['error']}")
    return "\n".join(lines)


def test_plugin_action_dispatches_records_and_replays(make_session):
    session = make_session(make_hello())
    assert session.dispatch(Greet(name="Ada")) == "Hello, Ada!"
    assert session.dispatch({"type": "hello.greet", "name": "Bo"}) == "Hello, Bo!"
    entry = session.history[-1]
    assert entry.python == "app.do(Greet(name='Bo'))" and entry.imports == f"from {Greet.__module__} import Greet"
    assert _status(session, "hello").state == "loaded"


def test_plugin_state_is_merged_under_plugins(make_session):
    session = make_session(make_hello())
    session.dispatch(Greet(name="Ada"))
    assert session.state()["plugins"] == {"hello": {"greeted": ["Ada"]}}


def test_plugin_settings_come_from_the_layers(make_session):
    session = make_session(make_hello(), **{"plugins.hello.prefix": "Hi"})
    assert session.dispatch(Greet(name="Ada")) == "Hi, Ada!"


def test_invalid_plugin_settings_at_start_mark_the_plugin_error(make_session):
    session = make_session(make_hello(), **{"plugins.hello.bogus": 1})
    assert _status(session, "hello").state == "error" and "plugins.hello.bogus" in _status(session, "hello").error
    assert not session.actions.has("hello.greet")


def test_set_setting_validates_plugin_settings(make_session):
    session = make_session(make_hello())
    with pytest.raises(ActionError, match="plugin 'hello'"):
        session.dispatch(SetSetting(key="plugins.hello.prefix", value=3))
    assert session.dispatch(SetSetting(key="plugins.hello.prefix", value="Hey"))["value"] == "Hey"
    assert session.dispatch(Greet(name="Ada")) == "Hey, Ada!"
    assert session.dispatch(SetSetting(key="plugins.hello.prefix", value=None))["value"] is None
    assert session.dispatch(Greet(name="Ada")) == "Hello, Ada!"


@pytest.mark.parametrize("scope", ["user", "project"])
def test_set_setting_checks_a_persisted_plugin_setting_before_writing(make_session, scope):
    session = make_session(make_hello())
    paths = {"user": session.loaded.user_path, "project": session.loaded.project_path}
    with pytest.raises(ActionError, match=r"plugins\.hello\.bogus"):
        session.dispatch(SetSetting(key="plugins.hello.bogus", value="x", scope=scope))
    with pytest.raises(ActionError, match="plugin 'hello'"):
        session.dispatch(SetSetting(key="plugins.hello", value={"prefix": 3}, scope=scope))
    assert not paths[scope].exists()  # nothing a plugin rejects ever reaches a file
    assert session.dispatch(SetSetting(key="plugins.hello.prefix", value="Yo", scope=scope))["value"] == "Yo"
    assert session.dispatch(Greet(name="Ada")) == "Yo, Ada!"


def test_settings_of_a_plugin_that_is_not_loaded_are_not_checked(make_session):
    session = make_session(make_hello(), **{"app.disabled_plugins": ["hello"]})
    assert session.dispatch(SetSetting(key="plugins.hello.anything", value=1))["value"] == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("plugins.hello.api_key", "x"),  # secret-named key
        ("plugins.hello.token", "x"),
        ("plugins.hello.prefix", "sk-ant-api03-" + "a" * 40),  # key-shaped value
        ("plugins.hello", {"auth": {"password": "x"}}),  # secret-named key nested in the value
        ("plugins.hello.endpoint", "https://user:pw@example.com/"),  # URL userinfo
    ],
)
def test_plugin_secrets_are_refused_like_any_setting(key, value):
    with pytest.raises(ValidationError) as info:
        SetSetting(key=key, value=value)
    assert "sk-ant" not in str(info.value) and "pw@" not in str(info.value)


def test_a_refused_plugin_secret_is_recorded_without_its_value(make_session):
    session = make_session(make_hello())
    with pytest.raises(ValidationError):
        session.dispatch({"type": "set_setting", "key": "plugins.hello.api_key", "value": "hunter2"})
    assert session.history == [] and "api_key" not in session.settings.plugins.get("hello", {})


@pytest.mark.parametrize(
    ("plugin", "overrides", "state"),
    [
        (make_hello(), {"app.disabled_plugins": ["hello"]}, "disabled"),
        (make_hello(requires_api="2.0"), {}, "incompatible"),
        (make_hello(id="Bad Id"), {}, "error"),
    ],
)
def test_plugins_that_do_not_install(make_session, plugin, overrides, state):
    session = make_session(plugin, **overrides)
    assert session.plugin_status[0].state == state and not session.actions.has("hello.greet")


def test_failing_on_load_installs_nothing(make_session):
    def boom(host):
        raise RuntimeError("cannot start")

    session = make_session(make_hello(on_load=boom))
    assert _status(session, "hello").state == "error" and "cannot start" in _status(session, "hello").error
    assert not session.actions.has("hello.greet") and "hello" not in session.plugins


def test_failing_state_is_reported_not_raised(make_session):
    def bad_state(host):
        raise RuntimeError("oops")

    session = make_session(make_hello(state=bad_state))
    assert session.state()["plugins"]["hello"] == {"error": "RuntimeError: oops"}


def test_duplicate_plugin_ids_keep_the_first(make_session):
    session = make_session(make_hello(), make_hello())
    assert [s.state for s in session.plugin_status] == ["loaded", "error"]


class Shout(BaseAction):
    type: Literal["loud.shout"] = "loud.shout"
    name: str


def test_plugins_call_each_other_through_actions(make_session):
    def shout(host, action: Shout) -> str:
        if not host.has_action("hello.greet"):
            return action.name.upper()
        return host.dispatch(Greet(name=action.name)).upper()

    loud = WorkbenchPlugin(
        id="loud", name="Loud", version="0.1", requires_api=HOST_API, actions=(ActionSpec(Shout, shout),)
    )
    assert make_session(loud).dispatch(Shout(name="ada")) == "ADA"
    both = make_session(make_hello(), loud)
    assert both.dispatch(Shout(name="ada")) == "HELLO, ADA!"
    assert [e.action["type"] for e in both.history] == ["hello.greet", "loud.shout"]  # inner first
    inner, outer = both.history
    assert inner.parent_seq == outer.seq and outer.parent_seq is None

    script, imports = session_script(both)
    assert "# via loud.shout: app.do(Greet(name='ada'))" in script.splitlines()
    assert imports[1:] == [f"from {Shout.__module__} import Shout"]  # the nested Greet needs no import
    replay = make_session(make_hello(), loud)
    exec(script.replace("app = Session()", "app = replay"), {"replay": replay}, {})
    assert replay.state() == both.state()  # replay is idempotent: the nested Greet ran once, not twice


class Widen(BaseAction):
    type: Literal["edit.widen"] = "edit.widen"
    mutates: ClassVar[bool] = True
    link_id: int


def test_a_plugin_previews_an_edit_on_a_derived_network(make_session, rdu_source):
    from corral.editing import Edit
    from netstead.workbench.actions import OpenNetwork

    def widen(host, action: Widen) -> str:
        preview = host.derive(None, label="Preview", note="preview")
        payload = {"predicate": lambda t: t.link_id == action.link_id, "set": {"lanes": 9}}
        host.mutate(preview, [Edit(op="update_rows", table="link", payload=payload)], note=f"widen {action.link_id}")
        return preview

    editor = WorkbenchPlugin(
        id="edit", name="Edit", version="0.1", requires_api=HOST_API, actions=(ActionSpec(Widen, widen),)
    )
    session = make_session(editor)
    session.dispatch(OpenNetwork(source=rdu_source))
    first = int(session.registry.get("rdu-i40").links_df()["link_id"].iloc[0])
    preview = session.dispatch(Widen(link_id=first))
    summary = next(n for n in session.state()["networks"] if n["id"] == preview)
    assert summary["derived_from"] == "rdu-i40" and summary["lineage"] == ["edit: preview", f"edit: widen {first}"]
    assert session.registry.get("rdu-i40").version == 0


class Boom(BaseAction):
    type: Literal["leaky.boom"] = "leaky.boom"


class Leak(BaseAction):
    type: Literal["leaky.leak"] = "leaky.leak"


#: A presigned-URL signature: ``redact.scrub`` strips it (and any userinfo) from a URL's query string.
_TOKEN = "sk-ant-api03-SECRETMARKER0123456789"
_URL_SECRET = f"https://me:{_TOKEN}@uploads.example.com/blob?sig={_TOKEN}"


class _Inner(BaseModel):
    value: int


def test_plugin_handler_failure_is_scrubbed_everywhere(make_session):
    """A plugin's raw exception message (``boom``, a URL carrying a signature) or a pydantic input
    value (``leak``) must not reach the history entry, the published event, or ``/api/history`` --
    unlike a core Action's failure, which keeps the raw message (pinned by
    ``test_unexpected_handler_error_is_recorded_not_raised_raw`` in test_workbench_session.py)."""

    def boom(host, action):
        raise ValueError(f"upload failed: {_URL_SECRET}")

    def leak(host, action):
        _Inner(value=_TOKEN)  # pydantic ValidationError carrying _TOKEN as the rejected input

    leaky = WorkbenchPlugin(
        id="leaky",
        name="Leaky",
        version="0.1",
        requires_api=HOST_API,
        actions=(ActionSpec(Boom, boom), ActionSpec(Leak, leak)),
    )
    session = make_session(leaky)
    published: list[dict[str, Any]] = []
    session.events.publish = published.append

    with pytest.raises(ActionError) as boom_info:
        session.dispatch(Boom())
    entry = session.history[-1]
    assert entry.ok is False and entry.error_type == "InternalError" and "ValueError" in entry.error
    assert _TOKEN not in entry.error and _TOKEN not in str(boom_info.value)
    assert _TOKEN not in json.dumps(published)

    published.clear()
    with pytest.raises(ActionError) as leak_info:
        session.dispatch(Leak())
    entry = session.history[-1]
    assert entry.ok is False and entry.error_type == "InternalError" and "ValidationError" in entry.error
    assert _TOKEN not in entry.error and _TOKEN not in str(leak_info.value)
    assert _TOKEN not in json.dumps(published)

    script, _ = session_script(session)
    assert _TOKEN not in script


# ---------------------------------------------------------------------------- a real entry point

_FAKE_PLUGIN = """
from typing import Literal
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin


class Ping(BaseAction):
    type: Literal["fake.ping"] = "fake.ping"


def plugin():
    return WorkbenchPlugin(id="fake", name="Fake", version="0.1", requires_api=HOST_API,
                           actions=(ActionSpec(Ping, lambda host, action: "pong"),))
"""


@pytest.mark.plugin_discovery
def test_installed_entry_point_is_discovered_by_default(tmp_path, isolated_env, monkeypatch):
    site = tmp_path / "site"
    dist = site / "netstead_fake_plugin-0.1.dist-info"
    dist.mkdir(parents=True)
    (site / "netstead_fake_plugin.py").write_text(_FAKE_PLUGIN, encoding="utf-8")
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: netstead-fake-plugin\nVersion: 0.1\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(
        "[netstead.workbench.plugins]\nfake = netstead_fake_plugin:plugin\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.delitem(__import__("sys").modules, "netstead_fake_plugin", raising=False)

    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser())  # plugins=None: discover
    assert _status(session, "fake").state == "loaded"
    assert session.dispatch({"type": "fake.ping"}) == "pong"
    assert session.history[-1].imports == "from netstead_fake_plugin import Ping"


# ---------------------------------------------------------------------------- install is all-or-nothing


class Wave(BaseAction):
    type: Literal["hello.wave"] = "hello.wave"


def test_a_plugin_declaring_a_type_twice_is_an_error_not_an_exception(make_session):
    session = make_session(make_hello(actions=(_spec(Wave), _spec(Greet), _spec(Greet))))
    assert _status(session, "hello").state == "error" and "declared twice" in _status(session, "hello").error
    assert not session.actions.has("hello.wave") and not session.actions.has("hello.greet")


def test_a_registration_failure_rolls_back_the_plugins_earlier_actions(make_session, monkeypatch):
    import netstead.workbench.session as session_module

    monkeypatch.setattr(session_module, "problems", lambda *args: [])  # as if a check were missed
    session = make_session(make_hello(actions=(_spec(Wave), _spec(Greet), _spec(StrTyped))))
    assert _status(session, "hello").state == "error" and "one value" in _status(session, "hello").error
    assert not session.actions.has("hello.wave") and not session.actions.has("hello.greet")
    assert "hello.wave" not in session._handlers and "hello" not in session.plugins
    assert session.dispatch({"type": "clear_selection"}) is None  # parsing still works for core Actions


def test_session_script_with_plugin_and_failed_entries_replays(make_session, rdu_source):
    """ "Copy session as Python" for a mixed core + plugin session, with a failed plugin entry, runs as is."""
    from netstead.workbench.actions import OpenNetwork, Style

    def hello_with_failures(**overrides: Any) -> WorkbenchPlugin:
        plugin = make_hello(**overrides)
        greet = plugin.actions[0].handler

        def picky(host, action: Greet) -> str:
            if action.name == "nobody":
                raise ActionError("nobody to greet")
            return greet(host, action)

        return make_hello(actions=(ActionSpec(Greet, picky),), **overrides)

    session = make_session(hello_with_failures())
    session.dispatch(OpenNetwork(source=rdu_source))
    session.dispatch(Greet(name="Ada"))
    with pytest.raises(ActionError):
        session.dispatch(Greet(name="nobody"))
    session.dispatch(Style(offset=False))
    script, imports = session_script(session)
    assert imports[1:] == [f"from {Greet.__module__} import Greet"]
    assert "# failed: app.do(Greet(name='nobody'))" in script

    replay = make_session(hello_with_failures())
    namespace: dict[str, Any] = {}
    exec(script.replace("app = Session()", "app = replay"), {"replay": replay}, namespace)
    assert namespace["Session"].__module__ == "netstead.workbench.session"  # never rebound by a plugin import
    assert replay.state() == session.state()


def test_settings_payload_redacts_plugin_values(make_session):
    """Defence in depth: even a plugin table that bypassed validation reaches the browser redacted."""
    session = make_session(make_hello(), **{"plugins.hello.prefix": "Hi"})
    session.settings.plugins["hello"]["token"] = "MARKER"  # in place: no validator runs
    values = session.settings_payload()["values"]["plugins"]
    assert values == {"hello": {"prefix": "Hi", "token": "[redacted]"}}


# ---------------------------------------------------------------------------- SystemExit vs KeyboardInterrupt


def _raise(exc: BaseException):
    def factory(*args: Any) -> Any:
        raise exc

    return factory


def test_discover_records_a_factory_calling_sys_exit():
    plugins, statuses = discover(eps=[FakeEntryPoint("quits", _raise(SystemExit(3)))])
    assert plugins == [] and statuses[0].state == "error" and "SystemExit" in statuses[0].error


def test_discover_lets_keyboard_interrupt_through():
    with pytest.raises(KeyboardInterrupt):
        discover(eps=[FakeEntryPoint("stop", _raise(KeyboardInterrupt()))])


def test_on_load_calling_sys_exit_marks_the_plugin_error(make_session):
    session = make_session(make_hello(on_load=_raise(SystemExit(1))))
    assert _status(session, "hello").state == "error" and "SystemExit" in _status(session, "hello").error


def test_on_load_keyboard_interrupt_propagates(make_session):
    with pytest.raises(KeyboardInterrupt):
        make_session(make_hello(on_load=_raise(KeyboardInterrupt())))


# ---------------------------------------------------------------------------- the hello example package


def test_hello_example_plugin_works(tmp_path, isolated_env, monkeypatch):
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / "examples" / "workbench-plugin-hello"
    monkeypatch.syspath_prepend(str(example))
    import netstead_hello

    session = Session(
        project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[netstead_hello.plugin()]
    )
    assert session.dispatch({"type": "hello.greet", "name": "Ada"}) == "Hello, Ada!"
    assert session.state()["plugins"]["hello"] == {"greeted": 1}
    assert session.history[-1].imports == "from netstead_hello import Greet"
    assert (netstead_hello.plugin().static_dir / "main.js").is_file()


@pytest.mark.plugin_discovery
def test_installed_hello_example_is_discovered_through_its_entry_point(tmp_path, isolated_env):
    """CI installs ``examples/workbench-plugin-hello``, so its real packaging metadata is exercised here."""
    from importlib.metadata import PackageNotFoundError, distribution

    try:
        distribution("netstead-hello")
    except PackageNotFoundError:
        pytest.skip("examples/workbench-plugin-hello is not installed (CI installs it)")
    session = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser())  # plugins=None: discover
    assert _status(session, "hello").state == "loaded"
    assert _status(session, "hello").frontend == "/plugins/hello/main.js"
    assert session.dispatch({"type": "hello.greet", "name": "Ada"}) == "Hello, Ada!"


# ---------------------------------------------------------------------------- dispatch: nesting and rollback

import contextlib  # noqa: E402

from corral.editing import Edit  # noqa: E402
from netstead.workbench.actions import OpenNetwork  # noqa: E402


class Wreck(BaseAction):
    type: Literal["wreck.all"] = "wreck.all"
    then: str = "raise"  # "raise", "action_error", "read_then_raise" or "ok"


class Preview(BaseAction):
    type: Literal["wreck.preview"] = "wreck.preview"


class Outer(BaseAction):
    type: Literal["wreck.outer"] = "wreck.outer"
    inner: str = "ok"  # how the nested Wreck ends
    then: str = "ok"  # how this one ends after it


def _delete_all_links() -> Edit:
    return Edit(op="delete_rows", table="link", payload={"predicate": lambda t: t.link_id >= 0})


def _wreck(host, action: Wreck) -> None:
    host.mutate(None, [_delete_all_links()], note="delete every link")
    if action.then == "raise":
        raise RuntimeError("after mutate")
    if action.then == "action_error":
        raise ActionError("refused after mutate")
    if action.then == "read_then_raise":
        assert len(host.network().links_df()) == 0  # cached for the mutated version
        raise RuntimeError("after reading")


def _preview(host, action: Preview) -> None:
    preview = host.derive(None, label="Preview", note="preview")
    host.mutate(preview, [_delete_all_links()], note="preview delete")
    raise RuntimeError("after derive")


def _outer(host, action: Outer) -> str:
    with contextlib.suppress(ActionError):  # a handler may carry on after a nested Action fails
        host.dispatch(Wreck(then=action.inner))
    host.dispatch({"type": "style", "offset": False})  # core state a nested Action changed
    if action.then == "raise":
        raise RuntimeError("outer failed")
    return "done"


def make_wreck() -> WorkbenchPlugin:
    specs = (ActionSpec(Wreck, _wreck), ActionSpec(Preview, _preview), ActionSpec(Outer, _outer))
    return WorkbenchPlugin(id="wreck", name="Wreck", version="0", requires_api=HOST_API, actions=specs)


@pytest.fixture
def wrecked(make_session, rdu_source):
    session = make_session(make_wreck())
    session.dispatch(OpenNetwork(source=rdu_source))
    return session


def _links(session: Session) -> int:
    return len(session.registry.get("rdu-i40").links_df())


@pytest.mark.parametrize("then", ["raise", "action_error"])
def test_a_handler_that_mutates_then_fails_is_rolled_back(wrecked, then):
    handle = wrecked.registry.get("rdu-i40")
    links = _links(wrecked)
    entry = wrecked.dispatch_recorded(Wreck(then=then))
    assert not entry.ok
    assert _links(wrecked) == links and handle.version == 0 and handle.lineage == []
    assert not handle.roadway.links.dirty


def test_a_handler_that_succeeds_keeps_its_mutation(wrecked):
    assert wrecked.dispatch_recorded(Wreck(then="ok")).ok
    assert _links(wrecked) == 0 and wrecked.registry.get("rdu-i40").lineage == ["wreck: delete every link"]


def test_a_failed_handler_unregisters_the_networks_it_derived(wrecked):
    assert not wrecked.dispatch_recorded(Preview()).ok
    assert wrecked.registry.ids() == ["rdu-i40"] and wrecked.active == "rdu-i40"


def test_a_failed_outer_action_rolls_back_its_nested_actions(wrecked):
    links, style = _links(wrecked), dict(wrecked.style)
    outer = wrecked.dispatch_recorded(Outer(inner="ok", then="raise"))
    assert not outer.ok and _links(wrecked) == links and wrecked.style == style
    nested = [e for e in wrecked.history if e.parent_seq == outer.seq]
    assert [e.action["type"] for e in nested] == ["wreck.all", "style"]
    assert all(not e.ok and e.error_type == "RolledBack" for e in nested)  # history never claims a rolled-back change


def test_a_failed_nested_action_is_rolled_back_alone(wrecked):
    links = _links(wrecked)
    outer = wrecked.dispatch_recorded(Outer(inner="action_error", then="ok"))
    assert outer.ok and _links(wrecked) == links and wrecked.style["offset"] is False
    inner = next(e for e in wrecked.history if e.action["type"] == "wreck.all")
    assert not inner.ok and inner.error == "refused after mutate" and inner.parent_seq == outer.seq


def test_a_rollback_drops_caches_built_from_the_rolled_back_tables(wrecked):
    links = _links(wrecked)
    assert not wrecked.dispatch_recorded(Wreck(then="read_then_raise")).ok  # caches 0 links, then fails
    assert _links(wrecked) == links


def test_a_failed_mutate_leaves_the_network_and_its_caches_untouched(wrecked):
    from corral.editing import UnsupportedEditOp
    from corral.validation.sync_state import DirtyTracker

    handle = wrecked.registry.get("rdu-i40")
    tracker = handle.roadway.dirty_tracker = DirtyTracker()
    stamp = tracker.stamp_table("link", handle.roadway.links.expr, handle.roadway.links.engine)
    cached = handle.links_df()
    with pytest.raises(UnsupportedEditOp):
        wrecked.mutate(None, [_delete_all_links(), Edit(op="explode", table="link")], note="bad")
    assert handle.links_df() is cached and len(cached) > 0  # the cache entry is still the valid one
    assert handle.version == 0 and not handle.roadway.links.dirty and tracker.get_table_stamp("link") is stamp


def test_a_rolled_back_mutate_restores_the_sync_stamp(wrecked):
    from corral.validation.sync_state import DirtyTracker

    handle = wrecked.registry.get("rdu-i40")
    tracker = handle.roadway.dirty_tracker = DirtyTracker()
    stamp = tracker.stamp_table("link", handle.roadway.links.expr, handle.roadway.links.engine)
    assert not wrecked.dispatch_recorded(Wreck()).ok
    assert tracker.get_table_stamp("link").content_hash == stamp.content_hash
    assert wrecked.dispatch_recorded(Wreck(then="ok")).ok
    assert tracker.get_table_stamp("link") is None  # a committed edit drops it, as corral's apply_edit does


def _js_session_script(history: dict[str, Any], tmp_path, run_node) -> str:
    """Run the real ``sessionScript`` from static/js/history.js on an ``/api/history`` payload."""
    import json
    import re

    from netstead.workbench.server import STATIC_DIR

    source = (STATIC_DIR / "js" / "history.js").read_text(encoding="utf-8")
    (tmp_path / "history.mjs").write_text(re.sub(r"^import .*$", "", source, flags=re.M), encoding="utf-8")
    probe = tmp_path / "probe.mjs"
    probe.write_text(
        'import { sessionScript } from "./history.mjs";\n'
        f"process.stdout.write(JSON.stringify(sessionScript({json.dumps(history)})));\n"
    )
    proc = run_node([str(probe)])
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.skipif(__import__("shutil").which("node") is None, reason="node not installed")
def test_history_js_writes_nested_entries_as_comments(wrecked, tmp_path, run_node):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    wrecked.dispatch(Outer(inner="action_error"))
    wrecked.dispatch_recorded(Outer(then="raise"))
    wrecked.mutate(None, [_delete_all_links()], note="by hand")
    history = TestClient(build_app(wrecked)).get("/api/history").json()
    script = _js_session_script(history, tmp_path, run_node)
    assert script == _script_lines(history)
    assert "# via wreck.outer: app.do(Wreck(then='action_error'))  # failed: refused after mutate" in script
    assert "# via wreck.outer: app.do(Style(offset=False))  # failed: rolled back: wreck.outer failed" in script
    assert script.endswith("\n# python changed rdu-i40 outside an Action: by hand (not replayable)")


# ---------------------------------------------------------------------------- mutate outside an Action (I-6)


class Spawn(BaseAction):
    type: Literal["wreck.spawn"] = "wreck.spawn"


def make_background(seen: dict[str, Any]) -> WorkbenchPlugin:
    """``wreck.spawn`` starts a job that mutates the active network; ``GET /trim`` mutates from a route."""
    from fastapi import APIRouter

    def spawn(host, action: Spawn) -> str:
        def work(ctx) -> int:
            ctx.stage("trim", progress=0.5)
            (result,) = host.mutate(None, [_delete_all_links()], note="trim in a job")
            return result.diff.rows_removed

        seen["job"] = host.submit_job("Trim links", work)
        return seen["job"]

    def router(host) -> APIRouter:
        api = APIRouter()

        @api.post("/trim")
        def trim() -> dict:
            host.mutate(None, [_delete_all_links()], note="trim\nfrom a route")
            return {"ok": True}

        return api

    return WorkbenchPlugin(
        id="wreck", name="Wreck", version="0", requires_api=HOST_API, actions=(ActionSpec(Spawn, spawn),), router=router
    )


def test_a_job_mutates_without_deadlock_and_is_recorded(make_session, rdu_source):
    seen: dict[str, Any] = {}
    session = make_session(make_background(seen))
    session.dispatch(OpenNetwork(source=rdu_source))
    links = _links(session)
    job_id = session.dispatch(Spawn())
    job = session.jobs.get(job_id)
    assert job_id == seen["job"] and job.wait(timeout=30), "the job's mutate deadlocked"
    assert job.status == "done" and job.result == links and job.kind == "wreck.job"
    assert _links(session) == 0
    entry = session.history[-1]
    assert entry.ok and entry.action == {"type": "wreck.mutate", "net_id": "rdu-i40", "note": "trim in a job"}
    assert entry.python == "# wreck changed rdu-i40 outside an Action: trim in a job (not replayable)"
    assert session.registry.get("rdu-i40").lineage == ["wreck: trim in a job"]


def test_a_route_mutation_is_recorded_as_a_one_line_comment(make_session, rdu_source):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    session = make_session(make_background({}))
    session.dispatch(OpenNetwork(source=rdu_source))
    assert TestClient(build_app(session)).post("/api/plugins/wreck/trim").json() == {"ok": True}
    script, imports = session_script(session)
    assert imports[1:] == []  # nothing to import for a comment
    assert script.splitlines()[-1] == "# wreck changed rdu-i40 outside an Action: trim from a route (not replayable)"


def test_a_python_mutate_outside_an_action_is_recorded(make_session, rdu_source):
    session = make_session()
    session.dispatch(OpenNetwork(source=rdu_source))
    session.mutate(None, [_delete_all_links()], note="by hand")
    entry = session.history[-1]
    assert entry.ok and entry.action["type"] == "python.mutate" and entry.imports == ""
    assert entry.python == "# python changed rdu-i40 outside an Action: by hand (not replayable)"


def test_a_mutate_inside_an_action_adds_no_entry_of_its_own(wrecked):
    wrecked.dispatch(Wreck(then="ok"))
    assert [e.action["type"] for e in wrecked.history] == ["open_network", "wreck.all"]


# ---------------------------------------------------------------------------- the Host surface


@pytest.fixture
def host_of(make_session, rdu_source):
    """``(session, host)``: an opened session and the Host its plugin was given."""
    hosts: list[Any] = []
    session = make_session(make_hello(on_load=hosts.append))
    session.dispatch(OpenNetwork(source=rdu_source))
    return session, hosts[0]


def test_host_network_defaults_to_the_active_one(host_of):
    session, host = host_of
    assert host.network() is session.network() is session.registry.get("rdu-i40")
    assert host.network("rdu-i40") is session.network("rdu-i40")
    with pytest.raises(ActionError, match="unknown network"):
        host.network("nope")


def test_host_selection_is_a_deep_copy(host_of):
    session, host = host_of
    assert host.selection is None
    first = int(session.network().links_df()["link_id"].iloc[0])
    session.dispatch({"type": "select", "link_ids": [first]})
    copied = host.selection
    assert copied == session.selection and copied is not session.selection
    copied["link_ids"].append(-1)
    copied["net_id"] = "elsewhere"
    assert session.selection["net_id"] == "rdu-i40" and -1 not in session.selection["link_ids"]
    assert session.selection_copy() == session.selection


def test_host_publish_sends_a_namespaced_plugin_event(host_of):
    session, host = host_of
    published: list[dict[str, Any]] = []
    session.events.publish = published.append
    host.publish("greeted", {"name": "Ada"})
    host.publish("ping")
    assert published == [
        {"type": "plugin", "plugin": "hello", "name": "greeted", "payload": {"name": "Ada"}},
        {"type": "plugin", "plugin": "hello", "name": "ping", "payload": None},
    ]


@pytest.mark.parametrize("name", ["other.thing", "Greeted", "1st", "", "greeted!", "hello greeted", "_x"])
def test_host_publish_refuses_a_name_wb_on_could_not_hear(host_of, name):
    # wb.on("a.b") means another plugin's event "b", so a dotted (or otherwise odd) name would never arrive.
    session, host = host_of
    published: list[dict[str, Any]] = []
    session.events.publish = published.append
    with pytest.raises(ValueError, match=r"hello: host\.publish event names are lowercase .*\[a-z\]\[a-z0-9_\]\*"):
        host.publish(name)
    host.publish("greeted_2")
    assert [e["name"] for e in published] == ["greeted_2"]


def test_host_writable_stays_inside_the_allowed_roots(host_of, tmp_path):
    from netstead.workbench.errors import PathNotAllowed

    _, host = host_of
    assert host.writable(tmp_path / "out" / "cards.json") == (tmp_path / "out" / "cards.json").resolve()
    with pytest.raises(PathNotAllowed):
        host.writable(tmp_path.parent / "elsewhere.json")
    with pytest.raises(PathNotAllowed):
        host.writable(tmp_path / ".." / "escape.json")


class Reopen(BaseAction):
    type: Literal["wreck.reopen"] = "wreck.reopen"
    source: str


def test_a_handler_dispatching_a_job_action_is_told_to_use_submit_job(make_session, rdu_source):
    def reopen(host, action: Reopen) -> Any:
        return host.dispatch(OpenNetwork(source=action.source))

    plugin = WorkbenchPlugin(
        id="wreck", name="W", version="0", requires_api=HOST_API, actions=(ActionSpec(Reopen, reopen),)
    )
    entry = make_session(plugin).dispatch_recorded(Reopen(source=rdu_source))
    assert not entry.ok and "open_network runs as a job" in entry.error and "host.submit_job" in entry.error


def test_closing_a_base_network_marks_its_derived_copies(host_of):
    session, host = host_of
    preview = host.derive(None, label="Preview", note="preview")
    session.dispatch({"type": "close_network", "net_id": "rdu-i40"})
    derived_from = session.network(preview).derived_from
    assert derived_from == "rdu-i40 (closed)" and derived_from not in session.registry.ids()


# ---------------------------------------------------------------------------- plugin errors never echo values (N-2)


class _Secretive(BaseModel):
    count: int


def _validation_error() -> Exception:
    try:
        _Secretive(count="hunter2-SECRET")  # type: ignore[arg-type]
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


SIGNED = "https://bucket.example/net.parquet?X-Amz-Signature=SECRET"


@pytest.mark.parametrize("exc", [_validation_error(), RuntimeError(f"could not fetch {SIGNED}")])
def test_on_load_errors_are_shown_without_values(make_session, exc):
    session = make_session(make_hello(on_load=_raise(exc)))
    error = _status(session, "hello").error
    assert _status(session, "hello").state == "error" and type(exc).__name__ in error and "SECRET" not in error


def test_a_validation_error_keeps_where_and_what(make_session):
    session = make_session(make_hello(on_load=_raise(_validation_error())))
    assert "count" in _status(session, "hello").error and "integer" in _status(session, "hello").error


def test_state_errors_are_shown_without_values(make_session):
    session = make_session(make_hello(state=_raise(_validation_error())))
    error = session.state()["plugins"]["hello"]["error"]
    assert error.startswith("ValidationError") and "SECRET" not in error


def test_router_and_discovery_errors_are_shown_without_values(make_session):
    session = make_session(make_hello(router=_raise(RuntimeError(f"no {SIGNED}"))))
    assert "SECRET" not in _status(session, "hello").error and "bucket.example" in _status(session, "hello").error
    _, statuses = discover(eps=[FakeEntryPoint("leaky", _raise(_validation_error()))])
    assert statuses[0].state == "error" and "SECRET" not in statuses[0].error


def test_describe_error_redacts_bare_api_keys():
    """A key-shaped token anywhere in a plugin's exception text never reaches the browser."""
    from netstead.workbench.redact import describe_error

    token = "sk-ant-api03-" + "A" * 40
    text = describe_error(ValueError(f"upstream refused token {token}"))
    assert token not in text
    assert "[redacted]" in text


# ---------------------------------------------------------------------------- JSON schemas (GET /api/actions)


class Opaque:
    """A field type pydantic can validate (``arbitrary_types_allowed``) but not describe in JSON Schema."""


class OpaqueAction(BaseAction):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    type: Literal["hello.opaque"] = "hello.opaque"
    thing: Opaque | None = None


def test_problems_refuses_an_action_with_no_json_schema():
    found = problems(make_hello(actions=(_spec(OpaqueAction),)), ActionRegistry(), taken=())
    assert any(p.startswith("hello.opaque: its fields have no JSON schema") for p in found), found


def test_a_plugin_whose_action_has_no_schema_is_refused_and_the_catalog_still_answers(make_session):
    session = make_session(make_hello(actions=(_spec(OpaqueAction),)))
    assert _status(session, "hello").state == "error" and "no JSON schema" in _status(session, "hello").error
    types = {entry["type"] for entry in session.action_catalog()}
    assert "hello.opaque" not in types and "open_network" in types


def test_the_catalog_lists_a_schema_it_cannot_generate_as_none(make_session, monkeypatch, caplog):
    """Defence in depth: install refuses such a model, but a failure later must not fail the whole catalog."""
    session = make_session(make_hello())

    def boom(cls, *args, **kwargs):
        raise RuntimeError("schema exploded")

    monkeypatch.setattr(Greet, "model_json_schema", classmethod(boom))
    catalog = {entry["type"]: entry for entry in session.action_catalog()}
    assert catalog["hello.greet"]["schema"] is None and catalog["open_network"]["schema"]["properties"]
    assert "hello.greet" in caplog.text


def test_the_plugin_id_core_is_reserved(make_session):
    """The front end tags core's own registrations with the owner "core": a plugin may not take that id."""
    assert any("reserved" in p for p in problems(make_hello(id="core", actions=()), ActionRegistry(), taken=()))
    session = make_session(make_hello(id="core", actions=()))
    assert _status(session, "core").state == "error" and "reserved" in _status(session, "core").error


def test_a_plugin_built_for_api_1_0_installs_on_this_host(make_session):
    """A minor bump only adds: a plugin that declares 1.0 still installs (and its Action registers) on 1.1."""
    assert HOST_API == "1.1"
    session = make_session(make_hello(requires_api="1.0"))
    assert _status(session, "hello").state == "loaded" and session.actions.has("hello.greet")


def test_the_action_catalog_drops_an_unloaded_plugins_actions(make_session):
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    session = make_session(make_hello())
    client = TestClient(build_app(session))
    assert "hello.greet" in {a["type"] for a in client.get("/api/actions").json()["actions"]}
    session.unload_plugin("hello", "its mount failed")
    types = {a["type"] for a in client.get("/api/actions").json()["actions"]}
    assert "hello.greet" not in types and "open_network" in types
