"""project_cards.remove_change and project_cards.edit_card: editing a draft, and loading a catalog card into one."""

from pathlib import Path
from typing import ClassVar, Literal

import pandas as pd
import pytest
from netstead.workbench.actions import EditCells
from netstead.workbench.errors import ActionError, PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import (
    AddChange,
    ApplyCard,
    CommitCard,
    EditCard,
    OpenCatalog,
    RemoveChange,
    UndoChange,
)
from netstead_project_cards.card import Dependencies, DraftCard, read_card_file
from netstead_project_cards.types import (
    PropertyChange,
    RoadwayAddition,
    RoadwayDeletion,
    RoadwayPropertyChange,
    Selection,
)

NET = "leavenworth"
NEW_LINK = {
    "link_id": 340,
    "from_node_id": 1,
    "to_node_id": 2,
    "name": "New St",
    "facility_type": "residential",
    "lanes": 1,
    "allowed_uses": "all",
}


def lanes(link_id, value, existing=None):
    return RoadwayPropertyChange(
        facility=Selection(ids=[link_id]), property_changes={"lanes": PropertyChange(set=value, existing=existing)}
    )


CHANGES = [
    lanes(1, 2),
    RoadwayDeletion(links=[5], clean_nodes=True),
    lanes(3, 4),
    RoadwayAddition(links=[NEW_LINK]),
]


def tables(session):
    handle = session.network(NET)
    links = handle.links_df().sort_values("link_id").reset_index(drop=True)
    nodes = handle.nodes_df().sort_values("node_id").reset_index(drop=True)
    return links, nodes


def draft(session):
    return session.state()["plugins"]["project_cards"]["drafts"].get(NET)


def described(session):
    d = draft(session)
    return [c["describe"] for c in d["changes"]] if d else []


class ThenFail(BaseAction):
    type: Literal["nester.then_fail"] = "nester.then_fail"
    mutates: ClassVar[bool] = True
    inner: dict


def _nester():
    def run(host, action):
        host.dispatch(action.inner)
        raise ActionError("the parent failed")

    return WorkbenchPlugin(
        id="nester", name="N", version="0", requires_api=HOST_API, actions=(ActionSpec(ThenFail, run),)
    )


# ---------------------------------------------------------------------------------------------- remove_change


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_removing_any_change_leaves_the_network_as_if_it_was_never_made(make_session, index):
    edited = make_session()
    for change in CHANGES:
        edited.dispatch(AddChange(change=change))
    result = edited.dispatch(RemoveChange(index=index))
    remaining = [c for i, c in enumerate(CHANGES) if i != index]
    assert result["index"] == index and result["count"] == 3 and result["readded"] == len(CHANGES) - index - 1
    fresh = make_session()
    for change in remaining:
        fresh.dispatch(AddChange(change=change))
    for got, want in zip(tables(edited), tables(fresh), strict=True):
        pd.testing.assert_frame_equal(got, want)
    assert described(edited) == described(fresh)
    assert [c["index"] for c in draft(edited)["changes"]] == [0, 1, 2]


def test_the_draft_after_a_removal_undoes_and_commits_as_usual(session, tmp_path):
    for change in CHANGES[:3]:
        session.dispatch(AddChange(change=change))
    session.dispatch(RemoveChange(index=0))
    session.dispatch(UndoChange())  # the re-added lanes(3, 4): its new marks are the network's newest edits
    assert described(session) == [described_one(session, CHANGES[1])]
    links, _ = tables(session)
    assert links.loc[links.link_id == 1, "lanes"].tolist() == [1] and 5 not in links.link_id.tolist()
    path = tmp_path / "card.yml"
    session.dispatch(CommitCard(path=str(path), project="Only the deletion", confirm_warnings=True))
    assert [c.type for c in read_card_file(path).changes] == ["roadway_deletion"]


def described_one(session, change):
    from netstead_project_cards.lower import describe

    return describe(change)


def test_a_removal_a_later_change_depends_on_is_refused_and_changes_nothing(session):
    session.dispatch(AddChange(change=RoadwayAddition(links=[NEW_LINK])))
    session.dispatch(AddChange(change=lanes(340, 2)))
    before, version = tables(session), session.network(NET).version
    with pytest.raises(ActionError, match="change 2 depends on the removed change 1"):
        session.dispatch(RemoveChange(index=0))
    for got, want in zip(tables(session), before, strict=True):
        pd.testing.assert_frame_equal(got, want)
    assert session.network(NET).version == version and draft(session)["count"] == 2
    session.dispatch(RemoveChange(index=1))  # the last change has nothing after it: that removal is fine
    assert draft(session)["count"] == 1


def test_a_later_change_that_matches_nothing_any_more_is_refused(session):
    session.dispatch(AddChange(change=RoadwayAddition(links=[NEW_LINK])))
    session.dispatch(AddChange(change=RoadwayDeletion(links=[340, 6], ignore_missing=True)))
    session.dispatch(RemoveChange(index=1))
    # A query-like deletion of only the added link, with ignore_missing: without the addition it matches nothing.
    session.dispatch(AddChange(change=RoadwayDeletion(links=[340], ignore_missing=True)))
    with pytest.raises(ActionError, match="nothing on leavenworth matches it any more"):
        session.dispatch(RemoveChange(index=0))
    assert draft(session)["count"] == 2


def test_remove_change_refusals(session):
    with pytest.raises(ActionError, match="has 0 changes: there is no change 1"):
        session.dispatch(RemoveChange(index=0))
    session.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError, match="has 1 change: there is no change 3"):
        session.dispatch(RemoveChange(index=2))
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        RemoveChange(index=-1)
    session.dispatch(EditCells(ids=[3], values={"lanes": 7}))  # a core edit on top: the plugin may not undo it
    with pytest.raises(ActionError):
        session.dispatch(RemoveChange(index=0))
    assert draft(session)["count"] == 1


def test_a_catalog_card_applied_on_top_is_not_undone_in_the_drafts_place(session, tmp_path):
    root = tmp_path / "cat"
    root.mkdir()
    (root / "a.yml").write_text(DraftCard(project="A", changes=[lanes(2, 3)]).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    session.dispatch(AddChange(change=lanes(1, 2)))
    session.dispatch(ApplyCard(card_id="a.yml"))  # the plugin's own edit, but not the draft's
    with pytest.raises(ActionError, match="is not the draft's"):
        session.dispatch(RemoveChange(index=0))
    links, _ = tables(session)
    assert links.loc[links.link_id.isin([1, 2]), "lanes"].tolist() == [2, 3] and draft(session)["count"] == 1


def test_a_removal_in_a_failed_parent_is_rolled_back(make_session):
    nested = make_session(_nester())
    for change in CHANGES[:3]:
        nested.dispatch(AddChange(change=change))
    before = tables(nested)
    names = described(nested)
    inner = RemoveChange(index=1).model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=inner))
    for got, want in zip(tables(nested), before, strict=True):
        pd.testing.assert_frame_equal(got, want)
    assert described(nested) == names  # the removed change and the re-added one are back as they were
    nested.dispatch(RemoveChange(index=1))  # and the draft's marks still undo the network's edits
    assert draft(nested)["count"] == 2


# ------------------------------------------------------------------------------------------------- edit_card


@pytest.fixture
def folder(session, tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    card = DraftCard(
        project="Main St",
        tags=["safety"],
        dependencies=Dependencies(conflicts=["Other"]),
        notes="from the plan",
        changes=[lanes(1, 2, existing=1), lanes(2, 3, existing=1)],
    )
    (root / "main.yml").write_text(card.to_yaml(), encoding="utf-8")
    (root / "empty.yml").write_text("project: Empty\nchanges: []\n", encoding="utf-8")
    bad = DraftCard(project="Bad", changes=[lanes(2, 3), lanes(999999, 3)])
    (root / "bad.yml").write_text(bad.to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    return root


def test_edit_loads_the_card_into_the_draft_and_remembers_its_file(session, folder):
    result = session.dispatch(EditCard(card_id="main.yml"))
    path = str((folder / "main.yml").resolve())
    assert result == {
        "card_id": "main.yml",
        "path": path,
        "net_id": NET,
        "project": "Main St",
        "count": 2,
        "replaced": 0,
    }
    d = draft(session)
    assert d["project"] == "Main St" and d["tags"] == ["safety"] and d["notes"] == "from the plan"
    assert d["dependencies"]["conflicts"] == ["Other"] and d["source"] == {"card_id": "main.yml", "path": path}
    links, _ = tables(session)
    assert links.loc[links.link_id.isin([1, 2]), "lanes"].tolist() == [2, 3]


def test_edit_remove_and_save_over_the_file(session, folder):
    session.dispatch(EditCard(card_id="main.yml"))
    session.dispatch(RemoveChange(index=0))
    path = folder / "main.yml"
    d = draft(session)
    result = session.dispatch(
        CommitCard(path=d["source"]["path"], project=d["project"], tags=d["tags"], overwrite=True)
    )
    assert result["listed"] is True  # in the open catalog's folder: rescanned
    saved = read_card_file(path)
    assert saved.project == "Main St" and len(saved.changes) == 1 and saved.changes[0].facility.ids == [2]
    catalog = session.state()["plugins"]["project_cards"]["catalog"]
    assert {c["card_id"]: c["change_count"] for c in catalog["cards"]}["main.yml"] == 1
    assert draft(session) is None  # a new, empty draft


def test_save_as_a_new_file_refuses_to_overwrite_without_asking(session, folder):
    session.dispatch(EditCard(card_id="main.yml"))
    with pytest.raises(ActionError, match="already exists"):
        session.dispatch(CommitCard(path=str(folder / "main.yml"), project="Main St"))
    result = session.dispatch(CommitCard(path=str(folder / "main-v2.yml"), project="Main St v2"))
    assert result["listed"] is True
    ids = [c["card_id"] for c in session.state()["plugins"]["project_cards"]["catalog"]["cards"]]
    assert "main-v2.yml" in ids and "main.yml" in ids


def test_a_commit_outside_the_catalog_folder_does_not_rescan(session, folder, tmp_path):
    session.dispatch(AddChange(change=lanes(1, 2)))
    result = session.dispatch(CommitCard(path=str(tmp_path / "elsewhere" / "a.yml"), project="A"))
    assert result["listed"] is False


def test_a_catalog_folder_gone_before_the_rescan_keeps_the_listing(session, folder, monkeypatch):
    import netstead_project_cards.handlers as handlers

    def gone(*args, **kwargs):
        raise ActionError("no such file or folder")

    session.dispatch(AddChange(change=lanes(1, 2)))
    monkeypatch.setattr(handlers, "load_catalog", gone)
    result = session.dispatch(CommitCard(path=str(folder / "new.yml"), project="New"))
    assert result["listed"] is False and (folder / "new.yml").is_file()


def test_edit_refuses_to_replace_a_draft_unless_asked(session, folder):
    links, _ = tables(session)
    was = links.loc[links.link_id == 3, "lanes"].tolist()
    session.dispatch(AddChange(change=lanes(3, 4)))
    with pytest.raises(ActionError, match="has 1 uncommitted change: edit with replace_draft=True to undo it"):
        session.dispatch(EditCard(card_id="main.yml"))
    assert draft(session)["count"] == 1 and draft(session)["source"] is None
    result = session.dispatch(EditCard(card_id="main.yml", replace_draft=True))
    assert result["replaced"] == 1 and result["count"] == 2
    links, _ = tables(session)
    assert links.loc[links.link_id == 3, "lanes"].tolist() == was  # the replaced change was undone


def test_edit_refusals_leave_the_draft(session, folder):
    with pytest.raises(ActionError, match="has no card"):
        session.dispatch(EditCard(card_id="nope.yml"))
    session.dispatch(EditCells(ids=[1], values={"lanes": 5}))  # the card's existing lanes (1) no longer holds
    with pytest.raises(ActionError, match="change 1 of 2"):
        session.dispatch(EditCard(card_id="main.yml"))
    assert draft(session) is None
    (folder / "main.yml").unlink()
    with pytest.raises(ActionError, match="no such file"):
        session.dispatch(EditCard(card_id="main.yml"))


def test_an_empty_card_or_a_file_outside_the_roots_is_refused(session, folder, monkeypatch):
    session.dispatch(OpenCatalog(path=str(folder)))
    ids = [c["card_id"] for c in session.state()["plugins"]["project_cards"]["catalog"]["cards"]]
    if "empty.yml" in ids:  # projectcard may refuse a card without changes outright: then it is not listed
        with pytest.raises(ActionError, match="has no changes to edit"):
            session.dispatch(EditCard(card_id="empty.yml"))
    host = session._hosts["project_cards"]

    def outside(path):
        raise PathNotAllowed(f"{path} is outside io.allowed_roots")

    monkeypatch.setattr(host, "readable", outside)
    with pytest.raises(PathNotAllowed):
        session.dispatch(EditCard(card_id="main.yml"))


def test_a_failed_edit_with_replace_draft_puts_the_draft_back(session, folder):
    session.dispatch(AddChange(change=lanes(1, 9)))
    with pytest.raises(ActionError, match="change 2 of 2"):  # undone, then the card fails on its missing link
        session.dispatch(EditCard(card_id="bad.yml", replace_draft=True))
    links, _ = tables(session)
    assert links.loc[links.link_id == 1, "lanes"].tolist() == [9] and draft(session)["count"] == 1


def test_an_edit_in_a_failed_parent_puts_the_draft_and_its_name_back(make_session, tmp_path):
    nested = make_session(_nester())
    root = tmp_path / "cat"
    root.mkdir()
    (root / "a.yml").write_text(DraftCard(project="A", changes=[lanes(2, 3)]).to_yaml(), encoding="utf-8")
    nested.dispatch(OpenCatalog(path=str(root)))
    nested.dispatch(AddChange(change=lanes(1, 2)))
    inner = EditCard(card_id="a.yml", replace_draft=True).model_dump(mode="json")
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inner=inner))
    d = draft(nested)
    assert d["count"] == 1 and d["project"] == "" and d["source"] is None
    links, _ = tables(nested)
    assert links.loc[links.link_id.isin([1, 2]), "lanes"].tolist() == [2, 1]
