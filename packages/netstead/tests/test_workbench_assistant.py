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


# ---------------------------------------------------------------------- the core: ask, run, draft, budget

from netstead.fixtures import leavenworth  # noqa: E402
from netstead.llm.types import Completion, ToolCall  # noqa: E402
from netstead.select.parse import StubParser  # noqa: E402
from netstead.workbench import Session  # noqa: E402
from netstead.workbench.actions import OpenNetwork  # noqa: E402
from netstead.workbench.assistant.core import AssistantBusy  # noqa: E402

SRC = str(leavenworth.parquet_dir())


class Scripted:
    """A provider that replays Completions; ``base_url`` decides whether it counts as local."""

    name, label = "ollama", "Scripted"

    def __init__(self, *replies, base_url="http://localhost:11434"):
        self.replies, self.requests, self.base_url = list(replies), [], base_url

    def complete(self, request):
        self.requests.append(request)
        return self.replies.pop(0)

    def list_models(self):
        return []


def _session(tmp_path, isolated_env, provider=None):
    s = Session(
        project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[], assistant_provider=provider
    )
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _reply(*calls, text="", tokens=(100, 20)):
    return Completion(
        tool_calls=tuple(ToolCall(n, a) for n, a in calls), text=text, input_tokens=tokens[0], output_tokens=tokens[1]
    )


SKI_HILL = ("select", {"utterance": "Ski Hill", "facility": {"name": "Ski Hill Drive"}})


def test_read_only_steps_run_at_once_and_are_recorded_via_the_assistant(tmp_path, isolated_env):
    p = Scripted(_reply(SKI_HILL, ("style", {"color_by": "lanes"}), text="Selected and coloured."))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("select ski hill drive and colour by lanes")
    assert [st["status"] for st in turn["steps"]] == ["done", "done"] and turn["draft_id"] is None
    assert turn["steps"][0]["result"] == "resolved: 20 links" and turn["text"] == "Selected and coloured."
    assert [e.via for e in s.history[-2:]] == ["assistant", "assistant"]
    assert s.history[-2].python.startswith("app.do(Select(utterance='Ski Hill', query=")
    assert s.style["color_by"] == "lanes"


def test_a_mutating_step_and_everything_after_it_become_one_draft(tmp_path, isolated_env):
    p = Scripted(
        _reply(
            SKI_HILL,
            ("edit_cells", {"on_selection": True, "values": {"lanes": 3}}),
            ("navigate", {"to_selection": True}),
        )
    )
    s = _session(tmp_path, isolated_env, p)
    before = s.network().version
    turn = s.assistant.ask("set ski hill drive to 3 lanes and zoom there")
    assert [st["status"] for st in turn["steps"]] == ["done", "drafted", "drafted"]
    assert turn["draft_id"] is not None and s.network().version == before  # nothing changed yet
    assert "app.do(EditCells(" in turn["steps"][1]["python"] and "ids=[" in turn["steps"][1]["python"]
    # on_selection became the ids of the selection the read-only step just made, not the (empty) one before it
    assert (
        s.assistant.draft.actions[0].ids == s.selection_copy()["link_ids"]
        and len(s.assistant.draft.actions[0].ids) == 20
    )


def test_on_selection_after_a_selection_change_inside_the_draft_is_repaired(tmp_path, isolated_env):
    bad = _reply(("edit_cells", {"ids": [1], "values": {"lanes": 1}}), SKI_HILL,
                 ("edit_cells", {"on_selection": True, "values": {"lanes": 3}}))  # fmt: skip
    p = Scripted(bad, _reply(text="Ask in two steps."))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("x")
    assert turn["steps"] == [] and turn["text"] == "Ask in two steps."
    assert "on_selection can't follow" in p.requests[1].messages[-1].content


def test_on_selection_with_nothing_found_fails_that_step_and_drafts_nothing(tmp_path, isolated_env):
    nowhere = ("select", {"utterance": "Nowhere", "facility": {"name": "Nowhere Street"}})
    p = Scripted(_reply(nowhere, ("edit_cells", {"on_selection": True, "values": {"lanes": 3}})))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("set nowhere street to 3 lanes")
    assert [st["status"] for st in turn["steps"]] == ["done", "failed"] and turn["draft_id"] is None
    assert "nothing is selected" in turn["steps"][1]["result"] and turn["steps"][1]["python"] == ""
    assert s.assistant.draft is None


def test_a_text_only_answer_has_no_steps(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, Scripted(_reply(text="Which street?")))
    turn = s.assistant.ask("select it")
    assert turn["steps"] == [] and turn["text"] == "Which street?"


def test_a_failed_read_only_step_stops_the_turn(tmp_path, isolated_env):
    p = Scripted(_reply(("navigate", {"to_selection": True}), ("style", {"color_by": "lanes"})))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("zoom to the selection and colour by lanes")
    assert [st["status"] for st in turn["steps"]] == ["failed", "skipped"]
    assert "nothing is selected" in turn["steps"][0]["result"] and s.style["color_by"] != "lanes"


def test_the_conversation_sent_back_carries_no_values(tmp_path, isolated_env):
    p = Scripted(_reply(SKI_HILL), _reply(text="ok"))
    s = _session(tmp_path, isolated_env, p)
    s.assistant.ask("select ski hill drive")
    s.assistant.ask("and now?")
    history = p.requests[1].messages
    assert [m.role for m in history] == ["user", "assistant", "user"]
    assert history[1].content == "done: select (resolved, 20 links)"


def test_the_remote_budget_refuses_and_local_is_never_refused(tmp_path, isolated_env):
    remote = Scripted(_reply(text="a", tokens=(150_000, 60_000)), _reply(text="b"), base_url="https://api.example")
    s = _session(tmp_path, isolated_env, remote)
    s.assistant.ask("one")
    turn = s.assistant.ask("two")
    assert turn["error"] and "assistant.remote_token_budget" in turn["error"] and len(remote.requests) == 1
    local = Scripted(_reply(text="a", tokens=(150_000, 60_000)), _reply(text="b"))
    (tmp_path / "2").mkdir()
    s2 = _session(tmp_path / "2", isolated_env, local)
    s2.assistant.ask("one")
    assert s2.assistant.ask("two")["error"] is None
    assert s2.assistant.used == {"ollama/None": 210_000 + 120}


def test_a_failed_answer_is_still_charged(tmp_path, isolated_env):
    bad = _reply(("style", {"color_by": 3}), tokens=(500, 10))
    p = Scripted(bad, bad, base_url="https://api.example")
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("colour")
    assert turn["error"] and "still invalid" in turn["error"]
    assert s.assistant.used == {"ollama/None": 1020}


def test_unreported_usage_is_counted_as_characters_over_four(tmp_path, isolated_env):
    p = Scripted(Completion(text="x" * 40), base_url="https://api.example")
    s = _session(tmp_path, isolated_env, p)
    s.assistant.ask("hi")
    req = p.requests[0]
    sent = len(req.system) + len(req.context) + sum(len(m.content) for m in req.messages)
    assert s.assistant.used["ollama/None"] == sent // 4 + 10


def test_one_question_at_a_time(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, Scripted(_reply(text="x")))
    assert s.assistant._busy.acquire(blocking=False)
    with pytest.raises(AssistantBusy):
        s.assistant.ask("hello")
    s.assistant._busy.release()


def test_the_stub_provider_plans_offline(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env)  # select.provider is "stub" by default
    turn = s.assistant.ask("colour by lanes")
    assert turn["steps"][0]["status"] == "done" and s.style["color_by"] == "lanes"
    assert s.assistant.payload()["status"]["provider"] == "stub"


def test_provider_errors_become_an_error_turn(tmp_path, isolated_env):
    from netstead.llm.errors import ProviderUnavailable

    class Down(Scripted):
        def complete(self, request):
            raise ProviderUnavailable("ollama", "could not reach Ollama at http://localhost:11434 (ConnectError).")

    s = _session(tmp_path, isolated_env, Down())
    turn = s.assistant.ask("hello")
    assert turn["error"].startswith("could not reach Ollama") and turn["steps"] == []


def test_a_new_question_supersedes_the_pending_draft(tmp_path, isolated_env):
    p = Scripted(_reply(("delete_rows", {"ids": [5]})), _reply(text="ok"))
    s = _session(tmp_path, isolated_env, p)
    first = s.assistant.ask("delete link 5")
    s.assistant.ask("never mind")
    assert s.assistant.draft.status == "superseded"
    assert s.assistant.turns[1].steps[0]["status"] == "superseded" and first["draft_id"] == s.assistant.draft.id


def test_the_payload_reads_no_key_and_lists_the_status(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, Scripted(_reply(text="x"), base_url="https://api.example"))
    s.assistant.ask("hi")
    status = s.assistant.payload()["status"]
    assert status["local"] is False and status["used"] == {"ollama/None": 120} and status["budget"] == 200_000
    assert "select" in status["tools"] and "open_network" not in status["tools"] and status["warnings"] == []
    assert status["sends"] and status["busy"] is False


# ---------------------------------------------------------------------- the allowlists name only what exists


def test_unknown_names_in_the_allowlists_are_refused_when_set(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env)
    from netstead.workbench.errors import ActionError

    with pytest.raises(ActionError, match="no Action named 'colourise'"):
        s.dispatch(SetSetting(key="assistant.actions", value=["style", "colourise"], scope="session"))
    with pytest.raises(ActionError, match="never offered"):
        s.dispatch(SetSetting(key="assistant.actions", value=["open_network"], scope="session"))
    with pytest.raises(ActionError, match="no plugin with the id 'cards'"):
        s.dispatch(SetSetting(key="assistant.plugins", value=["cards"], scope="project"))
    assert s.settings.assistant.plugins == [] and not (tmp_path / ".netstead").exists()  # nothing was saved
    s.dispatch(SetSetting(key="assistant.actions", value=["style", "add_rows"], scope="session"))
    assert s.settings.assistant.actions == ["style", "add_rows"]


def test_unknown_names_in_the_allowlists_are_warned_about_at_load(tmp_path, isolated_env, caplog):
    with caplog.at_level("WARNING", logger="netstead.workbench.session"):
        s = Session(
            project_dir=tmp_path,
            environ=isolated_env,
            parser=StubParser(),
            plugins=[],
            overrides={"assistant.plugins": ["cards"]},
        )
    assert "no plugin with the id 'cards'" in caplog.text
    assert s.assistant.payload()["status"]["warnings"] == ["assistant.plugins: there is no plugin with the id 'cards'"]


# ---------------------------------------------------------------------- approve, reject, clear


def _drafted(tmp_path, isolated_env, *more):
    p = Scripted(_reply(SKI_HILL, ("edit_cells", {"on_selection": True, "values": {"lanes": 3}})), *more)
    s = _session(tmp_path, isolated_env, p)
    return s, s.assistant.ask("set ski hill drive to 3 lanes")


def test_approve_runs_the_draft_and_records_it(tmp_path, isolated_env):
    s, turn = _drafted(tmp_path, isolated_env)
    before = s.network().version
    out = s.assistant.approve(turn["draft_id"])
    assert out["steps"][1]["status"] == "done" and s.history[-1].action["type"] == "edit_cells"
    assert s.history[-1].via == "assistant" and s.assistant.draft.status == "approved"
    assert s.network().version == before + 1 and out["steps"][1]["seq"] == s.history[-1].seq
    with pytest.raises(ValueError, match="no pending draft"):
        s.assistant.approve(turn["draft_id"])


def test_approve_stops_at_the_first_failure(tmp_path, isolated_env):
    p = Scripted(_reply(("edit_cells", {"ids": [1], "values": {"no_such_column": 3}}), ("undo_edit", {})))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("x")
    out = s.assistant.approve(turn["draft_id"])
    assert [st["status"] for st in out["steps"]] == ["failed", "skipped"]
    assert s.history[-1].action["type"] == "edit_cells" and not s.history[-1].ok


def test_a_draft_is_stale_once_its_network_moved(tmp_path, isolated_env):
    s, turn = _drafted(tmp_path, isolated_env)
    s.dispatch({"type": "edit_cells", "ids": [1], "values": {"lanes": 1}})
    with pytest.raises(ValueError, match="changed since"):
        s.assistant.approve(turn["draft_id"])
    assert s.assistant.draft.status == "stale" and s.assistant.turns[-1].steps[1]["status"] == "stale"
    assert s.history[-1].action["values"] == {"lanes": 1}  # nothing of the draft ran


def test_a_draft_on_the_active_network_is_stale_once_another_is_active(tmp_path, isolated_env):
    p = Scripted(_reply(("edit_cells", {"ids": [1], "values": {"lanes": 3}})))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("set link 1 to 3 lanes")
    s.dispatch(OpenNetwork(source=SRC))  # a second copy, now the active network
    assert s.active == "leavenworth-2"
    with pytest.raises(ValueError, match="changed since"):
        s.assistant.approve(turn["draft_id"])
    assert all(h.version == 0 for h in s.registry)


def test_reject_and_supersede(tmp_path, isolated_env):
    s, turn = _drafted(tmp_path, isolated_env)
    out = s.assistant.reject(turn["draft_id"])
    assert s.assistant.draft.status == "rejected" and s.history[-1].action["type"] == "select"
    assert [st["status"] for st in out["steps"]] == ["done", "rejected"]
    with pytest.raises(ValueError, match="no pending draft"):
        s.assistant.reject(turn["draft_id"])


def test_clear_forgets_the_conversation_and_the_draft(tmp_path, isolated_env):
    s, turn = _drafted(tmp_path, isolated_env)
    s.assistant.clear()
    assert s.assistant.turns == [] and s.assistant.draft is None
    with pytest.raises(ValueError, match="no pending draft"):
        s.assistant.approve(turn["draft_id"])
    assert s.history[-1].action["type"] == "select"  # what ran stays in the session history


def test_a_delete_from_a_draft_is_pinned_to_the_drafted_version(tmp_path, isolated_env):
    p = Scripted(_reply(("delete_rows", {"ids": [5]})))
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("delete link 5")
    s.assistant.approve(turn["draft_id"])
    assert s.history[-1].ok and s.history[-1].action["expected_version"] == 0
    assert "expected_version" not in s.history[-1].python


def test_every_change_publishes_an_assistant_event(tmp_path, isolated_env):
    s, turn = _drafted(tmp_path, isolated_env)
    seen = []
    s.events.publish = seen.append
    s.assistant.reject(turn["draft_id"])
    s.assistant.clear()
    assert [e["type"] for e in seen] == ["assistant", "assistant"] and seen[-1]["assistant"]["turns"] == []


def test_a_failed_repair_call_still_charges_the_answer_before_it(tmp_path, isolated_env):
    from netstead.llm.errors import ProviderUnavailable

    class FailsOnRepair(Scripted):
        def complete(self, request):
            if self.requests:
                raise ProviderUnavailable("ollama", "down")
            return super().complete(request)

    p = FailsOnRepair(_reply(("style", {"color_by": 3}), tokens=(700, 30)), base_url="https://api.example")
    s = _session(tmp_path, isolated_env, p)
    turn = s.assistant.ask("colour")
    assert turn["error"] == "down" and s.assistant.used == {"ollama/None": 730}
