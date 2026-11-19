"""Host API 1.11: cards from the Changes pane's changes.

``Host.edit_changes`` says what pending edits did (with the strict facility forms of an update's links),
``Host.mark_in_card`` records the card they went into (rolled back with a failing Action), a save names its file
(``saved_to``), and the browser's ``wb.registerCardMaker`` enables the Changes pane's "Make a card…".
"""

from __future__ import annotations

from typing import ClassVar, Literal

import pytest
from netstead.fixtures import leavenworth
from netstead.select.parse import StubParser
from netstead.workbench import Session
from netstead.workbench.actions import DeleteRows, EditCells, OpenNetwork, SaveNetwork
from netstead.workbench.plugins import HOST_API, ActionError, ActionSpec, BaseAction, WorkbenchPlugin
from netstead.workbench.plugins.spec import api_compatible

SRC = str(leavenworth.parquet_dir())
NET = "leavenworth"


def test_this_netstead_provides_host_api_1_11():
    assert HOST_API == "1.11" and api_compatible("1.11") and api_compatible("1.10") and not api_compatible("1.12")


class Mark(BaseAction):
    """Mark edits as in a card, then fail if asked."""

    type: Literal["probe.mark"] = "probe.mark"
    mutates: ClassVar[bool] = True
    seqs: list[int]
    fail: bool = False


def _mark(host, action: Mark) -> dict:
    host.mark_in_card(None, action.seqs, "cards/main.yml")
    if action.fail:
        raise ActionError("the card could not be written")
    return {"marked": action.seqs}


@pytest.fixture
def session(tmp_path, isolated_env) -> Session:
    probe = WorkbenchPlugin(
        id="probe", name="Probe", version="0", requires_api=HOST_API, actions=(ActionSpec(Mark, _mark),)
    )
    s = Session(project_dir=tmp_path, environ=isolated_env, parser=StubParser(), plugins=[probe])
    s.dispatch(OpenNetwork(source=SRC))
    s.dispatch(EditCells(table="link", ids=[1, 2], values={"lanes": 3}))
    s.dispatch(DeleteRows(table="link", ids=[3]))
    return s


def test_edit_changes_says_what_each_edit_did(session):
    host = session._hosts["probe"]
    update, deletion = host.edit_changes(None, [1, 2])
    assert (update["seq"], update["card"], update["saved_to"]) == (1, None, None)
    (op,) = update["ops"]
    assert {k: op[k] for k in ("op", "table", "keys", "set", "existing")} == {
        "op": "update",
        "table": "link",
        "keys": [1, 2],
        "set": {"lanes": 3},
        "existing": {"lanes": 1},  # both links had 1 lane before
    }
    forms = op["projectcard"]  # the Find tab's strict forms of these links, as host.selection carries them
    assert forms["link_list"]["gmns"] == {"links": {"model_link_id": [1, 2], "ignore_missing": True}}
    assert forms["link_list"]["osm"]["links"]["osm_link_id"] == ["5836396", "5850530"]
    assert forms["segment_problem"] == "This selection has no named segment; use Link list"
    # A deleted link takes its lanes and time-of-day rows: each table is one op.
    assert [(o["op"], o["table"], o["keys"]) for o in deletion["ops"]] == [
        ("delete", "lane", [3, 4]),
        ("delete", "link_tod", [1]),
        ("delete", "link", [3]),
    ]
    assert [e["seq"] for e in host.edit_changes(None, [2, 1])] == [2, 1]  # in the order asked


def test_edit_changes_refuses_an_edit_the_network_does_not_have(session):
    with pytest.raises(ActionError, match="leavenworth has no pending edit #9"):
        session._hosts["probe"].edit_changes(None, [1, 9])


def test_marked_edits_are_in_a_card_and_no_longer_staged(session):
    assert session.state()["edits"][NET]["unsaved"] == 2
    session.dispatch(Mark(seqs=[1]))
    views = {e["seq"]: e for e in session._hosts["probe"].pending_edits().edits}
    assert (views[1]["card"], views[2]["card"]) == ("cards/main.yml", None)
    summary = session.state()["edits"][NET]
    assert (summary["unsaved"], summary["in_card"], summary["dirty"]) == (1, 1, True)
    session.dispatch(Mark(seqs=[2]))
    summary = session.state()["edits"][NET]
    assert (summary["unsaved"], summary["in_card"], summary["dirty"]) == (0, 2, False)
    session.dispatch(Mark(seqs=[2]))  # marked again (another card): only `marks` moves, so the pane reloads
    assert session.state()["edits"][NET]["marks"] == summary["marks"] + 1


def test_a_failing_action_takes_the_mark_back(session):
    with pytest.raises(ActionError, match="could not be written"):
        session.dispatch(Mark(seqs=[1, 2], fail=True))
    assert [e["card"] for e in session._hosts["probe"].pending_edits().edits] == [None, None]
    assert session.state()["edits"][NET]["in_card"] == 0


def test_marking_an_unknown_edit_is_refused(session):
    with pytest.raises(ActionError, match="no pending edit #7"):
        session.dispatch(Mark(seqs=[7]))


def test_a_saved_copy_names_its_file(session, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    result = session.dispatch(
        SaveNetwork(output_dir=str(out), name="edited", output_format="parquet", accept_warnings=True)
    )
    edits = session._hosts["probe"].pending_edits().edits
    assert all(e["saved"] for e in edits) and {e["saved_to"] for e in edits} == {result["output"]}


# ---------------------------------------------------------------------------- the browser: wb.registerCardMaker

MAKER = """(() => {
  const log = [];
  const makers = { register: (owner, fn) => { log.push(["register", owner]); return () => log.push(["off", owner]); } };
  const deps = { cardMakers: makers, onError: (id, phase, e) => log.push(["error", phase, e.message]),
    store: { get: () => ({}), subscribe: () => () => {} }, frozenCopy: x => x };
  const { wb, rollback } = createWb("probe", deps);
  const off = wb.registerCardMaker(() => { throw new Error("boom"); });
  let refused = "";
  try { wb.registerCardMaker("nope"); } catch (e) { refused = e.message; }
  rollback();
  const bare = createWb("old", { store: deps.store, frozenCopy: x => x }).wb;
  return { log, refused, typeofOff: typeof off, bare: typeof bare.registerCardMaker };
})()"""


def test_a_plugin_registers_a_card_maker_and_a_rollback_removes_it(node_module):
    got = node_module("wbhost.js", ["createWb"], MAKER)
    assert got["log"] == [["register", "probe"], ["off", "probe"]]
    assert got["refused"] == "probe: registerCardMaker needs a function"
    assert got["typeofOff"] == "function"
    assert got["bare"] == "undefined"  # an older host has none: a plugin checks typeof wb.registerCardMaker


def test_make_a_card_is_enabled_only_with_a_card_maker(node_module):
    got = node_module(
        "editmodel.js", ["cardButton"], "[cardButton(false, 2), cardButton(true, 1), cardButton(true, 3)]"
    )
    assert got == [
        {"reason": "Make a card…: needs the project-cards plugin", "tip": ""},
        {"reason": "", "tip": "Make a card from this change…"},
        {"reason": "", "tip": "Make a card from these 3 changes…"},
    ]


ROWS = (
    '[{rows: {link: 1}, saved: false}, {rows: {link: 2}, saved: true, saved_to: "/out/copy"},'
    ' {rows: {link: 1}, saved: false, card: "/cards/main.yml"}, {rows: {node: 1}, saved: true, card: "copied YAML"},'
    " {rows: {link: 1}, saved: true}]"
)


def test_a_change_row_names_its_file(node_module):
    got = node_module(
        "editmodel.js",
        ["changeDetail", "changeTip", "stagedCells"],
        f"[{ROWS}.map(changeDetail), {ROWS}.map(changeTip)]",
    )
    assert got[0] == [
        "1 link · Staged",
        "2 link · Saved: copy",
        "1 link · In a card: main.yml",
        "1 node · Saved · In a card: copied YAML",
        "1 link · Saved",  # saved before 1.11: no file name
    ]
    assert got[1][1] == "Saved in /out/copy. Click to show it on the map and in Details."
    assert got[1][2] == "In a card: /cards/main.yml. Click to show it on the map and in Details."
    assert got[1][0].startswith("Staged: applied in this session")


def test_a_change_in_a_card_has_no_staged_outline(node_module):
    entries = (
        '[{saved: false, card: "c.yml", cells: {link: {keys: [1], fields: ["lanes"]}}},'
        ' {saved: false, cells: {link: {keys: [2], fields: ["lanes"]}}}]'
    )
    got = node_module("editmodel.js", ["stagedCells"], f"stagedCells({{entries: {entries}}})")
    assert got == {"link": {"2": ["lanes"]}}
