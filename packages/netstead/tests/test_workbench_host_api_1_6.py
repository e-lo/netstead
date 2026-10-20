"""Host API 1.6: readable storage refusals, stable edit ids, a rollback hook, and compact-mode plugin tools."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, ClassVar, Literal

import duckdb
import pytest
from corral.editing import Edit
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import CloseNetwork, EditCells, OpenNetwork
from netstead.workbench.assistant.tools import tool_specs
from netstead.workbench.plugins import (
    HOST_API,
    ActionError,
    ActionSpec,
    BaseAction,
    Mutation,
    PendingEdits,
    WorkbenchPlugin,
)

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


class Catcher(BaseAction):
    """Dispatch a failing ``probe.go``, catch its error, and succeed."""

    type: Literal["probe.catcher"] = "probe.catcher"
    mutates: ClassVar[bool] = True


def _session(tmp_path, isolated_env, handler, **plugin: Any) -> Session:
    def parent(host, action: Parent) -> None:
        host.dispatch(Go())
        raise ActionError("the parent failed")

    def catcher(host, action: Catcher) -> str:
        try:
            host.dispatch(Go(fail=True))
        except ActionError:
            return "caught"
        return "not raised"

    probe = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(Go, handler), ActionSpec(Parent, parent), ActionSpec(Catcher, catcher)),
        **plugin,
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[probe])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _lanes(host, link_id: int, value: int) -> Mutation:
    plan = host.plan_update(None, "link", [link_id], {"lanes": value})
    return host.mutate(None, plan.edits, note=f"lanes {link_id}", expected_version=plan.version)


def _seqs(host) -> list[int]:
    return [e["seq"] for e in host.pending_edits().edits]


def test_this_netstead_provides_host_api_1_6():
    assert HOST_API == "1.6"


# ---------------------------------------------------------------------------- storage errors are refusals


def test_a_failed_plugin_edit_is_a_readable_refusal_not_an_internal_error(tmp_path, isolated_env):
    def handler(host, action: Go) -> None:
        host.mutate(None, [Edit(op="update_rows", table="nope", payload={})], note="bad")

    s = _session(tmp_path, isolated_env, handler)
    entry = s.dispatch_recorded(Go())
    assert not entry.ok and entry.error_type == "ActionError"
    assert entry.error.startswith("the edit failed: UnknownTable:") and "nope" in entry.error
    assert s.network().version == 0 and NET not in s.edits


def test_a_failed_edit_outside_an_action_raises_action_error(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    with pytest.raises(ActionError, match="the edit failed"):
        s._hosts["probe"].mutate(None, [Edit(op="frobnicate", table="link", payload={})], note="bad")


SECRET_MESSAGE = (
    "HTTP 403 for https://bucket.s3.amazonaws.com/k.parquet?X-Amz-Signature=deadbeef&X-Amz-Credential=AKIAQQ "
    "via s3://alice:hunter2@minio.local/net and key sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
)


def _assert_scrubbed(text: str) -> None:
    for secret in ("deadbeef", "AKIAQQ", "hunter2", "alice", "sk-ant-api03-abcdef"):
        assert secret not in text, (secret, text)


def test_a_storage_error_s_secrets_never_reach_the_recorded_error(tmp_path, isolated_env, monkeypatch, caplog):
    from netstead.workbench import session as session_module

    def leaky(draft, edit):
        raise duckdb.IOException(SECRET_MESSAGE)

    def handler(host, action: Go) -> None:
        _lanes(host, 1, 2)

    s = _session(tmp_path, isolated_env, handler)
    monkeypatch.setattr(session_module, "apply_edit", leaky)
    with caplog.at_level(logging.WARNING):
        entry = s.dispatch_recorded(Go())
    assert entry.error_type == "ActionError" and entry.error.startswith("the edit failed: IOException: HTTP 403")
    _assert_scrubbed(entry.error)
    assert "IOException" in caplog.text  # logged with its traceback, for the person running the app


def test_a_storage_error_while_undoing_is_scrubbed_too(tmp_path, isolated_env, monkeypatch):
    from netstead.workbench import session as session_module

    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    _lanes(host, 1, 2)

    def leaky(draft, result):
        raise duckdb.IOException(SECRET_MESSAGE)

    monkeypatch.setattr(session_module, "reverse_edit", leaky)
    with pytest.raises(ActionError, match="the undo failed: IOException") as info:
        host.undo()
    _assert_scrubbed(str(info.value))
    assert _seqs(host) == [1]  # nothing was undone


def test_a_failure_after_the_edit_applied_is_not_reported_as_a_failed_edit(tmp_path, isolated_env, monkeypatch):
    """Only applying the edits to the draft is wrapped: a failure once they are in (here a storage-shaped one while
    settling) stays what it is, so nobody is told the edit failed when the network holds it."""

    def handler(host, action: Go) -> None:
        _lanes(host, 1, 2)

    s = _session(tmp_path, isolated_env, handler)

    def boom(*args, **kwargs):
        raise duckdb.IOException("while settling")

    monkeypatch.setattr(s, "_settle", boom)
    entry = s.dispatch_recorded(Go())
    assert entry.error_type == "InternalError" and "the edit failed" not in entry.error


def test_core_edit_actions_report_storage_errors_the_same_way(tmp_path, isolated_env, monkeypatch):
    from netstead.workbench import session as session_module

    s = _session(tmp_path, isolated_env, lambda host, action: None)
    monkeypatch.setattr(
        session_module, "apply_edit", lambda draft, edit: (_ for _ in ()).throw(duckdb.IOException("x"))
    )
    entry = s.dispatch_recorded(EditCells(ids=[1], values={"lanes": 2}))
    assert entry.error_type == "ActionError" and entry.error == "the edit failed: IOException: x"


# ---------------------------------------------------------------------------- stable edit ids


def test_mutate_returns_a_frozen_mutation_with_its_results_and_seq(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    first, second = _lanes(host, 1, 2), _lanes(host, 2, 3)
    assert isinstance(first.results, tuple) and len(first.results) == 1 and first.results[0].edit.table == "link"
    assert (first.seq, second.seq) == (1, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.seq = 9  # type: ignore[misc]
    assert host.mutate(None, [], note="nothing") == Mutation(results=(), seq=None)  # no pending edit


def test_pending_edits_lists_every_source_oldest_first(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    assert host.pending_edits() == PendingEdits(issued=0, edits=())
    mine = _lanes(host, 1, 2)
    s.dispatch(EditCells(ids=[2], values={"lanes": 3}))
    pending = host.pending_edits()
    assert pending.issued == 2
    assert [(e["seq"], e["source"], e["version"]) for e in pending.edits] == [
        (mine.seq, "probe", 1),
        (2, "workbench", 2),
    ]
    assert pending.edits[0]["note"] == "lanes 1" and host.pending_edits(NET) == pending
    with pytest.raises(dataclasses.FrozenInstanceError):
        pending.issued = 0  # type: ignore[misc]
    with pytest.raises(ActionError):
        host.pending_edits("no-such-network")


def test_undo_reports_the_seq_it_reversed_and_issued_keeps_its_high_water(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    first, second = _lanes(host, 1, 2), _lanes(host, 2, 3)
    assert host.undo()["seq"] == second.seq and host.undo()["seq"] == first.seq
    assert host.pending_edits() == PendingEdits(issued=2, edits=())


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
    assert seqs == [1, 2, 3, 4] and _seqs(s._hosts["probe"]) == [2, 4]


def test_a_reopened_network_id_continues_the_seqs(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    host = s._hosts["probe"]
    _lanes(host, 1, 2)
    _lanes(host, 2, 3)
    s.dispatch(CloseNetwork(net_id=NET))
    s.dispatch(OpenNetwork(source=SRC, net_id=NET))
    assert host.pending_edits() == PendingEdits(issued=2, edits=())
    assert _lanes(host, 1, 2).seq == 3


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


def test_a_nested_failure_caught_by_its_parent_runs_the_callback_exactly_once(tmp_path, isolated_env):
    calls = []

    def handler(host, action: Go) -> None:
        host.on_rollback(lambda: calls.append(action.fail))
        if action.fail:
            raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    assert s.dispatch(Catcher()) == "caught" and calls == [True]
    s.dispatch(Go())
    assert not s.dispatch_recorded(Parent()).ok  # a later rollback runs only its own callbacks
    assert calls == [True, False]


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


def test_a_callback_raising_base_exception_finishes_the_rollback_then_reraises(tmp_path, isolated_env):
    calls = []

    def stop() -> None:
        raise KeyboardInterrupt

    def handler(host, action: Go) -> None:
        host.on_rollback(lambda: calls.append("earlier"))
        _lanes(host, 1, 2)
        host.on_rollback(stop)
        raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    with pytest.raises(KeyboardInterrupt):
        s.dispatch_recorded(Go())
    assert calls == ["earlier"] and s.network().version == 0 and NET not in s.edits
    assert s._undo == [] and not s._rolling_back


@pytest.mark.parametrize("call", ["mutate", "derive", "undo", "on_rollback"])
def test_host_calls_from_a_rollback_callback_are_refused(tmp_path, isolated_env, caplog, call):
    def misbehave(host) -> None:
        if call == "mutate":
            _lanes(host, 2, 3)
        elif call == "derive":
            host.derive(None, label="x", note="x")
        elif call == "undo":
            host.undo()
        else:
            host.on_rollback(lambda: None)

    def handler(host, action: Go) -> None:
        _lanes(host, 1, 2)
        host.on_rollback(lambda: misbehave(host))
        raise ActionError("no")

    s = _session(tmp_path, isolated_env, handler)
    with caplog.at_level(logging.ERROR):
        assert s.dispatch_recorded(Go()).error == "no"
    assert "cannot be called from a rollback callback" in caplog.text
    assert s.network().version == 0 and s.registry.ids() == [NET] and NET not in s.edits


def test_on_rollback_outside_an_action_is_refused(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env, lambda host, action: None)
    with pytest.raises(RuntimeError, match="inside an Action handler"):
        s._hosts["probe"].on_rollback(lambda: None)


# ---------------------------------------------------------------------------- compact-mode plugin tools


class Tidy(BaseAction):
    """An everyday plugin edit, offered to small models too."""

    type: Literal["probe.tidy"] = "probe.tidy"
    mutates: ClassVar[bool] = True
    assistant_compact: ClassVar[bool] = True


class Fielded(BaseAction):
    """An Action with an ordinary field named ``compact``: not the class flag."""

    type: Literal["probe.fielded"] = "probe.fielded"
    mutates: ClassVar[bool] = True
    compact: bool = False


def test_a_plugin_action_marked_assistant_compact_is_offered_in_the_compact_tool_set(tmp_path, isolated_env):
    probe = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=tuple(ActionSpec(m, lambda host, a: None) for m in (Go, Tidy, Fielded)),
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
    assert {"probe.go", "probe.tidy", "probe.fielded"} <= full
    assert "probe.tidy" in compact and not {"probe.go", "probe.fielded"} & compact
    assert {"edit_cells", "delete_rows", "undo_edit"} <= compact and "save_network" not in compact  # core unchanged
    catalog = {a["type"]: a for a in s.action_catalog()}
    assert catalog["probe.tidy"]["assistant_compact"] and not catalog["probe.go"]["assistant_compact"]
    assert catalog["edit_cells"]["assistant_compact"] and not catalog["save_network"]["assistant_compact"]


def test_the_core_everyday_edits_are_the_ones_marked_assistant_compact():
    from netstead.workbench.actions import CORE_ACTIONS

    marked = {m.action_type() for m in CORE_ACTIONS if m.mutates and m.assistant_compact}
    assert marked == {"edit_cells", "delete_rows", "undo_edit"}
