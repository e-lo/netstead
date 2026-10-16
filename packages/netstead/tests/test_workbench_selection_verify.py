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


def test_pinning_a_candidate_with_no_path_is_refused_and_unpinning(session):
    ambiguous = session.dispatch(Select(utterance=CHERRY))
    with pytest.raises(ActionError, match="no path between resolved anchor nodes"):
        session.dispatch(PinAnchor(role="to", node_id=61))  # on West Center Street, but no Cherry Street path to it
    assert session.selection == ambiguous  # rolled back: the ambiguous selection is kept
    session.dispatch(PinAnchor(role="to", node_id=60))
    sel = session.dispatch(PinAnchor(role="to", node_id=None))
    assert sel["status"] == "ambiguous" and "to_node" not in sel["intent"]


def test_a_pin_off_the_facility_is_refused(session):
    ambiguous = session.dispatch(Select(utterance=CHERRY))
    with pytest.raises(ActionError, match="node 83 is not on the facility for 'West Center Street'"):
        session.dispatch(PinAnchor(role="to", node_id=83))
    assert session.selection == ambiguous and not session.history[-1].ok


def test_a_pin_by_text_id_finds_the_integer_node(session):
    session.dispatch(Select(utterance=CHERRY))
    sel = session.dispatch(PinAnchor(role="to", node_id="60"))
    assert sel["status"] == "resolved" and sel["intent"]["to_node"] == 60
    with pytest.raises(ActionError, match="no node 'abc' in leavenworth"):
        session.dispatch(PinAnchor(role="to", node_id="abc"))
    with pytest.raises(ActionError, match="no node 999999 in leavenworth"):
        session.dispatch(PinAnchor(role="to", node_id=999_999))


def test_pin_needs_a_query_selection_with_that_anchor(session):
    with pytest.raises(ActionError, match="selection made by a query"):
        session.dispatch(PinAnchor(role="from", node_id=53))
    session.dispatch(Select(link_ids=[170]))
    with pytest.raises(ActionError, match="selection made by a query"):
        session.dispatch(PinAnchor(role="from", node_id=53))
    session.dispatch(Select(utterance="Ski Hill Drive"))
    with pytest.raises(ActionError, match="no from anchor"):
        session.dispatch(PinAnchor(role="from", node_id=53))


def _rename(session, link_ids, name):
    plan = plan_update(session.network("leavenworth").roadway, "link", link_ids, {"name": name})
    session.mutate("leavenworth", plan.edits, note=f"rename to {name}")


def test_a_pin_survives_an_edit_re_resolution(session):
    session.dispatch(Select(utterance=CHERRY))
    before = session.dispatch(PinAnchor(role="to", node_id=60))
    assert "facility ['Cherry Street']: 8 link(s)" in before["diagnostics"]
    _rename(session, [193, 195], "Cherry Lane")  # the Cherry Street block past node 61 leaves the facility
    after = session.selection
    assert after is not before and "facility ['Cherry Street']: 6 link(s)" in after["diagnostics"]  # resolved again
    assert after["intent"]["to_node"] == 60 and _anchor(after, "to")["pinned"] is True
    assert after["status"] == "resolved" and after["link_ids"] == [170, 182, 187]


def test_an_edit_that_takes_the_pinned_node_off_the_facility_keeps_the_previous_links(session):
    session.dispatch(Select(utterance=CHERRY))
    session.dispatch(PinAnchor(role="to", node_id=60))
    _rename(session, [187, 189], "Cherry Court")  # node 60 is no longer on Cherry Street
    sel = session.selection
    assert sel["link_ids"] == [170, 182, 187] and sel["intent"]["to_node"] == 60  # not lost; the pin is recorded
    assert sel["utterance"] == CHERRY and _anchor(sel, "to")["pinned"] is True
    assert sel["diagnostics"] == ["pinned node 60 is no longer on the facility; kept the previous links"]


def test_the_fallback_drops_links_an_edit_deleted(session):
    from netstead.workbench.editing import plan_delete

    session.dispatch(Select(utterance=CHERRY))
    session.dispatch(PinAnchor(role="to", node_id=60))
    session.mutate("leavenworth", plan_delete(session.network().roadway, "link", [187, 189]).edits, note="drop")
    sel = session.selection
    assert sel["link_ids"] == [170, 182] and sel["intent"]["to_node"] == 60
    assert "pinned node 60 is no longer on the facility" in sel["diagnostics"][0]


def test_pin_replays_and_has_its_own_import_line(session):
    session.dispatch(Select(utterance=CHERRY))
    entry = session.dispatch_recorded(PinAnchor(role="to", node_id=60))
    assert entry.python == "app.do(PinAnchor(role='to', node_id=60))"
    assert script_imports([entry.imports])[1:] == ["from netstead.workbench import PinAnchor"]
