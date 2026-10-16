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
