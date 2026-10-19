"""project_cards.add_change / undo_change: applied through core, recorded, drafted per network, replayable."""

import pytest
from netstead.fixtures import leavenworth
from netstead.workbench.actions import (
    ClearSelection,
    CloseNetwork,
    EditCells,
    OpenNetwork,
    SaveSelection,
    Select,
)
from netstead.workbench.errors import ActionError, NotSupportedYet
from netstead_project_cards.actions import AddChange, UndoChange
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
    TransitPropertyChange,
)

NET = "leavenworth"


def props(**values):
    return RoadwayPropertyChange(property_changes={k: PropertyChange(**v) for k, v in values.items()})


def on(link_ids, **values):
    return props(**values).model_copy(update={"facility": Selection(ids=link_ids)})


def cell(session, key, column):
    links = session.network(NET).links_df()
    return links.loc[links.link_id == key, column].tolist()


def draft(session):
    return session.state()["plugins"]["project_cards"]["drafts"].get(NET)


def test_a_pick_is_applied_recorded_and_drafted(session):
    session.dispatch(Select(link_ids=[1]))
    result = session.dispatch(AddChange(use_selection=True, change=props(free_speed={"existing": 40, "set": 30})))
    assert cell(session, 1, "free_speed") == [30.0]
    assert (result["net_id"], result["count"], result["steps"]) == (NET, 1, 1)
    assert result["change"]["facility"] == {
        "table": "link",
        "ids": [1],
        "query": None,
        "resolved": None,
        "ignore_missing": False,
    }  # a pick is written by id, ignore_missing false
    assert session.edits[NET].last().source == "project_cards"
    assert session.network(NET).lineage[-1] == "project_cards: roadway_property_change link 1: free_speed 40 → 30"
    assert session.history[-1].imports == "from netstead_project_cards.actions import AddChange"
    assert draft(session) == {
        "project": "",
        "count": 1,
        "changes": [
            {
                "type": "roadway_property_change",
                "describe": "roadway_property_change link 1: free_speed 40 → 30",
                "steps": 1,
            }
        ],
    }


def test_a_query_selection_keeps_its_query_and_the_ids_it_resolved_to(session):
    session.dispatch(Select(utterance="Benton Street"))
    result = session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))
    facility = result["change"]["facility"]
    assert facility["query"] == {"links": {"name": ["Benton Street"], "ignore_missing": True}}  # the intent's value
    assert facility["resolved"] == [1, 5, 6, 8] and facility["ids"] is None
    assert facility["ignore_missing"] is True  # the query's own flag decides what a missing id does
    assert cell(session, 8, "lanes") == [2]


def test_a_query_without_ignore_missing_gets_the_default_true(session, host, monkeypatch):
    session.dispatch(Select(utterance="Benton Street"))
    real = type(host).selection.fget

    def without_flag(self):
        sel = real(self)
        del sel["projectcard"]["query"]["links"]["ignore_missing"]
        return sel

    monkeypatch.setattr(type(host), "selection", property(without_flag))
    result = session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))
    facility = result["change"]["facility"]
    assert facility["query"]["links"]["ignore_missing"] is True and facility["ignore_missing"] is True


def test_a_saved_selection_seeds_the_facility(session):
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(SaveSelection(name="benton"))
    session.dispatch(ClearSelection())
    result = session.dispatch(AddChange(saved_selection="benton", change=props(lanes={"set": 2})))
    assert result["change"]["facility"]["resolved"] == [1, 5, 6, 8]
    assert cell(session, 5, "lanes") == [2]


def test_a_deletion_takes_its_links_from_the_selection(session):
    session.dispatch(Select(link_ids=[2]))
    result = session.dispatch(AddChange(use_selection=True, change=RoadwayDeletion()))
    assert result["change"]["links"] == [2] and cell(session, 2, "lanes") == []


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (AddChange(use_selection=True, change=props(lanes={"set": 2})), "nothing is selected"),
        (AddChange(saved_selection="nope", change=props(lanes={"set": 2})), "no saved selection named 'nope'"),
        (AddChange(change=props(lanes={"set": 2})), "^a roadway_property_change needs a facility"),
        (AddChange(change=RoadwayDeletion()), "^a roadway_deletion needs links"),
    ],
)
def test_a_change_without_targets_is_refused_and_changes_nothing(session, action, message):
    with pytest.raises(ActionError, match=message):
        session.dispatch(action)
    assert session.network(NET).version == 0 and draft(session) is None


def test_an_addition_does_not_take_a_selection(session):
    session.dispatch(Select(link_ids=[1]))
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}
    with pytest.raises(ActionError, match="roadway_addition does not take a selection"):
        session.dispatch(AddChange(use_selection=True, change=RoadwayAddition(nodes=[node])))


def test_targets_from_both_a_selection_and_the_change_are_refused(session):
    session.dispatch(Select(link_ids=[1]))
    with pytest.raises(ActionError, match="not both"):
        session.dispatch(AddChange(use_selection=True, change=on([2], lanes={"set": 2})))
    with pytest.raises(ActionError, match="not both"):
        session.dispatch(AddChange(use_selection=True, change=RoadwayDeletion(links=[2])))


def test_a_selection_on_another_network_is_refused(session):
    session.dispatch(Select(link_ids=[1]))
    with pytest.raises(ActionError, match="no open network 'elsewhere'"):
        session.dispatch(AddChange(net_id="elsewhere", use_selection=True, change=props(lanes={"set": 2})))


def test_a_selection_on_another_network_is_refused_for_this_one(session):
    session.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir()), net_id="other"))
    session.dispatch(Select(link_ids=[1]))  # on "other", now the active network
    with pytest.raises(ActionError, match="the selection is on network 'other', not 'leavenworth'"):
        session.dispatch(AddChange(net_id=NET, use_selection=True, change=props(lanes={"set": 2})))


def test_an_unresolved_selection_is_refused(session):
    session.dispatch(Select(utterance="Nowhere Boulevard"))
    with pytest.raises(ActionError, match="not resolved"):
        session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))


def test_with_no_network_open_there_is_nothing_to_change(make_session):
    session = make_session(open_network=False)
    with pytest.raises(ActionError, match="no network is open"):
        session.dispatch(AddChange(change=on([1], lanes={"set": 2})))


def test_a_closed_networks_draft_is_dropped(session):
    session.dispatch(AddChange(change=on([1], lanes={"set": 2})))
    session.dispatch(CloseNetwork(net_id=NET))
    assert session.state()["plugins"]["project_cards"]["drafts"] == {}


def test_both_selection_sources_are_refused_by_the_model():
    with pytest.raises(ValueError, match="not both"):
        AddChange(use_selection=True, saved_selection="x", change=props(lanes={"set": 2}))


def test_a_change_that_would_not_be_a_valid_card_is_refused_before_it_is_applied(session):
    bad = Selection(query={"links": {"name": ["Benton Street"], "ref": ["US 2"], "ignore_missing": True}}, resolved=[1])
    with pytest.raises(ActionError, match=r"would not be a valid ProjectCard: .*facility/links"):
        session.dispatch(
            AddChange(change=RoadwayPropertyChange(facility=bad, property_changes={"lanes": PropertyChange(set=2)}))
        )
    assert session.network(NET).version == 0


def test_a_lowering_refusal_is_an_action_error(session):
    with pytest.raises(ActionError, match="link 999999 not found"):
        session.dispatch(AddChange(change=on([999999], lanes={"set": 2})))
    assert draft(session) is None


def test_transit_is_not_supported_yet(session):
    with pytest.raises(NotSupportedYet, match="transit_property_change is not yet supported"):
        session.dispatch(AddChange(change=TransitPropertyChange()))


def test_an_added_field_with_no_column_is_not_supported_yet(session):
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6, "walk_flag": 1}
    with pytest.raises(NotSupportedYet, match="walk_flag"):
        session.dispatch(AddChange(change=RoadwayAddition(nodes=[node])))


def test_a_deletion_is_all_or_nothing_across_its_two_steps(session):
    links_before = len(session.network(NET).links_df())
    with pytest.raises(ActionError, match="still used"):
        session.dispatch(AddChange(change=RoadwayDeletion(links=[1], nodes=[1])))  # node 1 has seven more links
    assert len(session.network(NET).links_df()) == links_before and session.network(NET).version == 0
    assert not getattr(session.edits.get(NET), "entries", None)  # no pending edit survived the rollback


def test_undo_reverses_the_last_change_then_the_one_before(session):
    session.dispatch(AddChange(change=on([1], lanes={"set": 2})))
    session.dispatch(AddChange(change=on([1], lanes={"set": 3})))
    result = session.dispatch(UndoChange())
    assert result["count"] == 1 and result["undone"].endswith("lanes → 3") and cell(session, 1, "lanes") == [2]
    assert session.dispatch(UndoChange())["count"] == 0 and cell(session, 1, "lanes") == [1]
    with pytest.raises(ActionError, match="nothing to undo"):
        session.dispatch(UndoChange())


def test_undo_takes_back_every_step_of_a_deletion(session):
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
    assert draft(session)["changes"][-1]["steps"] == 2
    session.dispatch(UndoChange())
    assert cell(session, 9001, "name") == ["New"] and len(session.edits[NET].entries) == 1
    nodes = session.network(NET).nodes_df()
    assert (nodes.node_id == 9001).sum() == 1


def test_undo_refuses_when_core_made_the_newest_change(session):
    session.dispatch(AddChange(change=on([1], lanes={"set": 2})))
    session.dispatch(EditCells(ids=[3], values={"lanes": 3}))
    with pytest.raises(ActionError, match="was made by"):
        session.dispatch(UndoChange())
    assert draft(session)["count"] == 1 and cell(session, 1, "lanes") == [2]


def test_every_cards_action_is_drafted_by_the_assistant(session):
    types = [t for t in session.actions.types() if t.startswith("project_cards.")]
    assert types and all(session.actions.model(t).mutates for t in types)


def test_a_replayed_session_reproduces_the_network(session, make_session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(AddChange(use_selection=True, change=props(free_speed={"set": 30})))
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(AddChange(use_selection=True, change=props(lanes={"set": 2})))
    session.dispatch(AddChange(use_selection=True, change=props(lanes={"change": 1})))
    session.dispatch(UndoChange())
    other = make_session(open_network=False)
    namespace = {"app": other}
    for entry in session.history:
        if entry.parent_seq is None and entry.ok:
            exec(entry.imports, namespace)  # our own recorded "copy as Python" lines
            exec(entry.python, namespace)
    links = other.network(NET).links_df()
    assert links.loc[links.link_id == 1, "free_speed"].tolist() == [30.0]
    assert links.loc[links.link_id.isin([1, 5, 6, 8]), "lanes"].tolist() == [2, 2, 2, 2]
    assert other.state()["plugins"]["project_cards"]["drafts"][NET]["count"] == 2


def test_a_replayed_saved_selection_change_reproduces_the_network(session, make_session):
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(SaveSelection(name="benton"))
    session.dispatch(ClearSelection())
    session.dispatch(AddChange(saved_selection="benton", change=props(lanes={"set": 2})))
    other = make_session(open_network=False)
    namespace = {"app": other}
    for entry in session.history:
        if entry.parent_seq is None and entry.ok:
            exec(entry.imports, namespace)
            exec(entry.python, namespace)
    links = other.network(NET).links_df()
    assert links.loc[links.link_id.isin([1, 5, 6, 8]), "lanes"].tolist() == [2, 2, 2, 2]
    assert other.state()["plugins"]["project_cards"]["drafts"][NET]["count"] == 1
