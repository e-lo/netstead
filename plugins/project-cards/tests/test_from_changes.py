"""project_cards.card_from_changes: a card made from the Changes pane's ticked changes (design 2e, Host API 1.11)."""

from pathlib import Path
from typing import ClassVar, Literal

import pytest
import yaml
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead.workbench.actions import AddRows, DeleteRows, EditCells
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import AddChange, CardFromChanges, CommitCard, OpenCatalog
from netstead_project_cards.errors import CardError
from netstead_project_cards.fromchanges import FacilityChoice, entry_changes, entry_options
from netstead_project_cards.types import PropertyChange, RoadwayAddition, RoadwayPropertyChange, Selection

NET = "leavenworth"
ROUTE = "/api/plugins/project_cards/from-changes"


def pending(session):
    return {e["seq"]: e for e in session._hosts["project_cards"].pending_edits(NET).edits}


def cards(session):
    return session.state()["plugins"]["project_cards"]


@pytest.fixture
def edited(session):
    """Two staged changes: links 1 and 2 to 3 lanes (#1), then link 3 deleted (#2)."""
    session.dispatch(EditCells(table="link", ids=[1, 2], values={"lanes": 3}))
    session.dispatch(DeleteRows(table="link", ids=[3]))
    return session


def make(session, *, destination, seqs=(1, 2), **extra):
    fields = {"project": "Benton diet", "year": 2030, "tags": ["bike"], **extra}
    return session.dispatch(CardFromChanges(seqs=list(seqs), destination=destination, **fields))


def test_a_new_folder_gets_the_card_and_becomes_the_catalog(edited, tmp_path):
    folder = tmp_path / "cards" / "2030"
    result = make(edited, destination={"kind": "folder", "folder": str(folder)})
    path = folder / "benton-diet.yml"
    assert result["path"] == str(path) and result["listed"] is True and result["seqs"] == [1, 2]
    card = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert card["project"] == "Benton diet" and card["tags"] == ["bike", "year:2030"]
    change, deletion = card["changes"]
    assert change["roadway_property_change"] == {
        "facility": {"links": {"model_link_id": [1, 2], "ignore_missing": False}},
        "property_changes": {"lanes": {"existing": 1, "existing_value_conflict": "error", "set": 3}},
    }
    assert deletion["roadway_deletion"] == {"links": {"model_link_id": [3], "ignore_missing": False}}
    assert "netstead.note[0]: link 1, 2: lanes = 3" in card["notes"]
    # The folder is the open catalog now, with the card listed (its fit icon is computed when the panel lists it).
    catalog = cards(edited)["catalog"]
    assert catalog["path"] == str(folder) and [c["card_id"] for c in catalog["cards"]] == ["benton-diet.yml"]
    # Both changes read "In a card" and are no longer staged.
    assert [pending(edited)[s]["card"] for s in (1, 2)] == [str(path)] * 2
    assert edited.state()["edits"][NET]["unsaved"] == 0


def test_the_open_catalog_is_rescanned(edited, tmp_path):
    folder = tmp_path / "mine"
    folder.mkdir()
    edited.dispatch(OpenCatalog(path=str(folder)))
    result = make(edited, destination={"kind": "folder", "folder": str(folder)}, seqs=[1], file="diet")
    assert result["path"] == str(folder / "diet.yml") and result["listed"] is True
    assert [c["card_id"] for c in cards(edited)["catalog"]["cards"]] == ["diet.yml"]
    assert pending(edited)[2]["card"] is None  # only the ticked change went in


def test_an_existing_file_is_replaced_only_when_asked(edited, tmp_path):
    (tmp_path / "benton-diet.yml").write_text("old: card\n", encoding="utf-8")
    with pytest.raises(ActionError, match="exists"):
        make(edited, destination={"kind": "folder", "folder": str(tmp_path)})
    assert pending(edited)[1]["card"] is None
    make(edited, destination={"kind": "folder", "folder": str(tmp_path), "overwrite": True})
    assert "Benton diet" in (tmp_path / "benton-diet.yml").read_text(encoding="utf-8")


@pytest.mark.parametrize(("kind", "where"), [("download", "benton-diet.yml (downloaded)"), ("copy", "copied YAML")])
def test_download_and_copy_hand_the_yaml_back(edited, kind, where):
    result = make(edited, destination={"kind": kind})
    assert result["where"] == where and result["file"] == "benton-diet.yml"
    assert yaml.safe_load(result["yaml"])["project"] == "Benton diet"
    assert pending(edited)[1]["card"] == where


def test_the_bundled_sample_catalog_is_refused(edited):
    sample = cards(edited)["samples"][0]["path"]
    with pytest.raises(ActionError):
        make(edited, destination={"kind": "folder", "folder": sample})
    assert pending(edited)[1]["card"] is None and not list(Path(sample).glob("benton-diet.yml"))


def test_openstreetmap_ids_and_an_unavailable_segment(edited):
    result = make(edited, destination={"kind": "copy"}, seqs=[1], choices={1: {"id_system": "osm"}})
    facility = yaml.safe_load(result["yaml"])["changes"][0]["roadway_property_change"]["facility"]
    assert facility == {"links": {"osm_link_id": ["5836396", "5850530"], "ignore_missing": False}}
    with pytest.raises(ActionError, match="no named segment; use Link list"):
        make(edited, destination={"kind": "copy"}, seqs=[1], choices={1: {"form": "segment"}})


def test_live_warnings_need_confirming(session):
    session.dispatch(EditCells(table="link", ids=[1], values={"from_node_id": 999999}))
    assert session.state()["edits"][NET]["warnings"] > 0
    with pytest.raises(ActionError, match="live edit warning"):
        make(session, destination={"kind": "copy"}, seqs=[1])
    assert make(session, destination={"kind": "copy"}, seqs=[1], confirm_warnings=True)["seqs"] == [1]


def test_an_unknown_change_is_refused(edited):
    with pytest.raises(ActionError, match="no pending edit #9"):
        make(edited, destination={"kind": "copy"}, seqs=[9])


class Fail(BaseAction):
    """Dispatch ``inner``, then fail."""

    type: Literal["nester2.fail"] = "nester2.fail"
    mutates: ClassVar[bool] = True
    inner: dict


def _fail(host, action: Fail) -> None:
    host.dispatch(action.inner)
    raise ActionError("the parent failed")


def test_a_failing_parent_takes_the_file_the_catalog_and_the_marks_back(make_session, tmp_path):
    nester = WorkbenchPlugin(
        id="nester2", name="N", version="0", requires_api=HOST_API, actions=(ActionSpec(Fail, _fail),)
    )
    session = make_session(nester)
    session.dispatch(EditCells(table="link", ids=[1], values={"lanes": 3}))
    folder = tmp_path / "new"
    inner = CardFromChanges(seqs=[1], project="X", destination={"kind": "folder", "folder": str(folder)})
    with pytest.raises(ActionError, match="the parent failed"):
        session.dispatch(Fail(inner=inner.model_dump(mode="json")))
    assert not (folder / "x.yml").exists() and cards(session)["catalog"] is None
    assert pending(session)[1]["card"] is None


def test_commit_marks_the_drafts_edits_in_a_card(session, tmp_path):
    change = RoadwayPropertyChange(facility=Selection(ids=[1]), property_changes={"lanes": PropertyChange(set=2)})
    session.dispatch(AddChange(change=change))
    path = tmp_path / "draft.yml"
    session.dispatch(CommitCard(path=str(path), project="Draft"))
    assert [e["card"] for e in pending(session).values()] == [str(path)]


# ---------------------------------------------------------------------------- the dialog's route


def test_the_route_lists_the_changes_and_previews_the_ticked_ones(edited):
    client = TestClient(build_app(edited))
    view = client.post(ROUTE, json={"seqs": [1, 2], "picked": [1], "project": "P", "year": 2035}).json()
    first, second = view["rows"]
    assert first["seq"] == 1 and first["problem"] is None and first["options"]["form"] == "link_list"
    assert first["options"]["forms"]["segment"] == "This selection has no named segment; use Link list"
    assert first["changes"] == ["roadway_property_change Link list: 2 links (GMNS): lanes 1 → 3"]
    assert second["options"] is None and second["changes"] == ["roadway_deletion link 3"]
    card = yaml.safe_load(view["yaml"])
    assert len(card["changes"]) == 1 and card["tags"] == ["year:2035"] and view["problems"] == []
    assert view["file"] == "p.yml" and view["warnings"] == []
    none = client.post(ROUTE, json={"seqs": [1, 2], "picked": []}).json()
    assert none["problems"] == ["Tick at least one change"] and none["yaml"] == ""
    bad = client.post(ROUTE, json={"seqs": [1], "choices": {"1": {"form": "segment"}}}).json()
    assert bad["problems"] == ["#1: This selection has no named segment; use Link list"]
    assert client.post(ROUTE, json={"seqs": [7]}).status_code == 400


def test_a_change_a_card_cannot_hold_is_listed_with_why(session):
    session.dispatch(EditCells(table="link", ids=[1], values={"name": None}))
    session.dispatch(EditCells(table="lane", ids=[1], values={"width": 3.0}))
    view = TestClient(build_app(session)).post(ROUTE, json={"seqs": [1, 2]}).json()
    assert view["rows"][0]["problem"] == "#1: link name was cleared: a ProjectCard cannot set a property to empty"
    assert view["rows"][1]["problem"].startswith("#2: a change to the lane table can't go in a card")
    assert view["problems"] == [view["rows"][0]["problem"]]


# ---------------------------------------------------------------------------- edits as card changes


def test_added_rows_are_an_addition(session):
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.59, "node_type": "intersection"}
    session.dispatch(AddRows(table="node", rows=[node]))
    (entry,) = session._hosts["project_cards"].edit_changes(NET, [1])
    (change,) = entry_changes(entry)
    assert isinstance(change, RoadwayAddition) and change.nodes[0]["node_id"] == 9001 and not change.links


def test_a_node_change_is_a_link_list_in_gmns_ids():
    entry = {
        "seq": 4,
        "note": "node 5",
        "ops": [{"op": "update", "table": "node", "keys": [5], "set": {"ctrl_type": "signal"}, "existing": {}}],
    }
    options = entry_options(entry)
    assert options["id_systems"]["osm"] == "Node changes are written in GMNS ids"
    (change,) = entry_changes(entry)
    assert change.facility == Selection(table="node", ids=[5])
    with pytest.raises(CardError, match="#4: Node changes are written in GMNS ids"):
        entry_changes(entry, FacilityChoice(id_system="osm"))
    links = {"seq": 5, "note": "", "ops": [{"op": "update", "table": "link", "keys": [1], "set": {"lanes": 2}}]}
    assert entry_options(links)["id_systems"]["osm"] == "These links can't be written in other ids"
    odd = {"seq": 6, "note": "", "ops": [{"op": "replace_table", "table": "link", "keys": []}]}
    with pytest.raises(CardError, match="#6: a replace_table of link can't go in a card"):
        entry_changes(odd)
