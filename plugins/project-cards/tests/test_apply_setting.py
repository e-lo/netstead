"""``[plugins.project_cards] enable_apply``: applying catalog cards is off unless it is on."""

from __future__ import annotations

from pathlib import Path

import pytest
from netstead.workbench.errors import ActionError
from netstead_project_cards import CardsSettings
from netstead_project_cards.actions import AddChange, ApplyCard, CheckCard, CommitCard, OpenCatalog
from netstead_project_cards.card import DraftCard
from netstead_project_cards.flow import APPLY_OFF
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"
LANES_3 = RoadwayPropertyChange(facility=Selection(ids=[1]), property_changes={"lanes": PropertyChange(set=3)})


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "p.yml").write_text(DraftCard(project="P", changes=[LANES_3]).to_yaml(), encoding="utf-8")
    return root


def test_the_setting_is_off_by_default():
    assert CardsSettings().enable_apply is False


def test_apply_card_is_refused_while_the_setting_is_off(make_session, catalog):
    session = make_session(enable_apply=False)
    session.dispatch(OpenCatalog(path=str(catalog)))
    with pytest.raises(ActionError, match=r"^applying cards is turned off \(plugins\.project_cards\.enable_apply\)$"):
        session.dispatch(ApplyCard(card_id="p.yml"))
    assert APPLY_OFF == "applying cards is turned off (plugins.project_cards.enable_apply)"
    assert session.network(NET).version == 0  # nothing applied
    assert session.history[-1].action["type"] == "project_cards.apply_card" and not session.history[-1].ok
    cards = session.state()["plugins"]["project_cards"]
    assert cards["enable_apply"] is False and cards["applied"] == []


def test_apply_card_works_with_the_setting_on(make_session, catalog):
    session = make_session(enable_apply=True)
    session.dispatch(OpenCatalog(path=str(catalog)))
    assert session.dispatch(ApplyCard(card_id="p.yml"))["changes"] == 1
    assert session.state()["plugins"]["project_cards"]["enable_apply"] is True


@pytest.mark.parametrize(("enable_apply", "fits"), [(False, True), (True, False)])
def test_a_saved_card_reads_as_already_applied_only_with_apply_on(make_session, catalog, enable_apply, fits):
    # Saving the draft over a catalog card records it; with apply off that is not "applied", so the card still fits.
    session = make_session(enable_apply=enable_apply)
    session.dispatch(AddChange(change=LANES_3.model_copy(update={"facility": Selection(ids=[2])})))
    session.dispatch(CommitCard(project="Q", path=str(catalog / "q.yml")))
    session.dispatch(OpenCatalog(path=str(catalog)))
    check = session.dispatch(CheckCard(card_id="q.yml"))
    assert check["ok"] is fits
    assert (check.get("reason") or "").startswith("already applied") is not fits
