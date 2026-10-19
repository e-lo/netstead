"""``apply_change`` inside a mutating Action: a failure anywhere rolls every step back with the Action."""

from typing import Any, ClassVar, Literal

import pytest
from netstead.workbench.plugins import HOST_API, ActionError, ActionSpec, BaseAction, EditRefused, WorkbenchPlugin
from netstead_project_cards.errors import CardError
from netstead_project_cards.lower import apply_change
from netstead_project_cards.types import parse_change

NET = "leavenworth"
NODE_USERS = [1, 2, 3, 4, 5, 185, 209, 293]  # every link that starts or ends at node 1


class ApplyCardChange(BaseAction):
    """A test Action: apply one change (a JSON dict) the way the plugin's own Actions do."""

    type: Literal["cardtest.apply"] = "cardtest.apply"
    mutates: ClassVar[bool] = True
    change: dict[str, Any]


def _apply(host: Any, action: ApplyCardChange) -> dict[str, Any]:
    try:
        return apply_change(host, NET, parse_change(action.change)).summary()
    except CardError as exc:
        raise ActionError(str(exc)) from exc


def cardtest() -> WorkbenchPlugin:
    return WorkbenchPlugin(
        id="cardtest",
        name="Card test",
        version="0",
        requires_api=HOST_API,
        actions=(ActionSpec(ApplyCardChange, _apply),),
    )


@pytest.fixture
def session(make_session):
    return make_session(cardtest())


@pytest.fixture
def host(session):
    return session._hosts["cardtest"]


def link_count(session):
    return len(session.network(NET).links_df())


def test_a_change_applies_inside_an_action(session):
    entry = session.dispatch_recorded(ApplyCardChange(change={"type": "roadway_deletion", "links": [1]}))
    assert entry.ok and session.network(NET).version == 1
    assert session.edits[NET].last().source == "cardtest"


def test_a_failed_second_step_rolls_the_first_back(session, host, monkeypatch):
    real = host.plan_delete

    def refuse_nodes(net_id, table, ids):
        if table == "node":
            raise EditRefused("refused for the test")
        return real(net_id, table, ids)

    monkeypatch.setattr(host, "plan_delete", refuse_nodes)
    links = link_count(session)
    entry = session.dispatch_recorded(
        ApplyCardChange(change={"type": "roadway_deletion", "links": NODE_USERS, "nodes": [1]})
    )
    assert not entry.ok and "refused for the test" in entry.error
    assert session.network(NET).version == 0 and link_count(session) == links  # step 1 (the links) is undone
    assert NET not in session.edits


def test_a_stale_expected_version_is_refused(session, host, monkeypatch):
    real = host.plan_update

    def race_after(*args):
        plan = real(*args)  # planned against the version the change read...
        host.mutate(NET, real(NET, "link", [3], {"lanes": 7}).edits, note="race")  # ...then the network moves
        return plan

    monkeypatch.setattr(host, "plan_update", race_after)
    change = {"type": "roadway_property_change", "facility": {"table": "link", "ids": [3]}}
    entry = session.dispatch_recorded(ApplyCardChange(change={**change, "property_changes": {"lanes": {"set": 2}}}))
    assert not entry.ok and "changed since the edit was planned" in entry.error
    assert session.network(NET).version == 0  # the Action (and the race inside it) rolled back


def test_an_edit_before_the_second_step_is_planned_is_refused(session, host, monkeypatch):
    real, plan_update = host.plan_delete, host.plan_update

    def race_nodes(net_id, table, ids):
        if table == "node":  # step 1 has deleted the links; another edit lands before the nodes are planned
            host.mutate(NET, plan_update(NET, "link", [10], {"lanes": 7}).edits, note="race")
        return real(net_id, table, ids)

    monkeypatch.setattr(host, "plan_delete", race_nodes)
    links = link_count(session)
    change = {"type": "roadway_deletion", "links": NODE_USERS, "nodes": [1]}
    entry = session.dispatch_recorded(ApplyCardChange(change=change))
    assert not entry.ok and "changed while the change was being planned" in entry.error
    assert session.network(NET).version == 0 and link_count(session) == links
