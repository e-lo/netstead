"""Pending edits: every mutation is recorded, re-checked live, reverted last-first, and shown as dirty state."""

from typing import ClassVar, Literal

import pytest
from corral.editing import Edit, UnsupportedEditOp
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import BaseAction, CloseNetwork, OpenNetwork, Select
from netstead.workbench.editing import plan_add, plan_delete, plan_update
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def handle(s):
    return s.network("leavenworth")


def cell(s, link_id, column="free_speed"):
    df = handle(s).links_df()
    return df.loc[df.link_id == link_id, column].tolist()


def mutate(s, plan, **kwargs):
    return s.mutate("leavenworth", plan.edits, note=plan.summary, **kwargs)


def test_a_mutation_is_a_pending_edit(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"free_speed": 30}))
    (entry,) = session.edits["leavenworth"].entries
    assert (entry.seq, entry.source, entry.version, entry.touched) == (1, "python", 1, {"link": {1}})
    assert entry.view()["rows"] == {"link": 1}
    assert session.state()["edits"]["leavenworth"] == {
        "pending": 1,
        "unsaved": 1,
        "dirty": True,
        "warnings": 0,
        "truncated": False,
        "last_source": "python",
    }
    assert session.history[-1].action["type"] == "python.mutate"  # Part 1: recorded outside an Action


def test_live_warnings_follow_later_edits(session):
    net = handle(session).roadway
    mutate(session, plan_update(net, "link", [1], {"from_node_id": 424242}))
    assert [w.code for w in session.edit_check("leavenworth").warnings] == ["edit.fk_missing"]
    mutate(session, plan_add(net, "node", [{"node_id": 424242, "x_coord": -120.66, "y_coord": 47.6}]))
    assert session.edit_check("leavenworth").warnings == ()  # the second edit fixed the first one's key


def test_undo_reverses_the_last_edit_only(session):
    h = handle(session)
    mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 30}))
    mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 25}))
    undone = session.undo_last("leavenworth", source="python")
    assert undone.seq == 2 and cell(session, 1) == [30.0] and h.version == 3
    assert h.lineage[-1] == "undo: link 1: free_speed = 25.0"
    assert session.history[-1].action["type"] == "python.revert"  # outside an Action: recorded, not replayable
    session.undo_last("leavenworth", source="python")
    assert cell(session, 1) == [40.0] and session.edits["leavenworth"].entries == []
    with pytest.raises(ActionError, match="nothing to undo"):
        session.undo_last("leavenworth", source="python")


def test_only_the_newest_change_can_be_reverted(session):
    h = handle(session)
    first = mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 30}))
    mutate(session, plan_update(h.roadway, "link", [2], {"free_speed": 30}))
    with pytest.raises(ActionError, match="only the newest change"):
        session.revert("leavenworth", first, note="undo")
    assert h.version == 2 and len(session.edits["leavenworth"].entries) == 2


def test_undo_restores_a_row_that_holds_nulls(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=str(leavenworth.csv_dir())))
    h = s.network()
    s.mutate(h.id, plan_update(h.roadway, "link", [27], {"lanes": 3}).edits, note="lanes")  # link 27's name is null
    s.undo_last(h.id, source="python")
    assert int((h.links_df().link_id == 27).sum()) == 1  # Task 1: not two copies


def test_core_does_not_undo_a_plugins_change(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}), origin="cards")
    assert session.edits["leavenworth"].last().source == "cards"
    with pytest.raises(ActionError, match="made by cards"):
        session.undo_last("leavenworth")
    assert session.undo_last("leavenworth", source="cards").source == "cards"
    assert handle(session).lineage[-1] == "cards: undo: link 1: lanes = 2"


def test_a_failed_mutation_records_nothing(session):
    h = handle(session)
    good = plan_update(h.roadway, "link", [1], {"lanes": 2}).edits
    with pytest.raises(UnsupportedEditOp):
        session.mutate("leavenworth", [*good, Edit(op="explode", table="link")], note="bad")
    assert "leavenworth" not in session.edits and h.version == 0 and cell(session, 1, "lanes") == [1]


class Scribble(BaseAction):
    type: Literal["scribble.go"] = "scribble.go"
    mutates: ClassVar[bool] = True
    fail: bool = False


def _scribble(host, action: Scribble) -> None:
    plan = plan_update(host.network().roadway, "link", [1], {"lanes": 2})
    host.mutate(None, plan.edits, note=plan.summary)
    if action.fail:
        raise RuntimeError("after mutate")


def test_a_failed_action_leaves_no_pending_edit(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="scribble", name="Scribble", version="0", requires_api=HOST_API, actions=(ActionSpec(Scribble, _scribble),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    assert not s.dispatch_recorded(Scribble(fail=True)).ok
    assert "leavenworth" not in s.edits and s.network().version == 0  # Part 1 rolled the swap back; P2 the ledger
    assert s.dispatch_recorded(Scribble()).ok
    assert s.edits["leavenworth"].last().source == "scribble" and s.history[-1].action["type"] == "scribble.go"


def test_saving_and_undoing_a_saved_edit(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    ledger = session.edits["leavenworth"]
    ledger.mark_saved()
    assert ledger.summary()["dirty"] is False and ledger.summary()["unsaved"] == 0
    session.undo_last("leavenworth", source="python")
    assert ledger.summary()["dirty"] is True  # the open network no longer matches the saved copy


def test_closing_the_network_drops_its_edits(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    assert session.edits == {} and "leavenworth" not in session.state()["edits"]


def test_an_id_selection_drops_deleted_links(session):
    session.dispatch(Select(link_ids=[1, 2]))
    assert session.selection["intent"] is None  # made by ids
    mutate(session, plan_delete(handle(session).roadway, "link", [1]))
    assert session.selection["link_ids"] == [2] and session.selection["status"] == "resolved"
    mutate(session, plan_delete(handle(session).roadway, "link", [2]))
    assert session.selection is None  # nothing left to select


def test_an_id_selection_survives_an_unrelated_edit_unchanged(session):
    session.dispatch(Select(link_ids=[1, 2]))
    before = session.selection
    mutate(session, plan_update(handle(session).roadway, "link", [3], {"lanes": 2}))
    assert session.selection is before


def test_a_query_selection_is_resolved_again(session):
    sel = session.dispatch(Select(utterance="Ski Hill Drive"))
    assert sel["status"] == "resolved" and len(sel["link_ids"]) == 20 and sel["intent"] is not None
    gone = sel["link_ids"][0]
    mutate(session, plan_delete(handle(session).roadway, "link", [gone]))
    assert len(session.selection["link_ids"]) == 19 and gone not in session.selection["link_ids"]
    assert session.selection["utterance"] == "Ski Hill Drive"
    session.undo_last("leavenworth", source="python")
    assert len(session.selection["link_ids"]) == 20  # the revert brings the link back into the query's answer


class Snip(BaseAction):
    type: Literal["snip.cut"] = "snip.cut"
    mutates: ClassVar[bool] = True


def _snip(host, action: Snip) -> None:
    plan = plan_delete(host.network().roadway, "link", [1])
    host.mutate(None, plan.edits, note=plan.summary)
    assert host.selection["link_ids"] == [2]  # R4 moved the selection off the deleted link...
    raise RuntimeError("after the delete")  # ...and this failure must put it back


def test_a_failed_action_keeps_the_old_selection(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="snip", name="Snip", version="0", requires_api=HOST_API, actions=(ActionSpec(Snip, _snip),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(Select(link_ids=[1, 2]))
    before = s.selection
    entry = s.dispatch_recorded(Snip())
    assert not entry.ok and "after the delete" in entry.error  # the handler got past its R4 assertion
    assert s.selection is before and s.selection["link_ids"] == [1, 2]  # Part 1's rollback restores the view
    assert 1 in set(s.network().links_df().link_id) and s.network().version == 0  # and the network
    assert "leavenworth" not in s.edits
