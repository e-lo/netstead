"""Named, saved selections: recorded, re-resolved on use, rolled back with a failed Action."""

import re
from typing import Literal

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import (
    BaseAction,
    CloseNetwork,
    DeleteSelection,
    OpenNetwork,
    SaveSelection,
    Select,
    UseSelection,
)
from netstead.workbench.editing import plan_delete
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _drop(session, link_id):
    session.mutate("leavenworth", plan_delete(session.network().roadway, "link", [link_id]).edits, note="drop")


def test_save_then_use_restores_the_selection(session):
    first = session.dispatch(Select(utterance="Ski Hill Drive"))
    session.dispatch(SaveSelection(name="ski hill"))
    session.dispatch(Select(link_ids=[1]))
    sel = session.dispatch(UseSelection(name="ski hill"))
    assert sel["link_ids"] == first["link_ids"] and sel["utterance"] == "Ski Hill Drive"
    assert session.state()["saved_selections"] == [
        {
            "name": "ski hill",
            "net_id": "leavenworth",
            "status": "resolved",
            "links": 20,
            "utterance": "Ski Hill Drive",
            "by": "query",
        }
    ]


def test_use_re_resolves_on_the_network_as_it_is_now(session):
    session.dispatch(Select(utterance="Ski Hill Drive"))
    gone = session.selection["link_ids"][0]
    session.dispatch(SaveSelection(name="ski"))
    _drop(session, gone)
    assert gone not in session.dispatch(UseSelection(name="ski"))["link_ids"]
    assert len(session.saved_selection("ski")["link_ids"]) == 20  # what was saved stays as saved


def test_an_id_selection_keeps_the_links_still_there(session):
    session.dispatch(Select(link_ids=[1, 2]))
    session.dispatch(SaveSelection(name="two"))
    _drop(session, 1)
    assert session.dispatch(UseSelection(name="two"))["link_ids"] == [2]


def test_names_are_unique_checked_and_deletable(session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(SaveSelection(name="a"))
    with pytest.raises(ActionError, match="already saved"):
        session.dispatch(SaveSelection(name="a"))
    for bad in ("../evil", " lead", "trail ", "", "x" * 65):
        with pytest.raises(ValueError):
            SaveSelection(name=bad)
    assert SaveSelection(name="a b.c_d-1").name == "a b.c_d-1" and SaveSelection(name="x" * 64)
    session.dispatch(DeleteSelection(name="a"))
    with pytest.raises(ActionError, match="no saved selection named 'a'"):
        session.dispatch(UseSelection(name="a"))


def test_save_needs_a_selection_and_use_needs_its_network(session):
    with pytest.raises(ActionError, match="nothing is selected"):
        session.dispatch(SaveSelection(name="x"))
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(SaveSelection(name="x"))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    with pytest.raises(ActionError, match=re.escape(f"'leavenworth' is not open; open {SRC} to use 'x'")):
        session.dispatch(UseSelection(name="x"))


def test_a_save_belongs_to_its_source_not_just_its_network_id(session, rdu_source):
    session.dispatch(Select(link_ids=[1, 2]))
    session.dispatch(SaveSelection(name="two"))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    session.dispatch(OpenNetwork(source=rdu_source, net_id="leavenworth"))  # another file, the same id
    assert session.active == "leavenworth"
    with pytest.raises(
        ActionError,
        match=r"'two' was saved on 'leavenworth' from .*leavenworth.*, but the open 'leavenworth' comes from .*rdu",
    ):
        session.dispatch(UseSelection(name="two"))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    session.dispatch(OpenNetwork(source=SRC))  # the same file again: the save is useful again
    assert session.dispatch(UseSelection(name="two"))["link_ids"] == [1, 2]


def test_a_save_keeps_what_using_and_exporting_need_not_the_whole_payload(session):
    sel = session.dispatch(Select(utterance="Ski Hill Drive"))
    session.dispatch(SaveSelection(name="ski"))
    saved = session.saved_selection("ski")
    assert set(saved) == {"net_id", "source", "status", "utterance", "intent", "parsed_by", "link_ids", "anchors"}
    assert saved["source"] == SRC and saved["intent"] == sel["intent"] and saved["link_ids"] == sel["link_ids"]


def test_saved_selections_are_capped(session):
    from netstead.workbench.session import MAX_SAVED_SELECTIONS

    session.dispatch(Select(link_ids=[1]))
    for i in range(MAX_SAVED_SELECTIONS):
        session.dispatch(SaveSelection(name=f"s{i}"))
    with pytest.raises(ActionError, match=f"{MAX_SAVED_SELECTIONS} selections are saved already; delete one"):
        session.dispatch(SaveSelection(name="one more"))
    session.dispatch(DeleteSelection(name="s0"))
    session.dispatch(SaveSelection(name="one more"))


class Hoard(BaseAction):
    type: Literal["hoard.save_and_fail"] = "hoard.save_and_fail"


def _hoard(host, action):
    host.dispatch(SaveSelection(name="kept"))
    raise RuntimeError("boom")


def test_a_failed_action_rolls_back_a_save_it_made(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="hoard", name="Hoard", version="0", requires_api=HOST_API, actions=(ActionSpec(Hoard, _hoard),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(Select(link_ids=[1]))
    assert not s.dispatch_recorded(Hoard()).ok
    assert s.saved_selections() == []
