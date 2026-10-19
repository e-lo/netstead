"""The draft always describes the network: a parent Action's rollback, a closed network and a stray edit.

Core rolls back a failed Action's network changes but not a plugin's own state, so the plugin checks its drafts
against the network (its lineage and version) before every handler and every ``state()``.
"""

from typing import ClassVar, Literal

import pytest
from netstead.fixtures import leavenworth
from netstead.workbench.actions import CloseNetwork, EditCells, OpenNetwork
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import AddChange, CommitCard, UndoChange
from netstead_project_cards.card import read_card_file
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


def _nester():
    def run(host, action):
        host.dispatch(action.inner)
        raise ActionError("the parent failed")

    return WorkbenchPlugin(
        id="nester", name="Nester", version="0", requires_api=HOST_API, actions=(ActionSpec(ThenFail, run),)
    )


@pytest.fixture
def nested(make_session):
    return make_session(_nester())


@pytest.fixture
def no_state_reads(monkeypatch):
    """``install(session)``: plugin ``state()`` is never called, so only the handlers can reconcile the drafts."""

    def install(session):
        monkeypatch.setattr(session, "_plugin_state", dict)

    return install


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
    from netstead_project_cards.state import CardsState

    state, held = CardsState(), []
    handle = session.network(NET)
    state.extend(NET, handle, [])
    state.preview = {"net_id": "gone", "base": NET, "describe": "x"}

    def network(net_id):
        held.append(state._lock._is_owned())
        return handle if net_id == NET else None

    snapshot = state.snapshot(network)
    assert held and not any(held) and snapshot["preview"] is None
