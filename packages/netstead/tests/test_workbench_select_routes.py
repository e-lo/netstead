"""GET /api/selection/export: read-only, resolved selections only, fragment schema-checked."""

import json

import pytest
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app

SRC = str(leavenworth.parquet_dir())
CHERRY = "Cherry Street between Prospect Street and West Center Street"
EXPORT = "/api/selection/export"


@pytest.fixture
def setup(tmp_path):
    env = {"NETSTEAD_CONFIG_DIR": str(tmp_path / "u"), "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([SRC, str(tmp_path)])}
    s = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[])
    s.dispatch({"type": "open_network", "source": SRC})
    return s, TestClient(build_app(s))


def test_nothing_selected_is_404(setup):
    _, client = setup
    assert client.get(EXPORT).status_code == 404


def test_an_ambiguous_selection_must_be_pinned_first(setup):
    s, client = setup
    s.dispatch({"type": "select", "utterance": CHERRY})
    r = client.get(EXPORT)
    assert r.status_code == 409 and "pin" in r.json()["detail"]


def test_fragment_forms_and_validation(setup):
    s, client = setup
    s.dispatch({"type": "select", "utterance": CHERRY})
    s.dispatch({"type": "pin_anchor", "role": "to", "node_id": 60})
    resolved = client.get(EXPORT, params={"what": "fragment", "form": "resolved"}).json()
    assert resolved["content"]["links"] == {"link_id": [170, 182, 187]}
    assert resolved["content"]["to"] == {"node_id": 60} and resolved["valid"] is True
    assert resolved["filename"] == "leavenworth-selection-fragment-resolved.json"
    query = client.get(EXPORT, params={"what": "fragment", "form": "query"}).json()
    assert query["content"]["links"]["name"] == ["Cherry Street"]
    assert len(s.history) == 3  # open, select, pin: exporting recorded nothing


def test_projectcard_form_and_a_saved_selection_by_name(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1, 2]})
    s.dispatch({"type": "save_selection", "name": "two links"})
    s.dispatch({"type": "clear_selection"})
    j = client.get(EXPORT, params={"what": "projectcard", "name": "two links"}).json()
    assert j["content"]["links"]["model_link_id"] == [1, 2] and j["content"]["links"]["ignore_missing"] is True
    assert j["valid"] is None  # validating a ProjectCard is the cards plugin's job
    assert j["filename"] == "leavenworth-two-links-projectcard-resolved.json"


def test_query_form_needs_a_query_selection(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1]})
    assert client.get(EXPORT, params={"form": "query"}).status_code == 409


def test_unknown_saved_name_is_404(setup):
    _, client = setup
    assert client.get(EXPORT, params={"name": "nope"}).status_code == 404


def test_a_saved_pinned_selection_exports_its_from_and_to_nodes(setup):
    s, client = setup
    s.dispatch({"type": "select", "utterance": CHERRY})
    s.dispatch({"type": "pin_anchor", "role": "to", "node_id": 60})
    s.dispatch({"type": "save_selection", "name": "cherry"})
    s.dispatch({"type": "clear_selection"})
    j = client.get(EXPORT, params={"name": "cherry"}).json()
    assert j["content"]["from"] == {"node_id": 53} and j["content"]["to"] == {"node_id": 60} and j["valid"] is True


def _stale_cherry(s):
    """A pinned Cherry selection whose pinned node an edit has taken off the facility (the fallback keeps it)."""
    from netstead.workbench.editing import plan_update

    s.dispatch({"type": "select", "utterance": CHERRY})
    s.dispatch({"type": "pin_anchor", "role": "to", "node_id": 60})
    plan = plan_update(s.network("leavenworth").roadway, "link", [187, 189], {"name": "Cherry Court"})
    s.mutate("leavenworth", plan.edits, note="node 60 leaves Cherry Street")


def test_a_stale_pin_warns_on_the_resolved_form_and_refuses_the_query_form(setup):
    s, client = setup
    _stale_cherry(s)
    assert s.selection["stale_pins"] == [60] and s.selection["status"] == "resolved"
    j = client.get(EXPORT, params={"what": "fragment", "form": "resolved"}).json()
    assert j["content"]["links"] == {"link_id": [170, 182, 187]}  # the links selected before the edit
    assert len(j["warnings"]) == 1 and "the to anchor's pinned node 60 is no longer on the facility" in j["warnings"][0]
    r = client.get(EXPORT, params={"what": "fragment", "form": "query"})
    assert r.status_code == 409 and "re-pin the to anchor first" in r.json()["detail"]
    s.dispatch({"type": "pin_anchor", "role": "to", "node_id": None})  # unpinned: resolved afresh, nothing stale
    assert s.selection["stale_pins"] == [] and "to_node" not in s.selection["intent"]


def test_exporting_a_saved_selection_resolves_it_on_the_network_as_it_is_now(setup):
    from netstead.workbench.editing import plan_delete

    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1, 2, 3]})
    s.dispatch({"type": "save_selection", "name": "three"})
    s.mutate("leavenworth", plan_delete(s.network().roadway, "link", [2]).edits, note="drop link 2")
    s.dispatch({"type": "select", "link_ids": [5]})
    seen = len(s.history)
    j = client.get(EXPORT, params={"name": "three"}).json()
    assert j["content"]["links"] == {"link_id": [1, 3]}  # not the stale ids
    assert j["warnings"] == ["'three' was saved on version 0 of 'leavenworth'; exported as of version 1"]
    assert s.selection["link_ids"] == [5] and len(s.history) == seen  # read-only: nothing used, nothing recorded


def test_a_saved_selection_whose_network_is_closed_exports_as_saved_with_a_warning(setup):
    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1, 2]})
    s.dispatch({"type": "save_selection", "name": "two"})
    s.dispatch({"type": "close_network", "net_id": "leavenworth"})
    j = client.get(EXPORT, params={"name": "two"}).json()
    assert j["content"]["links"] == {"link_id": [1, 2]}
    assert j["warnings"] == ["'leavenworth' is not open: exported 'two' as saved (version 0), without checking it"]


def test_a_saved_selection_with_none_of_its_links_left_is_409(setup):
    from netstead.workbench.editing import plan_delete

    s, client = setup
    s.dispatch({"type": "select", "link_ids": [1]})
    s.dispatch({"type": "save_selection", "name": "one"})
    s.mutate("leavenworth", plan_delete(s.network().roadway, "link", [1]).edits, note="drop link 1")
    r = client.get(EXPORT, params={"name": "one"})
    assert r.status_code == 409 and "none of the links saved as 'one' exist any more" in r.json()["detail"]
