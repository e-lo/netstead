"""Selection verification: candidates on the payload, pinning, and what a pin survives."""

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import OpenNetwork, Select

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
