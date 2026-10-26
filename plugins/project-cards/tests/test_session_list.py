"""The "This session" list (``plugins.project_cards.session``): what this session did with cards, newest first."""

from __future__ import annotations

from pathlib import Path

import pytest
from netstead_project_cards.actions import (
    AddChange,
    ApplyCard,
    CommitCard,
    DeleteCard,
    DuplicateCard,
    OpenCatalog,
    RenameCard,
    RestoreCard,
    UndoChange,
    UpdateCardMeta,
)
from netstead_project_cards.card import DraftCard
from netstead_project_cards.state import AppliedCard, CardsState, session_row
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"


def lanes(link: int, value: int = 3) -> RoadwayPropertyChange:
    return RoadwayPropertyChange(facility=Selection(ids=[link]), property_changes={"lanes": PropertyChange(set=value)})


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    (root / "p.yml").write_text(DraftCard(project="P", changes=[lanes(1)]).to_yaml(), encoding="utf-8")
    return root


def rows(session) -> list[tuple[str, str]]:
    return [(r["kind"], r["text"]) for r in session.state()["plugins"]["project_cards"]["session"]]


def test_saves_and_catalog_file_changes_are_listed_newest_first(make_session, catalog):
    session = make_session(enable_apply=False)
    assert rows(session) == []
    session.dispatch(AddChange(change=lanes(2)))
    session.dispatch(CommitCard(project="Q", path=str(catalog / "q.yml")))
    session.dispatch(OpenCatalog(path=str(catalog)))
    session.dispatch(RenameCard(card_id="p.yml", new_name="r.yml"))
    session.dispatch(DuplicateCard(card_id="r.yml"))
    session.dispatch(UpdateCardMeta(card_id="r.yml", project="R"))
    session.dispatch(DeleteCard(card_id="r-copy.yml"))
    session.dispatch(RestoreCard(card_id="r-copy.yml"))
    assert rows(session) == [
        ("restored", "Restored r-copy.yml"),
        ("deleted", "Deleted r-copy.yml (moved to .trash/)"),
        ("updated", "Edited the metadata of r.yml (project)"),
        ("duplicated", "Duplicated r.yml as r-copy.yml"),
        ("renamed", "Renamed p.yml to r.yml"),
        ("saved", "Saved q.yml"),
    ]
    saved = session.state()["plugins"]["project_cards"]["session"][-1]
    assert saved["verb"] == "Saved" and saved["detail"] == f"1 change(s) from {NET}" and saved["card_id"] == "q"
    assert saved["at"].endswith("+00:00")


def test_an_applied_card_is_listed_with_apply_on(make_session, catalog):
    session = make_session(enable_apply=True)
    session.dispatch(OpenCatalog(path=str(catalog)))
    session.dispatch(ApplyCard(card_id="p.yml"))
    assert rows(session) == [("applied", f"Applied p.yml to {NET}")]


@pytest.mark.parametrize(("enable_apply", "kinds"), [(False, ["saved"]), (True, ["applied", "saved"])])
def test_applied_cards_are_hidden_while_apply_is_off(enable_apply, kinds):
    state = CardsState()
    state.commit(NET, DraftCard(project="Q", changes=[lanes(1)]), "/c/q.yml")
    state.record_applied(AppliedCard("p.yml", NET, "/c/p.yml", "P", "2026-10-21T10:00:00+00:00", 1, "catalog", ()))
    assert [r["kind"] for r in state.session_rows(apply_enabled=enable_apply)] == kinds


def test_undoing_an_applied_card_takes_it_off_the_list(make_session, catalog):
    session = make_session(enable_apply=True)
    session.dispatch(OpenCatalog(path=str(catalog)))
    session.dispatch(ApplyCard(card_id="p.yml"))
    session.dispatch(UndoChange())
    assert rows(session) == []


def test_the_undos_take_entries_back_and_put_them_back():
    state = CardsState()
    forget_file = state.record_file({"action": "renamed", "card_id": "a.yml", "to": "b.yml"})
    applied = AppliedCard("c.yml", NET, "/c/c.yml", "C", "2026-10-21T10:00:00+00:00", 1, "catalog", (1,))
    forget_applied = state.record_applied(applied)
    assert [r["kind"] for r in state.session_rows(apply_enabled=True)] == ["applied", "renamed"]
    put_back = state.forget_applied(applied)
    assert [r["kind"] for r in state.session_rows(apply_enabled=True)] == ["renamed"]
    put_back()
    put_back()  # a second call adds nothing twice
    assert [r["kind"] for r in state.session_rows(apply_enabled=True)] == ["applied", "renamed"]
    forget_applied()
    forget_file()
    assert state.session_rows(apply_enabled=True) == []
    committed = AppliedCard("d", NET, "/c/d.yml", "D", "t", 1, "committed", ())
    state.record_applied(committed)  # a committed card is logged by commit() as "saved", not here
    assert state.session_rows(apply_enabled=True) == []


def test_a_rolled_back_commit_leaves_no_saved_entry():
    state = CardsState()
    _, undo = state.commit(NET, DraftCard(project="Q", changes=[lanes(1)]), "/c/q.yml")
    assert [r["text"] for r in state.session_rows()] == ["Saved q.yml"]
    undo()
    assert state.session_rows() == []


def test_an_unknown_file_action_still_reads():
    row = session_row({"kind": "file", "action": "moved", "card_id": "a.yml", "to": "b", "at": "t"})
    assert row["kind"] == "moved" and row["text"] == "Moved a.yml" and row["detail"] == ""
