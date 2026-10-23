"""The open catalog as the table view's ``cards_catalog`` (Host API 1.8): rows, sorting, filtering, freshness."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead_project_cards.actions import CheckCard, OpenCatalog, RenameCard
from netstead_project_cards.card import DraftCard
from netstead_project_cards.catalog_table import COLUMNS, _modified
from netstead_project_cards.types import PropertyChange, RoadwayDeletion, RoadwayPropertyChange, Selection

NET = "leavenworth"
TABLE = f"/api/n/{NET}/roadway/table/cards_catalog"


def lanes(*ids):
    return RoadwayPropertyChange(facility=Selection(ids=list(ids)), property_changes={"lanes": PropertyChange(set=3)})


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    cards = {
        "a.yml": DraftCard(project="Bridge", tags=["bike", "safety"], changes=[lanes(1, 2)]),
        "b.yml": DraftCard(project="Arterial", tags=["transit"], changes=[lanes(999999)]),
        "c.yml": DraftCard(project="Canal", tags=["bike"], changes=[lanes(3), RoadwayDeletion(links=[4])]),
    }
    for name, card in cards.items():
        (root / name).write_text(card.to_yaml(), encoding="utf-8")
    return root


@pytest.fixture
def client(make_session, folder) -> TestClient:
    session = make_session(enable_apply=False)
    session.dispatch(OpenCatalog(path=str(folder)))
    return TestClient(build_app(session))


def rows(client: TestClient, **body) -> list[dict]:
    payload = client.post(f"{TABLE}/rows", json=body).json()
    return [dict(zip(payload["columns"], r, strict=True)) for r in payload["rows"]]


def test_the_catalog_is_listed_under_the_plugins_tables(client):
    tables = client.get(f"/api/n/{NET}/roadway/tables").json()["tables"]
    (cat,) = [t for t in tables if t["name"] == "cards_catalog"]
    assert cat["plugin"] == "project_cards" and cat["title"] == "Cards catalog" and cat["read_only"] is True
    assert cat["rows"] == 3 and tuple(cat["columns"]) == COLUMNS


def test_each_card_is_a_row_with_its_fit_once_checked(client):
    before = {r["card_id"]: r["fits"] for r in rows(client)}
    assert before == {"a.yml": None, "b.yml": None, "c.yml": None}  # nothing checked yet
    client.get("/api/plugins/project_cards/catalog")  # the panel's listing checks them (and publishes)
    got = {r["card_id"]: r for r in rows(client)}
    assert got["a.yml"]["fits"] is True and got["a.yml"]["reason"] == ""
    assert got["b.yml"]["fits"] is False and got["b.yml"]["reason"] == "missing link 999999"
    c = got["c.yml"]
    assert (c["project"], c["tags"], c["change_count"], c["links_targeted"]) == ("Canal", "bike", 2, 2)
    assert c["change_types"] == "roadway_property_change, roadway_deletion"
    assert got["a.yml"]["tags"] == "bike, safety" and got["a.yml"]["modified"].endswith("+00:00")


def test_sort_by_project_and_filter_tags_any_of(client):
    assert [r["project"] for r in rows(client, sort="project")] == ["Arterial", "Bridge", "Canal"]
    bike = rows(client, filter=[{"col": "tags", "op": "any_of", "val": ["bike"]}])
    assert [r["card_id"] for r in bike] == ["a.yml", "c.yml"]
    got = client.get(f"{TABLE}/distinct", params={"col": "tags"}).json()
    assert got["values"] == ["bike", "safety", "transit"] and got["list_like"] is True


def test_a_check_and_a_file_change_refresh_the_rows(make_session, folder):
    session = make_session(enable_apply=False)
    session.dispatch(OpenCatalog(path=str(folder)))
    client = TestClient(build_app(session))
    assert {r["card_id"]: r["fits"] for r in rows(client)}["b.yml"] is None
    session.dispatch(CheckCard(card_id="b.yml"))
    assert {r["card_id"]: r["fits"] for r in rows(client)}["b.yml"] is False
    session.dispatch(RenameCard(card_id="a.yml", new_name="z.yml"))
    assert sorted(r["card_id"] for r in rows(client)) == ["b.yml", "c.yml", "z.yml"]


def test_no_catalog_is_an_empty_table(make_session):
    client = TestClient(build_app(make_session()))
    payload = client.post(f"{TABLE}/rows", json={}).json()
    assert payload["total"] == 0 and tuple(payload["columns"]) == COLUMNS


def test_a_file_that_cannot_be_read_has_no_modified_time(tmp_path):
    assert _modified(str(tmp_path / "gone.yml")) is None
