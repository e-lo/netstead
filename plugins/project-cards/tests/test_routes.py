"""GET /api/plugins/project_cards/form and /commit-check: what the panel's forms and the commit dialog show."""

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead.workbench.actions import SaveSelection, Select
from netstead_project_cards.actions import AddChange, PreviewChange
from netstead_project_cards.routes import column_schema, current_values, next_id
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

FORM = "/api/plugins/project_cards/form"
CHECK = "/api/plugins/project_cards/commit-check"


@pytest.fixture
def client(session):
    return TestClient(build_app(session))


def form(client, **params):
    response = client.get(FORM, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_property_change_form_on_a_pick(client, session):
    session.dispatch(Select(link_ids=[1, 2]))
    got = form(client, type="roadway_property_change", use_selection="true")
    assert got["supported"] is True and got["net_id"] == "leavenworth"
    assert got["targets"] == {"table": "link", "count": 2, "picked": True, "label": "2 picked links", "warnings": []}
    assert got["existing"]["facility_type"] == "residential" and got["existing"]["lanes"] == 1
    assert "free_speed" not in got["existing"]  # 40.0 and 40.23: not every target agrees
    columns = got["columns"]["properties"]
    assert "link_id" not in columns
    assert columns["lanes"] == {"anyOf": [{"type": "integer"}, {"type": "null"}], "title": "lanes (now 1)"}
    assert columns["free_speed"]["title"] == "free_speed (values differ)"
    assert got["options"]["properties"]["mode"]["enum"] == ["set", "change"]


def test_a_query_selection_is_labelled_by_its_query(client, session):
    session.dispatch(Select(utterance="Benton Street"))
    got = form(client, type="roadway_property_change", use_selection="true")
    assert got["targets"]["picked"] is False and got["targets"]["count"] == 4
    assert got["targets"]["label"] == '4 links matching {"name": ["Benton Street"]}'


def test_a_saved_selection_form(client, session):
    session.dispatch(Select(link_ids=[1]))
    session.dispatch(SaveSelection(name="one"))
    session.dispatch(Select(link_ids=[2]))
    got = form(client, type="roadway_property_change", saved="one")
    assert got["targets"]["label"] == "1 picked link" and got["existing"]["free_speed"] == 40.0


def test_deletion_and_addition_forms(client, session):
    session.dispatch(Select(link_ids=[2]))
    deletion = form(client, type="roadway_deletion", use_selection="true")
    assert deletion["targets"]["count"] == 1
    assert deletion["options"]["properties"]["clean_nodes"]["type"] == "boolean"
    addition = form(client, type="roadway_addition")
    assert addition["next_ids"] == {"link_id": 340, "node_id": 122}
    assert {"link_id", "from_node_id", "to_node_id", "name", "facility_type", "lanes", "allowed_uses"} <= set(
        addition["link"]["required"]
    )
    assert addition["node"]["required"] == ["node_id", "x_coord", "y_coord"]
    assert addition["link"]["properties"]["link_id"] == {"type": "integer", "title": "link_id"}


def test_a_form_on_the_preview_describes_its_base(client, session):
    session.dispatch(Select(link_ids=[1]))
    preview = session.dispatch(
        PreviewChange(
            use_selection=True, change=RoadwayPropertyChange(property_changes={"lanes": PropertyChange(set=3)})
        )
    )
    session.dispatch(Select(link_ids=[1]))  # on the preview, which is active now
    got = form(client, type="roadway_property_change", use_selection="true", net_id=preview["preview"])
    assert got["net_id"] == "leavenworth" and got["existing"]["lanes"] == 1  # the base's value, not the preview's


def test_transit_forms_say_not_yet_supported(client):
    got = form(client, type="transit_route_addition")
    assert got["type"] == "transit_route_addition" and got["supported"] is False
    assert got["message"].startswith("transit_route_addition is not yet supported: transit changes arrive")


@pytest.mark.parametrize(
    ("params", "status", "detail"),
    [
        ({"type": "pycode"}, 404, "no change type 'pycode'"),
        ({"type": "roadway_property_change", "use_selection": "true"}, 400, "nothing is selected"),
        ({"type": "roadway_deletion"}, 400, "acts on a selection"),
        ({"type": "roadway_deletion", "saved": "nope"}, 400, "no saved selection named 'nope'"),
        ({"type": "roadway_addition", "net_id": "nope"}, 400, "no open network 'nope'"),
    ],
)
def test_form_errors_are_400_or_404(client, params, status, detail):
    response = client.get(FORM, params=params)
    assert response.status_code == status and detail in response.json()["detail"]


def test_commit_check_shows_warnings_problems_and_the_yaml(client, session):
    empty = client.get(CHECK).json()
    assert empty["changes"] == 0 and empty["problems"] == ["the draft card has no changes"]
    bad_end = RoadwayPropertyChange(
        facility=Selection(ids=[1]), property_changes={"from_node_id": PropertyChange(set=424242)}
    )
    session.dispatch(AddChange(change=bad_end))
    got = client.get(CHECK, params={"project": "Bad end"}).json()
    assert got["net_id"] == "leavenworth" and got["changes"] == 1 and got["problems"] == []
    assert [w["code"] for w in got["warnings"]] == ["edit.fk_missing"]
    assert got["yaml"].startswith("project: Bad end\n") and "A:\n" in got["yaml"]


def test_commit_check_refuses_an_unknown_network(client):
    response = client.get(CHECK, params={"net_id": "nope"})
    assert response.status_code == 400 and "no open network 'nope'" in response.json()["detail"]


def test_column_helpers_on_a_small_frame():
    frame = pd.DataFrame({"id": ["a", "b"], "n": [1, 2], "x": [None, None], "flag": [True, True]})
    existing, mixed = current_values(frame, "id", ["a", "b"])
    assert existing == {"flag": True} and mixed == {"n"}
    schema = column_schema(frame, {"id"}, existing, mixed)["properties"]
    assert schema["x"]["title"] == "x (empty)" and schema["flag"]["anyOf"][0] == {"type": "boolean"}
    assert next_id(frame, "id") is None and next_id(frame, "n") == 3 and next_id(frame.iloc[:0], "n") is None
