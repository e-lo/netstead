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
    assert got["after"] == {"compose": None, "check": None, "catalog": None, "create": None, "inspect": None}


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


# ------------------------------------------------------------------------------------------- the New card mode


def test_the_step_rules(run_js):
    got = run_js(
        MODEL,
        """
import { chooseType, createAfter, startCreate, stepIndicator, typeChoices } from "./cardmodel.js";
const states = m => stepIndicator(m).map(s => `${s.key}:${s.state}`);
const start = startCreate();
const prop = chooseType(start, "roadway_property_change");
const add = chooseType(start, "roadway_addition");
const form = createAfter(prop, "form", "ski");
const previewed = createAfter(form, "previewed");
const added = createAfter(previewed, "added");
const errors = [];
for (const t of ["transit_routing_change", "nope"]) {
  try { chooseType(start, t); } catch (e) { errors.push(e.message); }
}
try { createAfter(start, "jump"); } catch (e) { errors.push(e.message); }
console.log(JSON.stringify({
  start: states(start), prop: [prop.step, states(prop)], add: [add.step, states(add)],
  form: [form.step, form.saved, states(form)], previewed: [previewed.step, states(previewed)],
  added: states(added),
  back: [createAfter(previewed, "back"), createAfter(prop, "back").step,
         createAfter(createAfter(add, "form"), "back").step],
  choices: typeChoices().map(c => [c.type, c.enabled, c.note]),
  errors,
}));
""",
    )
    assert got["start"] == ["type:current", "select:todo", "details:todo", "add:todo"]
    assert got["prop"] == ["select", ["type:done", "select:current", "details:todo", "add:todo"]]
    # An addition selects nothing: its form opens at once and Select is skipped.
    assert got["add"] == ["details", ["type:done", "select:skipped", "details:current", "add:todo"]]
    assert got["form"] == ["details", "ski", ["type:done", "select:done", "details:current", "add:todo"]]
    assert got["previewed"] == ["add", ["type:done", "select:done", "details:done", "add:current"]]
    assert got["added"] == ["type:done", "select:done", "details:done", "add:done"]
    back, from_select, from_addition_form = got["back"]
    assert back == {"step": "select", "type": "roadway_property_change", "saved": None}
    assert from_select == "type" and from_addition_form == "type"
    assert [c[1] for c in got["choices"]] == [True, True, True, False, False, False, False]
    assert got["choices"][3] == ["transit_property_change", False, "not yet supported"]
    assert got["errors"] == [
        "Transit routing change is not yet supported",
        "no change type nope",
        "no creation step event jump",
    ]


def test_the_select_step_counts_the_shared_selection_and_lists_saved_ones(run_js):
    got = run_js(
        MODEL,
        """
import { savedChoices, selectionStatus } from "./cardmodel.js";
const preview = { net_id: "base-2", base: "base", describe: "x", stale: false };
const at = (selection, extra = {}) => ({ selection, plugins: { project_cards: { preview } }, ...extra });
const resolved = { net_id: "base", status: "resolved", link_ids: [1, 2, 3], projectcard: { picked: true } };
const saved = [{ name: "ski", net_id: "base", links: 4 }, { name: "one", net_id: "base", links: 1 },
               { name: "far", net_id: "other", links: 2 }];
console.log(JSON.stringify({
  none: selectionStatus(at(null), "base"),
  ready: selectionStatus(at(resolved), "base"),
  onPreview: selectionStatus(at({ ...resolved, net_id: "base-2" }), "base-2").ready,
  baseFromPreview: selectionStatus(at(resolved), "base-2").ready,
  elsewhere: selectionStatus(at({ ...resolved, net_id: "other" }), "base").ready,
  one: selectionStatus(at({ ...resolved, link_ids: [5] }), "base").label,
  unresolved: selectionStatus(at({ ...resolved, status: "ambiguous", projectcard: null }), "base"),
  saved: savedChoices(at(null, { saved_selections: saved }), "base-2"),
  noSaved: savedChoices(null, "base"),
}));
""",
    )
    assert got["none"] == {"count": 0, "ready": False, "label": "Nothing selected on this network yet."}
    assert got["ready"] == {"count": 3, "ready": True, "label": "3 links selected."}
    assert got["onPreview"] is True and got["baseFromPreview"] is True and got["elsewhere"] is False
    assert got["one"] == "1 link selected."
    assert got["unresolved"]["ready"] is False and "not resolved yet" in got["unresolved"]["label"]
    assert got["saved"] == [{"name": "ski", "label": "ski (4 links)"}, {"name": "one", "label": "one (1 link)"}]
    assert got["noSaved"] == []


def test_the_controller_walks_the_steps_with_the_existing_actions(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
const steps = [];
const at = () => steps.push(ctl.state.create && ctl.state.create.step);
let refused = null;
try { await ctl.useFacility(); } catch (e) { refused = e.message; }
await ctl.start("roadway_deletion", { saved: "ski" });  // a right-click form is open: New card closes it
await ctl.preview({ options: {} });
await ctl.beginCreate(); at();
await ctl.chooseCreateType("roadway_property_change"); at();
await ctl.useFacility(); at();
await ctl.preview({ columns: { lanes: 3 }, options: {} }); at();
await ctl.backCreate(); at();  // leaving the form closes it and its preview
await ctl.useFacility({ saved: "ski" }); at();
await ctl.add({ columns: { lanes: 3 }, options: {} }); at();
ctl.anotherChange(); at();
await ctl.chooseCreateType("roadway_addition"); at();
await ctl.cancel(); at();
let empty = null;
try { await ctl.selectByText("  "); } catch (e) { empty = e.message; }
await ctl.selectByText("main street", "base");
ctl.endCreate();
console.log(JSON.stringify({ steps, refused, empty, compose: ctl.state.compose,
  calls: calls.map(c => c[0] !== "dispatch" ? c
    : [c[1].type, c[1].saved_selection || c[1].use_selection || c[1].utterance || null]) }));
""",
    )
    assert got["refused"] == "the New card mode is not open"
    assert got["steps"] == ["type", "select", "details", "add", "select", "details", "added", "type", "details", None]
    assert got["empty"].startswith("Describe the links to select")
    assert got["calls"] == [
        ["get", "/form?type=roadway_deletion&saved=ski"],
        ["project_cards.preview", "ski"],
        ["project_cards.close_preview", None],  # New card closed the right-click form and its preview
        ["get", "/form?type=roadway_property_change&use_selection=true"],
        ["project_cards.preview", True],
        ["project_cards.close_preview", None],  # Back from the form
        ["get", "/form?type=roadway_property_change&saved=ski"],
        ["project_cards.add_change", "ski"],
        ["get", "/form?type=roadway_addition"],  # an addition's form opens with its type
        ["select", "main street"],
    ]
    assert got["compose"] is None


# ------------------------------------------------------------------------------------------- editing a card


def test_commit_choices_and_the_edit_prompt(run_js):
    got = run_js(
        MODEL,
        """
import { baseName, commitChoices, editPrompt } from "./cardmodel.js";
const fields = { project: "P", path: "/cards/main.yml", tags: [], overwrite: false, net_id: "base" };
const source = { card_id: "main.yml", path: "/cards/main.yml" };
const good = { project: "Main", badge: "✓", tip: "Applies to base" };
const bad = { project: "Main", badge: "✗", tip: "lanes on 1 link: missing 9" };
console.log(JSON.stringify({
  plain: commitChoices(fields),
  same: commitChoices(fields, source),
  relative: commitChoices({ ...fields, path: "cards/main.yml" }, source)[1].disabled,
  other: commitChoices({ ...fields, path: "/cards/main-v2.yml", overwrite: true }, source)[1],
  names: [baseName("/a/b/c.yml"), baseName("c:\\\\x\\\\d.json"), baseName(""), baseName(null)],
  empty: editPrompt(null, good), emptyBad: editPrompt({ count: 0 }, bad).ask,
  one: editPrompt({ count: 1 }, good), two: editPrompt({ count: 2 }, bad).ask,
}));
""",
    )
    assert got["plain"] == [{"key": "commit", "label": None, "fields": got["plain"][0]["fields"], "disabled": ""}]
    over, as_new = got["same"]
    assert over["label"] == "Save over main.yml" and over["fields"]["overwrite"] is True
    assert over["fields"]["path"] == "/cards/main.yml" and over["disabled"] == ""
    assert as_new["label"] == "Save as new file" and as_new["disabled"].startswith("Give a different card file")
    assert got["relative"].startswith("Give a different")
    assert got["other"]["disabled"] == "" and got["other"]["fields"]["overwrite"] is False  # never replaces
    assert got["names"] == ["c.yml", "d.json", "", ""]
    assert got["empty"] == {"replace": False, "ask": ""}
    assert got["emptyBad"] == "Main does not apply to this network as it is: lanes on 1 link: missing 9"
    assert got["one"]["replace"] is True
    assert got["one"]["ask"] == (
        "The draft card has 1 uncommitted change. Edit undoes it (network edits included) and loads Main instead."
    )
    assert got["two"].startswith("The draft card has 2 uncommitted changes. Edit undoes them")
    assert got["two"].endswith("as it is: lanes on 1 link: missing 9")


def test_the_controller_removes_edits_and_loads_cards(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
const DRAFT = { changes: [
  { index: 0, type: "roadway_property_change", targets: { links: [3, 4] } },
  { index: 1, type: "roadway_addition", targets: { links: [340] } },
  { index: 2, type: "roadway_deletion", targets: { links: [] } } ] };
const get = wb.api.get;
wb.api.get = async path => (path.startsWith("/card?") ? (calls.push(["get", path]), DRAFT) : get(path));
const dispatch = wb.api.dispatch;
wb.api.dispatch = async action => {
  if (action.type === "select") server.selection = { link_ids: [...action.link_ids].reverse() };  // arrives later
  return dispatch(action);
};
await ctl.removeChange(1, "base");
await ctl.editCard("main.yml");
await ctl.editCard("main.yml", { replaceDraft: true, netId: "base" });
await ctl.editChange(0, "base");
const outdated = ctl.selectionChanged();  // the selection the form was read for is the one it shows
await ctl.editChange(1, "base");
await ctl.editChange(2);
let missing = null;
try { await ctl.editChange(5, "base"); } catch (e) { missing = e.message; }
console.log(JSON.stringify({ outdated, missing, calls: calls.map(c => (c[0] === "dispatch" ? c[1] : c)) }));
""",
    )
    assert got["outdated"] is False
    assert got["missing"] == "The draft has no change 6"
    assert got["calls"] == [
        {"type": "project_cards.remove_change", "index": 1, "net_id": "base"},
        {"type": "project_cards.edit_card", "card_id": "main.yml", "replace_draft": False},
        {"type": "project_cards.edit_card", "card_id": "main.yml", "replace_draft": True, "net_id": "base"},
        ["get", "/card?draft=true&net_id=base"],
        {"type": "project_cards.remove_change", "index": 0, "net_id": "base"},
        {"type": "select", "link_ids": [3, 4], "net_id": "base"},
        ["get", "/form?type=roadway_property_change&use_selection=true"],
        ["get", "/card?draft=true&net_id=base"],
        {"type": "project_cards.remove_change", "index": 1, "net_id": "base"},
        ["get", "/form?type=roadway_addition"],  # an addition: no selection
        ["get", "/card?draft=true"],
        {"type": "project_cards.remove_change", "index": 2},
        ["get", "/form?type=roadway_deletion&use_selection=true"],  # no links to select: the form says so
        ["get", "/card?draft=true&net_id=base"],
    ]


# ------------------------------------------------------------------------------------------ editing the catalog


def test_the_catalog_file_forms(run_js):
    got = run_js(
        MODEL,
        """
import { fileLine, metaFields, metaForm, renameName, splitList } from "./cardmodel.js";
const detail = { project: "Main", tags: ["a", "b"], dependencies: { prerequisites: ["Bridge"], conflicts: [] } };
console.log(JSON.stringify({
  split: splitList(" a, ,b ,, c "), empty: splitList(null),
  form: metaForm(detail), bare: metaForm({}),
  fields: metaFields({ project: " Main St ", tags: "x, y", prerequisites: "Bridge", corequisites: "", conflicts: "W" }),
  noProject: metaFields({ project: "  " }),
  rename: [renameName(" b.yml ", "a.yml"), renameName("", "a.yml"), renameName("a.yml", "a.yml")],
  lines: [
    { action: "renamed", card_id: "a.yml", to: "b.yml" }, { action: "duplicated", card_id: "a.yml", to: "a-copy.yml" },
    { action: "deleted", card_id: "a.yml", to: ".trash/" }, { action: "restored", card_id: "a.yml", to: "a.yml" },
    { action: "updated", card_id: "a.yml", to: "project, tags" },
  ].map(fileLine),
}));
""",
    )
    assert got["split"] == ["a", "b", "c"] and got["empty"] == []
    assert got["form"] == {
        "project": "Main",
        "tags": "a, b",
        "prerequisites": "Bridge",
        "corequisites": "",
        "conflicts": "",
    }
    assert got["bare"]["project"] == "" and got["bare"]["tags"] == ""
    assert got["fields"] == {
        "fields": {
            "project": "Main St",
            "tags": ["x", "y"],
            "dependencies": {"prerequisites": ["Bridge"], "corequisites": [], "conflicts": ["W"]},
        }
    }
    assert got["noProject"] == {"error": "Give the card a project name"}
    assert got["rename"] == [
        {"name": "b.yml"},
        {"error": "Give the card a new file name"},
        {"error": "a.yml already has that name"},
    ]
    assert got["lines"] == [
        "Renamed a.yml to b.yml",
        "Duplicated a.yml as a-copy.yml",
        "Moved a.yml to the catalog's .trash/ folder",
        "Restored a.yml",
        "Saved a.yml's project, tags",
    ]


def test_the_controller_dispatches_the_catalog_file_actions(run_js):
    got = run_js(
        BOTH,
        STUB_WB
        + """
await ctl.renameCard("a.yml", "b.yml");
await ctl.renameCard("a.yml", "b.yml", true);
await ctl.duplicateCard("a.yml");
await ctl.duplicateCard("a.yml", "c.yml");
await ctl.deleteCard("a.yml");
await ctl.restoreCard("a.yml");
await ctl.restoreCard("a.yml", true);
await ctl.updateCardMeta("a.yml", { project: "P", tags: [], dependencies: {} });
await ctl.cardDetail("a.yml", "base");
console.log(JSON.stringify(calls.map(c => (c[0] === "dispatch" ? c[1] : c))));
""",
    )
    assert got == [
        {"type": "project_cards.rename_card", "card_id": "a.yml", "new_name": "b.yml", "overwrite": False},
        {"type": "project_cards.rename_card", "card_id": "a.yml", "new_name": "b.yml", "overwrite": True},
        {"type": "project_cards.duplicate_card", "card_id": "a.yml"},
        {"type": "project_cards.duplicate_card", "card_id": "a.yml", "new_name": "c.yml"},
        {"type": "project_cards.delete_card", "card_id": "a.yml"},
        {"type": "project_cards.restore_card", "card_id": "a.yml"},
        {"type": "project_cards.restore_card", "card_id": "a.yml", "overwrite": True},
        {
            "type": "project_cards.update_card_meta",
            "card_id": "a.yml",
            "project": "P",
            "tags": [],
            "dependencies": {},
        },
        ["get", "/card?card_id=a.yml&net_id=base"],
    ]
