"""project_cards.rename_card / duplicate_card / delete_card / restore_card / update_card_meta: the catalog's files."""

import json
from pathlib import Path
from typing import ClassVar, Literal

import projectcard
import pytest
import yaml
from netstead.workbench.errors import ActionError, PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import (
    DeleteCard,
    DuplicateCard,
    OpenCatalog,
    RenameCard,
    RestoreCard,
    SessionSummary,
    UpdateCardMeta,
)
from netstead_project_cards.card import DraftCard
from netstead_project_cards.catalog_edits import TRASH
from netstead_project_cards.errors import CardError
from netstead_project_cards.files import move_file, undo_move
from netstead_project_cards.types import PropertyChange, RoadwayAddition, RoadwayPropertyChange, Selection


def lanes(*ids, value=3):
    return RoadwayPropertyChange(
        facility=Selection(ids=list(ids)), property_changes={"lanes": PropertyChange(set=value)}, note="wider"
    )


CARD = DraftCard(project="Main St", tags=["safety"], notes="from the plan", changes=[lanes(1, 2)])


@pytest.fixture
def folder(session, tmp_path: Path) -> Path:
    root = (tmp_path / "catalog").resolve()
    (root / "sub").mkdir(parents=True)
    (root / "main.yml").write_text(CARD.to_yaml() + "# a comment\n", encoding="utf-8")
    (root / "other.yml").write_text(DraftCard(project="Other", changes=[lanes(3)]).to_yaml(), encoding="utf-8")
    (root / "card.json").write_text(json.dumps(CARD.to_card(), indent=2), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(root)))
    return root


def ids(session):
    catalog = session.state()["plugins"]["project_cards"]["catalog"]
    return sorted(c["card_id"] for c in catalog["cards"])


def files(session):
    return [(f["action"], f["card_id"], f["to"]) for f in session.state()["plugins"]["project_cards"]["files"]]


# ---------------------------------------------------------------------------------------------- rename


def test_rename_moves_the_file_and_rescans_the_catalog(session, folder):
    data = (folder / "main.yml").read_bytes()
    result = session.dispatch(RenameCard(card_id="main.yml", new_name="main-street"))  # the suffix is kept
    assert result == {
        "action": "renamed",
        "card_id": "main.yml",
        "to": "main-street.yml",
        "path": str(folder / "main-street.yml"),
    }
    assert not (folder / "main.yml").exists() and (folder / "main-street.yml").read_bytes() == data
    assert ids(session) == ["card.json", "main-street.yml", "other.yml"]
    assert files(session) == [("renamed", "main.yml", "main-street.yml")]
    session.dispatch(RenameCard(card_id="main-street.yml", new_name="sub/main.yml"))  # one sub-folder is fine
    assert ids(session) == ["card.json", "other.yml", "sub/main.yml"]


def test_rename_refuses_another_file_unless_asked(session, folder):
    with pytest.raises(ActionError, match="already exists in the catalog: rename with overwrite=True"):
        session.dispatch(RenameCard(card_id="main.yml", new_name="other.yml"))
    assert (folder / "main.yml").is_file() and ids(session) == ["card.json", "main.yml", "other.yml"]
    session.dispatch(RenameCard(card_id="main.yml", new_name="other.yml", overwrite=True))
    assert projectcard.read_card(folder / "other.yml").project == "Main St"
    assert ids(session) == ["card.json", "other.yml"]


def test_rename_onto_an_identical_file_needs_no_overwrite(session, folder):
    (folder / "twin.yml").write_bytes((folder / "main.yml").read_bytes())
    session.dispatch(RenameCard(card_id="main.yml", new_name="twin.yml"))
    assert not (folder / "main.yml").exists() and (folder / "twin.yml").is_file()


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("main.yml", "is already called main.yml"),
        ("  ", "give the card a file name"),
        ("main.txt", "a card file ends in .yml, .yaml or .json"),
        (".hidden.yml", "is not hidden"),
        ("a/b/c.yml", "one sub-folder"),
        (f"{TRASH}/main.yml", "is not hidden"),
    ],
)
def test_rename_refusals(session, folder, name, message):
    with pytest.raises(ActionError, match=message):
        session.dispatch(RenameCard(card_id="main.yml", new_name=name))
    assert (folder / "main.yml").is_file() and files(session) == []


def test_a_name_outside_the_catalog_or_the_roots_is_refused(session, folder, tmp_path):
    with pytest.raises(ActionError, match="is outside the catalog folder"):
        session.dispatch(RenameCard(card_id="main.yml", new_name="../escaped.yml"))
    with pytest.raises(ActionError, match="is outside the catalog folder"):
        session.dispatch(RenameCard(card_id="main.yml", new_name=str(tmp_path / "elsewhere.yml")))
    outside = Path(__file__).resolve().parent / "nope.yml"  # not under io.allowed_roots
    with pytest.raises(PathNotAllowed):
        session.dispatch(RenameCard(card_id="main.yml", new_name=str(outside)))
    assert not outside.exists() and not (tmp_path / "escaped.yml").exists()


def test_an_invalid_card_is_not_renamed(session, folder):
    link = {"link_id": 340, "from_node_id": 1, "to_node_id": 2, "name": "New", "facility_type": "local", "lanes": 1}
    bad = DraftCard(project="Bad", changes=[RoadwayAddition(links=[{**link, "allowed_uses": "all"}])])
    (folder / "bad.yml").write_text(bad.to_yaml(), encoding="utf-8")  # "local" is not a ProjectCard roadway
    session.dispatch(OpenCatalog(path=str(folder)))
    assert "bad.yml" in ids(session)  # the tolerant reader lists it
    with pytest.raises(ActionError, match="not a valid ProjectCard"):
        session.dispatch(RenameCard(card_id="bad.yml", new_name="still-bad.yml"))


def test_catalog_file_actions_need_a_catalog_and_a_card(session, tmp_path):
    with pytest.raises(ActionError, match="no catalog is open"):
        session.dispatch(RenameCard(card_id="main.yml", new_name="x.yml"))
    with pytest.raises(ActionError, match="no catalog is open"):
        session.dispatch(RestoreCard(card_id="main.yml"))


def test_a_card_whose_file_went_away_is_refused(session, folder):
    (folder / "main.yml").unlink()
    with pytest.raises(ActionError, match="no such file"):
        session.dispatch(DuplicateCard(card_id="main.yml"))
    (folder / "main.yml").mkdir()  # a folder where the card was
    with pytest.raises(ActionError, match="is not a file"):
        session.dispatch(DeleteCard(card_id="main.yml"))


# ---------------------------------------------------------------------------------------------- duplicate


def test_duplicate_copies_byte_for_byte_with_a_free_copy_name(session, folder):
    first = session.dispatch(DuplicateCard(card_id="main.yml"))
    second = session.dispatch(DuplicateCard(card_id="main.yml"))
    assert (first["to"], second["to"]) == ("main-copy.yml", "main-copy-2.yml")
    assert (folder / "main-copy.yml").read_bytes() == (folder / "main.yml").read_bytes()
    named = session.dispatch(DuplicateCard(card_id="main.yml", new_name="sub/v2"))
    assert named["to"] == "sub/v2.yml" and "sub/v2.yml" in ids(session)
    assert [f[0] for f in files(session)] == ["duplicated"] * 3


def test_duplicate_refusals(session, folder):
    with pytest.raises(ActionError, match="already exists in the catalog: duplicate with overwrite=True"):
        session.dispatch(DuplicateCard(card_id="main.yml", new_name="other.yml"))
    with pytest.raises(ActionError, match="needs another name"):
        session.dispatch(DuplicateCard(card_id="main.yml", new_name="main.yml"))
    session.dispatch(DuplicateCard(card_id="main.yml", new_name="other.yml", overwrite=True))
    assert (folder / "other.yml").read_bytes() == (folder / "main.yml").read_bytes()


# ---------------------------------------------------------------------------------------------- delete, restore


def test_delete_moves_to_the_trash_and_restore_puts_it_back(session, folder):
    data = (folder / "main.yml").read_bytes()
    result = session.dispatch(DeleteCard(card_id="main.yml"))
    trash = folder / TRASH / "main.yml"
    assert result["trash"] == str(trash) and trash.read_bytes() == data and not (folder / "main.yml").exists()
    assert ids(session) == ["card.json", "other.yml"]  # the hidden trash is not scanned
    state = session.state()["plugins"]["project_cards"]
    assert state["trash"] == [{"card_id": "main.yml", "path": str(folder / "main.yml"), "trash": str(trash)}]
    restored = session.dispatch(RestoreCard(card_id="main.yml"))
    assert restored["card_id"] == "main.yml" and (folder / "main.yml").read_bytes() == data and not trash.exists()
    assert ids(session) == ["card.json", "main.yml", "other.yml"]
    assert session.state()["plugins"]["project_cards"]["trash"] == []
    assert files(session) == [("deleted", "main.yml", f"{TRASH}/"), ("restored", "main.yml", "main.yml")]


def test_a_second_delete_of_the_same_name_gets_a_free_trash_name(session, folder):
    session.dispatch(DeleteCard(card_id="main.yml"))
    (folder / "main.yml").write_text(CARD.model_copy(update={"project": "Again"}).to_yaml(), encoding="utf-8")
    session.dispatch(OpenCatalog(path=str(folder)))
    result = session.dispatch(DeleteCard(card_id="main.yml"))
    assert result["trash"] == str(folder / TRASH / "main-2.yml")
    session.dispatch(RestoreCard(card_id="main.yml"))  # the newest deletion
    assert projectcard.read_card(folder / "main.yml").project == "Again"
    (folder / "main.yml").unlink()
    session.dispatch(RestoreCard(card_id="main.yml"))  # then the older one
    assert projectcard.read_card(folder / "main.yml").project == "Main St"


def test_restore_refusals_and_a_trash_left_by_an_earlier_session(session, make_session, folder):
    with pytest.raises(ActionError, match="there is no deleted card"):
        session.dispatch(RestoreCard(card_id="main.yml"))
    session.dispatch(DeleteCard(card_id="main.yml"))
    (folder / "main.yml").write_text(DraftCard(project="New", changes=[lanes(4)]).to_yaml(), encoding="utf-8")
    with pytest.raises(ActionError, match="already exists in the catalog: restore with overwrite=True"):
        session.dispatch(RestoreCard(card_id="main.yml"))
    (folder / "main.yml").unlink()
    later = make_session()  # a new session: nothing noted, the trash is on disk
    later.dispatch(OpenCatalog(path=str(folder)))
    later.dispatch(RestoreCard(card_id="main.yml"))
    assert projectcard.read_card(folder / "main.yml").project == "Main St"


# ---------------------------------------------------------------------------------------------- metadata


def test_update_meta_changes_only_those_keys(session, folder):
    before = yaml.safe_load((folder / "main.yml").read_text(encoding="utf-8"))
    result = session.dispatch(
        UpdateCardMeta(
            card_id="main.yml",
            project="Main Street road diet",
            tags=["safety", " ", "bike"],
            dependencies={"prerequisites": ["Bridge"], "conflicts": ["Widening"]},
        )
    )
    assert result["to"] == "dependencies, project, tags" and result["tags"] == ["safety", "bike"]
    after = yaml.safe_load((folder / "main.yml").read_text(encoding="utf-8"))
    assert after["project"] == "Main Street road diet" and after["tags"] == ["safety", "bike"]
    assert after["dependencies"] == {"prerequisites": ["Bridge"], "conflicts": ["Widening"]}
    assert after["changes"] == before["changes"] and after["notes"] == before["notes"]
    card = projectcard.read_card(folder / "main.yml", validate=True)
    assert card.project == "Main Street road diet"
    catalog = session.state()["plugins"]["project_cards"]["catalog"]
    assert next(c for c in catalog["cards"] if c["card_id"] == "main.yml")["tags"] == ["safety", "bike"]


def test_update_meta_clears_tags_and_keeps_json(session, folder):
    session.dispatch(UpdateCardMeta(card_id="card.json", tags=[], dependencies={}))
    data = json.loads((folder / "card.json").read_text(encoding="utf-8"))
    assert "tags" not in data and "dependencies" not in data and data["project"] == "Main St"
    session.dispatch(UpdateCardMeta(card_id="card.json", project="Renamed"))
    assert json.loads((folder / "card.json").read_text(encoding="utf-8"))["project"] == "Renamed"


def test_invalid_metadata_is_refused_and_the_file_is_unchanged(session, folder):
    data = (folder / "main.yml").read_bytes()
    for bad in (
        {"card_id": "main.yml", "project": ""},
        {"card_id": "main.yml", "dependencies": {"prerequisites": "Bridge"}},
        {"card_id": "main.yml", "dependencies": {"before": ["Bridge"]}},
        {"card_id": "main.yml", "tags": "safety"},
    ):
        with pytest.raises((ActionError, ValueError)):  # the Action's own fields refuse it (pydantic)
            session.dispatch({"type": "project_cards.update_card_meta", **bad})
    with pytest.raises(ActionError, match="needs a project name"):
        session.dispatch(UpdateCardMeta(card_id="main.yml", project="   "))
    assert (folder / "main.yml").read_bytes() == data and files(session) == []


def test_update_meta_refuses_a_card_projectcard_rejects(session, folder, monkeypatch):
    import netstead_project_cards.catalog_edits as edits

    monkeypatch.setattr(edits, "card_problems", lambda card: ["tags: not allowed here"])
    with pytest.raises(ActionError, match="not a valid ProjectCard: tags: not allowed here"):
        session.dispatch(UpdateCardMeta(card_id="main.yml", tags=["x"]))
    with pytest.raises(ActionError, match="is not a valid ProjectCard"):
        session.dispatch(DuplicateCard(card_id="main.yml"))


def test_update_meta_on_a_file_that_is_no_longer_yaml(session, folder):
    (folder / "main.yml").write_text("project: [unclosed\n", encoding="utf-8")
    with pytest.raises(ActionError):
        session.dispatch(UpdateCardMeta(card_id="main.yml", tags=["x"]))


# ---------------------------------------------------------------------------------------------- recorded, undone


def test_the_summary_lists_the_catalog_file_changes(session, folder):
    session.dispatch(RenameCard(card_id="main.yml", new_name="a.yml"))
    session.dispatch(DuplicateCard(card_id="a.yml"))
    session.dispatch(DeleteCard(card_id="other.yml"))
    session.dispatch(UpdateCardMeta(card_id="a.yml", tags=["x"]))
    text = session.dispatch(SessionSummary())["text"]
    section = text.split("## Catalog files\n\n")[1].split("\n\n")[0].splitlines()
    assert [line.split(" at ")[0] for line in section] == [
        "- renamed main.yml → a.yml",
        "- duplicated a.yml → a-copy.yml",
        "- deleted other.yml (moved to .trash/)",
        "- updated a.yml: tags",
    ]
    types = [e.action["type"] for e in session.history]
    assert "project_cards.rename_card" in types and "project_cards.update_card_meta" in types


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


def test_file_changes_in_a_failed_parent_are_put_back(make_session, tmp_path):
    nested = make_session(_nester())
    root = (tmp_path / "cat").resolve()
    root.mkdir()
    (root / "main.yml").write_text(CARD.to_yaml(), encoding="utf-8")
    (root / "b.yml").write_text(DraftCard(project="B", changes=[lanes(3)]).to_yaml(), encoding="utf-8")
    nested.dispatch(OpenCatalog(path=str(root)))
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    inners = [
        RenameCard(card_id="main.yml", new_name="renamed.yml").model_dump(mode="json"),
        DuplicateCard(card_id="renamed.yml").model_dump(mode="json"),
        UpdateCardMeta(card_id="renamed.yml", tags=["x"]).model_dump(mode="json"),
        DeleteCard(card_id="b.yml").model_dump(mode="json"),
    ]
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inners=inners))
    after = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert after == before and not (root / TRASH).exists()  # the trash folder the delete made is gone too
    state = nested.state()["plugins"]["project_cards"]
    assert state["files"] == [] and state["trash"] == []
    assert sorted(c["card_id"] for c in state["catalog"]["cards"]) == ["b.yml", "main.yml"]


def test_a_restore_in_a_failed_parent_goes_back_to_the_trash(make_session, tmp_path):
    nested = make_session(_nester())
    root = (tmp_path / "cat").resolve()
    root.mkdir()
    (root / "main.yml").write_text(CARD.to_yaml(), encoding="utf-8")
    nested.dispatch(OpenCatalog(path=str(root)))
    nested.dispatch(DeleteCard(card_id="main.yml"))
    with pytest.raises(ActionError, match="the parent failed"):
        nested.dispatch(ThenFail(inners=[RestoreCard(card_id="main.yml").model_dump(mode="json")]))
    assert not (root / "main.yml").exists() and (root / TRASH / "main.yml").is_file()
    assert [t["card_id"] for t in nested.state()["plugins"]["project_cards"]["trash"]] == ["main.yml"]


def test_a_catalog_folder_gone_before_the_rescan_keeps_the_file_change(session, folder, monkeypatch):
    import netstead_project_cards.catalog_edits as edits

    def gone(*args, **kwargs):
        raise ActionError("no such file or folder")

    monkeypatch.setattr(edits, "load_catalog", gone)
    session.dispatch(DuplicateCard(card_id="main.yml"))
    assert (folder / "main-copy.yml").is_file()


def test_the_assistant_is_not_offered_the_file_actions():
    assert DeleteCard.assistant is False and RenameCard.mutates is False


# ---------------------------------------------------------------------------------------------- files.move_file


def test_a_move_never_loses_the_file(tmp_path, monkeypatch):
    source, dest = tmp_path / "a.yml", tmp_path / "sub" / "b.yml"
    with pytest.raises(CardError, match="could not read"):
        move_file(source, dest, overwrite=False)
    source.write_text("x", encoding="utf-8")
    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self == source:
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(CardError, match="could not remove"):
        move_file(source, dest, overwrite=False)
    assert source.read_text(encoding="utf-8") == "x" and not dest.exists() and not dest.parent.exists()
    monkeypatch.setattr(Path, "unlink", real)
    moved = move_file(source, dest, overwrite=False)
    source.write_text("new", encoding="utf-8")  # a file appeared where the moved one was: never replaced
    assert undo_move(moved) == "restored" and source.read_text(encoding="utf-8") == "new"
    source.unlink()
    assert undo_move(moved) is None and source.read_text(encoding="utf-8") == "x" and not dest.exists()
