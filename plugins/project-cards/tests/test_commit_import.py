"""project_cards.commit writes a projectcard-valid YAML file (warnings confirmed first); .import applies one."""

import errno
import io
import os
import sys
from pathlib import Path
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


def replay(session, other):
    namespace = {"app": other}
    for entry in session.history:
        if entry.parent_seq is None and entry.ok:
            exec(entry.imports, namespace)  # our own recorded "copy as Python" lines
            exec(entry.python, namespace)


def test_a_replayed_commit_confirms_warnings_but_does_not_overwrite(session, make_session, tmp_path):
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    session.dispatch(CommitCard(path=str(path), project="C"))
    python = session.history[-1].python
    assert "confirm_warnings=True" in python and "overwrite" not in python
    with pytest.raises(ActionError, match="already exists"):  # core's save_network convention: never clobber
        replay(session, make_session(open_network=False))
    path.unlink()
    replay(session, make_session(open_network=False))
    assert "project: C" in path.read_text(encoding="utf-8")


def test_a_replayed_import_reproduces_the_network_and_the_draft(session, make_session, tmp_path):
    path = write(DraftCard(project="Imported", changes=[lanes(1, 3)]), tmp_path / "card.yml")
    session.dispatch(ImportCard(path=path))
    other = make_session(open_network=False)
    replay(session, other)
    assert cell(other, 1, "lanes") == [3] and cards_state(other)["drafts"][NET]["project"] == "Imported"


class _DiskFull:
    """A file whose every write fails as a full disk does (after the file was opened, so truncated)."""

    def __init__(self, f):
        self._f = f

    def write(self, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._f.close()

    def __getattr__(self, name):
        return getattr(self._f, name)


@pytest.fixture
def disk_full(monkeypatch, tmp_path):
    """``disk_full()``: from then on, every file opened for writing under ``tmp_path`` fails to write."""
    real = io.open

    def opening(file, mode="r", *args, **kwargs):
        f = real(file, mode, *args, **kwargs)
        ours = isinstance(file, int) or str(tmp_path.resolve()) in str(Path(file).resolve())  # an fd: a temp file
        return _DiskFull(f) if ours and any(m in mode for m in "wxa") else f

    return lambda: monkeypatch.setattr(io, "open", opening)


def files(folder):
    return sorted(p.name for p in folder.iterdir() if p.name != "user")


def test_a_failed_overwrite_keeps_the_previous_file(session, tmp_path, disk_full):
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    path.write_bytes(b"keep me")
    disk_full()
    with pytest.raises(ActionError, match=r"could not write .*No space left"):
        session.dispatch(CommitCard(path=str(path), project="C", overwrite=True))
    assert path.read_bytes() == b"keep me" and files(tmp_path) == ["c.yml"]


def test_a_failed_write_removes_the_folders_it_made(session, tmp_path, disk_full):
    session.dispatch(AddChange(change=lanes(1, 2)))
    disk_full()
    with pytest.raises(ActionError, match="could not write"):
        session.dispatch(CommitCard(path=str(tmp_path / "a" / "b" / "c.yml"), project="C"))
    assert not (tmp_path / "a").exists()


def test_a_filesystem_without_hard_links_still_never_overwrites(session, tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = tmp_path / "c.yml"
    session.dispatch(CommitCard(path=str(path), project="C"))
    assert "project: C" in path.read_text(encoding="utf-8")
    assert files(tmp_path) == ["c.yml"]  # no temporary file left behind
    session.dispatch(AddChange(change=lanes(2, 2)))
    with pytest.raises(ActionError, match="already exists"):
        session.dispatch(CommitCard(path=str(path), project="D"))


needs_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions that bind (not root)"
)


@pytest.fixture
def locked(tmp_path):
    """``locked(path, mode)``: chmod ``path`` for the test, restored afterwards so ``tmp_path`` can be removed."""
    done = []

    def lock(path, mode):
        done.append((path, path.stat().st_mode))
        path.chmod(mode)

    yield lock
    for path, mode in reversed(done):
        path.chmod(mode)


@needs_permissions
def test_an_unreadable_card_is_an_action_error(session, tmp_path, locked):
    path = write(DraftCard(project="Locked", changes=[lanes(1, 3)]), tmp_path / "card.yml")
    locked(tmp_path / "card.yml", 0)
    with pytest.raises(ActionError, match=r"could not read .*card\.yml: Permission denied"):
        session.dispatch(ImportCard(path=path))


@needs_permissions
def test_a_folder_that_cannot_be_searched_is_an_action_error(session, tmp_path, locked):
    session.dispatch(AddChange(change=lanes(1, 2)))
    (tmp_path / "shut").mkdir()
    locked(tmp_path / "shut", 0)
    with pytest.raises(ActionError, match=r"could not write .*Permission denied"):
        session.dispatch(CommitCard(path=str(tmp_path / "shut" / "c.yml"), project="C"))


class AddToCatalog(BaseAction):
    type: Literal["catalog.add_card"] = "catalog.add_card"
    mutates: ClassVar[bool] = True
    path: str


def _catalog(added, fail=False, before_failing=None):
    def add(host, action):
        if fail:
            if before_failing is not None:
                before_failing()
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


def test_a_failing_catalog_removes_the_folders_the_commit_made(make_session, tmp_path):
    session = make_session(_catalog([], fail=True))
    session.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError, match="read-only"):
        session.dispatch(CommitCard(path=str(tmp_path / "a" / "b" / "c.yml"), project="C"))
    assert not (tmp_path / "a").exists()


@needs_permissions
def test_a_restore_that_fails_keeps_the_catalogs_error_and_says_so(make_session, tmp_path, locked):
    folder = tmp_path / "cards"
    folder.mkdir()
    session = make_session(_catalog([], fail=True, before_failing=lambda: locked(folder, 0o500)))
    session.dispatch(AddChange(change=lanes(1, 2)))
    path = folder / "c.yml"
    path.write_text("keep me", encoding="utf-8")
    with pytest.raises(ActionError, match=r"read-only; the file at .*c\.yml could not be restored"):
        session.dispatch(CommitCard(path=str(path), project="C", overwrite=True))


@needs_permissions
def test_a_new_file_that_cannot_be_removed_keeps_the_catalogs_error_and_says_so(make_session, tmp_path, locked):
    folder = tmp_path / "cards"
    folder.mkdir()
    session = make_session(_catalog([], fail=True, before_failing=lambda: locked(folder, 0o500)))
    session.dispatch(AddChange(change=lanes(1, 2)))
    with pytest.raises(ActionError, match=r"read-only; the file at .*c\.yml could not be removed"):
        session.dispatch(CommitCard(path=str(folder / "c.yml"), project="C"))


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
    with pytest.raises(NotSupportedYet, match="change 1 of 1: query without provenance; re-resolution not supported"):
        session.dispatch(ImportCard(path=str(path)))
    assert session.network(NET).version == 0


def test_import_refusals(session, tmp_path):
    with pytest.raises(PathNotAllowed):
        session.dispatch(ImportCard(path="/etc/card.yml"))
    with pytest.raises(ActionError, match=r"no such file or folder: .*missing\.yml"):
        session.dispatch(ImportCard(path=str(tmp_path / "missing.yml")))
    (tmp_path / "folder.yml").mkdir()
    with pytest.raises(ActionError, match="is not a file"):
        session.dispatch(ImportCard(path=str(tmp_path / "folder.yml")))
    empty = write(DraftCard(project="Empty"), tmp_path / "empty.yml")
    with pytest.raises(ActionError, match="no changes to import"):
        session.dispatch(ImportCard(path=empty))
    toml = tmp_path / "card.toml"
    toml.write_text('project = "t"\n', encoding="utf-8")
    with pytest.raises(NotSupportedYet, match="TOML"):
        session.dispatch(ImportCard(path=str(toml)))


@needs_permissions
def test_a_card_in_a_folder_that_cannot_be_searched_is_an_action_error(session, tmp_path, locked):
    (tmp_path / "shut").mkdir()
    path = write(DraftCard(project="Shut", changes=[lanes(1, 3)]), tmp_path / "shut" / "card.yml")
    locked(tmp_path / "shut", 0)
    with pytest.raises(ActionError, match=r"^(?!internal error).*card\.yml"):  # a user-facing error, not a crash
        session.dispatch(ImportCard(path=path))
