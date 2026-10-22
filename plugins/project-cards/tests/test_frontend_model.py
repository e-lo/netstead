"""cardmodel.js and controller.js under node: the change a form describes, the badge, and the panel's flow."""

from pathlib import Path

import netstead_project_cards

STATIC = Path(netstead_project_cards.__file__).parent / "static"
MODEL = {"cardmodel.js": STATIC / "cardmodel.js"}
BOTH = {**MODEL, "controller.js": STATIC / "controller.js"}


def test_a_property_change_from_form_values(run_js):
    got = run_js(
        MODEL,
        """
import { propertyChange } from "./cardmodel.js";
const set = propertyChange({ lanes: 3, name: "", free_speed: null }, { mode: "set", note: "widened" }, { lanes: 1 });
const delta = propertyChange({ lanes: "1" }, { mode: "change", conflict: "warn" }, {});
const errors = [];
for (const [columns, options] of [[{ lanes: "" }, {}], [{ lanes: "two" }, { mode: "change" }]]) {
  try { propertyChange(columns, options, {}); } catch (e) { errors.push(e.message); }
}
console.log(JSON.stringify({ set, delta, errors }));
""",
    )
    assert got["set"] == {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
        "note": "widened",
    }
    assert got["delta"] == {"type": "roadway_property_change", "property_changes": {"lanes": {"change": 1}}}
    assert got["errors"] == [
        "give a new value for at least one property",
        "lanes: change is a number, the delta to add",
    ]


def test_deletion_addition_and_the_action(run_js):
    got = run_js(
        MODEL,
        """
import { addition, changeAction, deletion, needsSelection } from "./cardmodel.js";
const del = deletion({ clean_nodes: true });
const add = addition({ link_id: 340, from_node_id: 1, to_node_id: 122, name: "", lanes: 1 },
                     { node_id: 122, x_coord: 1, y_coord: 2 });
const linkOnly = addition({ link_id: 340, from_node_id: 1, to_node_id: 2 }, {});
console.log(JSON.stringify({
  del, add, linkOnly,
  onSelection: changeAction("add_change", del),
  onSaved: changeAction("preview", del, { saved: "ski", netId: "base" }),
  addAction: changeAction("add_change", add),
  needs: ["roadway_property_change", "roadway_deletion", "roadway_addition"].map(needsSelection),
}));
""",
    )
    assert got["del"] == {"type": "roadway_deletion", "clean_nodes": True}
    assert got["add"]["links"] == [{"link_id": 340, "from_node_id": 1, "to_node_id": 122, "lanes": 1}]
    assert got["add"]["nodes"] == [{"node_id": 122, "x_coord": 1, "y_coord": 2}] and got["linkOnly"]["nodes"] == []
    assert got["onSelection"] == {"type": "project_cards.add_change", "change": got["del"], "use_selection": True}
    assert got["onSaved"] == {
        "type": "project_cards.preview",
        "change": got["del"],
        "net_id": "base",
        "saved_selection": "ski",
    }
    assert got["addAction"] == {"type": "project_cards.add_change", "change": got["add"]}
    assert got["needs"] == [True, True, False]


def test_the_badge_follows_the_draft_and_the_preview(run_js):
    got = run_js(
        MODEL,
        """
import { draftBadge, draftNet, draftOf, openPreview, warningLines } from "./cardmodel.js";
const state = { plugins: { project_cards: {
  drafts: { base: { count: 2, project: "", changes: [] }, named: { count: 0, project: "Lanes", changes: [] } },
  preview: { net_id: "base-2", base: "base", describe: "x", stale: true } } } };
console.log(JSON.stringify({
  badge: draftBadge(state, "base"), onPreview: draftBadge(state, "base-2"), none: draftBadge(state, "other"),
  named: draftBadge(state, "named"), net: draftNet(state, "base-2"), same: draftNet(state, "other"),
  draft: draftOf(state, "base-2").count, missing: draftOf({}, "base"),
  preview: openPreview(state), noPreview: openPreview({ plugins: {} }),
  lines: warningLines([{ table: "link", key: 1, message: "to_node_id 424242 is not a node" },
                       { table: "link", key: 5, message: "link 5: to_node_id 424242 is not a node.node_id" }]),
}));
""",
    )
    badge = {"text": "2", "dirty": True, "title": "2 uncommitted card change(s)"}
    assert got["badge"] == badge and got["onPreview"] == badge  # a preview shows its base's draft
    assert got["none"] is None and got["named"] is None
    assert got["net"] == "base" and got["same"] == "other" and got["draft"] == 2 and got["missing"] is None
    assert got["preview"] == {"net_id": "base-2", "base": "base", "describe": "x", "stale": True}
    assert got["noPreview"] is None
    # Core's messages already name the record: it is not named twice.
    assert got["lines"] == [
        "link 1: to_node_id 424242 is not a node",
        "link 5: to_node_id 424242 is not a node.node_id",
    ]


def test_the_commit_form_dialog_and_preview_texts(run_js):
    got = run_js(
        MODEL,
        """
import { commitFields, commitView, previewLine } from "./cardmodel.js";
const warn = { table: "link", key: 3, message: "to_node_id 999 is not a node" };
console.log(JSON.stringify({
  ok: commitFields({ project: " Lanes ", path: " cards/l.yml ", tags: "a, ,b", overwrite: 1 }, "base"),
  noNet: commitFields({ project: "P", path: "p.yml" }),
  missing: commitFields({ project: " ", path: "" }, "base"),
  noPath: commitFields({ project: "P" }),
  clean: commitView({ warnings: [], problems: [], yaml: "project: P" }),
  warned: commitView({ warnings: [warn, warn], problems: [] }),
  invalid: commitView({ warnings: [], problems: ["changes: is required"] }),
  preview: previewLine({ net_id: "base-2", base: "base", describe: "lanes on 2 links", stale: false }),
  stale: previewLine({ net_id: "base-2", base: "base", describe: "lanes on 2 links", stale: true }),
  none: previewLine(null),
}));
""",
    )
    assert got["ok"] == {
        "fields": {"project": "Lanes", "path": "cards/l.yml", "overwrite": True, "tags": ["a", "b"], "net_id": "base"}
    }
    assert got["noNet"] == {"fields": {"project": "P", "path": "p.yml", "overwrite": False, "tags": []}}
    assert got["missing"] == {"error": "Give the card a project name and a file"}
    assert got["noPath"] == {"error": "Give the card a file"}
    assert got["clean"] == {
        "lines": [],
        "needsConfirm": False,
        "blocked": False,
        "summary": "No live edit warnings.",
        "problems": "",
        "button": "Commit",
    }
    assert got["warned"]["lines"] == ["link 3: to_node_id 999 is not a node"] * 2
    assert got["warned"]["needsConfirm"] is True and got["warned"]["blocked"] is False
    assert got["warned"]["summary"].startswith("2 live edit warnings.")
    assert got["warned"]["button"] == "Commit anyway"
    assert got["invalid"]["blocked"] is True
    assert got["invalid"]["problems"] == "Not a valid ProjectCard yet: changes: is required"
    assert got["preview"] == "Previewing lanes on 2 links on base-2."
    assert got["stale"] == "Previewing lanes on 2 links on base-2. base has changed since: preview again to see it."
    assert got["none"] == ""


# The stub's server state follows the preview Actions, as the real one does over SSE: the controller reads the open
# preview from it (`wb.store.get()`), never from its own memory, so a preview closed elsewhere is noticed.
STUB_WB = """
import { createController } from "./controller.js";
const calls = [];
const server = { plugins: { project_cards: { drafts: {}, preview: null } } };
const cards = server.plugins.project_cards;
const forms = {
  roadway_property_change: { supported: true, net_id: "base", existing: { lanes: 1 }, targets: { count: 2 } },
  roadway_deletion: { supported: true, net_id: "base", targets: { count: 1 } },
  roadway_addition: { supported: true, net_id: "base", next_ids: { link_id: 340, node_id: 122 } },
  transit_property_change: { supported: false, message: "transit_property_change is not yet supported" },
};
const refuse = new Set();
const wb = {
  api: {
    get: async path => { calls.push(["get", path]);
      if (path.startsWith("/commit-check")) {
        const net_id = new URLSearchParams(path.split("?")[1]).get("net_id") || "base";
        return { net_id, warnings: [{ code: "edit.fk_missing" }], problems: [], yaml: "" };
      }
      return forms[new URLSearchParams(path.split("?")[1]).get("type")]; },
    dispatch: async action => { calls.push(["dispatch", action]);
      if (refuse.has(action.type)) throw new Error(`${action.type} refused`);
      if (action.type === "project_cards.close_preview") cards.preview = null;
      if (action.type !== "project_cards.preview") return { count: 1, describe: "x" };
      cards.preview = { net_id: "net-2", base: "base", describe: "x", stale: false };
      return { preview: "net-2" }; },
  },
  store: { get: () => server },
  toast: message => calls.push(["toast", message]),
};
const ctl = createController(wb);
"""

CONTROLLER_HARNESS = (
    STUB_WB
    + """
const values = { columns: { lanes: 3 }, options: { mode: "set" } };
await ctl.start("transit_property_change");
await ctl.start("roadway_property_change");
const afterPreview = await ctl.preview(values);
await ctl.add(values);
await ctl.check({ project: "Lanes", net_id: "base" });
await ctl.commit({ project: "Lanes", path: "cards/lanes.yml", tags: [], net_id: "base" });
console.log(JSON.stringify({ calls, afterPreview, compose: ctl.state.compose, preview: cards.preview }));
"""
)


def test_the_controller_reads_the_form_then_dispatches_actions(run_js):
    got = run_js(BOTH, CONTROLLER_HARNESS)
    calls = got["calls"]
    assert calls[0] == ["get", "/form?type=transit_property_change"]
    assert calls[1] == ["toast", "transit_property_change is not yet supported"]
    assert calls[2] == ["get", "/form?type=roadway_property_change&use_selection=true"]
    change = {
        "type": "roadway_property_change",
        "property_changes": {"lanes": {"set": 3, "existing": 1, "existing_value_conflict": "error"}},
    }
    # The form was read for "base": the Action names it, so it lands where the form looked.
    on_base = {"change": change, "net_id": "base", "use_selection": True}
    assert calls[3] == ["dispatch", {"type": "project_cards.preview", **on_base}]
    assert got["afterPreview"] == {"preview": "net-2"}
    assert calls[4] == ["dispatch", {"type": "project_cards.add_change", **on_base}]
    assert calls[5] == ["dispatch", {"type": "project_cards.close_preview"}]  # adding closes the preview
    assert calls[6] == ["get", "/commit-check?project=Lanes&net_id=base"]
    assert calls[7] == [
        "dispatch",
        {
            "type": "project_cards.commit",
            "project": "Lanes",
            "path": "cards/lanes.yml",
            "tags": [],
            "net_id": "base",
            "confirm_warnings": True,
        },
    ]  # the dialog showed a warning: committing confirms it
    assert len(calls) == 8
    assert got["compose"] is None and got["preview"] is None


def test_a_refused_add_keeps_the_form_and_the_preview(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
await ctl.preview({ options: { clean_nodes: false } });
refuse.add("project_cards.add_change");
let error = null;
try { await ctl.add({ options: {} }); } catch (e) { error = e.message; }
const kept = { compose: ctl.state.compose !== null, preview: cards.preview && cards.preview.net_id };
await ctl.cancel();
await ctl.start("roadway_addition");
const addition = ctl.changeFrom({ link: { link_id: 340, from_node_id: 1, to_node_id: 2 }, node: {} });
await ctl.undo();
await ctl.commit({ project: "No check", path: "a.yml" });
await ctl.importCard("cards/a.yml");
await ctl.cancel();  // no preview open: nothing to close
console.log(JSON.stringify({ calls, error, kept, addition, after: ctl.state }));
""",
    )
    calls = got["calls"]
    assert calls[0] == ["get", "/form?type=roadway_deletion&saved=ski"]
    deletion = {"type": "roadway_deletion", "clean_nodes": False}
    assert calls[1][1] == {
        "type": "project_cards.preview",
        "change": deletion,
        "net_id": "base",
        "saved_selection": "ski",
    }
    assert got["error"] == "project_cards.add_change refused"
    assert got["kept"] == {"compose": True, "preview": "net-2"}  # nothing closed on a refusal
    assert calls[3] == ["dispatch", {"type": "project_cards.close_preview"}]  # cancel closes the preview
    assert calls[4] == ["get", "/form?type=roadway_addition"]
    assert got["addition"] == {
        "type": "roadway_addition",
        "links": [{"link_id": 340, "from_node_id": 1, "to_node_id": 2}],
        "nodes": [],
    }
    assert calls[5:] == [
        ["dispatch", {"type": "project_cards.undo_change"}],
        # No check was read: nothing was shown, so nothing is confirmed.
        [
            "dispatch",
            {"type": "project_cards.commit", "project": "No check", "path": "a.yml", "confirm_warnings": False},
        ],
        ["dispatch", {"type": "project_cards.import", "path": "cards/a.yml"}],
    ]
    assert got["after"] == {"compose": None, "check": None, "catalog": None}


def test_a_preview_closed_elsewhere_is_not_closed_again(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
await ctl.preview({ options: {} });
cards.preview = null;  // closed from the palette, the assistant or another tab: state() no longer reports it
await ctl.add({ options: {} });
await ctl.start("roadway_deletion", { saved: "ski" });
cards.preview = { net_id: "net-9", base: "base", describe: "y", stale: true };  // opened elsewhere
await ctl.cancel();
console.log(JSON.stringify(calls.map(c => c[0] === "dispatch" ? c[1].type : c[0])));
""",
    )
    assert got == [
        "get",
        "project_cards.preview",
        "project_cards.add_change",  # and no close_preview: the server says none is open
        "get",
        "project_cards.close_preview",  # cancel closes the preview the server reports
    ]


def test_an_add_that_succeeded_resolves_even_when_closing_the_preview_is_refused(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
await ctl.preview({ options: {} });
refuse.add("project_cards.close_preview");
const result = await ctl.add({ options: {} });  // must not reject: a retry would add the change twice
console.log(JSON.stringify({ result, compose: ctl.state.compose, calls: calls.slice(-2) }));
""",
    )
    assert got["result"] == {"count": 1, "describe": "x"} and got["compose"] is None
    assert got["calls"] == [
        ["dispatch", {"type": "project_cards.close_preview"}],
        ["toast", "The change was added; the preview stayed open: project_cards.close_preview refused"],
    ]


def test_commit_confirms_only_the_warnings_the_dialog_showed(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
const confirmed = () => calls.filter(c => c[0] === "dispatch" && c[1].type === "project_cards.commit")
  .map(c => c[1].confirm_warnings);
const fields = { project: "P", path: "p.yml", net_id: "base" };
// Refused for another reason (the file exists): the retry still confirms the warnings the dialog showed.
await ctl.check(fields);
refuse.add("project_cards.commit");
try { await ctl.commit(fields); } catch (e) { /* the dialog shows it */ }
refuse.delete("project_cards.commit");
await ctl.commit({ ...fields, overwrite: true });
await ctl.commit(fields);  // committed: that check is spent
// A check for another network confirms nothing here.
await ctl.check({ ...fields, net_id: "other" });
await ctl.commit(fields);
// Anything that changes the draft makes a check stale.
const stale = [];
const addOne = async () => { await ctl.start("roadway_deletion", { saved: "s" }); await ctl.add({ options: {} }); };
for (const step of [() => ctl.undo(), () => ctl.importCard("a.yml"), () => ctl.cancel(), addOne]) {
  await ctl.check(fields);
  await step();
  stale.push(ctl.state.check);
}
await ctl.undo({ netId: "base" });
const noForm = [];
for (const step of [() => ctl.preview({}), () => ctl.add({})]) {
  try { await step(); } catch (e) { noForm.push(e.message); }
}
console.log(JSON.stringify({ confirmed: confirmed(), stale, undo: calls.at(-1), noForm }));
""",
    )
    assert got["confirmed"] == [True, True, False, False]
    assert got["stale"] == [None, None, None, None]
    assert got["undo"] == ["dispatch", {"type": "project_cards.undo_change", "net_id": "base"}]
    assert got["noForm"] == ["no change form is open", "no change form is open"]


def test_the_change_types_match_the_plugins(run_js):
    # cardmodel.js names the types to register commands at activation, before any state arrives: pin them here.
    from netstead_project_cards.flow import SELECTABLE
    from netstead_project_cards.types import ROADWAY_TYPES, TRANSIT_TYPES

    got = run_js(
        MODEL,
        """
import { ROADWAY_TYPES, TRANSIT_TYPES, TYPE_TITLES, needsSelection } from "./cardmodel.js";
const all = [...ROADWAY_TYPES, ...TRANSIT_TYPES];
console.log(JSON.stringify({ roadway: ROADWAY_TYPES, transit: TRANSIT_TYPES, titled: Object.keys(TYPE_TITLES),
  selectable: all.filter(needsSelection) }));
""",
    )
    assert set(got["roadway"]) == ROADWAY_TYPES and set(got["transit"]) == TRANSIT_TYPES
    assert set(got["titled"]) == ROADWAY_TYPES | TRANSIT_TYPES and set(got["selectable"]) == SELECTABLE


def test_an_unsupported_type_leaves_the_open_form_open(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
const started = await ctl.start("transit_property_change");
await ctl.preview({ options: {} });  // the deletion form still works
console.log(JSON.stringify({ started, type: ctl.state.compose && ctl.state.compose.type, last: calls.at(-1) }));
""",
    )
    assert got["started"] is None and got["type"] == "roadway_deletion"
    assert got["last"][1]["type"] == "project_cards.preview"


def test_a_step_already_running_refuses_a_second_one(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.start("roadway_deletion", { saved: "ski" });
const first = ctl.add({ options: {} }), second = ctl.add({ options: {} });
const settled = await Promise.allSettled([first, second]);
await ctl.check({ project: "P", net_id: "base" });
const fields = { project: "P", path: "p.yml" };
const commits = await Promise.allSettled([ctl.commit(fields), ctl.commit(fields)]);
const after = await ctl.undo();  // free again once the step settles
const dispatched = calls.filter(c => c[0] === "dispatch").map(c => c[1].type);
console.log(JSON.stringify({ dispatched, settled: settled.map(s => s.status), second: settled[1].reason.message,
  commits: commits.map(s => s.status), after }));
""",
    )
    assert got["dispatched"] == ["project_cards.add_change", "project_cards.commit", "project_cards.undo_change"]
    assert got["settled"] == ["fulfilled", "rejected"] and got["commits"] == ["fulfilled", "rejected"]
    assert got["second"] == "Wait for the last step to finish"
    assert got["after"] == {"count": 1, "describe": "x"}


def test_a_check_goes_stale_when_its_draft_changes_elsewhere(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
cards.drafts.base = { count: 1, project: "", changes: [{ describe: "a" }] };
await ctl.check({ project: "P", net_id: "base" });
cards.drafts.other = { count: 1, project: "", changes: [] };  // another network's draft: still current
const unrelated = ctl.serverChanged();
const kept = ctl.state.check !== null;
cards.drafts.base = { count: 2, project: "", changes: [{ describe: "a" }, { describe: "b" }] };  // another tab added
const changed = ctl.serverChanged();
const again = ctl.serverChanged();  // already cleared: nothing more to report
await ctl.commit({ project: "P", path: "p.yml", net_id: "base" });
console.log(JSON.stringify({ unrelated, kept, changed, again, check: ctl.state.check, commit: calls.at(-1)[1] }));
""",
    )
    assert got["unrelated"] is False and got["kept"] is True
    assert got["changed"] is True and got["again"] is False and got["check"] is None
    assert got["commit"]["confirm_warnings"] is False  # the warnings shown are no longer the draft's


def test_an_import_needs_a_path(run_js):
    got = run_js(
        MODEL,
        """
import { importPath } from "./cardmodel.js";
console.log(JSON.stringify([importPath("  cards/a.yml "), importPath("  "), importPath()]));
""",
    )
    missing = {"error": "Give a card or edit log path"}
    assert got == [{"path": "cards/a.yml"}, missing, missing]


def test_a_selection_change_outdates_a_form_read_from_the_selection(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
const pick = ids => ({ net_id: "base", status: "resolved", link_ids: ids, projectcard: { picked: true },
                       anchors: [] });
server.selection = pick([1, 2]);
await ctl.start("roadway_property_change");
server.selection = { ...pick([1, 2]), net_id: "net-2", anchors: [{ node_id: 5 }] };  // same links, on the preview
const sameLinks = ctl.selectionChanged();
server.selection = pick([1, 2, 3]);
const changed = ctl.selectionChanged();
const again = ctl.selectionChanged();  // already outdated: nothing more to report
const refused = [];
for (const step of [() => ctl.preview({ columns: { lanes: 3 } }), () => ctl.add({ columns: { lanes: 3 } })]) {
  try { await step(); } catch (e) { refused.push(e.message); }
}
const outdated = ctl.state.compose.outdated;
await ctl.reload();  // reads the form again, for the selection as it is now
const reloaded = [ctl.state.compose.outdated, ctl.state.compose.type, calls.at(-1)];
// A saved selection, an addition: the shared selection does not feed them.
await ctl.start("roadway_deletion", { saved: "ski" });
server.selection = pick([4]);
const saved = ctl.selectionChanged();
await ctl.start("roadway_addition");
server.selection = null;
const addition = ctl.selectionChanged();
console.log(JSON.stringify({ sameLinks, changed, again, refused, outdated, reloaded, saved, addition,
  dispatched: calls.filter(c => c[0] === "dispatch").length }));
""",
    )
    assert got["sameLinks"] is False
    assert got["changed"] is True and got["again"] is False and got["outdated"] is True
    reload_first = "The selection changed since this form opened: reload the form"
    assert got["refused"] == [reload_first, reload_first] and got["dispatched"] == 0
    assert got["reloaded"] == [
        False,
        "roadway_property_change",
        ["get", "/form?type=roadway_property_change&use_selection=true"],
    ]
    assert got["saved"] is False and got["addition"] is False


CATALOG_SNAPSHOT = """
const ok = { ok: true, net_id: "base", version: 3, changes: [{ describe: "lanes on 2 links", resolved: 2,
  missing: [], reason: null }] };
const missing = { ok: false, net_id: "base", version: 3, changes: [
  { describe: "lanes on 3 links", resolved: 2, missing: [999999], reason: null },
  { describe: "delete 1 link", resolved: 1, missing: [], reason: null }] };
const reason = { ok: false, net_id: "base", version: 3, changes: [{ describe: "lanes on a query", resolved: 0,
  missing: [], reason: "query without provenance; re-resolution not supported" }] };
const entry = (card_id, applicability, extra = {}) => ({ card_id, path: `/cards/${card_id}`, project: card_id,
  tags: [], change_count: 1, applicability, ...extra });
const snapshot = { plugins: { project_cards: { drafts: {}, preview: null,
  catalog: { path: "/cards", rejected: [], cards: [
    entry("good.yml", ok, { project: "Good", tags: ["a", "b"], change_count: 2 }),
    entry("bad.yml", missing), entry("query.yml", reason), entry("new.yml", null)] },
  applied: [
    { card_id: "first", net_id: "base", path: "/c/first.yml", project: "First", applied_at: "2026-10-21T09:05:00+00:00",
      changes: 2, source: "committed", edit_ids: [1, 2] },
    { card_id: "second", net_id: "base", path: "/c/second.yml", project: "Second",
      applied_at: "2026-10-21T10:15:30+00:00", changes: 1, source: "catalog", edit_ids: [3] }] } } };
"""


def test_catalog_rows_badge_and_tip(run_js):
    got = run_js(
        MODEL,
        'import { catalogRows, catalogOf } from "./cardmodel.js";'
        + CATALOG_SNAPSHOT
        + """
const rows = catalogRows(snapshot);
console.log(JSON.stringify({ rows, none: catalogRows({ plugins: { project_cards: { catalog: null } } }),
  fresher: catalogRows(snapshot, { path: "/cards", cards: [entry("new.yml", ok)] }).map(r => r.badge),
  open: catalogOf(snapshot).path }));
""",
    )
    rows = {r["card_id"]: r for r in got["rows"]}
    assert [r["card_id"] for r in got["rows"]] == ["good.yml", "bad.yml", "query.yml", "new.yml"]
    assert rows["good.yml"]["badge"] == "✓" and rows["good.yml"]["tip"] == "Applies to base"
    good = rows["good.yml"]
    assert (good["project"], good["changes"], good["tags"]) == ("Good", 2, ["a", "b"])
    assert rows["bad.yml"]["badge"] == "✗" and rows["bad.yml"]["tip"] == "lanes on 3 links: missing 999999"
    assert rows["query.yml"]["badge"] == "✗"
    assert rows["query.yml"]["tip"] == "lanes on a query: query without provenance; re-resolution not supported"
    assert rows["new.yml"]["badge"] == "?" and rows["new.yml"]["tip"] == "Not checked yet"
    assert got["none"] == [] and got["fresher"] == ["✓"] and got["open"] == "/cards"


def test_apply_defaults_ignore_missing_only_for_missing_ids(run_js):
    got = run_js(
        MODEL,
        'import { applyDefaults, catalogRows, fewIds } from "./cardmodel.js";'
        + CATALOG_SNAPSHOT
        + """
const rows = catalogRows(snapshot);
console.log(JSON.stringify({ defaults: rows.map(r => [r.card_id, applyDefaults(r)]),
  none: applyDefaults(null), few: fewIds([1, 2, 3, 4, 5, 6, 7]) }));
""",
    )
    assert got["defaults"] == [
        ["good.yml", {"existing_value_conflict": "error", "ignore_missing": False}],
        ["bad.yml", {"existing_value_conflict": "error", "ignore_missing": True}],
        ["query.yml", {"existing_value_conflict": "error", "ignore_missing": False}],
        ["new.yml", {"existing_value_conflict": "error", "ignore_missing": False}],
    ]
    assert got["none"] == {"existing_value_conflict": "error", "ignore_missing": False}
    assert got["few"] == "1, 2, 3, 4, 5 (+2 more)"


def test_applied_rows_newest_first(run_js):
    got = run_js(
        MODEL,
        'import { appliedRows, summaryFileName } from "./cardmodel.js";'
        + CATALOG_SNAPSHOT
        + """
console.log(JSON.stringify({ rows: appliedRows(snapshot), none: appliedRows({}),
  name: [summaryFileName("leavenworth"), summaryFileName(null)] }));
""",
    )
    assert [(r["card_id"], r["source"], r["time"], r["changes"]) for r in got["rows"]] == [
        ("second", "catalog", "10:15", 1),
        ("first", "committed", "09:05", 2),
    ]
    assert got["rows"][0]["project"] == "Second"
    assert got["none"] == []
    assert got["name"] == ["leavenworth-cards-session.txt", "network-cards-session.txt"]


CATALOG_WB = STUB_WB.replace(
    'get: async path => { calls.push(["get", path]);',
    'get: async path => { calls.push(["get", path]);\n      if (path.startsWith("/catalog")) return listing;',
).replace(
    "const wb = {",
    'const listing = { path: "/cards", cards: [{ card_id: "a.yml", applicability: { ok: true } }], rejected: [] };\n'
    "const wb = {",
)


def test_controller_catalog_flow(run_js):
    got = run_js(
        BOTH,
        CATALOG_WB
        + """
wb.api.fetch = async path => { calls.push(["fetch", path]); return { ok: true, text: async () => "SUMMARY" }; };
const none = [ctl.catalog(), ctl.catalogChanged()];
cards.catalog = { path: "/cards", cards: [{ card_id: "a.yml", applicability: null }], rejected: [] };
const opened = ctl.catalogChanged();
const again = ctl.catalogChanged();
await ctl.loadCatalog("base");
const fresher = ctl.catalog().cards[0].applicability;
cards.catalog = { path: "/other", cards: [], rejected: [] };
const other = ctl.catalog().path;
cards.preview = { net_id: "net-2", base: "base", describe: "x", stale: false };
await ctl.applyCard("a.yml", { existing_value_conflict: "warn", ignore_missing: true }, "base");
const closed = cards.preview;
await ctl.previewCard("a.yml", "base");
const text = await ctl.summary("base");
cards.catalog = null;
const gone = [ctl.catalog(), ctl.catalogChanged()];
console.log(JSON.stringify({ none, opened, again, fresher, other, closed, text, gone,
  calls: calls.filter(c => c[0] !== "get" || c[1].startsWith("/catalog")) }));
""",
    )
    assert got["none"] == [None, False]
    assert got["opened"] is True and got["again"] is False
    assert got["fresher"] == {"ok": True}  # the loaded listing's check, not the snapshot's null
    assert got["other"] == "/other"  # a different catalog: the loaded listing no longer applies
    assert got["closed"] is None  # applying closes an open preview
    assert got["text"] == "SUMMARY"
    assert got["gone"] == [None, False]
    types = [c[1].get("type") if c[0] == "dispatch" else c[1] for c in got["calls"]]
    assert types == [
        "/catalog?net_id=base",
        "project_cards.apply_card",
        "project_cards.close_preview",
        "project_cards.preview_card",
        "/summary.txt?net_id=base",
    ]
    apply = next(c[1] for c in got["calls"] if c[0] == "dispatch" and c[1]["type"] == "project_cards.apply_card")
    assert apply == {
        "type": "project_cards.apply_card",
        "card_id": "a.yml",
        "existing_value_conflict": "warn",
        "ignore_missing": True,
        "net_id": "base",
    }
