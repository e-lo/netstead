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
    assert got["after"] == {"compose": None, "check": None}


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
