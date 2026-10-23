"""GET /api/plugins/project_cards/card: the read-only Inspect view of a catalog card or of the draft."""

import json
from pathlib import Path

import netstead_project_cards
import pytest
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead.workbench.actions import CloseNetwork
from netstead_project_cards.actions import AddChange, OpenCatalog
from netstead_project_cards.card import Dependencies, DraftCard
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
)

CARD = "/api/plugins/project_cards/card"
NET = "leavenworth"
MODEL = {"cardmodel.js": Path(netstead_project_cards.__file__).parent / "static" / "cardmodel.js"}


def lanes(*ids, value=3, existing=None):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids)),
        property_changes={"lanes": PropertyChange(set=value, existing=existing)},
        note="wider",
    )


ADDED_LINK = {
    "link_id": 340,
    "from_node_id": 1,
    "to_node_id": 2,
    "name": "New St",
    "facility_type": "local",
    "lanes": 1,
    "allowed_uses": "all",
}


@pytest.fixture
def client(session):
    return TestClient(build_app(session))


@pytest.fixture
def catalog(session, tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    card = DraftCard(
        project="Main St",
        tags=["safety"],
        dependencies=Dependencies(prerequisites=["Bridge"]),
        notes="from the plan",
        changes=[
            lanes(1, 2, existing=1),
            RoadwayDeletion(links=[999999], ignore_missing=False),
            RoadwayAddition(links=[ADDED_LINK]),
        ],
    )
    (root / "main.yml").write_text(card.to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    return root


def get(client, **params):
    response = client.get(CARD, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_catalog_card_with_each_change_its_targets_and_its_check(client, catalog):
    got = get(client, card_id="main.yml")
    assert got["source"] == "catalog" and got["card_id"] == "main.yml" and got["net_id"] == NET
    assert got["path"] == str((catalog / "main.yml").resolve())
    assert got["project"] == "Main St" and got["tags"] == ["safety"] and got["notes"] == "from the plan"
    assert got["dependencies"] == {"prerequisites": ["Bridge"], "corequisites": [], "conflicts": []}
    assert got["yaml"] == (catalog / "main.yml").read_text(encoding="utf-8")  # the file itself
    assert got["applies"] is False  # link 999999 is missing
    prop, deletion, addition = got["changes"]
    assert prop["index"] == 0 and prop["type"] == "roadway_property_change"
    assert prop["facility"]["ids"] == [1, 2] and prop["note"] == "wider"
    assert prop["property_changes"]["lanes"]["existing"] == 1 and prop["property_changes"]["lanes"]["set"] == 3
    assert prop["targets"] == {"links": [1, 2], "nodes": []}
    assert prop["check"] == {"describe": prop["check"]["describe"], "resolved": 2, "missing": [], "reason": None}
    assert deletion["targets"] == {"links": [999999], "nodes": []} and deletion["check"]["missing"] == [999999]
    assert addition["targets"] == {"links": [340], "nodes": []} and addition["check"]["reason"] is None


def test_inspecting_changes_nothing(client, session, catalog):
    before, history = session.state(), len(session.history)
    get(client, card_id="main.yml")
    after = session.state()
    assert after["plugins"]["project_cards"]["catalog"] == before["plugins"]["project_cards"]["catalog"]
    assert session.network(NET).version == 0 and len(session.history) == history


def test_the_draft_is_reported_as_applied(client, session):
    session.dispatch(AddChange(change=lanes(1, existing=1)))
    got = get(client, draft="true")
    assert got["source"] == "draft" and got["card_id"] is None and got["path"] is None and got["applies"] is None
    assert got["net_id"] == NET and got["yaml"].startswith("project: untitled netstead card")
    (change,) = got["changes"]
    assert change["check"] is None and change["targets"]["links"] == [1]


def test_a_catalog_card_without_an_open_network_is_not_checked(client, session, catalog):
    session.dispatch(CloseNetwork(net_id=NET))
    got = get(client, card_id="main.yml")
    assert got["net_id"] is None and got["applies"] is None
    assert [c["check"] for c in got["changes"]] == [None, None, None]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "inspect a catalog card (card_id) or the draft (draft=true), not both"),
        ({"card_id": "main.yml", "draft": "true"}, "not both"),
        ({"draft": "true"}, "the draft card on leavenworth has no changes"),
        ({"card_id": "nope.yml"}, "has no card 'nope.yml'"),
    ],
)
def test_refusals(client, catalog, params, message):
    response = client.get(CARD, params=params)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_no_catalog_and_a_file_that_is_gone(client, session, tmp_path):
    response = client.get(CARD, params={"card_id": "main.yml"})
    assert response.status_code == 400 and "no catalog is open" in response.json()["detail"]
    root = tmp_path / "gone"
    root.mkdir()
    (root / "a.yml").write_text(DraftCard(project="A", changes=[lanes(1)]).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    (root / "a.yml").unlink()
    response = client.get(CARD, params={"card_id": "a.yml"})
    assert response.status_code == 400 and "no such file" in response.json()["detail"]


def test_a_file_that_is_not_utf8_text_is_refused(client, session, tmp_path, monkeypatch):
    root = tmp_path / "bin"
    root.mkdir()
    (root / "a.yml").write_text(DraftCard(project="A", changes=[lanes(1)]).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))

    def broken(self, *args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(Path, "read_text", broken)
    response = client.get(CARD, params={"card_id": "a.yml"})
    assert response.status_code == 400 and "could not read" in response.json()["detail"]


# cardmodel.js's rows for the route's real answers (a catalog card, then the draft).
ROWS = """
import { checkText, facilityText, inspectHeader, inspectRows, propertyLines } from "./cardmodel.js";
const query = { type: "roadway_property_change", property_changes: {},
  facility: { table: "link", query: { links: { name: ["Main"], ignore_missing: true } }, resolved: [1, 2] } };
const bare = { ...query, facility: { ...query.facility, resolved: null } };
console.log(JSON.stringify({
  header: inspectHeader(DETAIL), rows: inspectRows(DETAIL), draft: inspectRows(DRAFT),
  query: facilityText(query), bare: facilityText(bare), none: facilityText({ type: "roadway_property_change" }),
  nodes: facilityText({ type: "roadway_property_change", facility: { table: "node", ids: [4] } }),
  clean: facilityText({ type: "roadway_deletion", links: [1], nodes: [9], clean_nodes: true }),
  empty: facilityText({ type: "roadway_deletion", links: [], nodes: [] }),
  props: propertyLines({ property_changes: { lanes: { existing: 2, change: -1, existing_value_conflict: "warn" },
    speed: { change: 5 }, name: { set: "Main St" } } }),
  unknown: checkText(null), refused: checkText({ reason: "query without provenance", missing: [], resolved: 0 }),
  header2: inspectHeader({ project: "", tags: [], dependencies: {}, notes: null, path: null }),
}));
"""


def test_the_inspect_rows_for_the_routes_answers(client, session, catalog, run_js):
    detail = get(client, card_id="main.yml")
    session.dispatch(AddChange(change=lanes(1, existing=1)))
    draft = get(client, draft="true")
    harness = f"const DETAIL = {json.dumps(detail)};\nconst DRAFT = {json.dumps(draft)};\n" + ROWS
    got = run_js(MODEL, harness)
    assert got["header"] == [
        ["Project", "Main St"],
        ["Tags", "safety"],
        ["Dependencies", "prerequisites: Bridge"],
        ["Notes", "from the plan"],
        ["File", detail["path"]],
    ]
    prop, deletion, addition = got["rows"]
    assert prop == {
        "index": 0,
        "title": "1. Property change",
        "facility": "links 1, 2",
        "properties": ["lanes: 1 → 3"],
        "note": "wider",
        "check": {"state": "ok", "text": "Applies: 2 records found"},
        "links": [1, 2],
    }
    assert deletion["facility"] == "deletes links 999999"
    assert deletion["check"] == {"state": "no", "text": "0 found; missing 999999"}
    assert addition["facility"] == "adds links 340" and addition["links"] == [340]
    assert addition["check"]["state"] == "ok"
    (row,) = got["draft"]
    assert row["check"] == {"state": "ok", "text": "In the draft: applied to leavenworth"}
    assert got["query"] == 'links matching {"name":["Main"]} (resolved to 2 links)'
    assert got["bare"] == 'links matching {"name":["Main"]} (no provenance: cannot be applied yet)'
    assert got["none"] == "no facility" and got["nodes"] == "nodes 4"
    assert got["clean"] == "deletes links 1; nodes 9 (and end nodes no other link uses)"
    assert got["empty"] == "deletes nothing"
    assert got["props"] == ["lanes: 2 → change by -1 (if it differs: warn)", "speed: change by +5", "name: set Main St"]
    assert got["unknown"] == {"state": "unknown", "text": "Not checked: no network is open"}
    assert got["refused"] == {"state": "no", "text": "Does not apply: query without provenance"}
    assert got["header2"] == [["Project", "(unnamed)"]]
