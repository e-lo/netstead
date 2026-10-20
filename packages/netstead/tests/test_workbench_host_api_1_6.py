"""Host API 1.6: readable storage refusals, stable edit ids, a rollback hook, and compact-mode plugin tools."""

from __future__ import annotations

import logging
from typing import Any, ClassVar, Literal

import pytest
from corral.editing import Edit
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import EditCells, OpenNetwork
from netstead.workbench.assistant.tools import tool_specs
from netstead.workbench.plugins import HOST_API, ActionError, ActionSpec, BaseAction, Mutation, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())
NET = "leavenworth"


class Go(BaseAction):
    """Run the test's handler."""

    type: Literal["probe.go"] = "probe.go"
    mutates: ClassVar[bool] = True
    fail: bool = False


class Parent(BaseAction):
    """Dispatch ``probe.go`` (succeeding), then fail."""

    type: Literal["probe.parent"] = "probe.parent"
    mutates: ClassVar[bool] = True


def _session(tmp_path, isolated_env, handler, **plugin: Any) -> Session:
    def parent(host, action: Parent) -> None:
        host.dispatch(Go())
        raise ActionError("the parent failed")

    probe = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(Go, handler), ActionSpec(Parent, parent)),
        **plugin,
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[probe])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _lanes(host, link_id: int, value: int) -> Mutation:
    plan = host.plan_update(None, "link", [link_id], {"lanes": value})
    return host.mutate(None, plan.edits, note=f"lanes {link_id}", expected_version=plan.version)


def test_this_netstead_provides_host_api_1_6():
    assert HOST_API == "1.6"


# ---------------------------------------------------------------------------- mutate wraps storage-layer errors


def test_a_failed_plugin_edit_is_a_readable_refusal_not_an_internal_error(tmp_path, isolated_env):
    def handler(host, action: Go) -> None:
        host.mutate(None, [Edit(op="update_rows", table="nope", payload={})], note="bad")

    s = _session(tmp_path, isolated_env, handler)
    entry = s.dispatch_recorded(Go())
    assert not entry.ok and entry.error_type == "ActionError"
    assert entry.error.startswith("the edit failed:") and "nope" in entry.error
    assert s.network().version == 0 and NET not in s.edits


def test_a_failed_edit_outside_an_action_raises_action_error(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    with pytest.raises(ActionError, match="the edit failed"):
        s._hosts["probe"].mutate(None, [Edit(op="frobnicate", table="link", payload={})], note="bad")


# ---------------------------------------------------------------------------- stable edit ids


def test_mutate_returns_its_results_and_the_pending_edits_seq(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    first, second = _lanes(host, 1, 2), _lanes(host, 2, 3)
    assert isinstance(first, list) and len(first) == 1 and first[0].edit.table == "link"  # still the results
    assert (first.seq, second.seq) == (1, 2)
    assert host.mutate(None, [], note="nothing").seq is None  # no pending edit


def test_pending_edits_lists_every_source_oldest_first(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    mine = _lanes(host, 1, 2)
    s.dispatch(EditCells(ids=[2], values={"lanes": 3}))
    edits = host.pending_edits()
    assert [(e["seq"], e["source"], e["version"]) for e in edits] == [(mine.seq, "probe", 1), (2, "workbench", 2)]
    assert edits[0]["note"] == "lanes 1" and host.pending_edits(NET) == edits
    with pytest.raises(ActionError):
        host.pending_edits("no-such-network")


def test_undo_reports_the_seq_it_reversed(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    first, second = _lanes(host, 1, 2), _lanes(host, 2, 3)
    assert host.undo()["seq"] == second.seq and host.undo()["seq"] == first.seq and host.pending_edits() == []


def test_a_rolled_back_edits_seq_is_never_reused(tmp_path, isolated_env):
    """A plugin holding the seq of an edit that was rolled back must not find a later edit under it."""
    seqs = []

    def handler(host, action: Go) -> None:
        seqs.append(_lanes(host, 1, 2).seq)
        if action.fail:
            raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    assert not s.dispatch_recorded(Go(fail=True)).ok  # the network had no ledger before it
    s.dispatch(Go())
    assert not s.dispatch_recorded(Go(fail=True)).ok  # the ledger existed before it
    s.dispatch(Go())
    assert seqs == [1, 2, 3, 4] and [e["seq"] for e in s._hosts["probe"].pending_edits()] == [2, 4]


# ---------------------------------------------------------------------------- the rollback hook


def test_on_rollback_runs_when_the_handler_fails_and_not_when_it_succeeds(tmp_path, isolated_env):
    calls = []

    def handler(host, action: Go) -> None:
        host.on_rollback(lambda: calls.append(("undo", host.network().version)))
        _lanes(host, 1, 2)
        if action.fail:
            raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    s.dispatch(Go())
    assert calls == []
    assert not s.dispatch_recorded(Go(fail=True)).ok
    assert calls == [("undo", 1)]  # LIFO: the edit after it was undone first, so the network is back at 1


def test_on_rollback_runs_when_a_parent_fails_after_the_nested_action_succeeded(tmp_path, isolated_env):
    calls = []

    def handler(host, action: Go) -> None:
        host.on_rollback(lambda: calls.append("first"))
        host.on_rollback(lambda: calls.append("second"))

    s = _session(tmp_path, isolated_env, handler)
    entry = s.dispatch_recorded(Parent())
    assert not entry.ok and calls == ["second", "first"]  # newest first
    calls.clear()
    s.dispatch(Go())  # succeeded outermost: its callbacks are dropped, never called later
    assert not s.dispatch_recorded(Parent()).ok and calls == ["second", "first"]


def test_a_failing_rollback_callback_is_logged_and_the_rest_of_the_rollback_still_runs(tmp_path, isolated_env, caplog):
    calls = []

    def handler(host, action: Go) -> None:
        host.on_rollback(lambda: calls.append("earlier"))
        _lanes(host, 1, 2)
        host.on_rollback(lambda: 1 / 0)
        raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    with caplog.at_level(logging.ERROR):
        entry = s.dispatch_recorded(Go())
    assert entry.error == "no" and calls == ["earlier"] and s.network().version == 0
    assert "probe" in caplog.text and "ZeroDivisionError" in caplog.text


def test_on_rollback_outside_an_action_is_refused(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    with pytest.raises(RuntimeError, match="inside an Action handler"):
        s._hosts["probe"].on_rollback(lambda: None)


# ---------------------------------------------------------------------------- compact-mode plugin tools


class Tidy(BaseAction):
    """An everyday plugin edit, offered to small models too."""

    type: Literal["probe.tidy"] = "probe.tidy"
    mutates: ClassVar[bool] = True
    compact: ClassVar[bool] = True


def test_a_plugin_action_marked_compact_is_offered_in_the_compact_tool_set(tmp_path, isolated_env):
    probe = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(Go, lambda host, a: None), ActionSpec(Tidy, lambda host, a: None)),
    )
    s = Session(
        project_dir=tmp_path,
        environ=isolated_env,
        parser=StubParser(),
        plugins=[probe],
        overrides={"assistant.plugins": ["probe"]},
    )
    s.dispatch(OpenNetwork(source=SRC))
    full = {t.action_type for t in tool_specs(s)}
    compact = {t.action_type for t in tool_specs(s, compact=True)}
    assert {"probe.go", "probe.tidy"} <= full
    assert "probe.tidy" in compact and "probe.go" not in compact  # go mutates and is not marked compact
    assert {"edit_cells", "delete_rows", "undo_edit"} <= compact and "save_network" not in compact  # core unchanged


def test_the_core_everyday_edits_are_the_ones_marked_compact():
    from netstead.workbench.actions import CORE_ACTIONS

    marked = {m.action_type() for m in CORE_ACTIONS if m.mutates and m.compact}
    assert marked == {"edit_cells", "delete_rows", "undo_edit"}
