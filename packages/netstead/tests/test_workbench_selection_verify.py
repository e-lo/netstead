"""Selection verification: candidates on the payload, pinning, and what a pin survives."""

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import OpenNetwork, PinAnchor, Select, script_imports
from netstead.workbench.editing import plan_update
from netstead.workbench.errors import ActionError

SRC = str(leavenworth.parquet_dir())
CHERRY = "Cherry Street between Prospect Street and West Center Street"


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def _anchor(sel, role):
    return next(a for a in sel["anchors"] + sel["anchors_unplaced"] if a["role"] == role)


def test_an_ambiguous_selection_lists_its_candidates_with_coordinates(session):
    sel = session.dispatch(Select(utterance=CHERRY))
    assert sel["status"] == "ambiguous" and sel["link_ids"] == [170, 182, 187]
    to = _anchor(sel, "to")
    assert to["node_id"] == 60 and to["anchor"] == "West Center Street" and to["pinned"] is False
    assert [c["node_id"] for c in to["candidates"]] == [61]
    assert {"lon", "lat"} <= set(to["candidates"][0])
    assert _anchor(sel, "from")["candidates"] == []


def test_a_resolved_selection_has_no_candidates_and_a_fragment(session):
    sel = session.dispatch(Select(utterance="Ski Hill Drive"))
    assert sel["status"] == "resolved" and sel["anchors"] == [] and sel["fragment"] is not None


def test_pinning_the_chosen_candidate_makes_the_selection_resolved(session):
    session.dispatch(Select(utterance=CHERRY))
    sel = session.dispatch(PinAnchor(role="to", node_id=60))
    assert sel["status"] == "resolved" and sel["fragment"]["to"] == {"node_id": 60}
    assert _anchor(sel, "to")["pinned"] is True and sel["intent"]["to_node"] == 60
    assert sel["utterance"] == CHERRY  # still the query's selection


def test_pinning_another_candidate_and_unpinning(session):
    session.dispatch(Select(utterance=CHERRY))
    assert session.dispatch(PinAnchor(role="to", node_id=61))["status"] == "not_found"
    sel = session.dispatch(PinAnchor(role="to", node_id=None))
    assert sel["status"] == "ambiguous" and "to_node" not in sel["intent"]


def test_pin_needs_a_query_selection_with_that_anchor(session):
    with pytest.raises(ActionError, match="selection made by a query"):
        session.dispatch(PinAnchor(role="from", node_id=53))
    session.dispatch(Select(link_ids=[170]))
    with pytest.raises(ActionError, match="selection made by a query"):
        session.dispatch(PinAnchor(role="from", node_id=53))
    session.dispatch(Select(utterance="Ski Hill Drive"))
    with pytest.raises(ActionError, match="no from anchor"):
        session.dispatch(PinAnchor(role="from", node_id=53))


def test_a_pin_survives_an_edit_re_resolution(session):
    session.dispatch(Select(utterance=CHERRY))
    session.dispatch(PinAnchor(role="to", node_id=60))
    handle = session.network("leavenworth")
    plan = plan_update(handle.roadway, "link", [170], {"name": "Cherry Street"})
    session.mutate("leavenworth", plan.edits, note="rename to the same")
    assert session.selection["intent"]["to_node"] == 60 and session.selection["status"] == "resolved"


def test_pin_replays_and_has_its_own_import_line(session):
    session.dispatch(Select(utterance=CHERRY))
    entry = session.dispatch_recorded(PinAnchor(role="to", node_id=60))
    assert entry.python == "app.do(PinAnchor(role='to', node_id=60))"
    assert script_imports([entry.imports])[1:] == ["from netstead.workbench import PinAnchor"]
