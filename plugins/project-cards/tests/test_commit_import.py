"""project_cards.commit writes a projectcard-valid YAML file (warnings confirmed first); .import applies one."""

from typing import ClassVar, Literal

import pytest
from netstead.workbench.actions import Select
from netstead.workbench.errors import ActionError, NotSupportedYet, PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionSpec, BaseAction, WorkbenchPlugin
from netstead_project_cards.actions import AddChange, CommitCard, ImportCard, UndoChange
from netstead_project_cards.card import DraftCard, read_card_file
from netstead_project_cards.types import PropertyChange, RoadwayPropertyChange, Selection

NET = "leavenworth"


def lanes(link_id, value):
    return RoadwayPropertyChange(
        facility=Selection(ids=[link_id]), property_changes={"lanes": PropertyChange(set=value)}
    )


def cell(session, key, column):
    links = session.network(NET).links_df()
    return links.loc[links.link_id == key, column].tolist()


def cards_state(session):
    return session.state()["plugins"]["project_cards"]


def test_commit_writes_a_card_projectcard_reads_and_starts_a_new_draft(session, tmp_path):
    import projectcard

    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "project_cards" / "speed.yml"
    result = session.dispatch(
        CommitCard(
            path=str(path),
            project="Speed fixes",
            tags=["fixes"],
            dependencies={"prerequisites": ["widening"]},
            notes="Fixes from the review.",
        )
    )
    assert result == {
        "card_id": "speed-fixes",
        "net_id": NET,
        "path": str(path.resolve()),
        "changes": 1,
        "warnings": 0,
        "catalog": False,
    }
    card = projectcard.read_card(path, validate=True)
    assert card.project == "Speed fixes" and card.tags == ["fixes"]
    assert card.dependencies == {"prerequisites": ["widening"]}
    state = cards_state(session)
    assert NET not in state["drafts"] and state["committed"][0]["card_id"] == "speed-fixes"
    with pytest.raises(ActionError, match="nothing to undo"):
        session.dispatch(UndoChange())  # a committed card is frozen
    session.dispatch(AddChange(change=lanes(2, 2)))
    other = tmp_path / "project_cards" / "speed-2.yml"
    assert session.dispatch(CommitCard(path=str(other), project="Speed fixes"))["card_id"] == "speed-fixes-2"


def test_live_edit_warnings_must_be_confirmed(session, tmp_path):
    bad_end = RoadwayPropertyChange(
        facility=Selection(ids=[1]), property_changes={"from_node_id": PropertyChange(set=424242)}
    )
    session.dispatch(AddChange(change=bad_end))
    path = tmp_path / "card.yml"
    with pytest.raises(ActionError, match="live edit warning"):
        session.dispatch(CommitCard(path=str(path), project="Bad end"))
    assert not path.exists()
    result = session.dispatch(CommitCard(path=str(path), project="Bad end", confirm_warnings=True))
    assert result["warnings"] >= 1 and path.exists()


def test_commit_refusals(session, tmp_path):
    with pytest.raises(ActionError, match="no changes to commit"):
        session.dispatch(CommitCard(path=str(tmp_path / "a.yml"), project="Empty"))
    session.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(PathNotAllowed):
        session.dispatch(CommitCard(path="/etc/cards/a.yml", project="Outside"))
    taken = tmp_path / "taken.yml"
    taken.write_text("keep me", encoding="utf-8")
    with pytest.raises(ActionError, match="already exists"):
        session.dispatch(CommitCard(path=str(taken), project="Taken"))
    assert taken.read_text(encoding="utf-8") == "keep me"
    session.dispatch(CommitCard(path=str(taken), project="Taken", overwrite=True))
    assert "project: Taken" in taken.read_text(encoding="utf-8")


def test_a_replayed_commit_confirms_and_overwrites(session, tmp_path):
    session.dispatch(AddChange(change=lanes(1, 2)))
    session.dispatch(CommitCard(path=str(tmp_path / "c.yml"), project="C"))
    assert "confirm_warnings=True" in session.history[-1].python and "overwrite=True" in session.history[-1].python


class AddToCatalog(BaseAction):
    type: Literal["catalog.add_card"] = "catalog.add_card"
    mutates: ClassVar[bool] = True
    path: str


def _catalog(added, fail=False):
    def add(host, action):
        if fail:
            raise ActionError("the catalog is read-only")
        added.append(action.path)
        return "added"

    return WorkbenchPlugin(
        id="catalog", name="Catalog", version="0", requires_api=HOST_API, actions=(ActionSpec(AddToCatalog, add),)
    )


def test_commit_adds_the_card_to_the_catalog_when_there_is_one(make_session, tmp_path):
    added = []
    session = make_session(_catalog(added))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    assert session.dispatch(CommitCard(path=str(path), project="C"))["catalog"] is True
    assert added == [str(path.resolve())]


def test_a_failing_catalog_leaves_no_file_and_the_draft(make_session, tmp_path):
    session = make_session(_catalog([], fail=True))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    with pytest.raises(ActionError, match="read-only"):
        session.dispatch(CommitCard(path=str(path), project="C"))
    assert not path.exists() and cards_state(session)["drafts"][NET]["count"] == 1


def test_a_failing_catalog_restores_the_file_it_overwrote(make_session, tmp_path):
    session = make_session(_catalog([], fail=True))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ActionError, match="read-only"):
        session.dispatch(CommitCard(path=str(path), project="C", overwrite=True))
    assert path.read_text(encoding="utf-8") == "keep me"


def write(card, path):
    path.write_text(card.to_yaml(), encoding="utf-8")
    return str(path)


def test_import_applies_a_card_and_names_the_draft(session, tmp_path):
    path = write(DraftCard(project="Imported", tags=["x"], changes=[lanes(1, 3)]), tmp_path / "card.yml")
    result = session.dispatch(ImportCard(path=path))
    assert result["imported"] == 1 and result["project"] == "Imported" and cell(session, 1, "lanes") == [3]
    assert cards_state(session)["drafts"][NET]["project"] == "Imported"
    assert session.history[-1].python == f"app.do(ImportCard(path={path!r}))"


def test_import_into_a_draft_with_changes_keeps_its_name(session, tmp_path):
    session.dispatch(AddChange(change=lanes(2, 2)))
    path = write(DraftCard(project="Imported", changes=[lanes(1, 3)]), tmp_path / "card.yml")
    result = session.dispatch(ImportCard(path=path))
    assert result["count"] == 2 and result["project"] == ""


def test_import_is_all_or_nothing(session, tmp_path):
    path = write(DraftCard(project="Bad", changes=[lanes(1, 3), lanes(999999, 3)]), tmp_path / "bad.yml")
    with pytest.raises(ActionError, match="change 2 of 2: link 999999 not found"):
        session.dispatch(ImportCard(path=path))
    assert (
        cell(session, 1, "lanes") == [1]
        and session.network(NET).version == 0
        and NET not in cards_state(session)["drafts"]
    )


def test_a_committed_query_selection_imports_with_its_ignore_missing(session, tmp_path):
    session.dispatch(Select(utterance="Benton Street"))
    session.dispatch(AddChange(use_selection=True, change=lanes(1, 2).model_copy(update={"facility": None})))
    path = tmp_path / "benton.yml"
    session.dispatch(CommitCard(path=str(path), project="Benton"))
    facility = read_card_file(path).changes[0].facility
    assert facility.resolved == [1, 5, 6, 8] and facility.ignore_missing is True


def test_import_reads_the_offline_reports_edit_log(session, tmp_path):
    from netstead.map.edits import Edit, EditLog, dump_edit_log

    now = cell(session, 7, "lanes")[0]
    path = tmp_path / "edits.yaml"
    dump_edit_log(
        EditLog(
            edits=[
                Edit(
                    id="e1",
                    kind="fix",
                    table="link",
                    pk={"link_id": 7},
                    column="lanes",
                    from_value=now,
                    to_value=now + 1,
                    reason="too few",
                )
            ]
        ),
        path,
    )
    session.dispatch(ImportCard(path=str(path)))
    assert cell(session, 7, "lanes") == [now + 1]


def test_import_refuses_a_query_without_provenance(session, tmp_path):
    path = tmp_path / "query.yml"
    path.write_text(
        "project: q\nchanges:\n- roadway_property_change:\n    facility:\n      links:\n        name: [Benton Street]\n"
        "        ignore_missing: true\n    property_changes:\n      lanes: {set: 2}\n",
        encoding="utf-8",
    )
    with pytest.raises(NotSupportedYet, match="apply_card"):
        session.dispatch(ImportCard(path=str(path)))


def test_import_refusals(session, tmp_path):
    with pytest.raises(PathNotAllowed):
        session.dispatch(ImportCard(path="/etc/card.yml"))
    with pytest.raises(ActionError, match="is not a file"):
        session.dispatch(ImportCard(path=str(tmp_path / "missing.yml")))
    empty = write(DraftCard(project="Empty"), tmp_path / "empty.yml")
    with pytest.raises(ActionError, match="no changes to import"):
        session.dispatch(ImportCard(path=empty))
    toml = tmp_path / "card.toml"
    toml.write_text('project = "t"\n', encoding="utf-8")
    with pytest.raises(NotSupportedYet, match="TOML"):
        session.dispatch(ImportCard(path=str(toml)))
