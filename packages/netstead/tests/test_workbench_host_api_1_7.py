"""Host API 1.7: reading inside the allowed roots (``readable``, ``list_dir``) and a read-only view of history."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import EditCells, OpenNetwork
from netstead.workbench.errors import PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionError, ActionSpec, BaseAction, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


class Go(BaseAction):
    """A recorded plugin Action (its handler does nothing)."""

    type: Literal["probe.go"] = "probe.go"
    mutates: ClassVar[bool] = True
    fail: bool = False


class Other(BaseAction):
    """A second plugin Action type, to filter history by prefix."""

    type: Literal["probe.other"] = "probe.other"


def _session(tmp_path: Path, isolated_env: dict[str, str]) -> Session:
    def go(host, action: Go) -> None:
        if action.fail:
            raise ActionError("no")

    probe = WorkbenchPlugin(
        id="probe",
        name="Probe",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(Go, go), ActionSpec(Other, lambda host, action: None)),
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[probe])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def test_this_netstead_provides_host_api_1_7():
    assert HOST_API == "1.7"


# ---------------------------------------------------------------------------- readable


def test_readable_resolves_an_existing_path_inside_the_roots(tmp_path, isolated_env):
    host = _session(tmp_path, isolated_env)._hosts["probe"]
    card = tmp_path / "cards" / "a.yml"
    card.parent.mkdir()
    card.write_text("project: a\n", encoding="utf-8")
    assert host.readable(card) == card.resolve()
    assert host.readable(str(tmp_path / "cards")) == (tmp_path / "cards").resolve()  # a folder reads too


def test_readable_refuses_a_path_outside_the_roots_before_looking_at_the_disk(tmp_path, isolated_env):
    host = _session(tmp_path, isolated_env)._hosts["probe"]
    with pytest.raises(PathNotAllowed):
        host.readable(tmp_path.parent / "elsewhere.yml")
    with pytest.raises(PathNotAllowed):
        host.readable(tmp_path / ".." / "escape.yml")


def test_readable_requires_the_path_to_exist(tmp_path, isolated_env):
    host = _session(tmp_path, isolated_env)._hosts["probe"]
    with pytest.raises(ActionError, match=r"no such file or folder: .*missing\.yml"):
        host.readable(tmp_path / "missing.yml")


# ---------------------------------------------------------------------------- list_dir


def test_list_dir_lists_files_and_folders_and_skips_hidden_entries(tmp_path, isolated_env):
    host = _session(tmp_path, isolated_env)._hosts["probe"]
    folder = tmp_path / "cards"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.yml").write_text("project: a\n", encoding="utf-8")
    (folder / ".hidden.yml").write_text("project: h\n", encoding="utf-8")
    entries = host.list_dir(folder)
    assert entries == [
        {"name": "sub", "path": str(folder / "sub"), "kind": None, "is_dir": True},
        {"name": "a.yml", "path": str(folder / "a.yml"), "kind": None, "is_dir": False},
    ]
    assert host.list_dir(str(folder)) == entries


def test_list_dir_refusals(tmp_path, isolated_env):
    host = _session(tmp_path, isolated_env)._hosts["probe"]
    with pytest.raises(PathNotAllowed):
        host.list_dir(tmp_path.parent)
    with pytest.raises(ActionError, match="no such folder"):
        host.list_dir(tmp_path / "nope")
    (tmp_path / "file.yml").write_text("x", encoding="utf-8")
    with pytest.raises(ActionError, match="not a folder"):
        host.list_dir(tmp_path / "file.yml")


# ---------------------------------------------------------------------------- history


def test_history_is_a_read_only_view_filtered_by_prefix(tmp_path, isolated_env):
    s = _session(tmp_path, isolated_env)
    host = s._hosts["probe"]
    s.dispatch(Go())
    s.dispatch(EditCells(ids=[1], values={"lanes": 2}))
    s.dispatch(Other())
    assert not s.dispatch_recorded(Go(fail=True)).ok
    everything = host.history()
    assert [e["action"]["type"] for e in everything] == [
        "open_network",
        "probe.go",
        "edit_cells",
        "probe.other",
        "probe.go",
    ]
    mine = host.history("probe.")
    assert [(e["seq"], e["action"]["type"], e["ok"]) for e in mine] == [
        (2, "probe.go", True),
        (4, "probe.other", True),
        (5, "probe.go", False),
    ]
    assert mine[-1]["error"] == "no" and "versions_after" not in mine[0]
    assert set(mine[0]) >= {"seq", "action", "python", "ok", "error", "ts", "via"}
    mine[0]["action"]["type"] = "tampered"  # a copy: the session's record is untouched
    assert s.history[1].action["type"] == "probe.go"
