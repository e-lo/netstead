"""Renaming, deleting and restoring a card's file keeps an open draft's remembered source right."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

import pytest
from netstead.workbench.errors import ActionError
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import DeleteCard, EditCard, OpenCatalog, RenameCard, RestoreCard
from netstead_project_cards.card import DraftCard
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"


class ThenFail(BaseAction):
    type: Literal["nester.then_fail"] = "nester.then_fail"
    mutates: ClassVar[bool] = True
    inners: list[dict]


def _nester():
    def run(host, action):
        for inner in action.inners:
            host.dispatch(inner)
        raise ActionError("the parent failed")

    return WorkbenchPlugin(
        id="nester", name="N", version="0", requires_api=HOST_API, actions=(ActionSpec(ThenFail, run),)
    )


def card(project):
    change = RoadwayPropertyChange(facility=Selection(ids=[1]), property_changes={"lanes": PropertyChange(set=3)})
    return DraftCard(project=project, changes=[change])


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = (tmp_path / "cat").resolve()
    root.mkdir()
    (root / "main.yml").write_text(card("Main").to_yaml(), encoding="utf-8")
    (root / "other.yml").write_text(card("Other").to_yaml(), encoding="utf-8")
    return root


@pytest.fixture
def nested(make_session, folder):
    session = make_session(_nester())
    session.dispatch(OpenCatalog(path=str(folder)))
    session.dispatch(EditCard(card_id="main.yml"))
    return session


def source(session):
    return session.state()["plugins"]["project_cards"]["drafts"][NET]["source"]


def at(folder, name):
    return {"card_id": name, "path": str(folder / name)}


def test_renaming_the_file_updates_the_drafts_source(nested, folder):
    nested.dispatch(RenameCard(card_id="main.yml", new_name="renamed.yml"))
    assert source(nested) == at(folder, "renamed.yml")


def test_renaming_another_file_leaves_the_source_alone(nested, folder):
    nested.dispatch(RenameCard(card_id="other.yml", new_name="renamed.yml"))
    assert source(nested) == at(folder, "main.yml")


def test_a_rolled_back_rename_puts_the_source_back(nested, folder):
    inner = RenameCard(card_id="main.yml", new_name="renamed.yml").model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inners=[inner]))
    assert source(nested) == at(folder, "main.yml")


def test_deleting_the_file_clears_the_source_and_restoring_it_gives_it_back(nested, folder):
    nested.dispatch(DeleteCard(card_id="main.yml"))
    assert source(nested) is None
    nested.dispatch(RestoreCard(card_id="main.yml"))
    assert source(nested) == at(folder, "main.yml")


def test_deleting_another_file_leaves_the_source_alone(nested, folder):
    nested.dispatch(DeleteCard(card_id="other.yml"))
    nested.dispatch(RestoreCard(card_id="other.yml"))
    assert source(nested) == at(folder, "main.yml")


def test_a_rolled_back_delete_puts_the_source_back(nested, folder):
    inner = DeleteCard(card_id="main.yml").model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inners=[inner]))
    assert source(nested) == at(folder, "main.yml")


def test_a_rolled_back_restore_clears_the_source_again(nested, folder):
    nested.dispatch(DeleteCard(card_id="main.yml"))
    inner = RestoreCard(card_id="main.yml").model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inners=[inner]))
    assert source(nested) is None
    nested.dispatch(RestoreCard(card_id="main.yml"))
    assert source(nested) == at(folder, "main.yml")


def test_a_restore_does_not_give_a_source_to_a_draft_that_has_another(nested, folder):
    nested.dispatch(DeleteCard(card_id="main.yml"))
    nested.dispatch(EditCard(card_id="other.yml", replace_draft=True))
    nested.dispatch(RestoreCard(card_id="main.yml"))
    assert source(nested) == at(folder, "other.yml")
