"""Pending edits: every mutation is recorded, re-checked live, reverted last-first, and shown as dirty state."""

import shutil
import warnings
from pathlib import Path
from typing import ClassVar, Literal

import pytest
from corral.editing import Edit, UnsupportedEditOp
from netstead import Network
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import (
    AddRows,
    BaseAction,
    CloseNetwork,
    DeleteRows,
    EditCells,
    OpenNetwork,
    SaveNetwork,
    Select,
    UndoEdit,
)
from netstead.workbench.editing import plan_add, plan_delete, plan_update
from netstead.workbench.errors import ActionError, NotSupportedYet, PathNotAllowed
from netstead.workbench.plugins import HOST_API, ActionSpec, WorkbenchPlugin

SRC = str(leavenworth.parquet_dir())


@pytest.fixture
def session(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=SRC))
    return s


def handle(s):
    return s.network("leavenworth")


def cell(s, link_id, column="free_speed"):
    df = handle(s).links_df()
    return df.loc[df.link_id == link_id, column].tolist()


def mutate(s, plan, **kwargs):
    return s.mutate("leavenworth", plan.edits, note=plan.summary, **kwargs)


def test_a_mutation_is_a_pending_edit(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"free_speed": 30}))
    (entry,) = session.edits["leavenworth"].entries
    assert (entry.seq, entry.source, entry.version, entry.touched) == (1, "python", 1, {"link": {1}})
    assert entry.view()["rows"] == {"link": 1}
    assert session.state()["edits"]["leavenworth"] == {
        "pending": 1,
        "unsaved": 1,
        "dirty": True,
        "warnings": 0,
        "truncated": False,
        "check_failed": False,
        "last_source": "python",
    }
    assert session.history[-1].action["type"] == "python.mutate"  # Part 1: recorded outside an Action


def test_live_warnings_follow_later_edits(session):
    net = handle(session).roadway
    mutate(session, plan_update(net, "link", [1], {"from_node_id": 424242}))
    assert [w.code for w in session.edit_check("leavenworth").warnings] == ["edit.fk_missing"]
    mutate(session, plan_add(net, "node", [{"node_id": 424242, "x_coord": -120.66, "y_coord": 47.6}]))
    assert session.edit_check("leavenworth").warnings == ()  # the second edit fixed the first one's key


def test_undo_reverses_the_last_edit_only(session):
    h = handle(session)
    mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 30}))
    mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 25}))
    undone = session.undo_last("leavenworth", source="python")
    assert undone.seq == 2 and cell(session, 1) == [30.0] and h.version == 3
    assert h.lineage[-1] == "undo: link 1: free_speed = 25.0"
    assert session.history[-1].action["type"] == "python.revert"  # outside an Action: recorded, not replayable
    session.undo_last("leavenworth", source="python")
    assert cell(session, 1) == [40.0] and session.edits["leavenworth"].entries == []
    with pytest.raises(ActionError, match="nothing to undo"):
        session.undo_last("leavenworth", source="python")


def test_only_the_newest_change_can_be_reverted(session):
    h = handle(session)
    first = mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 30}))
    mutate(session, plan_update(h.roadway, "link", [2], {"free_speed": 30}))
    with pytest.raises(ActionError, match="only the newest change"):
        session.revert("leavenworth", first, note="undo")
    assert h.version == 2 and len(session.edits["leavenworth"].entries) == 2


def test_undo_restores_a_row_that_holds_nulls(tmp_path, isolated_env):
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=str(leavenworth.csv_dir())))
    h = s.network()
    s.mutate(h.id, plan_update(h.roadway, "link", [27], {"lanes": 3}).edits, note="lanes")  # link 27's name is null
    s.undo_last(h.id, source="python")
    assert int((h.links_df().link_id == 27).sum()) == 1  # Task 1: not two copies


def test_core_does_not_undo_a_plugins_change(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}), origin="cards")
    assert session.edits["leavenworth"].last().source == "cards"
    with pytest.raises(ActionError, match="made by cards"):
        session.undo_last("leavenworth")
    assert session.undo_last("leavenworth", source="cards").source == "cards"
    assert handle(session).lineage[-1] == "cards: undo: link 1: lanes = 2"


def test_a_failed_mutation_records_nothing(session):
    h = handle(session)
    good = plan_update(h.roadway, "link", [1], {"lanes": 2}).edits
    with pytest.raises(UnsupportedEditOp):
        session.mutate("leavenworth", [*good, Edit(op="explode", table="link")], note="bad")
    assert "leavenworth" not in session.edits and h.version == 0 and cell(session, 1, "lanes") == [1]


class Scribble(BaseAction):
    type: Literal["scribble.go"] = "scribble.go"
    mutates: ClassVar[bool] = True
    fail: bool = False


def _scribble(host, action: Scribble) -> None:
    plan = plan_update(host.network().roadway, "link", [1], {"lanes": 2})
    host.mutate(None, plan.edits, note=plan.summary)
    if action.fail:
        raise RuntimeError("after mutate")


def test_a_failed_action_leaves_no_pending_edit(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="scribble", name="Scribble", version="0", requires_api=HOST_API, actions=(ActionSpec(Scribble, _scribble),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    assert not s.dispatch_recorded(Scribble(fail=True)).ok
    assert "leavenworth" not in s.edits and s.network().version == 0  # Part 1 rolled the swap back; P2 the ledger
    assert s.dispatch_recorded(Scribble()).ok
    assert s.edits["leavenworth"].last().source == "scribble" and s.history[-1].action["type"] == "scribble.go"


def test_saving_and_undoing_a_saved_edit(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    ledger = session.edits["leavenworth"]
    ledger.mark_saved()
    assert ledger.summary()["dirty"] is False and ledger.summary()["unsaved"] == 0
    session.undo_last("leavenworth", source="python")
    assert ledger.summary()["dirty"] is True  # the open network no longer matches the saved copy


def test_closing_the_network_drops_its_edits(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    session.dispatch(CloseNetwork(net_id="leavenworth"))
    assert session.edits == {} and "leavenworth" not in session.state()["edits"]


def test_an_id_selection_drops_deleted_links(session):
    session.dispatch(Select(link_ids=[1, 2]))
    assert session.selection["intent"] is None  # made by ids
    mutate(session, plan_delete(handle(session).roadway, "link", [1]))
    assert session.selection["link_ids"] == [2] and session.selection["status"] == "resolved"
    mutate(session, plan_delete(handle(session).roadway, "link", [2]))
    assert session.selection is None  # nothing left to select


def test_an_id_selection_survives_an_unrelated_edit_unchanged(session):
    session.dispatch(Select(link_ids=[1, 2]))
    before = session.selection
    mutate(session, plan_update(handle(session).roadway, "link", [3], {"lanes": 2}))
    assert session.selection is before


def test_a_query_selection_is_resolved_again(session):
    sel = session.dispatch(Select(utterance="Ski Hill Drive"))
    assert sel["status"] == "resolved" and len(sel["link_ids"]) == 20 and sel["intent"] is not None
    gone = sel["link_ids"][0]
    mutate(session, plan_delete(handle(session).roadway, "link", [gone]))
    assert len(session.selection["link_ids"]) == 19 and gone not in session.selection["link_ids"]
    assert session.selection["utterance"] == "Ski Hill Drive"
    session.undo_last("leavenworth", source="python")
    assert len(session.selection["link_ids"]) == 20  # the revert brings the link back into the query's answer


class Snip(BaseAction):
    type: Literal["snip.cut"] = "snip.cut"
    mutates: ClassVar[bool] = True


def _snip(host, action: Snip) -> None:
    plan = plan_delete(host.network().roadway, "link", [1])
    host.mutate(None, plan.edits, note=plan.summary)
    assert host.selection["link_ids"] == [2]  # R4 moved the selection off the deleted link...
    raise RuntimeError("after the delete")  # ...and this failure must put it back


def test_a_failed_action_keeps_the_old_selection(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="snip", name="Snip", version="0", requires_api=HOST_API, actions=(ActionSpec(Snip, _snip),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(Select(link_ids=[1, 2]))
    before = s.selection
    entry = s.dispatch_recorded(Snip())
    assert not entry.ok and "after the delete" in entry.error  # the handler got past its R4 assertion
    assert s.selection is before and s.selection["link_ids"] == [1, 2]  # Part 1's rollback restores the view
    assert 1 in set(s.network().links_df().link_id) and s.network().version == 0  # and the network
    assert "leavenworth" not in s.edits


# ---------------------------------------------------------------------------- the edit Actions


def test_a_cell_edit_is_one_recorded_action(session):
    result = session.dispatch(EditCells(ids=[1], values={"free_speed": 30}, note="signed 30 mph"))
    assert cell(session, 1) == [30.0] and result["version"] == 1 and result["warnings"] == []
    assert result["summary"] == "link 1: free_speed = 30.0" and result["rows"] == {"link": 1} and result["keys"] == [1]
    entry = session.history[-1]
    assert entry.python == "app.do(EditCells(ids=[1], values={'free_speed': 30}, note='signed 30 mph'))"
    assert entry.imports == "from netstead.workbench import EditCells"
    assert handle(session).lineage == ["link 1: free_speed = 30.0 (signed 30 mph)"]
    assert session.edits["leavenworth"].last().source == "workbench"
    assert [e.action["type"] for e in session.history] == ["open_network", "edit_cells"]  # no python.mutate entry


def test_an_edit_the_spec_disagrees_with_is_applied_and_warned(session):
    result = session.dispatch(EditCells(table="node", ids=[1], values={"ctrl_type": "bogus"}))
    assert [w["code"] for w in result["warnings"]] == ["edit.enum"] and result["open_warnings"] == 1
    assert session.state()["edits"]["leavenworth"]["warnings"] == 1


def test_what_storage_cannot_hold_is_refused_and_recorded(session):
    with pytest.raises(ActionError, match="does not fit"):
        session.dispatch(EditCells(ids=[1], values={"lanes": 1.5}))
    assert not session.history[-1].ok and handle(session).version == 0 and "leavenworth" not in session.edits


def test_delete_a_link_then_undo(session):
    lanes = handle(session).roadway.tables["lane"].count()
    result = session.dispatch(DeleteRows(ids=[1]))
    assert result["rows"] == {"lane": 1, "link": 1} and 1 not in set(handle(session).links_df().link_id)
    undone = session.dispatch(UndoEdit())
    assert undone["undone"]["source"] == "workbench" and undone["version"] == 2
    assert 1 in set(handle(session).links_df().link_id) and handle(session).roadway.tables["lane"].count() == lanes
    assert [e.action["type"] for e in session.history[-2:]] == ["delete_rows", "undo_edit"]


def test_deleting_a_node_a_link_uses_is_refused(session):
    with pytest.raises(ActionError, match="still used by link"):
        session.dispatch(DeleteRows(table="node", ids=[1]))


def test_core_undo_refuses_a_python_change(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    with pytest.raises(ActionError, match="made by python"):
        session.dispatch(UndoEdit())


def test_add_rows_from_python(session):
    node = {"node_id": 9001, "x_coord": -120.66, "y_coord": 47.6}
    link = {"link_id": 9001, "from_node_id": 1, "to_node_id": 9001, "directed": False, "name": "New Street"}
    session.dispatch(AddRows(table="node", rows=[node]))
    result = session.dispatch(AddRows(table="link", rows=[link]))
    assert result["warnings"] == [] and cell(session, 9001, "name") == ["New Street"]


def test_undo_with_nothing_is_a_recorded_failure(session):
    with pytest.raises(ActionError, match="nothing to undo"):
        session.dispatch(UndoEdit())
    assert not session.history[-1].ok


def test_transit_edits_are_not_supported_yet(session):
    with pytest.raises(NotSupportedYet):
        session.dispatch(EditCells(component="transit", ids=[1], values={"lanes": 2}))


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "edit_cells", "ids": [], "values": {"lanes": 2}},
        {"type": "edit_cells", "ids": [1], "values": {}},
        {"type": "add_rows", "table": "link", "rows": []},
    ],
)
def test_empty_requests_fail_validation(session, bad):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        session.actions.parse(bad)


def test_a_replayed_history_reproduces_the_edits(session, tmp_path):
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    session.dispatch(EditCells(ids=[2], values={"lanes": 2}))
    session.dispatch(UndoEdit())
    from fastapi.testclient import TestClient
    from netstead.workbench import build_app

    history = TestClient(build_app(session)).get("/api/history").json()
    assert history["imports"][1:] == [
        "from netstead.workbench import EditCells",
        "from netstead.workbench import UndoEdit",
    ]
    script = "\n".join([*history["imports"], *(e["python"] for e in history["entries"])])
    other = Session(project_dir=tmp_path, environ=session._environ, parser=StubParser(), plugins=[])
    exec(script, {"app": other})  # replaying our own recorded snippets
    df = other.network("leavenworth").links_df()
    assert df.loc[df.link_id == 1, "free_speed"].tolist() == [30.0]
    assert df.loc[df.link_id == 2, "lanes"].tolist() == [1]


# ---------------------------------------------------------------------------- saving a copy


def outdir(tmp_path):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return str(out)


def test_save_writes_a_new_copy_and_never_the_source(session, tmp_path):
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    result = session.dispatch(SaveNetwork(output_dir=outdir(tmp_path), name="edited", output_format="parquet"))
    assert Path(result["output"]).parent == Path(outdir(tmp_path)) and result["edits_saved"] == 1
    assert result["edited_while_saving"] is False and result["accepted_warnings"] == 0
    assert any("stale" in n for n in result["notes"])  # corral's OutOfSyncWarning: kept as text, not raised
    assert session.state()["edits"]["leavenworth"]["dirty"] is False
    assert session.history[-1].imports == "from netstead.workbench import SaveNetwork"
    back = Network.from_source(result["output"]).tables["link"].to_pandas()
    assert back.loc[back.link_id == 1, "free_speed"].tolist() == [30.0]
    source = Network.from_source(SRC).tables["link"].to_pandas()
    assert source.loc[source.link_id == 1, "free_speed"].tolist() == [40.0]
    with pytest.raises(ActionError, match="already exists"):
        session.dispatch(SaveNetwork(output_dir=outdir(tmp_path), name="edited", output_format="parquet"))


def test_saving_with_open_warnings_needs_confirmation(session, tmp_path):
    session.dispatch(EditCells(table="node", ids=[1], values={"ctrl_type": "bogus"}))
    with pytest.raises(ActionError, match="1 open edit warning"):
        session.dispatch(SaveNetwork(output_dir=outdir(tmp_path), name="edited", output_format="parquet"))
    assert session.history[-1].result["warnings"][0]["code"] == "edit.enum"
    assert not (tmp_path / "out" / "edited").exists()
    result = session.dispatch(
        SaveNetwork(output_dir=outdir(tmp_path), name="edited", output_format="parquet", accept_warnings=True)
    )
    assert result["accepted_warnings"] == 1
    assert "accept_warnings=True" in session.history[-1].python  # a replay makes the same choice


def test_save_stays_inside_the_allowed_roots(session):
    with pytest.raises(PathNotAllowed):
        session.dispatch(SaveNetwork(output_dir="/etc", name="x", output_format="parquet"))


def test_save_refuses_the_source_folder(tmp_path, isolated_env):
    src = tmp_path / "src"
    shutil.copytree(SRC, src)
    (src / "sub").mkdir()
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[])
    s.dispatch(OpenNetwork(source=str(src)))
    s.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    for folder in (src, src / "sub"):
        with pytest.raises(ActionError, match="own source folder"):
            s.dispatch(SaveNetwork(output_dir=str(folder), name="edited", output_format="parquet"))
    assert sorted(p.name for p in src.iterdir()) == sorted([*(p.name for p in Path(SRC).iterdir()), "sub"])
    assert not any((src / "sub").iterdir())
    assert s.dispatch(SaveNetwork(output_dir=str(tmp_path), name="edited", output_format="parquet"))["output"]


def test_save_scrubs_the_warning_text(session, tmp_path, monkeypatch):
    from netstead.workbench import build

    real = build.write_output

    def warn_then_write(net, tmp, output_format, ctx):
        from corral.dataset.package import OutOfSyncWarning

        text = "Package source 'https://h.example/n?X-Amz-Signature=s3cr3t' has 1 stale table"
        warnings.warn(text, OutOfSyncWarning, stacklevel=2)
        real(net, tmp, output_format, ctx)

    monkeypatch.setattr(build, "write_output", warn_then_write)
    result = session.dispatch(SaveNetwork(output_dir=outdir(tmp_path), name="edited", output_format="parquet"))
    assert any("https://h.example/n" in n for n in result["notes"])
    assert not any("s3cr3t" in n for n in result["notes"])


# ---------------------------------------------------------------------------- what the live check covers


def test_a_raw_delete_flags_the_rows_left_pointing_at_it(session):
    lanes = handle(session).roadway.tables["lane"].to_pandas()
    orphans = set(lanes.loc[lanes.link_id == 1, "lane_id"])
    edit = Edit(op="delete_rows", table="link", payload={"predicate": lambda t: t.link_id == 1})
    session.mutate("leavenworth", [edit], note="raw delete", origin="cards")  # no cascade: lanes stay behind
    found = {(w.code, w.table, w.key) for w in session.edit_check("leavenworth").warnings}
    assert found == {("edit.fk_missing", "lane", k) for k in orphans}
    assert session.edits["leavenworth"].last().touched == {"lane": orphans}


def test_rows_left_at_a_cycle_are_flagged(session, monkeypatch):
    from netstead.workbench import editcheck, editing
    from netstead.workbench.related import ForeignKey

    extra = [
        ForeignKey("link_tod", "link_tod_id", "lane", "lane_id"),
        ForeignKey("lane", "lane_num", "link_tod", "link_tod_id"),
    ]
    for module in (editing, editcheck):
        real = module.foreign_keys
        monkeypatch.setattr(
            module,
            "foreign_keys",
            lambda spec, cols, self_refs=False, real=real: [*real(spec, cols, self_refs=self_refs), *extra],
        )
    session.dispatch(DeleteRows(ids=[1]))
    lanes = handle(session).roadway.tables["lane"].to_pandas()
    pointing = set(lanes.loc[lanes.lane_num == 1, "lane_id"])
    flagged = {w.key for w in session.edit_check("leavenworth").warnings if w.code == "edit.fk_missing"}
    assert pointing and flagged == pointing


def test_the_newest_edit_is_checked_before_older_ones(session, monkeypatch):
    from netstead.workbench import editcheck

    monkeypatch.setattr(editcheck, "MAX_CHECKED_KEYS", 1)
    session.dispatch(EditCells(table="node", ids=[1], values={"ctrl_type": "bogus"}))
    result = session.dispatch(EditCells(table="node", ids=[2], values={"ctrl_type": "bogus"}))
    assert [(w["key"], w["code"]) for w in result["warnings"]] == [(2, "edit.enum")]  # node 1 sorts first
    assert session.state()["edits"]["leavenworth"]["truncated"] is True


def test_a_crashed_check_is_reported_as_failed_not_truncated(session, monkeypatch):
    from netstead.workbench import session as session_module

    def boom(*args, **kwargs):
        raise RuntimeError("check crashed")

    monkeypatch.setattr(session_module, "check_rows", boom)
    session.dispatch(EditCells(ids=[1], values={"free_speed": 30}))
    summary = session.state()["edits"]["leavenworth"]
    assert summary["check_failed"] is True and summary["truncated"] is False and cell(session, 1) == [30.0]


# ---------------------------------------------------------------------------- undo and the ledger


def test_undo_refuses_after_a_change_the_ledger_did_not_see(session):
    from corral.editing.apply import reverse_edit

    h = handle(session)
    (result,) = mutate(session, plan_update(h.roadway, "link", [1], {"free_speed": 30}))
    reverse_edit(h.roadway, result)  # in place, behind the ledger's back
    h.bump()
    with pytest.raises(ActionError, match="changed outside the Workbench since; undo is unavailable"):
        session.undo_last("leavenworth", source="python")
    assert int((h.links_df().link_id == 1).sum()) == 1 and cell(session, 1) == [40.0]  # not reversed twice


def test_core_undo_last_refuses_a_python_entry(session):
    mutate(session, plan_update(handle(session).roadway, "link", [1], {"lanes": 2}))
    with pytest.raises(ActionError, match="made by python"):
        session.undo_last("leavenworth")  # the default source is the Workbench's
    assert len(session.edits["leavenworth"].entries) == 1


class Unwind(BaseAction):
    type: Literal["unwind.go"] = "unwind.go"
    mutates: ClassVar[bool] = True


def _unwind(host, action: Unwind) -> None:
    host.undo()
    raise RuntimeError("after the undo")


def test_a_failed_action_restores_what_its_revert_undid(tmp_path, isolated_env):
    plugin = WorkbenchPlugin(
        id="unwind", name="Unwind", version="0", requires_api=HOST_API, actions=(ActionSpec(Unwind, _unwind),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[plugin])
    s.dispatch(OpenNetwork(source=SRC))
    s.mutate(None, plan_update(s.network().roadway, "link", [1], {"free_speed": 30}).edits, note="x", origin="unwind")
    entry = s.edits["leavenworth"].last()
    assert not s.dispatch_recorded(Unwind()).ok
    assert s.edits["leavenworth"].last() == entry and s.edits["leavenworth"].version == s.network().version == 1
    assert cell(s, 1) == [30.0] and s.network().lineage == ["unwind: x"]
    assert s._hosts["unwind"].undo()["seq"] == entry.seq and cell(s, 1) == [40.0]  # still undoable afterwards
