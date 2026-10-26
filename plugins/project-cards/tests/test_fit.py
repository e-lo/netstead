"""The catalog's status pill data (``catalog.fit``): fits, doesn't fit (with short reasons), not checked."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from netstead.workbench import build_app
from netstead_project_cards.actions import ApplyCard, CheckCard, OpenCatalog
from netstead_project_cards.card import DraftCard
from netstead_project_cards.catalog import fit
from netstead_project_cards.types import PropertyChange, RoadwayDeletion, RoadwayPropertyChange, Selection

NET = "leavenworth"


def lanes(*ids, existing=None, ignore_missing=False):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids), ignore_missing=ignore_missing),
        property_changes={"lanes": PropertyChange(set=3, existing=existing, existing_value_conflict="error")},
    )


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    cards = {
        "good.yml": [lanes(1)],
        "missing.yml": [lanes(1), lanes(999999)],
        "skipped.yml": [lanes(1, 999999, ignore_missing=True)],
        "nodes.yml": [RoadwayDeletion(nodes=[888888])],
        "conflict.yml": [lanes(1, existing=9)],
    }
    for name, changes in cards.items():
        (root / name).write_text(DraftCard(project=name, changes=changes).to_yaml(), encoding="utf-8")
    return root


def fits_of(session, folder) -> dict[str, dict]:
    session.dispatch(OpenCatalog(path=str(folder)))
    for card in ("good.yml", "missing.yml", "skipped.yml", "nodes.yml", "conflict.yml"):
        session.dispatch(CheckCard(card_id=card))
    return {c["card_id"]: c["fit"] for c in session.state()["plugins"]["project_cards"]["catalog"]["cards"]}


def test_each_card_reads_as_fits_or_doesnt_with_short_reasons(make_session, folder):
    got = fits_of(make_session(enable_apply=False), folder)
    assert got["good.yml"] == {
        "state": "fits",
        "label": "Fits this network",
        "reasons": [],
        "warnings": [],
        "net_id": NET,
    }
    assert got["missing.yml"]["state"] == "no" and got["missing.yml"]["label"] == "Doesn't fit"
    assert got["missing.yml"]["reasons"] == ["missing link 999999"]
    assert got["skipped.yml"]["state"] == "fits"  # its selection skips missing ids
    assert got["nodes.yml"]["reasons"] == ["missing node 888888"]
    (conflict,) = got["conflict.yml"]["reasons"]
    assert conflict.startswith("conflict: lanes: expected 9, found ")


def test_a_card_already_applied_does_not_fit_with_apply_on(make_session, folder):
    session = make_session(enable_apply=True)
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(ApplyCard(card_id="good.yml"))
    session.dispatch(CheckCard(card_id="good.yml"))
    (good,) = [c for c in session.state()["plugins"]["project_cards"]["catalog"]["cards"] if c["card_id"] == "good.yml"]
    assert good["fit"]["state"] == "no" and good["fit"]["reasons"][0].startswith(f"already applied on {NET} at ")


def test_the_catalog_route_carries_the_fit(make_session, folder):
    session = make_session(enable_apply=False)
    session.dispatch(OpenCatalog(path=str(folder)))
    listing = TestClient(build_app(session)).get("/api/plugins/project_cards/catalog").json()
    by_id = {c["card_id"]: c["fit"]["state"] for c in listing["cards"]}
    assert by_id == {
        "conflict.yml": "no",
        "good.yml": "fits",
        "missing.yml": "no",
        "nodes.yml": "no",
        "skipped.yml": "fits",
    }


def test_fit_without_a_check_or_a_reason():
    assert fit(None)["state"] == "unchecked"
    assert fit({"ok": False, "net_id": NET, "changes": []})["reasons"] == ["does not apply"]
    warned = {"ok": True, "net_id": NET, "changes": [{"reason": None, "warnings": ["lanes: expected 1, found 2"]}]}
    assert fit(warned)["warnings"] == ["lanes: expected 1, found 2"]
