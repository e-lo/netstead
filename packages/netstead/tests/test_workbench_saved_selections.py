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
    PinAnchor,
    SaveSelection,
    Select,
    UseSelection,
    script_imports,
)
from netstead.workbench.editing import plan_delete, plan_update
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
            "stale": False,
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


def test_exporting_a_save_whose_network_is_open_from_another_source_exports_it_unchecked(session, rdu_source):
    session.dispatch(Select(link_ids=[1, 2]))
    session.dispatch(SaveSelection(name="two"))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    session.dispatch(OpenNetwork(source=rdu_source, net_id="leavenworth"))  # another file, the same id
    found = session.saved_selection_now("two")
    assert found is not None
    sel, notes = found
    assert sel["link_ids"] == [1, 2] and sel["source"] == SRC  # as saved, not resolved on the other file
    assert len(notes) == 1
    why = r"'leavenworth' is open from another source \(.*rdu.*\)"
    assert re.fullmatch(why + r": exported 'two' as saved \(version 0\), without checking it", notes[0])


def test_a_save_keeps_what_using_and_exporting_need_not_the_whole_payload(session):
    sel = session.dispatch(Select(utterance="Ski Hill Drive"))
    session.dispatch(SaveSelection(name="ski"))
    saved = session.saved_selection("ski")
    assert set(saved) == {
        "net_id",
        "source",
        "version",
        "status",
        "utterance",
        "intent",
        "parsed_by",
        "link_ids",
        "anchors",
        "stale_pins",
    }
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


class Forget(BaseAction):
    type: Literal["hoard.delete_and_fail"] = "hoard.delete_and_fail"


def _forget(host, action):
    host.dispatch(DeleteSelection(name="kept"))
    raise RuntimeError("boom")


def test_a_failed_action_rolls_back_a_delete_it_made(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="hoard", name="Hoard", version="0", requires_api=HOST_API, actions=(ActionSpec(Forget, _forget),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(Select(link_ids=[1]))
    s.dispatch(SaveSelection(name="kept"))
    assert not s.dispatch_recorded(Forget()).ok
    assert [x["name"] for x in s.saved_selections()] == ["kept"]


def test_save_use_and_delete_replay_with_their_own_import_lines(session):
    session.dispatch(Select(link_ids=[1]))
    entries = [
        session.dispatch_recorded(SaveSelection(name="one")),
        session.dispatch_recorded(UseSelection(name="one")),
        session.dispatch_recorded(DeleteSelection(name="one")),
    ]
    assert [e.python for e in entries] == [
        "app.do(SaveSelection(name='one'))",
        "app.do(UseSelection(name='one'))",
        "app.do(DeleteSelection(name='one'))",
    ]
    assert script_imports([e.imports for e in entries])[1:] == [
        "from netstead.workbench import SaveSelection",
        "from netstead.workbench import UseSelection",
        "from netstead.workbench import DeleteSelection",
    ]


def test_using_a_pinned_save_after_an_edit_keeps_its_pin(session):
    session.dispatch(Select(utterance="Cherry Street between Prospect Street and West Center Street"))
    session.dispatch(PinAnchor(role="to", node_id=60))
    session.dispatch(SaveSelection(name="cherry"))
    session.dispatch(Select(link_ids=[1]))
    plan = plan_update(session.network().roadway, "link", [193, 195], {"name": "Cherry Lane"})
    session.mutate("leavenworth", plan.edits, note="rename")  # the facility changes; node 60 stays on it
    sel = session.dispatch(UseSelection(name="cherry"))
    assert sel["status"] == "resolved" and sel["link_ids"] == [170, 182, 187] and sel["intent"]["to_node"] == 60
    assert "facility ['Cherry Street']: 6 link(s)" in sel["diagnostics"]  # resolved on the network as it is now


def test_a_save_made_after_a_pin_went_stale_says_so(session):
    from netstead.workbench.plugins.host import Host

    session.dispatch(Select(utterance="Cherry Street between Prospect Street and West Center Street"))
    session.dispatch(PinAnchor(role="to", node_id=60))
    plan = plan_update(session.network().roadway, "link", [187, 189], {"name": "Cherry Court"})
    session.mutate("leavenworth", plan.edits, note="node 60 leaves Cherry Street")
    session.dispatch(SaveSelection(name="cherry"))
    assert session.saved_selection("cherry")["stale_pins"] == [60]
    assert session.state()["saved_selections"][0]["stale"] is True
    saved = Host(session, "probe").saved_selection("cherry")
    assert saved["stale_pins"] == [60] and "re-pin the to anchor" in saved["warnings"][0]
    sel = session.dispatch(UseSelection(name="cherry"))  # still off the facility: the fallback again
    assert sel["stale_pins"] == [60] and sel["link_ids"] == [170, 182, 187]


def test_a_remote_save_keeps_its_locator_not_its_signature(session):
    from netstead import Network

    loaded = Network.from_source(SRC)
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    session.add_network(loaded, source="https://bucket.example.com/lw/?X-Amz-Signature=one", net_id="lw")
    session.dispatch(Select(link_ids=[1, 2]))
    session.dispatch(SaveSelection(name="remote"))
    saved = session.saved_selection("remote")
    assert saved["source"] == "https://bucket.example.com/lw/" and "Signature" not in str(session.saved_selections())
    session.dispatch(CloseNetwork(net_id="lw"))
    session.add_network(loaded, source="https://bucket.example.com/lw/?X-Amz-Signature=two", net_id="lw")
    assert session.dispatch(UseSelection(name="remote"))["link_ids"] == [1, 2]  # the same object, signed again
