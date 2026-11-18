"""The sample ProjectCard catalog bundled with the Leavenworth fixture: valid, fitting, and openable from the panel.

The session here allows only ``tmp_path`` (not the fixture's folder), like an installed netstead whose fixtures sit
outside ``io.allowed_roots``: the catalog opens through the Host's read-only reach into the bundled folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session, build_app
from netstead.workbench.actions import OpenNetwork
from netstead.workbench.errors import ActionError
from netstead_project_cards import plugin
from netstead_project_cards.actions import (
    AddChange,
    CheckCard,
    CommitCard,
    DeleteCard,
    DuplicateCard,
    OpenCatalog,
    RenameCard,
    UpdateCardMeta,
)
from netstead_project_cards.card import read_card_file
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

CARDS = leavenworth.cards_dir()
FILES = sorted(CARDS.glob("*.yml"))
NOT_FITTING = "cedar-street-road-diet-stale.yml"
SAMPLE = "Sample cards (Leavenworth)"


@pytest.fixture
def session(tmp_path: Path) -> Session:
    """A Workbench session on Leavenworth whose only allowed root is ``tmp_path``."""
    env = {
        "NETSTEAD_CONFIG_DIR": str(tmp_path / "user"),
        "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([str(tmp_path.resolve())]),
    }
    built = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[plugin()], keyring=None)
    built.dispatch(OpenNetwork(source=str(leavenworth.parquet_dir())))  # a bundled example: opens outside the roots
    return built


@pytest.fixture
def links_and_nodes(session):
    net = session.network("leavenworth")
    return net.links_df(), net.nodes_df()


def cards_state(session):
    return session.state()["plugins"]["project_cards"]


# ---------------------------------------------------------------------------------------------------- the files


def test_the_catalog_has_nine_cards_each_with_a_year_and_a_theme_tag():
    assert len(FILES) == 9
    for path in FILES:
        tags = read_card_file(path).tags
        assert any(t.startswith("year:20") for t in tags), path.name
        assert {"safety", "bike", "transit", "maintenance"} & set(tags), path.name


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_card_validates_with_projectcard(path):
    import projectcard

    assert projectcard.read_card(path, validate=True).project == read_card_file(path).project


def test_every_card_but_the_stale_one_fits_leavenworth(links_and_nodes):
    from netstead_project_cards.catalog import applicability, fit

    links, nodes = links_and_nodes
    verdicts = {p.name: fit(applicability(read_card_file(p), links, nodes)) for p in FILES}
    assert {n for n, v in verdicts.items() if v["state"] != "fits"} == {NOT_FITTING}
    assert verdicts[NOT_FITTING]["reasons"] == ["conflict: lanes: expected 2, found 1 on 12 of 12 links"]
    assert all(v["warnings"] == [] for v in verdicts.values())


def test_the_forms_the_catalog_shows_are_all_there():
    cards = {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in FILES}
    kinds = {name: [next(iter(c)) for c in card["changes"]] for name, card in cards.items()}
    assert {k for ks in kinds.values() for k in ks} == {
        "roadway_property_change",
        "roadway_deletion",
        "roadway_addition",
    }
    facilities = [
        c["roadway_property_change"]["facility"]
        for card in cards.values()
        for c in card["changes"]
        if "roadway_property_change" in c
    ]
    assert any("from" in f and "to" in f for f in facilities)  # a Segment
    assert any("model_link_id" in f.get("links", {}) for f in facilities)  # a Link list
    assert any("osm_link_id" in f.get("links", {}) for f in facilities)
    assert any("osm_node_id" in f.get("nodes", {}) for f in facilities)
    assert "netstead.resolved[0]" in cards["front-street-pedestrian-improvements.yml"]["notes"]
    assert cards["commercial-street-bike-lanes.yml"]["dependencies"]["prerequisites"] == [
        read_card_file(CARDS / "commercial-street-road-diet.yml").project
    ]


def test_the_added_ids_do_not_collide_with_the_network(links_and_nodes):
    links, _ = links_and_nodes
    added = yaml.safe_load((CARDS / "nordic-circle-connector.yml").read_text(encoding="utf-8"))
    ids = [r["model_link_id"] for r in added["changes"][0]["roadway_addition"]["links"]]
    assert not set(ids) & set(links.link_id) and min(ids) > links.link_id.max()


def test_the_readme_lists_every_card():
    readme = (CARDS / "README.md").read_text(encoding="utf-8")
    assert all(p.name in readme for p in FILES)


# ---------------------------------------------------------------------------------------------- the Workbench


def test_the_host_offers_the_sample_catalog_and_the_state_carries_it(session):
    (sample,) = cards_state(session)["samples"]
    assert sample == {
        "id": "leavenworth",
        "name": SAMPLE,
        "path": str(CARDS),
        "network": "leavenworth",
        "cards": 9,
    }


def test_the_sample_catalog_opens_outside_the_allowed_roots_and_lists_its_cards(session):
    session.dispatch(OpenCatalog(path=str(CARDS)))
    catalog = cards_state(session)["catalog"]
    assert catalog["path"] == str(CARDS) and catalog["rejected"] == []
    assert [c["card_id"] for c in catalog["cards"]] == [p.name for p in FILES]


def test_checking_the_catalog_gives_eight_fits_and_one_doesnt_with_its_reason(session):
    session.dispatch(OpenCatalog(path=str(CARDS)))
    listing = TestClient(build_app(session)).get("/api/plugins/project_cards/catalog").json()
    fits = {c["card_id"]: c["fit"] for c in listing["cards"]}
    assert [n for n, f in fits.items() if f["state"] == "no"] == [NOT_FITTING]
    assert fits[NOT_FITTING]["label"] == "Doesn't fit"
    assert fits[NOT_FITTING]["reasons"] == ["conflict: lanes: expected 2, found 1 on 12 of 12 links"]
    assert sum(f["state"] == "fits" for f in fits.values()) == 8
    session.dispatch(CheckCard(card_id="commercial-street-road-diet.yml"))  # one card, on its own


def test_a_card_can_be_inspected(session):
    session.dispatch(OpenCatalog(path=str(CARDS)))
    card = TestClient(build_app(session)).get(
        "/api/plugins/project_cards/card", params={"card_id": "commercial-street-road-diet.yml"}
    )
    assert card.status_code == 200 and "lanes" in json.dumps(card.json())


def test_a_folder_beside_the_sample_catalog_is_still_refused(session):
    with pytest.raises(ActionError, match="outside the allowed folders"):
        session.dispatch(OpenCatalog(path=str(CARDS.parent)))
    with pytest.raises(ActionError, match="outside the allowed folders"):
        session.dispatch(OpenCatalog(path=str(CARDS / "..")))


@pytest.mark.parametrize(
    "action",
    [
        RenameCard(card_id="commercial-street-road-diet.yml", new_name="renamed.yml"),
        DuplicateCard(card_id="commercial-street-road-diet.yml"),
        DeleteCard(card_id="commercial-street-road-diet.yml"),
        UpdateCardMeta(card_id="commercial-street-road-diet.yml", tags=["x"]),
    ],
    ids=lambda a: a.type,
)
def test_the_sample_catalog_refuses_every_file_change(session, action):
    before = {p.name: p.read_bytes() for p in CARDS.iterdir() if p.is_file()}
    session.dispatch(OpenCatalog(path=str(CARDS)))
    with pytest.raises(ActionError, match="bundled sample catalog"):
        session.dispatch(action)
    assert {p.name: p.read_bytes() for p in CARDS.iterdir() if p.is_file()} == before
    assert not (CARDS / ".trash").exists()


def test_a_commit_into_the_sample_folder_is_refused(session):
    change = RoadwayPropertyChange(facility=Selection(ids=[1]), property_changes={"lanes": PropertyChange(set=2)})
    session.dispatch(AddChange(change=change))
    with pytest.raises(ActionError, match="bundled sample catalog"):
        session.dispatch(CommitCard(path=str(CARDS / "mine.yml"), project="Mine"))
    assert not (CARDS / "mine.yml").exists()


def test_the_sample_catalog_stays_read_only_even_inside_the_allowed_roots(tmp_path):
    env = {
        "NETSTEAD_CONFIG_DIR": str(tmp_path / "user"),
        "NETSTEAD_IO__ALLOWED_ROOTS": json.dumps([str(CARDS.parent.resolve())]),
    }
    session = Session(project_dir=tmp_path, environ=env, parser=StubParser(), plugins=[plugin()], keyring=None)
    session.dispatch(OpenCatalog(path=str(CARDS)))
    with pytest.raises(ActionError, match="bundled sample catalog"):
        session.dispatch(DeleteCard(card_id=NOT_FITTING))
    assert (CARDS / NOT_FITTING).is_file()
