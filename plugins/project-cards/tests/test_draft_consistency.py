"""The draft always describes the network: a parent Action's rollback, a closed network and a stray edit.

Core rolls back a failed Action's network changes but not a plugin's own state, so the plugin checks its drafts
against the network's pending edits (``host.pending_edits``) before every handler and every ``state()``, and a
commit registers ``host.on_rollback`` to take its file back.
"""

from typing import ClassVar, Literal

import pytest
from netstead.fixtures import leavenworth
from netstead.workbench.actions import CloseNetwork, EditCells, OpenNetwork
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import AddChange, CommitCard, ImportCard, UndoChange
from netstead_project_cards.card import DraftCard, read_card_file
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
)

NET = "leavenworth"


def lanes(link_id, value):
    return RoadwayPropertyChange(
        facility=Selection(ids=[link_id]), property_changes={"lanes": PropertyChange(set=value)}
    )


def cell(session, key, column="lanes"):
    links = session.network(NET).links_df()
    return links.loc[links.link_id == key, column].tolist()


def count(session):
    draft = session.state()["plugins"]["project_cards"]["drafts"].get(NET)
    return 0 if draft is None else draft["count"]


class ThenFail(BaseAction):
    """Dispatch ``inner`` (a project_cards Action), then fail: core rolls the network back."""

    type: Literal["nester.then_fail"] = "nester.then_fail"
    mutates: ClassVar[bool] = True
    inner: dict


class AllThenFail(BaseAction):
    """Dispatch each of ``inners`` in turn, then fail: core rolls all of them back together."""

    type: Literal["nester.all_then_fail"] = "nester.all_then_fail"
    mutates: ClassVar[bool] = True
    inners: list[dict]


def _nester():
    def run(host, action):
        host.dispatch(action.inner)
        raise ActionError("the parent failed")

    def run_all(host, action):
        for inner in action.inners:
            host.dispatch(inner)
        raise ActionError("the parent failed")

    return WorkbenchPlugin(
        id="nester",
        name="Nester",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(ThenFail, run), ActionSpec(AllThenFail, run_all)),
    )


@pytest.fixture
def nested(make_session):
    return make_session(_nester())


def add(link_id, value):
    return AddChange(change=lanes(link_id, value)).model_dump(mode="json")


def test_a_nested_change_whose_parent_fails_leaves_the_draft(nested):
    nested.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=add(2, 3)))
    assert cell(nested, 2) == [1] and count(nested) == 1


def test_the_draft_is_reconciled_before_undo_without_a_state_read(nested, no_state_reads):
    no_state_reads(nested)
    nested.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=add(2, 3)))
    result = nested.dispatch(UndoChange())
    assert result["count"] == 0 and result["undone"].endswith("link 1: lanes → 2") and cell(nested, 1) == [1]


def test_a_core_edit_after_the_rollback_does_not_bring_the_phantom_back(nested, tmp_path, no_state_reads):
    no_state_reads(nested)
    nested.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError):
        nested.dispatch(ThenFail(inner=add(2, 3)))
    nested.dispatch(EditCells(ids=[3], values={"lanes": 3}))  # the network is back at the phantom's version
    path = tmp_path / "card.yml"
    nested.dispatch(CommitCard(path=str(path), project="One"))
    assert [c.facility.ids for c in read_card_file(path).changes] == [[1]]


def test_a_nested_undo_whose_parent_fails_keeps_the_change_in_the_draft(nested, no_state_reads):
    no_state_reads(nested)
    nested.dispatch(AddChange(change=lanes(1, 2)))
    nested.dispatch(AddChange(change=lanes(2, 3)))
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=UndoChange().model_dump(mode="json")))
    assert cell(nested, 2) == [3]
    assert nested.dispatch(UndoChange())["count"] == 1 and cell(nested, 2) == [1]
    assert nested.dispatch(UndoChange())["count"] == 0 and cell(nested, 1) == [1]


def test_a_nested_undo_whose_parent_fails_shows_in_state(nested):
    nested.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError):
        nested.dispatch(ThenFail(inner=UndoChange().model_dump(mode="json")))
    assert count(nested) == 1


def test_an_undo_and_a_change_rolled_back_together_put_the_undone_change_back(nested, no_state_reads):
    no_state_reads(nested)
    before = cell(nested, 3)
    nested.dispatch(AddChange(change=lanes(1, 2)))
    nested.dispatch(AddChange(change=lanes(2, 3)))
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(AllThenFail(inners=[UndoChange().model_dump(mode="json"), add(3, 4)]))
    assert cell(nested, 2) == [3] and cell(nested, 3) == before
    assert nested.dispatch(UndoChange())["count"] == 1 and cell(nested, 2) == [1]


def test_an_undo_rolled_back_under_a_change_added_and_undone_in_the_same_parent_comes_back(nested, no_state_reads):
    no_state_reads(nested)
    before = cell(nested, 3)
    nested.dispatch(AddChange(change=lanes(1, 2)))
    nested.dispatch(AddChange(change=lanes(2, 3)))
    undo = UndoChange().model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(AllThenFail(inners=[undo, add(3, 4), undo]))
    assert cell(nested, 2) == [3] and cell(nested, 3) == before
    assert nested.dispatch(UndoChange())["undone"].endswith("link 2: lanes → 3") and cell(nested, 2) == [1]


def _cards_state(session):
    """The plugin's ``CardsState`` (in its factory's closure), through a bound handler."""
    return session.plugins["project_cards"].actions[0].handler.__self__.state


def test_a_stale_pending_read_never_drops_a_change_made_after_it(session, host, no_state_reads):
    """A route reads the pending edits outside the session lock; a change landing before it reconciles must stay."""
    from netstead_project_cards.flow import pending_seqs

    no_state_reads(session)
    session.dispatch(AddChange(change=lanes(1, 2)))
    state = _cards_state(session)
    stale = pending_seqs(host, state, NET)  # the route's read
    session.dispatch(AddChange(change=lanes(2, 3)))  # lands before the route reconciles
    state.sync(NET, session.network(NET), stale)
    assert [e.change.facility.ids for e in state._drafts[NET].entries] == [[1], [2]]


def test_a_stale_pending_read_never_puts_back_a_change_undone_after_it(session, host, no_state_reads):
    """A read from before an undo still lists the undone edit as pending: it must not look like a rolled-back undo."""
    from netstead_project_cards.flow import pending_seqs

    no_state_reads(session)
    session.dispatch(AddChange(change=lanes(1, 2)))
    state = _cards_state(session)
    stale = pending_seqs(host, state, NET)  # the route's read: the change's edit is pending
    session.dispatch(UndoChange())  # lands before the route reconciles
    state.sync(NET, session.network(NET), stale)
    assert state._drafts[NET].entries == [] and len(state._drafts[NET].undone) == 1
    state.sync(NET, session.network(NET), pending_seqs(host, state, NET))  # a fresh read agrees
    assert state._drafts[NET].entries == [] and cell(session, 1) == [1]


def test_a_nested_commit_whose_parent_fails_removes_the_file_and_restores_the_draft(nested, tmp_path):
    nested.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "cards" / "card.yml"
    commit = CommitCard(path=str(path), project="One").model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=commit))
    assert not path.exists() and not path.parent.exists()  # the folder the commit made goes too
    state = nested.state()["plugins"]["project_cards"]
    assert state["committed"] == [] and count(nested) == 1 and cell(nested, 1) == [2]
    nested.dispatch(CommitCard(path=str(path), project="One"))  # the draft commits as if nothing happened
    assert [c.facility.ids for c in read_card_file(path).changes] == [[1]]
    assert [c["card_id"] for c in nested.state()["plugins"]["project_cards"]["committed"]] == ["one"]


def test_a_nested_overwriting_commit_whose_parent_fails_restores_the_old_file(nested, tmp_path, no_state_reads):
    no_state_reads(nested)
    path = tmp_path / "card.yml"
    path.write_text("old: card\n", encoding="utf-8")
    nested.dispatch(AddChange(change=lanes(1, 2)))
    commit = CommitCard(path=str(path), project="One", overwrite=True).model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=commit))
    assert path.read_text(encoding="utf-8") == "old: card\n"
    assert nested.dispatch(UndoChange())["count"] == 0 and cell(nested, 1) == [1]  # the draft came back


def test_a_nested_commit_whose_file_cannot_be_restored_is_logged(nested, tmp_path, monkeypatch, caplog):
    import logging

    from netstead_project_cards import handlers

    monkeypatch.setattr(handlers, "undo_write", lambda written: "removed")
    nested.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "card.yml"
    with caplog.at_level(logging.WARNING), pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=CommitCard(path=str(path), project="One").model_dump(mode="json")))
    assert f"{path} could not be removed" in caplog.text and count(nested) == 1


def test_a_change_that_applies_nothing_is_refused(session):
    nothing = RoadwayPropertyChange(
        facility=Selection(ids=[999999], ignore_missing=True), property_changes={"lanes": PropertyChange(set=2)}
    )
    with pytest.raises(ActionError, match=f"nothing on {NET} matches this change; nothing was added"):
        session.dispatch(AddChange(change=nothing))
    assert count(session) == 0 and session.network(NET).version == 0


def test_an_imported_change_that_applied_nothing_outlives_a_parents_rollback(nested, tmp_path):
    """The documented limit (state.py): such a change has no marks, so reconciling cannot tell it was rolled back."""
    nothing = RoadwayPropertyChange(
        facility=Selection(ids=[999999], ignore_missing=True), property_changes={"lanes": PropertyChange(set=2)}
    )
    path = tmp_path / "card.yml"
    path.write_text(DraftCard(project="Mixed", changes=[lanes(1, 3), nothing]).to_yaml(), encoding="utf-8")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=ImportCard(path=str(path)).model_dump(mode="json")))
    assert cell(nested, 1) == [1] and count(nested) == 1


def test_undo_refuses_a_stray_edit_it_did_not_draft(session, host):
    session.dispatch(AddChange(change=lanes(1, 2)))
    host.mutate(NET, host.plan_update(NET, "link", [2], {"lanes": 4}).edits, note="stray")  # not in the draft
    with pytest.raises(ActionError, match="not the draft's last change"):
        session.dispatch(UndoChange())
    assert cell(session, 2) == [4] and count(session) == 1


def test_a_two_step_undo_whose_second_step_fails_undoes_nothing(session, host, monkeypatch):
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}
    link = {
        "link_id": 9001,
        "from_node_id": 1,
        "to_node_id": 9001,
        "name": "New",
        "facility_type": "residential",
        "lanes": 1,
        "allowed_uses": "auto",
    }
    session.dispatch(AddChange(change=RoadwayAddition(nodes=[node], links=[link])))
    session.dispatch(AddChange(change=RoadwayDeletion(links=[9001], clean_nodes=True)))
    real, calls = host.undo, []

    def second_fails(net_id=None):
        calls.append(net_id)
        if len(calls) == 2:
            raise ActionError("refused")
        return real(net_id)

    monkeypatch.setattr(host, "undo", second_fails)
    with pytest.raises(ActionError, match="refused"):
        session.dispatch(UndoChange())
    assert cell(session, 9001, "name") == [] and len(session.edits[NET].entries) == 3 and count(session) == 2


def test_a_reopened_network_does_not_inherit_the_closed_ones_draft(session, no_state_reads):
    no_state_reads(session)
    session.dispatch(AddChange(change=lanes(1, 2)))
    session.dispatch(CloseNetwork(net_id=NET))
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir()), net_id=NET))
    assert session.dispatch(AddChange(change=lanes(2, 2)))["count"] == 1


def test_state_reads_the_networks_before_it_takes_its_own_lock(session):
    import weakref

    from netstead_project_cards.lower import Pending
    from netstead_project_cards.state import CardsState, Preview

    state, held = CardsState(), []
    handle = session.network(NET)
    state.extend(NET, handle, [])
    ref = weakref.ref(handle)
    state.set_preview(Preview(net_id="gone", copy=ref, base=NET, base_handle=ref, base_version=0, describe="x"))

    def network(net_id):
        held.append(state._lock._is_owned())
        return handle if net_id == NET else None

    snapshot = state.snapshot(network, lambda net_id: Pending(frozenset(), 0, 0))
    assert held and not any(held) and snapshot["preview"] is None
